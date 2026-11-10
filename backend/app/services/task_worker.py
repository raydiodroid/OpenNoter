"""Execution adapter. HTTP handlers only validate and submit immutable arguments."""
from dataclasses import asdict
import logging

from app.services.task_service import TaskService
from app.services.task_serial_executor import task_serial_executor

logger = logging.getLogger(__name__)


def submit_note(task_id: str, *, user_id=None, attempt_id=None, root=None, **options):
    service = TaskService(root)

    def execute():
        try:
            state = service.snapshot(task_id)
            if state is None or state["deleted"] or state["attempt_id"] != attempt_id or state["status"] in {"SUCCESS", "FAILED"}:
                return
            from app.services.note import NoteGenerator
            generator = NoteGenerator(user_id=user_id, attempt_id=attempt_id)
            note = generator.generate(task_id=task_id, **options)
            if not note or not note.markdown:
                state = service.snapshot(task_id)
                if state and state["status"] not in {"SUCCESS", "FAILED"}:
                    service.update(task_id, "FAILED", "未生成有效笔记", attempt_id=attempt_id)
                return
            service.save_result(task_id, asdict(note), attempt_id=attempt_id)
            service.update(task_id, "SUCCESS", attempt_id=attempt_id)
            try:
                from app.services.vector_store import VectorStoreManager
                VectorStoreManager().index_task(task_id)
            except Exception:
                logger.warning("向量索引失败: %s", task_id, exc_info=True)
        except Exception:
            logger.exception("任务执行失败: %s", task_id)
            state = service.snapshot(task_id)
            if state and state["attempt_id"] == attempt_id and state["status"] not in {"SUCCESS", "FAILED"}:
                service.update(task_id, "FAILED", "任务执行失败，请检查服务日志", attempt_id=attempt_id)

    try:
        return task_serial_executor.submit_task(task_id, execute)
    except Exception:
        service.update(task_id, "FAILED", "任务队列不可用，请稍后重试", attempt_id=attempt_id)
        raise
