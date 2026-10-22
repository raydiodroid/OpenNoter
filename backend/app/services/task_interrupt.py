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
