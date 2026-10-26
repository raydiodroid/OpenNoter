import os
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable


class ConcurrentTaskExecutor:
    """使用线程池并发执行任务，替代原来的串行锁。

    ## 并发度的选择

    任务是 **IO 密集型**（下载视频、调用 ASR、调用 LLM），CPU 占用很低
    （实测满载时后端容器 CPU < 1%）。所以并发度不该按核数定，而受限在：

    - **内存**：每个任务峰值约 200-400MB（转写缓冲 + 分块处理）
    - **外部限流**：LLM 网关 / ASR 服务的并发额度
    - **磁盘 IO**：多任务同时写缓存

    默认 6 是折中：4 核机器上足够抹平 IO 等待，单任务 300MB 时
    峰值约 1.8GB，对 4GB 容器限额是安全的。

    单用户的实际并发由 `app/services/quota.py` 的
    `USER_MAX_CONCURRENT_TASKS`（默认 2）限制，避免一个用户占满全部 worker。
    """

    def __init__(self, max_workers: int | None = None):
        self._max_workers = max_workers or int(os.getenv("TASK_MAX_WORKERS", "6"))
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="note-task"
        )
        # task_id → Future：供 reaper 判断「这个任务是不是真的在跑」（F49）
        self._active: dict[str, Future] = {}
        self._active_lock = threading.Lock()

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """提交并**等待**结果（同步语义）。

        保留这个方法是因为不少既有调用方（以及测试）依赖「调用返回即完成」。
        但**后台任务入口不应该用它** —— 见 :meth:`submit` 的说明（F26）。
        """
        future: Future = self._pool.submit(fn, *args, **kwargs)
        return future.result()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        """提交任务并**立即返回** Future，不等结果。

        ## 为什么需要它（F26）

        `run_note_task` 本身是 Starlette `BackgroundTask` —— 它已经占着一个
        anyio 工作线程了。若里面再调 `run()`（阻塞在 `future.result()`），
        就等于「一个 anyio 线程 + 一个池线程」被同一个任务同时占着：

          - anyio 默认线程池只有 40 个；
          - note pool 只有 6 个。

        结果是 6 个任务就能把池占满，同时**额外**占住 6 个 anyio 线程空转等待。
        并发一高，anyio 侧会先耗尽，其它后台任务（例如日志写盘、Cookie 同步）
        被拖住。

        正确做法：后台入口只 `submit` 就返回，把「执行 + 收尾」整体放进池内。
        """
        return self._pool.submit(fn, *args, **kwargs)

    # ------------------------------------------------------------------
    # 运行期任务跟踪（F49 的 stale-task reaper 依赖它）
    # ------------------------------------------------------------------
    def track(self, task_id: str, future: Future) -> None:
        """登记一个「正在池里跑」的 task_id，完成时自动摘除。

        reaper 需要区分两种「状态文件说还在跑」的情况：
          (a) 真的在跑（别碰它）；
          (b) 进程重启/被杀后遗留的僵尸（要标 FAILED）。
        光看状态文件无法区分，必须问线程池。
        """
        with self._active_lock:
            self._active[task_id] = future

        def _discard(_fut):
            with self._active_lock:
                self._active.pop(task_id, None)

        future.add_done_callback(_discard)

    def is_active(self, task_id: str) -> bool:
        """该 task_id 是否正在本进程的池里执行（排队或运行中）。"""
        with self._active_lock:
            fut = self._active.get(task_id)
        if fut is None:
            return False
        if fut.done():
            with self._active_lock:
                self._active.pop(task_id, None)
            return False
        return True

    def active_task_ids(self) -> frozenset[str]:
        """当前池里还没结束的 task_id 集合（快照）。"""
        with self._active_lock:
            items = list(self._active.items())
        return frozenset(tid for tid, fut in items if not fut.done())

    def active_count(self) -> int:
        """当前排队 + 执行中的任务数（供监控/健康检查用）。"""
        try:
            return self._pool._work_queue.qsize() + len(self._pool._threads)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - 内部属性变动时不要抛
            return -1

    def shutdown(self, wait: bool = True):
        self._pool.shutdown(wait=wait)


# 已删除 `SerialTaskExecutor = ConcurrentTaskExecutor` 这个别名（F48）。
#
# 别名本身无害，但**名字在骗人**：`SerialTaskExecutor` 读起来是「串行执行器」，
# 而它实际是 6 线程并发池。有人据此对并发行为做假设（例如以为两个任务不会
# 同时跑、可以安全地共用某个非线程安全的东西），会写出难查的竞态。
#
# 全仓确认只有本文件定义、无任何调用点（另一个引用是测试里的历史说明注释）。
# 需要旧名字的地方请直接写 `ConcurrentTaskExecutor`。
task_serial_executor = ConcurrentTaskExecutor()
