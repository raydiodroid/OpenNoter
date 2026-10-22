import os
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

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        future: Future = self._pool.submit(fn, *args, **kwargs)
        return future.result()

    def shutdown(self, wait: bool = True):
        self._pool.shutdown(wait=wait)


# 保持向后兼容的导出名
SerialTaskExecutor = ConcurrentTaskExecutor
task_serial_executor = ConcurrentTaskExecutor()
