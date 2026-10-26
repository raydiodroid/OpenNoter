"""进程退出 / 崩溃重启时，把进行中的笔记任务标成 FAILED。

设计取舍（审查报告 §6 P2-11）
--------------------------------
任务目前走 FastAPI ``BackgroundTasks`` + 进程内线程池，**没有** Redis/Celery
一类持久化队列。完整队列会牵涉：新组件、worker 进程、失败重试语义、与
SQLite 单写者的并发，对自托管单机场景成本偏高。

最小可靠方案：
1. **优雅退出**（SIGTERM / lifespan shutdown）：扫描 ``*.status.json``，
   把仍处于进行中的状态写成 FAILED，前端轮询会停，不再永远转圈。
2. **崩溃 / SIGKILL / OOM**：shutdown 钩子跑不到；**下次启动**再扫一次，
   同样标 FAILED（「进程重启，任务已中断」）。

这覆盖「已返回 task_id、进程却没了」的体验问题；**不**保证任务可恢复执行。
若要多副本可靠投递，需改用外部队列 + Postgres（见 README / compose 注释）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

from app.enmus.task_status_enums import TaskStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)

IN_PROGRESS_STATUSES: frozenset[str] = frozenset(
    {
        TaskStatus.PENDING.value,
        TaskStatus.PARSING.value,
        TaskStatus.DOWNLOADING.value,
        TaskStatus.TRANSCRIBING.value,
        TaskStatus.SUMMARIZING.value,
        TaskStatus.FORMATTING.value,
        TaskStatus.SAVING.value,
    }
)

DEFAULT_SHUTDOWN_MESSAGE = "服务正在关闭，任务已中断（当前未使用持久化队列，无法自动续跑）"
DEFAULT_STARTUP_MESSAGE = "服务重启后发现未完成任务，已标记失败（当前未使用持久化队列）"


def _note_output_dir() -> Path:
    return Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))


def iter_status_files(note_dir: Path | None = None) -> Iterable[Path]:
    root = note_dir if note_dir is not None else _note_output_dir()
    if not root.is_dir():
        return ()
    return root.glob("*.status.json")


def _read_status(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def fail_interrupted_tasks(
    *,
    reason: str,
    note_dir: Path | None = None,
) -> int:
    """把进行中的任务标为 FAILED。返回改写数量。

    通过 ``NoteGenerator._update_status`` 落盘 + 同步 DB，与正常失败路径一致。
    """
    from app.services import note as note_mod
    from app.services.note import NoteGenerator

    root = note_dir if note_dir is not None else _note_output_dir()
    # NoteGenerator._update_status 用模块级 NOTE_OUTPUT_DIR；测试/自定义目录时对齐。
    prev_dir = note_mod.NOTE_OUTPUT_DIR
    note_mod.NOTE_OUTPUT_DIR = root
    try:
        marked = _fail_interrupted_tasks_locked(reason=reason, root=root)
    finally:
        note_mod.NOTE_OUTPUT_DIR = prev_dir
    return marked


def _fail_interrupted_tasks_locked(*, reason: str, root: Path) -> int:
    from app.services.note import NoteGenerator

    gen = NoteGenerator()
    marked = 0
    for path in iter_status_files(root):
        data = _read_status(path)
        if not data:
            continue
        status = str(data.get("status") or "").upper()
        if status not in IN_PROGRESS_STATUSES:
            continue
        task_id = path.name[: -len(".status.json")]
        if not task_id:
            continue
        try:
            gen._update_status(task_id, TaskStatus.FAILED, message=reason)
            marked += 1
        except Exception:
            logger.warning(
                "标记中断任务失败 task_id=%s path=%s",
                task_id,
                path,
                exc_info=True,
            )
    if marked:
        logger.info("已将 %s 个进行中任务标记为 FAILED（%s）", marked, reason[:80])
    return marked


# ---------------------------------------------------------------------------
# 运行期僵尸任务清理（F49）
# ---------------------------------------------------------------------------
# 启动时的 `fail_interrupted_tasks()` 只覆盖「进程正常重启」这一种情况。
# 如果进程被 `kill -9`、被 OOM killer 杀掉、或者宿主机断电，那些任务的状态
# 文件会**永久停在执行中**：
#   - 前端每 3 秒轮询一个永远不会变化的任务，永远转圈；
#   - 该任务的配额槽位可能一直占着，用户被自己的僵尸任务卡住；
#   - 用户只能手动删任务才能恢复。
#
# 在「4GB 容器 + Whisper + 500MB 上传」的部署下这不是罕见场景。

# 默认判定阈值：状态文件的 mtime 超过这么久**且**任务不在池里，才认为是僵尸。
#
# 为什么不能只看 mtime：一个正常的长任务（大视频下载 + 转写 + 长文总结）
# 可能 10 分钟都不更新状态（中间没有状态切换）。阈值太小会误杀。
# 默认 30 分钟远大于任何「无状态切换」的合法间隔。
DEFAULT_STALE_AFTER_SECONDS = int(os.getenv("TASK_STALE_AFTER_SECONDS", str(30 * 60)))


def reap_stale_tasks(
    *,
    stale_after: float = DEFAULT_STALE_AFTER_SECONDS,
    note_dir: Path | None = None,
    dry_run: bool = True,
) -> dict:
    """把「状态说还在跑、但其实已经没人跑」的任务标为 FAILED。

    ## 双条件判定（缺一不可）

      1. 状态文件 mtime 距今超过 ``stale_after`` 秒；
      2. **该 task_id 不在本进程的线程池里**（`task_serial_executor.is_active`）。

    只看 (1) 会误杀正常长任务；只看 (2) 会把「刚重启、任务确实丢了」的
    情况也放过（池是空的，但状态文件写着 DOWNLOADING）。

    ## 问不到池状态时保守跳过

    如果 executor 没有跟踪能力（例如某些测试里替换了实现），
    `is_active` 可能缺失 —— 那种情况下**不清理**，宁可留一个僵尸任务，
    也不要误杀正在跑的任务。

    :param dry_run: 默认 True，只统计不改写。定时任务应显式传 False。
    :return: 统计字典（scanned / stale / reaped / skipped_active / errors）
    """
    from app.services.task_serial_executor import task_serial_executor

    root = note_dir if note_dir is not None else _note_output_dir()
    stats = {
        "scanned": 0,
        "stale": 0,
        "reaped": 0,
        "skipped_active": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    now = time.time()

    # 拿一次池的活跃快照，避免每条都加锁
    active_ids: frozenset[str] | None
    try:
        active_ids = task_serial_executor.active_task_ids()
    except Exception:  # noqa: BLE001 - 换个实现就没有跟踪能力
        logger.warning("线程池不支持活跃任务查询，本次跳过僵尸清理（避免误杀）")
        return stats

    for path in iter_status_files(root):
        stats["scanned"] += 1
        data = _read_status(path)
        if not data:
            stats["errors"] += 1
            continue
        status = str(data.get("status") or "").upper()
        if status not in IN_PROGRESS_STATUSES:
            continue

        task_id = path.name[: -len(".status.json")]
        if not task_id:
            continue

        try:
            age = now - path.stat().st_mtime
        except OSError:
            stats["errors"] += 1
            continue

        if age < stale_after:
            # 刚更新过 —— 可能真的在跑，也可能刚启动。无论如何不动。
            continue

        stats["stale"] += 1

        # 条件 2：在池里就不碰（这是「双条件」里最关键的一条）
        if task_id in active_ids:
            stats["skipped_active"] += 1
            continue

        if dry_run:
            logger.info(
                "发现疑似僵尸任务 (task_id=%s, status=%s, 静默 %.0f 秒)",
                task_id, status, age,
            )
            continue

        try:
            # 复用与启动时一致的状态写入路径（文件 + DB 同步）
            from app.services.note import NoteGenerator

            gen = NoteGenerator.__new__(NoteGenerator)
            gen._update_status(  # noqa: SLF001 - 与 fail_interrupted_tasks 同一入口
                task_id,
                TaskStatus.FAILED,
                message=f"任务已中断（超过 {int(stale_after / 60)} 分钟无进展，已自动清理）",
            )
            stats["reaped"] += 1
            logger.info("已清理僵尸任务 (task_id=%s)", task_id)
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning("清理僵尸任务失败 (task_id=%s)", task_id, exc_info=True)

    if stats["reaped"]:
        logger.info("僵尸任务清理完成：%s 个", stats["reaped"])
    return stats
