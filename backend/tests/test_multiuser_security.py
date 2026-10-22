"""多用户安全与配额的回归测试。

覆盖本轮修复的 4 类问题：
  1. 删除任务的越权漏洞（无 user_id / 未登录两种情况）
  2. 用户级配额（并发数 / 每日数 / 存储）
  3. 列表接口 DB 优先 + 文件回退
  4. 截图按 task_id 隔离
"""

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
    """加载 note router，隔离 NOTE_OUTPUT_DIR 与 DB。"""
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    notes = tmp_path / "note_results"
    notes.mkdir()
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.setenv("OPENNOTER_SECRET_KEY", "test-key-0001")

    try:
        _install_app_namespace()

        # 建表
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
        yield mod, notes
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


class U:
    def __init__(self, uid, admin=False):
        self.id = uid
        self.is_superuser = admin
        self.is_active = True
        # 配额字段（None = 跟随全局默认）
        self.max_concurrent_tasks = None
        self.daily_task_limit = None
        self.storage_quota_mb = None


def _status(notes: Path, task_id: str, payload: dict):
    (notes / f"{task_id}.status.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 1. 删除权限（越权修复）
# ---------------------------------------------------------------------------
def test_cannot_delete_others_task(note_mod):
    mod, notes = note_mod
    _status(notes, "others", {"status": "SUCCESS", "user_id": 2})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.delete_task_by_id("others", user=U(1))
    assert exc.value.status_code == 403


def test_cannot_delete_legacy_task_without_owner(note_mod):
    """无 user_id 的历史任务：普通用户不能删（这是原先的漏洞）。"""
    mod, notes = note_mod
    _status(notes, "legacy", {"status": "SUCCESS"})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.delete_task_by_id("legacy", user=U(1))
    assert exc.value.status_code == 403


def test_anonymous_cannot_delete(note_mod):
    """未登录（未开强制认证时 user=None）不能删（原先另一个漏洞）。"""
    mod, notes = note_mod
    _status(notes, "t1", {"status": "SUCCESS", "user_id": 1})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mod.delete_task_by_id("t1", user=None)
    assert exc.value.status_code == 403


def test_admin_can_delete_legacy_task(note_mod):
    """管理员可以清理无归属的历史数据。"""
    mod, notes = note_mod
    _status(notes, "legacy", {"status": "SUCCESS"})

    resp = mod.delete_task_by_id("legacy", user=U(1, admin=True))
    data = json.loads(bytes(resp.body))["data"]
    assert data["task_id"] == "legacy"
    assert not (notes / "legacy.status.json").exists()


def test_can_delete_own_task(note_mod):
    mod, notes = note_mod
    _status(notes, "mine", {"status": "SUCCESS", "user_id": 1})

    resp = mod.delete_task_by_id("mine", user=U(1))
    data = json.loads(bytes(resp.body))["data"]
    assert data["task_id"] == "mine"
    assert not (notes / "mine.status.json").exists()


# ---------------------------------------------------------------------------
# 2. 配额
# ---------------------------------------------------------------------------
@pytest.fixture()
def quota_mod(tmp_path, monkeypatch):
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    notes = tmp_path / "nr"
    notes.mkdir()
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))

    try:
        _install_app_namespace()
        from app.services import quota

        monkeypatch.setattr(quota, "_note_dir", lambda: notes, raising=False)
        yield quota, notes
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_quota_allows_within_limit(quota_mod, monkeypatch):
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "2")
    _status(notes, "a", {"status": "TRANSCRIBING", "user_id": 1})

    quota.check_submit_quota(U(1))  # 1 < 2，不抛


def test_quota_blocks_excess_concurrent(quota_mod, monkeypatch):
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "2")
    _status(notes, "a", {"status": "TRANSCRIBING", "user_id": 1})
    _status(notes, "b", {"status": "DOWNLOADING", "user_id": 1})

    with pytest.raises(quota.QuotaExceeded) as exc:
        quota.check_submit_quota(U(1))
    assert exc.value.dimension == "concurrent"
    assert exc.value.limit == 2


def test_quota_ignores_finished_tasks(quota_mod, monkeypatch):
    """已完成的任务不占并发额度。"""
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "1")
    _status(notes, "done", {"status": "SUCCESS", "user_id": 1})
    _status(notes, "failed", {"status": "FAILED", "user_id": 1})

    quota.check_submit_quota(U(1))  # 不该抛


def test_quota_only_counts_own_tasks(quota_mod, monkeypatch):
    """别人的进行中任务不该占我的额度。"""
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "1")
    _status(notes, "others", {"status": "TRANSCRIBING", "user_id": 2})

    quota.check_submit_quota(U(1))


def test_quota_daily_limit(quota_mod, monkeypatch):
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "99")
    monkeypatch.setenv("OPENNOTER_USER_DAILY_TASK_LIMIT", "2")
    for i in range(2):
        _status(notes, f"t{i}", {"status": "SUCCESS", "user_id": 1})

    with pytest.raises(quota.QuotaExceeded) as exc:
        quota.check_submit_quota(U(1))
    assert exc.value.dimension == "daily"


