"""`GET /tasks` 任务列表接口的回归测试。

背景：任务列表原先只存在前端 IndexedDB，换浏览器/设备就看不到历史。
本接口把列表能力补到服务端，让历史跟着账号走。

重点验证**归属隔离**，这是安全红线：
  - 有 user_id 且等于当前用户 → 可见
  - 有 user_id 但不是当前用户 → 不可见
  - 无 user_id（历史数据）→ 仅管理员可见
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
def note_router(tmp_path, monkeypatch):
    """加载 note router，并把 NOTE_OUTPUT_DIR 指向临时目录。

    与其它测试隔离：snapshot/restore sys.modules 里的 app.* 条目，
    避免 test_whisper_models 之类的模块级 stub 互相干扰。
    """
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    notes = tmp_path / "note_results"
    notes.mkdir()
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")

    try:
        _install_app_namespace()
        import importlib
        import importlib.util

        # 按文件路径加载，绕过 app/__init__.py 的聚合导入
        for name, path in (
            ("app.services", BACKEND_DIR / "app" / "services"),
            ("app.utils", BACKEND_DIR / "app" / "utils"),
            ("app.db", BACKEND_DIR / "app" / "db"),
            ("app.enmus", BACKEND_DIR / "app" / "enmus"),
            ("app.exceptions", BACKEND_DIR / "app" / "exceptions"),
            ("app.models", BACKEND_DIR / "app" / "models"),
            ("app.downloaders", BACKEND_DIR / "app" / "downloaders"),
            ("app.validators", BACKEND_DIR / "app" / "validators"),
        ):
            if name not in sys.modules:
                m = types.ModuleType(name)
                m.__package__ = name
                m.__path__ = [str(path)]
                sys.modules[name] = m

        target = BACKEND_DIR / "app" / "routers" / "note.py"
        spec = importlib.util.spec_from_file_location("app.routers.note", target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["app.routers.note"] = mod
        spec.loader.exec_module(mod)
        # 关键：模块级 NOTE_OUTPUT_DIR 已经在 import 时求值，这里改成临时目录
        monkeypatch.setattr(mod, "NOTE_OUTPUT_DIR", str(notes), raising=False)
        yield mod, notes
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


class FakeUser:
    def __init__(self, uid: int, admin: bool = False):
        self.id = uid
        self.is_superuser = admin
        self.is_active = True


def _write_status(notes: Path, task_id: str, payload: dict):
    (notes / f"{task_id}.status.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _call(router_mod, user, **kwargs):
    """直接调用 list_tasks（它是普通函数，返回 JSONResponse）。"""
    import json as _json

    resp = router_mod.list_tasks(user=user, **kwargs)
    return _json.loads(bytes(resp.body))["data"]


# ---------------------------------------------------------------------------
# 归属隔离（安全红线）
# ---------------------------------------------------------------------------
def test_only_own_tasks_visible(note_router):
    """用户只能看到自己的任务。"""
    mod, notes = note_router
    _write_status(notes, "mine", {"status": "SUCCESS", "user_id": 1})
    _write_status(notes, "others", {"status": "SUCCESS", "user_id": 2})

    data = _call(mod, FakeUser(1))
    ids = [i["task_id"] for i in data["items"]]
    assert ids == ["mine"]
    assert data["total"] == 1


def test_anonymous_sees_nothing_owned(note_router):
    """未登录用户看不到任何有归属的任务。"""
    mod, notes = note_router
    _write_status(notes, "a", {"status": "SUCCESS", "user_id": 1})

    data = _call(mod, None)
    assert data["items"] == []
    assert data["total"] == 0


def test_legacy_tasks_without_owner_hidden_from_normal_user(note_router):
    """无 user_id 的历史任务：普通用户看不到（避免翻到别人早期任务）。"""
    mod, notes = note_router
    _write_status(notes, "legacy", {"status": "SUCCESS"})

    data = _call(mod, FakeUser(1, admin=False))
    assert data["items"] == []


def test_legacy_tasks_visible_to_admin(note_router):
    """无 user_id 的历史任务：管理员可见（便于排查）。"""
    mod, notes = note_router
    _write_status(notes, "legacy", {"status": "SUCCESS"})

    data = _call(mod, FakeUser(1, admin=True))
    assert [i["task_id"] for i in data["items"]] == ["legacy"]


def test_admin_still_only_sees_own_tagged_tasks(note_router):
    """管理员能看无归属的历史，但**不能**看别人有归属的任务。"""
    mod, notes = note_router
    _write_status(notes, "admins", {"status": "SUCCESS", "user_id": 1})
    _write_status(notes, "others", {"status": "SUCCESS", "user_id": 2})
    _write_status(notes, "legacy", {"status": "SUCCESS"})

    data = _call(mod, FakeUser(1, admin=True))
    ids = {i["task_id"] for i in data["items"]}
    assert ids == {"admins", "legacy"}
    assert "others" not in ids


# ---------------------------------------------------------------------------
# 分页 / 过滤 / 健壮性
# ---------------------------------------------------------------------------
def test_pagination(note_router):
    mod, notes = note_router
    for i in range(5):
        _write_status(notes, f"t{i}", {"status": "SUCCESS", "user_id": 1})

    page1 = _call(mod, FakeUser(1), limit=2, offset=0)
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert page1["limit"] == 2

    page3 = _call(mod, FakeUser(1), limit=2, offset=4)
    assert len(page3["items"]) == 1


def test_limit_is_clamped(note_router):
    """limit 过大时收敛到 200，避免一次拉爆。"""
    mod, notes = note_router
    _write_status(notes, "a", {"status": "SUCCESS", "user_id": 1})

    data = _call(mod, FakeUser(1), limit=99999)
    assert data["limit"] == 200


def test_status_filter(note_router):
    mod, notes = note_router
    _write_status(notes, "ok", {"status": "SUCCESS", "user_id": 1})
    _write_status(notes, "bad", {"status": "FAILED", "user_id": 1})

    only_failed = _call(mod, FakeUser(1), status="failed")
    assert [i["task_id"] for i in only_failed["items"]] == ["bad"]


def test_corrupt_files_are_skipped_not_fatal(note_router):
    """坏 JSON 不能让整个列表 500。"""
    mod, notes = note_router
    _write_status(notes, "good", {"status": "SUCCESS", "user_id": 1})
    (notes / "broken.status.json").write_text("{not json", encoding="utf-8")
    (notes / "nondict.status.json").write_text(json.dumps(["x"]), encoding="utf-8")

    data = _call(mod, FakeUser(1))
    assert [i["task_id"] for i in data["items"]] == ["good"]


def test_missing_dir_returns_empty(note_router, tmp_path):
    mod, _ = note_router
    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(mod, "NOTE_OUTPUT_DIR", str(tmp_path / "nope"), raising=False)
    try:
        data = _call(mod, FakeUser(1))
        assert data["items"] == []
        assert data["total"] == 0
    finally:
        mp.undo()


def test_sorted_by_updated_desc(note_router):
    """列表按更新时间倒序。"""
    import time

    mod, notes = note_router
    _write_status(notes, "old", {"status": "SUCCESS", "user_id": 1})
    time.sleep(0.05)
    _write_status(notes, "new", {"status": "SUCCESS", "user_id": 1})

    data = _call(mod, FakeUser(1))
    assert [i["task_id"] for i in data["items"]] == ["new", "old"]


def test_has_result_flag(note_router):
    """有正文的任务标 has_result=True，前端据此决定是否需要拉全文。"""
    mod, notes = note_router
    _write_status(notes, "with-body", {"status": "SUCCESS", "user_id": 1})
    (notes / "with-body.json").write_text('{"markdown": "..."}', encoding="utf-8")
    _write_status(notes, "no-body", {"status": "FAILED", "user_id": 1})

    data = _call(mod, FakeUser(1))
    by_id = {i["task_id"]: i for i in data["items"]}
    assert by_id["with-body"]["has_result"] is True
    assert by_id["no-body"]["has_result"] is False


# ---------------------------------------------------------------------------
# 残留文件过滤：`{task_id}_markdown.status.json` 不该被当成独立任务
# ---------------------------------------------------------------------------
def test_markdown_residual_files_are_ignored(note_router):
    """旧版代码留下的 `_markdown.status.json` 会被误认为独立任务，必须排除。"""
    mod, notes = note_router
    uid = "744a5d88-579d-4c18-b835-ab59719e06ab"
    _write_status(notes, uid, {"status": "SUCCESS", "user_id": 1})
    # 残留：内容是过期的 SUMMARIZING
    (notes / f"{uid}_markdown.status.json").write_text(
        json.dumps({"status": "SUMMARIZING"}), encoding="utf-8"
    )
    (notes / "abc_audio.status.json").write_text(
        json.dumps({"status": "SUMMARIZING"}), encoding="utf-8"
    )

    data = _call(mod, FakeUser(1))
    ids = [i["task_id"] for i in data["items"]]
    assert ids == [uid], f"残留文件不应出现，实际: {ids}"
    assert data["total"] == 1
