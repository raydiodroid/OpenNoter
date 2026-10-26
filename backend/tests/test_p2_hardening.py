"""P2 批次修复的回归测试。

覆盖「特定条件下才会炸」的那几条 —— 它们最难靠人工验证，必须用测试钉住：

- **F49**：运行期僵尸任务清理（双条件判定，只看 mtime 会误杀长任务）
- **F13**：任务中间媒体清理（按任务登记，不删 uploads 里的用户原件）
- **F47**：SQLite 文件权限 0600（含 WAL/SHM 旁文件）
- **F25**：`is_retryable` 白名单（旧实现默认 True，把代码 bug 也重试了）
- **F24**：转写异常必须重新抛出，不能吞掉返回 None
- **O-2**：状态文件损坏必须降级，不能 500
- **O-4**：空 markdown 不能被 `prepend_source_link` "补"成非空
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# F49 · 僵尸任务清理
# ---------------------------------------------------------------------------
def _write_status(directory: Path, task_id: str, status: str, age_seconds: float) -> Path:
    """在 directory 下写一个状态文件，并把 mtime 调到 age_seconds 之前。"""
    path = directory / f"{task_id}.status.json"
    path.write_text(
        json.dumps({"status": status, "task_id": task_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


class TestStaleTaskReaper:
    """F49：定期清理「状态说还在跑、但已经没人跑」的任务。"""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        from app.services import note as note_mod

        monkeypatch.setattr(note_mod, "NOTE_OUTPUT_DIR", tmp_path)
        return tmp_path

    def test_reaps_zombie_task(self, env, monkeypatch):
        from app.services import task_interrupt as ti
        from app.services.task_serial_executor import task_serial_executor

        monkeypatch.setattr(task_serial_executor, "active_task_ids", lambda: frozenset())
        path = _write_status(env, "zombie", "DOWNLOADING", 3600)

        stats = ti.reap_stale_tasks(stale_after=1800, note_dir=env, dry_run=False)

        assert stats["reaped"] == 1
        assert json.loads(path.read_text())["status"] == "FAILED"

    def test_does_not_reap_active_task(self, env, monkeypatch):
        """**最关键的一条**：在池里跑着的任务不能被当成僵尸。"""
        from app.services import task_interrupt as ti
        from app.services.task_serial_executor import task_serial_executor

        monkeypatch.setattr(
            task_serial_executor, "active_task_ids", lambda: frozenset({"running"})
        )
        path = _write_status(env, "running", "TRANSCRIBING", 3600)

        stats = ti.reap_stale_tasks(stale_after=1800, note_dir=env, dry_run=False)

        assert stats["reaped"] == 0
        assert stats["skipped_active"] == 1
        assert json.loads(path.read_text())["status"] == "TRANSCRIBING"

    def test_does_not_reap_fresh_task(self, env, monkeypatch):
        """mtime 很新的任务不动（可能正在正常推进，只是没有状态切换）。"""
        from app.services import task_interrupt as ti
        from app.services.task_serial_executor import task_serial_executor

        monkeypatch.setattr(task_serial_executor, "active_task_ids", lambda: frozenset())
        path = _write_status(env, "fresh", "SUMMARIZING", 5)

        stats = ti.reap_stale_tasks(stale_after=1800, note_dir=env, dry_run=False)

        assert stats["reaped"] == 0
        assert json.loads(path.read_text())["status"] == "SUMMARIZING"

    def test_ignores_terminal_tasks(self, env, monkeypatch):
        """已经是 SUCCESS / FAILED 的不动。"""
        from app.services import task_interrupt as ti
        from app.services.task_serial_executor import task_serial_executor

        monkeypatch.setattr(task_serial_executor, "active_task_ids", lambda: frozenset())
        ok = _write_status(env, "done", "SUCCESS", 7200)
        bad = _write_status(env, "failed", "FAILED", 7200)

        stats = ti.reap_stale_tasks(stale_after=1800, note_dir=env, dry_run=False)

        assert stats["reaped"] == 0
        assert json.loads(ok.read_text())["status"] == "SUCCESS"
        assert json.loads(bad.read_text())["status"] == "FAILED"

    def test_dry_run_does_not_write(self, env, monkeypatch):
        """默认 dry_run：只统计，不改写。"""
        from app.services import task_interrupt as ti
        from app.services.task_serial_executor import task_serial_executor

        monkeypatch.setattr(task_serial_executor, "active_task_ids", lambda: frozenset())
        path = _write_status(env, "zombie", "PENDING", 3600)

        stats = ti.reap_stale_tasks(stale_after=1800, note_dir=env)  # 默认 dry_run=True

        assert stats["dry_run"] is True
        assert stats["stale"] == 1
        assert stats["reaped"] == 0
        assert json.loads(path.read_text())["status"] == "PENDING"

    def test_skips_entirely_when_pool_untracked(self, env, monkeypatch):
        """问不到池状态时**保守跳过** —— 宁可留僵尸也不能误杀。"""
        from app.services import task_interrupt as ti
        from app.services.task_serial_executor import task_serial_executor

        def _boom():
            raise RuntimeError("no tracking")

        monkeypatch.setattr(task_serial_executor, "active_task_ids", _boom)
        path = _write_status(env, "zombie", "DOWNLOADING", 7200)

        stats = ti.reap_stale_tasks(stale_after=1800, note_dir=env, dry_run=False)

        assert stats["reaped"] == 0
        assert json.loads(path.read_text())["status"] == "DOWNLOADING"


# ---------------------------------------------------------------------------
# F13 · 中间媒体清理
# ---------------------------------------------------------------------------
class TestTaskMediaCleanup:
    """F13：清理本任务产生的中间文件，但绝不碰 uploads 里的用户原件。"""

    def test_removes_intermediate_media(self, tmp_path):
        from app.services.note import NoteGenerator

        media = tmp_path / "video.mp4"
        media.write_bytes(b"x" * 1024)

        gen = NoteGenerator.__new__(NoteGenerator)
        gen.video_path = media

        gen._cleanup_task_media("task-1")

        assert not media.exists(), "中间媒体应该被清理"

    def test_never_removes_user_uploads(self):
        """**核心安全护栏**：uploads 里的用户源文件不能删。

        报告里明确警告过：`LocalDownloader.download_video()` 返回的就是
        uploads 里的原件，一刀切按 video_id 前缀删会把用户上传的视频弄丢。
        """
        from app.services.note import NoteGenerator
        from app.utils.upload_paths import uploads_root

        root = uploads_root()
        root.mkdir(parents=True, exist_ok=True)
        user_file = root / "user_video_upload_test.mp4"
        user_file.write_bytes(b"user data")
        try:
            gen = NoteGenerator.__new__(NoteGenerator)
            gen.video_path = user_file

            gen._cleanup_task_media("task-2")

            assert user_file.exists(), "uploads 里的用户原件被误删了！"
        finally:
            user_file.unlink(missing_ok=True)

    def test_missing_path_does_not_raise(self, tmp_path):
        from app.services.note import NoteGenerator

        gen = NoteGenerator.__new__(NoteGenerator)
        gen.video_path = tmp_path / "does-not-exist.mp4"

        gen._cleanup_task_media("task-3")  # 不该抛

    def test_no_video_path_is_safe(self):
        """没设置 video_path（流程早期就失败）也不能抛。"""
        from app.services.note import NoteGenerator

        gen = NoteGenerator.__new__(NoteGenerator)
        gen._cleanup_task_media("task-4")

    def test_none_task_id_is_noop(self, tmp_path):
        from app.services.note import NoteGenerator

        media = tmp_path / "keep.mp4"
        media.write_bytes(b"x")

        gen = NoteGenerator.__new__(NoteGenerator)
        gen.video_path = media

        gen._cleanup_task_media(None)

        assert media.exists(), "没有 task_id 时不该动文件"


# ---------------------------------------------------------------------------
# F47 · SQLite 权限
# ---------------------------------------------------------------------------
class TestSqlitePermissions:
    """F47：库文件（及 WAL/SHM）必须是 0600。"""

    def test_db_file_is_owner_only(self):
        from sqlalchemy import text

        from app.db.engine import engine, sqlite_db_path
        from app.db.init_db import init_db

        if sqlite_db_path is None:
            pytest.skip("非 sqlite 部署")

        init_db()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        if not sqlite_db_path.exists():
            pytest.skip(f"库文件不存在：{sqlite_db_path}")

        mode = stat.S_IMODE(sqlite_db_path.stat().st_mode)
        assert mode == 0o600, f"库文件权限应为 0600，实际 {oct(mode)}"

    def test_wal_files_are_owner_only(self):
        """WAL/SHM 含未合并的新数据，必须一起收紧。"""
        from sqlalchemy import text

        from app.db.engine import engine, sqlite_db_path
        from app.db.init_db import init_db

        if sqlite_db_path is None:
            pytest.skip("非 sqlite 部署")

        init_db()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        for suffix in ("-wal", "-shm"):
            sibling = sqlite_db_path.with_name(sqlite_db_path.name + suffix)
            if not sibling.exists():
                continue
            mode = stat.S_IMODE(sibling.stat().st_mode)
            assert mode == 0o600, f"{suffix} 权限应为 0600，实际 {oct(mode)}"


# ---------------------------------------------------------------------------
# F25 / F24 / O-2 / O-4 · 小修复的回归锁
# ---------------------------------------------------------------------------
class TestIsRetryable:
    """F25：只有网络瞬时故障才重试；确定性失败不重试。"""

    @pytest.mark.parametrize(
        "exc",
        [
            AttributeError("code bug"),
            KeyError("schema changed"),
            ValueError("bad data"),
            TypeError("wrong type"),
            json.JSONDecodeError("bad json", "", 0),
            FileNotFoundError("gone"),
        ],
    )
    def test_deterministic_errors_not_retried(self, exc):
        from app.downloaders.errors import is_retryable

        assert is_retryable(exc) is False, (
            f"{type(exc).__name__} 是确定性失败，重试只会浪费配额并掩盖真 bug"
        )

    @pytest.mark.parametrize("exc", [TimeoutError(), ConnectionError()])
    def test_network_errors_retried(self, exc):
        from app.downloaders.errors import is_retryable

        assert is_retryable(exc) is True

    def test_business_errors_respect_their_flag(self):
        from app.downloaders.errors import (
            RetryableDownloadError,
            SizeLimitExceeded,
            is_retryable,
        )

        assert is_retryable(RetryableDownloadError()) is True
        assert is_retryable(SizeLimitExceeded()) is False


class TestWhisperRaises:
    """F24：转写异常必须抛出，不能 print 后返回 None。"""

    def test_raises_instead_of_returning_none(self):
        from unittest.mock import MagicMock

        from app.transcriber.whisper import WhisperTranscriber

        t = WhisperTranscriber.__new__(WhisperTranscriber)
        t.model = MagicMock()
        t.model.transcribe.side_effect = RuntimeError("CUDA out of memory")

        with pytest.raises(RuntimeError) as excinfo:
            t.transcript("/tmp/fake.wav")

        # 原始原因必须保留（否则排障时只知道"转写失败"）
        assert "CUDA out of memory" in str(excinfo.value)


class TestPrependSourceLink:
    """O-4：空 markdown 不能被补成非空，否则空笔记会被标 SUCCESS。"""

    @pytest.mark.parametrize("empty", ["", "   ", "\n\n", "\t"])
    def test_empty_stays_empty(self, empty):
        from app.utils.note_helper import prepend_source_link

        out = prepend_source_link(empty, "https://example.com/v")
        assert not (out or "").strip(), "空内容被补成了非空 → 会让失败的任务显示成功"

    def test_normal_content_gets_link(self):
        from app.utils.note_helper import prepend_source_link

        out = prepend_source_link("# 标题\n\n正文", "https://example.com/v")
        assert out is not None
        assert "https://example.com/v" in out
        assert "# 标题" in out

    def test_none_stays_none(self):
        from app.utils.note_helper import prepend_source_link

        assert prepend_source_link(None, "https://example.com/v") is None
