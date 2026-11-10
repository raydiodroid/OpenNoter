"""Task lifecycle boundary: transactional claims, attempt fencing and durable state.

The executor is deliberately single-process. Database state survives a restart;
unfinished attempts are failed at startup rather than silently resubmitted.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
import threading
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_

from app.db.engine import SessionLocal
from app.db.models.video_tasks import VideoTask
from app.db.models.task_attempts import TaskAttempt
from app.storage.task_files import LocalTaskStorage, TaskStorage
from app.utils.note_paths import validate_task_id

logger = logging.getLogger(__name__)
RUNNING = frozenset({"PENDING", "PARSING", "DOWNLOADING", "TRANSCRIBING", "SUMMARIZING", "FORMATTING", "SAVING"})
TERMINAL = frozenset({"SUCCESS", "FAILED"})


class TaskConflict(ValueError):
    pass


class StaleAttempt(TaskConflict):
    pass


_lifecycle_lock = threading.RLock()


def serialized(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        with _lifecycle_lock:
            return method(*args, **kwargs)
    return guarded


class TaskService:
    def __init__(self, root: str | Path | None = None, *, sessions=None, storage: TaskStorage | None = None):
        self.sessions = sessions or SessionLocal
        self.storage = storage or LocalTaskStorage(root or os.getenv("NOTE_OUTPUT_DIR", "note_results"))

    @serialized
    def claim(self, task_id: str, *, user_id: int | None, platform: str, video_url: str,
              request: dict, retry: bool) -> str:
        """A retry is a new attempt, never another worker for the current attempt."""
        validate_task_id(task_id)
        attempt = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self.sessions.begin() as db:
            row = db.query(VideoTask).filter_by(task_id=task_id).first()
            if retry:
                if row is None or row.deleted_at is not None or row.user_id != user_id:
                    raise TaskConflict("任务不存在或不属于当前用户")
                changed = db.query(VideoTask).filter(
                    VideoTask.task_id == task_id, VideoTask.deleted_at.is_(None),
                    or_(VideoTask.status.is_(None), VideoTask.status.in_(TERMINAL)),
                ).update({
                    "status": "PENDING", "attempt_id": attempt, "updated_at": now,
                    "error": None, "failed_at": None, "platform": platform,
                    "video_id": video_url, "request_json": json.dumps(request, ensure_ascii=False),
                }, synchronize_session=False)
                if changed != 1:
                    raise TaskConflict("任务已在排队或执行，请等待当前任务结束")
            else:
                if row is not None:
                    raise TaskConflict("任务已存在")
                db.add(VideoTask(task_id=task_id, user_id=user_id, platform=platform,
                                 video_id=video_url, status="PENDING", attempt_id=attempt,
                                 updated_at=now, request_json=json.dumps(request, ensure_ascii=False)))
                db.flush()
            db.add(TaskAttempt(id=attempt, task_id=task_id, user_id=user_id, created_at=now))
        self._mirror(task_id)
        return attempt

    def snapshot(self, task_id: str) -> dict | None:
        validate_task_id(task_id)
        with self.sessions() as db:
            row = db.query(VideoTask).filter_by(task_id=task_id).first()
            if row is None:
                return None
            return {"task_id": row.task_id, "user_id": row.user_id,
                    "status": row.status, "message": row.error or "",
                    "attempt_id": row.attempt_id, "deleted": row.deleted_at is not None,
                    "request": json.loads(row.request_json) if row.request_json else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None}

    @serialized
    def update(self, task_id: str, status: str, message: str | None = None, *,
               attempt_id: str | None = None, user_id=None, platform=None, video_id=None):
        validate_task_id(task_id)
        status = getattr(status, "value", status)
        if status not in RUNNING | TERMINAL:
            raise ValueError(f"未知任务状态: {status}")
        with self.sessions.begin() as db:
            row = db.query(VideoTask).filter_by(task_id=task_id).first()
            if row is None:
                if attempt_id:
                    raise StaleAttempt("执行批次已失效")
                if platform and video_id:
                    row = VideoTask(task_id=task_id, user_id=user_id, platform=platform, video_id=video_id)
                    db.add(row)
                else:
                    # Compatibility for pre-database artifacts only. New submissions use claim().
                    data = self.storage.read(task_id, ".status.json") or {}
                    data.update(status=status, message=message or "")
                    if user_id is not None:
                        data.setdefault("user_id", user_id)
                    if status == "FAILED":
                        data["failed_at"] = datetime.now(timezone.utc).isoformat()
                    self.storage.write(task_id, data, ".status.json")
                    return
            if row.deleted_at is not None or (attempt_id and row.attempt_id != attempt_id):
                raise StaleAttempt("执行批次已失效")
            if attempt_id and row.status in TERMINAL and row.status != status:
                raise StaleAttempt("执行批次已经结束")
            row.status = status
            row.error = (message or "")[:512] or None
            row.updated_at = datetime.now(timezone.utc)
            row.failed_at = row.updated_at if status == "FAILED" else None
        self._mirror(task_id)

    def _mirror(self, task_id: str):
        """Legacy status files are a projection; a failed mirror never erases DB truth."""
        try:
            data = self.snapshot(task_id)
            if data:
                self.storage.write(task_id, {k: v for k, v in data.items() if k != "request"}, ".status.json")
        except OSError:
            logger.warning("状态镜像写入失败: %s", task_id, exc_info=True)

    @serialized
    def save_result(self, task_id: str, result: dict, *, attempt_id: str | None = None):
        row = self.snapshot(task_id)
        if attempt_id and (row is None or row["status"] in TERMINAL):
            raise StaleAttempt("执行批次已经结束")
        if row and (row["deleted"] or (attempt_id and row["attempt_id"] != attempt_id)):
            raise StaleAttempt("执行批次已失效，拒绝覆盖结果")
        self.storage.write(task_id, result)

    @serialized
    def import_legacy(self, root: Path):
        """One-way adoption of old status files; never overwrite existing DB state."""
        from app.db.models.users import User
        for path in root.glob("*.status.json"):
            task_id = path.name[:-len(".status.json")]
            try:
                validate_task_id(task_id)
                data = self.storage.read(task_id, ".status.json")
                if not data or data.get("status") not in RUNNING | TERMINAL:
                    continue
                if task_id.endswith(("_markdown", "_audio", "_transcript", "_meta")):
                    continue
                with self.sessions.begin() as db:
                    row = db.query(VideoTask).filter_by(task_id=task_id).first()
                    if row is not None and row.status:
                        continue
                    owner = data.get("user_id")
                    if owner is not None and db.get(User, owner) is None:
                        # Unknown ownership must not become public anonymous content.
                        logger.warning("跳过归属用户不存在的历史任务: %s", task_id)
                        continue
                    if row is not None and row.user_id is not None and owner is not None and row.user_id != owner:
                        logger.warning("跳过归属与数据库不符的历史任务: %s", task_id)
                        continue
                    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                    if row is None:
                        row = VideoTask(task_id=task_id, user_id=owner, created_at=modified,
                                        platform=data.get("platform") or "legacy",
                                        video_id=data.get("video_id") or task_id)
                        db.add(row)
                    if row.user_id is None and owner is not None:
                        row.user_id = owner
                    row.status = data["status"]
                    row.error = data.get("message") if row.status == "FAILED" else None
                    row.updated_at = modified
                    row.failed_at = modified if row.status == "FAILED" else None
                    db.flush()
                    if db.query(TaskAttempt).filter_by(task_id=task_id).first() is None:
                        db.add(TaskAttempt(id=str(uuid.uuid4()), task_id=task_id,
                                           user_id=row.user_id, created_at=row.created_at or row.updated_at))
            except (ValueError, OSError):
                logger.warning("历史任务导入失败: %s", task_id, exc_info=True)

    @serialized
    def fail_missing_result(self, task_id: str, attempt_id: str | None):
        if self.storage.read(task_id) is not None:
            return
        with self.sessions.begin() as db:
            db.query(VideoTask).filter(
                VideoTask.task_id == task_id, VideoTask.attempt_id == attempt_id,
                VideoTask.deleted_at.is_(None),
                or_(VideoTask.status == "SUCCESS", VideoTask.status.is_(None)),
            ).update({"status": "FAILED", "error": "笔记结果文件缺失或损坏，请重新生成",
                      "failed_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)},
                     synchronize_session=False)
        self._mirror(task_id)

    @serialized
    def delete(self, task_id: str) -> tuple[list[str], bool]:
        """Commit the tombstone before cleanup; failed IO cannot restore access."""
        validate_task_id(task_id)
        with self.sessions.begin() as db:
            row = db.query(VideoTask).filter_by(task_id=task_id).first()
            if row and row.status in RUNNING:
                raise TaskConflict("任务仍在执行，请等待结束后删除")
            exists = row is not None
            if row:
                row.deleted_at = row.updated_at = datetime.now(timezone.utc)
        return self.storage.delete(task_id), exists

    @serialized
    def recover(self, reason: str, *, exclude: frozenset[str] = frozenset(), before=None) -> int:
        with self.sessions() as db:
            query = db.query(VideoTask).filter(VideoTask.status.in_(RUNNING), VideoTask.deleted_at.is_(None))
            if before is not None:
                query = query.filter(VideoTask.updated_at < before)
            rows = [(r.task_id, r.attempt_id) for r in query.all() if r.task_id not in exclude]
        for task_id, attempt_id in rows:
            self.update(task_id, "FAILED", reason, attempt_id=attempt_id)
        return len(rows)
