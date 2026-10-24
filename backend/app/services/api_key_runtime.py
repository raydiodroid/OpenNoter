"""API Key 的运行时状态：限流计数、活动记录缓冲、用量日志。

这些都是**进程内**状态，不额外引入 Redis：
  - 后端是单进程单副本（`uvicorn.run` 没传 workers），进程内状态够用
  - 限流计数重启丢失完全可接受 —— 限流语义本就允许「够用就好」
    （AWS 记录 last-used 也是 15 分钟采样）
  - 活动记录用「15 分钟采样 + 攒批落盘」控制写放大（借鉴 AWS IAM）

真正需要跨重启查询的是**用量明细**，那是数据不是状态，走 SQLite，
由中间件异步写入。
"""

import queue
import threading
import time
from collections import defaultdict
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 限流窗口（秒）
RATE_LIMIT_WINDOW = 60
# 活动记录落盘采样间隔（秒）—— 15 分钟，对齐 AWS IAM 的 last-used 采样
ACTIVITY_FLUSH_INTERVAL = 900
# 用量明细保留天数
USAGE_RETENTION_DAYS = 90


# ---------------- 限流 ----------------

class FixedWindowRateLimiter:
    """固定窗口计数器。

    固定窗口的已知缺陷：窗口边界处可能放过 2 倍流量（前一秒 59 次 + 后一秒
    59 次 = 120 次/2 秒）。对「防止脚本失控」这个用途够用，滑动窗口/ZSET
    留给真正需要精确限流的场景。
    """

    def __init__(self, window: int = RATE_LIMIT_WINDOW):
        self._window = window
        self._buckets: dict[tuple[int, int], int] = {}
        self._lock = threading.Lock()

    def check(self, key_id: int, limit: int) -> tuple[bool, int]:
        """计数并判断是否放行。返回 (是否放行, 本窗口剩余额度)。"""
        window_id = int(time.time()) // self._window
        with self._lock:
            self._prune(window_id)
            bucket = (key_id, window_id)
            count = self._buckets.get(bucket, 0) + 1
            self._buckets[bucket] = count
            return count <= limit, max(0, limit - count)

    def reset(self, key_id: int) -> None:
        """吊销/编辑 key 后清掉它的计数。"""
        with self._lock:
            for bucket in [k for k in self._buckets if k[0] == key_id]:
                del self._buckets[bucket]

    def _prune(self, current_window: int) -> None:
        """只保留最近两个窗口。"""
        cutoff = current_window - 1
        stale = [k for k in self._buckets if k[1] < cutoff]
        for key in stale:
            del self._buckets[key]


# ---------------- 活动记录（攒批落盘） ----------------

class ActivityTracker:
    """活动记录缓冲：攒批写 last_used_at / use_count，避免每请求一次写事务。

    之前每次 API 请求都 `db.commit()` 写 last_used_at，在
    `journal_mode=delete` 的 SQLite 上意味着每个请求一次 fsync + 锁库。
    """

    def __init__(self, flush_interval: int = ACTIVITY_FLUSH_INTERVAL):
        self._flush_interval = flush_interval
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()

    def record(self, key_id: int, ip: str, endpoint: str, user_agent: str) -> None:
        now = time.time()
        with self._lock:
            entry = self._pending.get(key_id)
            if entry is None:
                entry = {"first_seen": now, "count": 0}
                self._pending[key_id] = entry
            entry["count"] += 1
            entry["ip"] = ip
            entry["endpoint"] = endpoint
            entry["user_agent"] = user_agent
            entry["last_seen"] = now

    def due_for_flush(self) -> bool:
        with self._lock:
            if not self._pending:
                return False
            return time.monotonic() - self._last_flush >= self._flush_interval

    def drain(self) -> list[tuple[int, dict]]:
        """取出待落盘的记录并清空缓冲。"""
        with self._lock:
            items = list(self._pending.items())
            self._pending.clear()
            self._last_flush = time.monotonic()
            return items

    def peek(self) -> dict[int, dict]:
        with self._lock:
            return dict(self._pending)


# ---------------- 用量明细 ----------------

class UsageLogger:
    """用量明细写入器：队列 + 后台线程攒批写库。

    不在请求路径上同步写 —— SQLite 在 delete journal 模式下写事务要 fsync，
    放在事件循环里会拖慢响应。队列满了就丢弃（统计用途，宁可少几条也不能
    把请求拖住）。
    """

    def __init__(self, max_queue: int = 10000):
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="api-key-usage", daemon=True)
        self._worker.start()
        logger.info("API Key 用量日志写入线程已启动")

    def stop(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=5)
            self._worker = None

    def record(self, key_id: int, method: str, path: str, status_code: int,
               ip: str, duration_ms: int) -> None:
        try:
            self._queue.put_nowait((key_id, method, path, status_code, ip, duration_ms))
        except queue.Full:
            logger.warning("API Key 用量队列已满，丢弃一条记录")

    def _run(self) -> None:
        from app.db.engine import SessionLocal
        from app.db.models.api_keys import ApiKeyUsage

        last_prune = time.time()
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._maybe_prune(last_prune)
                continue

            batch = [item]
            # 攒批：最多等 200 条或 500ms
            deadline = time.time() + 0.5
            while len(batch) < 200 and time.time() < deadline:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            try:
                db = SessionLocal()
                try:
                    for key_id, method, path, status_code, ip, duration_ms in batch:
                        db.add(ApiKeyUsage(
                            key_id=key_id,
                            method=method,
                            path=path,
                            status_code=status_code,
                            ip=ip,
                            duration_ms=duration_ms,
                        ))
                    db.commit()
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"写入 API Key 用量日志失败: {e}")

            last_prune = self._maybe_prune(last_prune)

    def _maybe_prune(self, last_prune: float) -> float:
        """每小时清一次过期明细。"""
        if time.time() - last_prune < 3600:
            return last_prune
        last_prune = time.time()
        try:
            from sqlalchemy import text

            from app.db.engine import get_engine

            with get_engine().begin() as conn:
                conn.execute(text(
                    "DELETE FROM api_key_usage WHERE created_at < datetime('now', ?)"
                ), (f"-{USAGE_RETENTION_DAYS} days",))
            logger.info(f"已清理 {USAGE_RETENTION_DAYS} 天前的 API Key 用量记录")
        except Exception as e:
            logger.warning(f"清理 API Key 用量记录失败: {e}")
        return last_prune


# ---------------- 单例 ----------------

rate_limiter = FixedWindowRateLimiter()
activity_tracker = ActivityTracker()
usage_logger = UsageLogger()