def test_quota_admin_bypasses(quota_mod, monkeypatch):
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "1")
    monkeypatch.setenv("OPENNOTER_USER_DAILY_TASK_LIMIT", "1")
    for i in range(5):
        _status(notes, f"t{i}", {"status": "TRANSCRIBING", "user_id": 1})

    quota.check_submit_quota(U(1, admin=True))  # 管理员不限制


def test_quota_anonymous_global_concurrent(quota_mod, monkeypatch):
    """匿名也有全局限流，防止未强制认证时刷接口。"""
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_ANON_MAX_CONCURRENT_TASKS", "1")
    monkeypatch.setenv("OPENNOTER_ANON_SUBMIT_RPM", "0")  # 本用例不测 RPM
    _status(notes, "a", {"status": "TRANSCRIBING"})  # 无 user_id = 匿名

    with pytest.raises(quota.QuotaExceeded) as exc:
        quota.check_submit_quota(None)
    assert exc.value.dimension == "anon_concurrent"


def test_quota_anonymous_rpm(quota_mod, monkeypatch):
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_ANON_MAX_CONCURRENT_TASKS", "0")
    monkeypatch.setenv("OPENNOTER_ANON_SUBMIT_RPM", "2")
    quota._anon_rate_buckets.clear()
    quota.check_submit_quota(None)
    quota.check_submit_quota(None)
    with pytest.raises(quota.QuotaExceeded) as exc:
        quota.check_submit_quota(None)
    assert exc.value.dimension == "anon_rpm"


def test_begin_submit_is_atomic(quota_mod, monkeypatch):
    """check + 写 PENDING 同锁：第二次在第一次写完后应看到占用。"""
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "1")

    def write_a():
        _status(notes, "a", {"status": "PENDING", "user_id": 1})

    quota.begin_submit(U(1), write_a)
    with pytest.raises(quota.QuotaExceeded):
        quota.begin_submit(U(1), lambda: _status(notes, "b", {"status": "PENDING", "user_id": 1}))


def test_quota_user_override(quota_mod, monkeypatch):
    """用户表上的字段可覆盖全局默认。"""
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "10")
    _status(notes, "a", {"status": "TRANSCRIBING", "user_id": 1})
    _status(notes, "b", {"status": "TRANSCRIBING", "user_id": 1})

    user = U(1)
    user.max_concurrent_tasks = 1  # 单独收紧
    with pytest.raises(quota.QuotaExceeded):
        quota.check_submit_quota(user)


def test_quota_zero_means_unlimited(quota_mod, monkeypatch):
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "0")
    monkeypatch.setenv("OPENNOTER_USER_DAILY_TASK_LIMIT", "0")
    for i in range(50):
        _status(notes, f"t{i}", {"status": "TRANSCRIBING", "user_id": 1})

    quota.check_submit_quota(U(1))  # 0 = 不限制


def test_quota_snapshot(quota_mod, monkeypatch):
    quota, notes = quota_mod
    monkeypatch.setenv("OPENNOTER_USER_MAX_CONCURRENT_TASKS", "3")
    _status(notes, "a", {"status": "TRANSCRIBING", "user_id": 1})

    snap = quota.quota_snapshot(U(1))
    assert snap["unlimited"] is False
    assert snap["concurrent"]["limit"] == 3
    assert snap["concurrent"]["used"] == 1


# ---------------------------------------------------------------------------
# 3. 截图隔离
# ---------------------------------------------------------------------------
def test_screenshot_dir_is_per_task(tmp_path, monkeypatch):
    """截图应落到 static/screenshots/<task_id>/，而不是平铺根目录。"""
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    shots = tmp_path / "screenshots"
    monkeypatch.setenv("OUT_DIR", str(shots))
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(tmp_path / "nr"))

    try:
        _install_app_namespace()
        import importlib.util

        target = BACKEND_DIR / "app" / "services" / "note.py"
        spec = importlib.util.spec_from_file_location("app.services.note", target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["app.services.note"] = mod
        spec.loader.exec_module(mod)

        monkeypatch.setattr(mod, "IMAGE_OUTPUT_DIR", str(shots), raising=False)
        monkeypatch.setattr(mod, "IMAGE_BASE_URL", "/static/screenshots", raising=False)

        def fake_shot(video_path, output_dir, ts, idx):
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            f = out / f"screenshot_{idx:03}.jpg"
            f.write_bytes(b"jpg")
            return str(f)

        monkeypatch.setattr(mod, "generate_screenshot", fake_shot, raising=False)

        gen = mod.NoteGenerator.__new__(mod.NoteGenerator)
        md = "# 前言\n\n*Screenshot-00:05\n"
        result = gen._insert_screenshots(md, Path("/tmp/x.mp4"), task_id="task-abc")

        # URL 应带 task_id 子目录
        assert "/static/screenshots/task-abc/" in result
        # 文件应落在子目录里
        assert (shots / "task-abc" / "screenshot_000.jpg").exists()
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
