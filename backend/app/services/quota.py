"""用户级配额：防止单个用户占满任务队列 / 磁盘。

## 为什么必须有

任务执行器是固定线程池（`TASK_MAX_WORKERS`，默认 3）。一个任务通常跑
1~5 分钟（下载 + 转写 + LLM）。没有配额时：

- 一个用户连续提交 10 个任务 → 3 个 worker 全被占满 → 其他人全部排队
- 一个用户生成上千篇笔记 → 磁盘被吃光

这不是理论风险：`POST /notes` 没有任何限制，登录用户随便刷。

## 三层限制

| 维度 | 检查时机 | 默认值 | 环境变量 |
|---|---|---|---|
| **并发任务数** | 提交前 | 2 | `USER_MAX_CONCURRENT_TASKS` |
| **每日任务数** | 提交前 | 50 | `USER_DAILY_TASK_LIMIT` |
| **存储占用** | 提交前（粗查） | 2048 MB | `USER_STORAGE_QUOTA_MB` |

「0」表示不限制；用户表上的同名字段可覆盖全局默认（管理员可为单个用户放宽）。

## 设计取舍

- **不阻止已排队的任务**：只拦「新提交」，避免把用户正在跑的任务打断
- **管理员不参与配额**：管理员需要排查问题时不该被拦
- **匿名也有全局限流**：未强制认证时 `user is None`，用
  `ANON_MAX_CONCURRENT_TASKS` / `ANON_SUBMIT_RPM` 防止刷接口
- **check + 写 PENDING 在同一把锁里**：`begin_submit` 避免并发双检都通过
- **存储检查是粗查**：只统计该用户 `note_results` 下相关文件的体积，
  不做精确归属（那需要索引表）。足够拦住「狂刷到爆盘」的场景。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.db.engine import SessionLocal
from app.db.models.users import User
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 进程内锁：把「读配额 → 写 PENDING」收成临界区，避免并发双检都通过。
# 单副本部署够用；多副本需换 Postgres 行锁 / Redis（见 README / RELEASING）。
_submit_lock = threading.Lock()

# 匿名提交 RPM（固定窗口）；与 API Key 限流同思路，重启清零可接受。
_ANON_RATE_WINDOW = 60
_anon_rate_buckets: dict[int, int] = {}
_anon_rate_lock = threading.Lock()

# 进行中的任务状态（与 task_status_enums 保持一致，但这里不 import 避免循环依赖）
_RUNNING_STATES = {
    "PENDING",
    "PARSING",
    "DOWNLOADING",
    "TRANSCRIBING",
    "SUMMARIZING",
    "FORMATTING",
    "SAVING",
}


def _env_int(name: str, default: int) -> int:
    from app.utils.env_compat import getint

    return getint(name, default)


def _note_dir() -> Path:
    return Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))


def _resolve_limit(user_value: Optional[int], env_name: str, env_default: int) -> int:
    """用户级覆盖优先；NULL 时用环境变量默认。"""
    if user_value is not None:
        return int(user_value)
    return _env_int(env_name, env_default)


class QuotaExceeded(Exception):
    """配额超限。带 code 供接口层转成 429。"""

    def __init__(self, message: str, *, dimension: str, limit: int, current: int, retry_after: int = 60):
        super().__init__(message)
        self.message = message
        self.dimension = dimension
        self.limit = limit
        self.current = current
        self.retry_after = retry_after


def _count_user_running(user_id: int, note_dir: Path) -> int:
    """统计该用户「进行中」的任务数（扫状态文件）。

    这里必须读全量文件的 user_id —— 与列表接口同样是 O(n)，
    但提交任务的频率远低于刷新列表，且这是**安全兜底**，可以接受。
    """
    running = 0
    if not note_dir.exists():
        return 0
    for path in note_dir.glob("*.status.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("user_id") != user_id:
            continue
        if str(payload.get("status") or "").upper() in _RUNNING_STATES:
            running += 1
    return running


def _count_user_today(user_id: int, note_dir: Path) -> int:
    """统计该用户「今天（UTC）」提交的任务数。

    用状态文件的 mtime 近似「提交时间」——任务一旦创建就会写 PENDING，
    mtime 必然在提交时刻附近。重试会刷新 mtime，会被重复计数，
    这是**保守方向**（宁可多算，不要放过刷接口的人）。
    """
    if not note_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    count = 0
    for path in note_dir.glob("*.status.json"):
        try:
            st = path.stat()
            if datetime.fromtimestamp(st.st_mtime, tz=timezone.utc) < cutoff:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("user_id") == user_id:
            count += 1
    return count


def _user_storage_mb(user_id: int, note_dir: Path) -> float:
    """粗算该用户笔记产物的体积（MB）。

    只统计 `{task_id}.json` / `.status.json` / `_transcript.json`，
    不扫截图与视频缓存（那些走另一套清理策略）。
    """
    if not note_dir.exists():
        return 0.0
    total = 0
    for path in note_dir.glob("*.status.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get("user_id") != user_id:
            continue
        task_id = path.name[: -len(".status.json")]
        for suffix in (".json", "_transcript.json"):
            f = note_dir / f"{task_id}{suffix}"
            try:
                total += f.stat().st_size
            except OSError:
                pass
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total / 1024 / 1024


def _count_anon_running(note_dir: Path) -> int:
    """统计无归属（匿名）进行中任务数。"""
    running = 0
    if not note_dir.exists():
        return 0
    for path in note_dir.glob("*.status.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("user_id") is not None:
            continue
        if str(payload.get("status") or "").upper() in _RUNNING_STATES:
            running += 1
    return running


def _check_anon_submit_rpm() -> None:
    """匿名全局限流（次/分钟）。0 = 不限制。"""
    limit = _env_int("ANON_SUBMIT_RPM", 10)
    if limit <= 0:
        return
    window_id = int(time.time()) // _ANON_RATE_WINDOW
    with _anon_rate_lock:
        # 只保留最近两个窗口
        stale = [k for k in _anon_rate_buckets if k < window_id - 1]
        for k in stale:
            del _anon_rate_buckets[k]
        count = _anon_rate_buckets.get(window_id, 0) + 1
        _anon_rate_buckets[window_id] = count
        if count > limit:
            raise QuotaExceeded(
                f"匿名提交过于频繁（{limit} 次/分钟），请稍后再试或登录后提交",
                dimension="anon_rpm",
                limit=limit,
                current=count,
                retry_after=_ANON_RATE_WINDOW,
            )


def check_submit_quota(user: Optional[User]) -> None:
    """提交任务前的配额校验。超限抛 QuotaExceeded。

    - 管理员不限制
    - 匿名：全局限流（并发 + RPM），防止未强制认证时刷接口
    - 登录用户：并发 / 每日 / 存储三层
    """
    if user is not None and getattr(user, "is_superuser", False):
        return

    note_dir = _note_dir()

    if user is None:
        _check_anon_submit_rpm()
        max_concurrent = _env_int("ANON_MAX_CONCURRENT_TASKS", 2)
        if max_concurrent > 0:
            running = _count_anon_running(note_dir)
            if running >= max_concurrent:
                raise QuotaExceeded(
                    f"匿名同时进行的任务已达上限（{running}/{max_concurrent}），"
                    f"请等待完成、登录后提交，或开启 OPENNOTER_AUTH_ENFORCE",
                    dimension="anon_concurrent",
                    limit=max_concurrent,
                    current=running,
                )
        return

    user_id = user.id

    # 1) 并发任务数
    max_concurrent = _resolve_limit(
        getattr(user, "max_concurrent_tasks", None), "USER_MAX_CONCURRENT_TASKS", 2
    )
    if max_concurrent > 0:
        running = _count_user_running(user_id, note_dir)
        if running >= max_concurrent:
            raise QuotaExceeded(
                f"同时进行的任务已达上限（{running}/{max_concurrent}），"
                f"请等待现有任务完成后再提交",
                dimension="concurrent",
                limit=max_concurrent,
                current=running,
            )

    # 2) 每日任务数
    daily_limit = _resolve_limit(
        getattr(user, "daily_task_limit", None), "USER_DAILY_TASK_LIMIT", 50
    )
    if daily_limit > 0:
        today = _count_user_today(user_id, note_dir)
        if today >= daily_limit:
            raise QuotaExceeded(
                f"今日任务数已达上限（{today}/{daily_limit}），请明天再试",
                dimension="daily",
                limit=daily_limit,
                current=today,
                retry_after=3600,
            )

    # 3) 存储占用
    storage_limit = _resolve_limit(
        getattr(user, "storage_quota_mb", None), "USER_STORAGE_QUOTA_MB", 2048
    )
    if storage_limit > 0:
        used = _user_storage_mb(user_id, note_dir)
        if used >= storage_limit:
            raise QuotaExceeded(
                f"笔记存储占用已达上限（{used:.0f}/{storage_limit} MB），"
                f"请删除部分历史笔记后再提交",
                dimension="storage",
                limit=storage_limit,
                current=int(used),
            )


def begin_submit(user: Optional[User], write_pending) -> None:
    """原子化：持锁校验配额并立即写 PENDING，避免并发突破上限。

    ``write_pending`` 无参可调用，负责落盘 PENDING（以及任何必须与配额
    同临界区的副作用）。调用方在锁外做模型校验等无副作用准备。
    """
    with _submit_lock:
        check_submit_quota(user)
        write_pending()


def quota_snapshot(user: Optional[User]) -> dict:
    """返回当前用量快照（给「我的配额」类接口/前端提示用）。"""
    if user is None:
        note_dir = _note_dir()
        max_concurrent = _env_int("ANON_MAX_CONCURRENT_TASKS", 2)
        rpm = _env_int("ANON_SUBMIT_RPM", 10)
        return {
            "unlimited": max_concurrent <= 0 and rpm <= 0,
            "reason": "anonymous",
            "concurrent": {
                "limit": max_concurrent,
                "used": _count_anon_running(note_dir),
            },
            "submit_rpm": {"limit": rpm},
        }
    if getattr(user, "is_superuser", False):
        return {"unlimited": True, "reason": "admin"}

    note_dir = _note_dir()
    max_concurrent = _resolve_limit(
        getattr(user, "max_concurrent_tasks", None), "USER_MAX_CONCURRENT_TASKS", 2
    )
    daily_limit = _resolve_limit(
        getattr(user, "daily_task_limit", None), "USER_DAILY_TASK_LIMIT", 50
    )
    storage_limit = _resolve_limit(
        getattr(user, "storage_quota_mb", None), "USER_STORAGE_QUOTA_MB", 2048
    )
    return {
        "unlimited": False,
        "concurrent": {
            "limit": max_concurrent,
            "used": _count_user_running(user.id, note_dir),
        },
        "daily": {
            "limit": daily_limit,
            "used": _count_user_today(user.id, note_dir),
        },
        "storage_mb": {
            "limit": storage_limit,
            "used": round(_user_storage_mb(user.id, note_dir), 1),
        },
    }
