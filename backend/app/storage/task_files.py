"""Atomic, sandboxed task artifacts. Database rows own task lifecycle state."""
from __future__ import annotations

import json
import hashlib
import os
import tempfile
import logging
import shutil
from pathlib import Path
from typing import Protocol

from app.utils.note_paths import note_file, resolve_under, validate_task_id, UnsafeTaskId


def cache_fingerprint(video_url, quality) -> str:
    raw = f"{video_url}|{getattr(quality, 'value', quality)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class TaskStorage(Protocol):
    def delete(self, task_id: str) -> list[str]: ...
    def size(self, task_id: str) -> int: ...
    def read(self, task_id: str, suffix: str = ".json") -> dict | None: ...
    def write(self, task_id: str, data: dict, suffix: str = ".json") -> None: ...


class LocalTaskStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def read(self, task_id: str, suffix: str = ".json") -> dict | None:
        path = note_file(self.root, task_id, suffix)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (FileNotFoundError, ValueError):
            return None

    def write(self, task_id: str, data: dict, suffix: str = ".json") -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = note_file(self.root, task_id, suffix)
        # Unique temp names avoid writers racing on the same .tmp file.
        fd, temporary = tempfile.mkstemp(prefix=f".{task_id}-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _artifacts(self, task_id: str):
        validate_task_id(task_id)
        names = {f"{task_id}{suffix}" for suffix in (
            ".json", ".status.json", "_audio.json", "_transcript.json", "_markdown.md",
        )}
        for pattern in ("*_audio.json", "*_transcript.json", "*_markdown.md", "*_markdown.status.json"):
            names.update(p.name for p in self.root.glob(f"{task_id}_{pattern}"))
        for name in sorted(names):
            try:
                path = resolve_under(self.root, name)
                if path.is_file():
                    yield path
            except (OSError, UnsafeTaskId):
                continue

    def _screenshots(self, task_id: str) -> Path:
        validate_task_id(task_id)
        return resolve_under(os.getenv("OUT_DIR", "./static/screenshots"), task_id)

    def size(self, task_id: str) -> int:
        total = 0
        paths = list(self._artifacts(task_id))
        try:
            screenshots = self._screenshots(task_id)
            for path in screenshots.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    paths.append(resolve_under(screenshots, str(path.relative_to(screenshots))))
        except (OSError, UnsafeTaskId):
            pass
        for path in paths:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def delete(self, task_id: str) -> list[str]:
        removed = []
        for path in self._artifacts(task_id):
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                logging.getLogger(__name__).warning("任务产物清理失败: %s", path, exc_info=True)
        try:
            screenshots = self._screenshots(task_id)
            if screenshots.is_dir():
                shutil.rmtree(screenshots)
                removed.append(str(screenshots))
        except (OSError, UnsafeTaskId):
            logging.getLogger(__name__).warning("截图清理失败: %s", task_id, exc_info=True)
        return removed
