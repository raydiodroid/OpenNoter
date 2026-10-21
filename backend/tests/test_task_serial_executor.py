"""任务执行器测试。

历史背景：这个类原本是 `SerialTaskExecutor`，用一把全局锁串行跑任务；后来
改成 `ConcurrentTaskExecutor`（线程池），但测试一直断言「peak_active == 1」
这个串行语义，于是长期红着。

现在的断言改成验证**当前真正承诺的行为**：

1. `run()` 会等任务跑完并返回结果（同步语义，调用方拿得到返回值）；
2. 并发度受 `max_workers` 约束——不是无限并发，也不是硬串行；
3. 任务抛异常时异常会传播给调用方。
"""

import importlib.util
import pathlib
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "services" / "task_serial_executor.py"
spec = importlib.util.spec_from_file_location("task_serial_executor", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError("task_serial_executor module spec not found")
task_serial_executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_serial_executor)
ConcurrentTaskExecutor = task_serial_executor.ConcurrentTaskExecutor


class TestConcurrentTaskExecutor(unittest.TestCase):
    def test_run_returns_task_result(self):
        executor = ConcurrentTaskExecutor(max_workers=2)
        try:
            self.assertEqual(executor.run(lambda a, b: a + b, 2, 3), 5)
        finally:
            executor.shutdown()

    def test_run_propagates_exception(self):
        executor = ConcurrentTaskExecutor(max_workers=1)

        def boom():
            raise ValueError("boom")

        try:
            with self.assertRaises(ValueError):
                executor.run(boom)
        finally:
            executor.shutdown()

    def test_concurrency_is_bounded_by_max_workers(self):
        max_workers = 2
        executor = ConcurrentTaskExecutor(max_workers=max_workers)
        state_lock = threading.Lock()
        state = {"active": 0, "peak_active": 0}
        release = threading.Event()

        def hold():
            with state_lock:
                state["active"] += 1
                state["peak_active"] = max(state["peak_active"], state["active"])
            # 等到外面放行；受 max_workers 限制，同时最多只有 max_workers 个在跑
            release.wait(timeout=5)
            with state_lock:
                state["active"] -= 1

        threads = [
            threading.Thread(target=executor.run, args=(hold,))
            for _ in range(max_workers * 2)
        ]
        for thread in threads:
            thread.start()
        # 给线程池时间把 worker 拉满
        time.sleep(0.2)
        release.set()
        for thread in threads:
            thread.join(timeout=10)
        executor.shutdown()

        self.assertGreaterEqual(state["peak_active"], 1, "至少应该跑起来一个任务")
        self.assertLessEqual(
            state["peak_active"],
            max_workers,
            f"并发度不应超过 max_workers={max_workers}",
        )


if __name__ == "__main__":
    unittest.main()
