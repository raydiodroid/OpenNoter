"""P0 / 可控 P1 安全加固回归。

覆盖审查报告 §1：
  P0-1 匿名创建与任务 ACL 一致（未强制认证时可凭 task_id 读无归属任务）
  P0-2 上传文件名净化 + uploads 路径沙箱
  P0-3 get_task_status 仅有结果文件时也做归属校验
  P1-6 配额错误不再包进 R.success
  P1-7 _purge_task 清指纹缓存
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _install_app_namespace():
    if "app" in sys.modules:
        return
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(BACKEND_DIR / "app")]
    pkg.__package__ = "app"
    sys.modules["app"] = pkg


@pytest.fixture()
def note_mod(tmp_path, monkeypatch):
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    notes = tmp_path / "note_results"
    notes.mkdir()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("OPENNOTER_SECRET_KEY", "test-key-0001")
    monkeypatch.delenv("AUTH_ENFORCE", raising=False)
    monkeypatch.delenv("OPENNOTER_AUTH_ENFORCE", raising=False)
    monkeypatch.delenv("BILINOTE_AUTH_ENFORCE", raising=False)
    monkeypatch.setenv("OPENNOTER_AUTH_ENFORCE", "false")

    try:
        _install_app_namespace()
        from app.db.engine import Base, get_engine
        from app.db.models.models import Model  # noqa: F401
        from app.db.models.providers import Provider  # noqa: F401
        from app.db.models.users import User  # noqa: F401
        from app.db.models.api_keys import ApiKey  # noqa: F401
        from app.db.models.video_tasks import VideoTask  # noqa: F401

        Base.metadata.create_all(bind=get_engine())

        import importlib.util

        target = BACKEND_DIR / "app" / "routers" / "note.py"
        spec = importlib.util.spec_from_file_location("app.routers.note", target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["app.routers.note"] = mod
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod, "NOTE_OUTPUT_DIR", str(notes), raising=False)
        monkeypatch.setattr(mod, "UPLOAD_DIR", str(uploads), raising=False)
        yield mod, notes, uploads
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


class U:
    def __init__(self, uid, admin=False):
        self.id = uid
        self.is_superuser = admin
        self.is_active = True
        self.storage_quota_mb = None


def _status(notes: Path, task_id: str, payload: dict):
    (notes / f"{task_id}.status.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _result(notes: Path, task_id: str, payload: dict | None = None):
    (notes / f"{task_id}.json").write_text(
        json.dumps(payload or {"markdown": "secret note"}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# P0-1 ACL：匿名 ↔ 无归属
# ---------------------------------------------------------------------------
def test_anonymous_can_poll_ownerless_when_auth_not_enforced(note_mod, monkeypatch):
    mod, notes, _uploads = note_mod
    monkeypatch.setenv("OPENNOTER_AUTH_ENFORCE", "false")
    _status(notes, "anon1", {"status": "PENDING", "message": "排队中"})

    resp = mod.get_task_status("anon1", user=None)
    body = json.loads(bytes(resp.body))
    assert body["code"] == 0
    assert body["data"]["status"] == "PENDING"


def test_anonymous_cannot_poll_owned_task(note_mod):
    mod, notes, _uploads = note_mod
    _status(notes, "owned", {"status": "PENDING", "user_id": 1})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.get_task_status("owned", user=None)
    assert exc.value.status_code == 403


def test_logged_in_non_admin_cannot_access_ownerless(note_mod):
    mod, notes, _uploads = note_mod
    _status(notes, "legacy", {"status": "SUCCESS"})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.get_task_status("legacy", user=U(1))
    assert exc.value.status_code == 403


def test_ownerless_denied_when_auth_enforced(note_mod, monkeypatch):
    """强制认证开启后，无归属仅管理员；匿名不应再靠 task_id 读。"""
    mod, notes, _uploads = note_mod
    monkeypatch.setenv("OPENNOTER_AUTH_ENFORCE", "true")
    # auth_enforced 读环境变量；清一下可能的缓存无关，getbool 每次读
    _status(notes, "legacy2", {"status": "SUCCESS"})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.get_task_status("legacy2", user=None)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# P0-3 结果文件旁路
# ---------------------------------------------------------------------------
def test_result_only_requires_acl_for_owned_via_db(note_mod):
    """无 status、仅有结果：有 DB 归属时非主人 403。"""
    mod, notes, _uploads = note_mod
    _result(notes, "r1", {"markdown": "private"})

    from app.db.video_task_dao import insert_video_task

    insert_video_task(video_id="v1", platform="bilibili", task_id="r1", user_id=2)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.get_task_status("r1", user=U(1))
    assert exc.value.status_code == 403


def test_result_only_owner_can_read(note_mod):
    mod, notes, _uploads = note_mod
    _result(notes, "r2", {"markdown": "mine"})

    from app.db.video_task_dao import insert_video_task

    insert_video_task(video_id="v2", platform="bilibili", task_id="r2", user_id=1)

    resp = mod.get_task_status("r2", user=U(1))
    body = json.loads(bytes(resp.body))
    assert body["code"] == 0
    assert body["data"]["result"]["markdown"] == "mine"


def test_result_only_no_owner_anonymous_ok_when_not_enforced(note_mod, monkeypatch):
    mod, notes, _uploads = note_mod
    monkeypatch.setenv("OPENNOTER_AUTH_ENFORCE", "false")
    _result(notes, "r3", {"markdown": "legacy"})

    resp = mod.get_task_status("r3", user=None)
    body = json.loads(bytes(resp.body))
    assert body["code"] == 0
    assert body["data"]["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# P0-2 上传路径沙箱
# ---------------------------------------------------------------------------
def test_resolve_under_uploads_blocks_traversal(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    monkeypatch.chdir(tmp_path)

    from app.utils.upload_paths import resolve_under_uploads

    with pytest.raises(ValueError):
        resolve_under_uploads("/uploads/../secret.txt")
    with pytest.raises(ValueError):
        resolve_under_uploads("/uploads/../../etc/passwd")


def test_resolve_under_uploads_allows_safe_file(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    target = uploads / "abc.mp4"
    target.write_bytes(b"x")
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    monkeypatch.chdir(tmp_path)

    from app.utils.upload_paths import resolve_under_uploads

    got = resolve_under_uploads("/uploads/abc.mp4")
    assert got == target.resolve()


def test_sanitize_store_name_rejects_bad_ext():
    from app.utils.upload_paths import sanitize_store_name

    with pytest.raises(ValueError):
        sanitize_store_name("../../etc/passwd")
    with pytest.raises(ValueError):
        sanitize_store_name("evil.exe")
    name = sanitize_store_name("my video.MP4")
    assert name.endswith(".mp4")
    assert "/" not in name and ".." not in name


def test_local_downloader_rejects_traversal(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    monkeypatch.chdir(tmp_path)

    from app.downloaders.local_downloader import LocalDownloader

    dl = LocalDownloader()
    with pytest.raises(FileNotFoundError):
        dl._resolve_local_path("/uploads/../note_results/x.json")


# ---------------------------------------------------------------------------
# P1-7 purge 指纹缓存
# ---------------------------------------------------------------------------
def test_purge_clears_fingerprint_caches(note_mod):
    mod, notes, _uploads = note_mod
    tid = "purgeme"
    _status(notes, tid, {"status": "SUCCESS", "user_id": 1})
    _result(notes, tid, {"markdown": "x"})
    (notes / f"{tid}_abcd1234_audio.json").write_text("{}", encoding="utf-8")
    (notes / f"{tid}_abcd1234_transcript.json").write_text("{}", encoding="utf-8")
    (notes / f"{tid}_abcd1234_markdown.md").write_text("# x", encoding="utf-8")
    (notes / f"{tid}_audio.json").write_text("{}", encoding="utf-8")

    removed, _ = mod._purge_task(tid)
    assert not (notes / f"{tid}.status.json").exists()
    assert not (notes / f"{tid}_abcd1234_audio.json").exists()
    assert not (notes / f"{tid}_abcd1234_transcript.json").exists()
    assert not (notes / f"{tid}_abcd1234_markdown.md").exists()
    assert not (notes / f"{tid}_audio.json").exists()
    assert any("abcd1234_audio" in p for p in removed)


# ---------------------------------------------------------------------------
# P1-6 配额失败不包进 success（单元：直接测返回值分支逻辑）
# ---------------------------------------------------------------------------
def test_submit_quota_error_not_wrapped_as_task_id_string(note_mod, monkeypatch):
    """_submit_note_task 配额失败返回 JSONResponse；调用方必须识别非 str。"""
    mod, notes, _uploads = note_mod

    class Exc(Exception):
        def __init__(self):
            self.message = "超额"
            self.dimension = "daily"
            self.limit = 1
            self.current = 1
            self.retry_after = 60

    def boom(_user):
        raise Exc()

    # 把 QuotaExceeded 换成我们的 Exc，并让 check_submit_quota 抛它
    import app.services.quota as quota_mod

    class QE(Exception):
        def __init__(self):
            self.message = "超额"
            self.dimension = "daily"
            self.limit = 1
            self.current = 1
            self.retry_after = 60

    monkeypatch.setattr(quota_mod, "QuotaExceeded", QE)
    monkeypatch.setattr(quota_mod, "check_submit_quota", lambda u: (_ for _ in ()).throw(QE()))

    from fastapi import BackgroundTasks
    from app.enmus.note_enums import DownloadQuality

    # resolve_model_selection 不应被调用到；若配额先拦则 OK
    out = mod._submit_note_task(
        BackgroundTasks(),
        video_url="https://www.bilibili.com/video/BV1xx411c7mD",
        platform="bilibili",
        quality=DownloadQuality.fast,
        screenshot=False,
        link=False,
        model_name=None,
        provider_id=None,
        task_id=None,
        fmt=[],
        style=None,
        extras=None,
        video_understanding=False,
        video_interval=0,
        grid_size=[],
        prefetched_transcript={"language": "zh", "full_text": "hi", "segments": []},
        user=U(1),
    )
    assert not isinstance(out, str)
    body = json.loads(bytes(out.body))
    assert body["code"] == 429
    assert body["data"]["reason"] == "quota_exceeded"
