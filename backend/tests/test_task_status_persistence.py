"""任务状态持久化 / 后处理失败语义 的回归测试。

覆盖 `_audit/REPORT.md` 里此前标记为「未修复」的几项：

1. `_update_status` 写盘失败时不能再把错误文本写进 `.status.json`
   （旧实现产出合法但非 JSON 的坏文件，监控计入 unreadable、前端永远 PENDING）
2. `_insert_screenshots` 单张截图失败不能把整篇笔记变成 None
   （旧实现 return None → 笔记空白但任务标 SUCCESS）
3. `video_tasks` 状态列迁移幂等 + 可回填
4. `update_task_status` 语义：FAILED 写 failed_at，恢复成功时清空

这些测试尽量只依赖「纯函数 + 临时目录 + 临时 sqlite」，不启动 HTTP。
"""

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _register_ns(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mod.__package__ = name
        sys.modules[name] = mod
    paths = list(getattr(mod, "__path__", []) or [])
    if str(path) not in paths:
        paths.append(str(path))
    try:
        mod.__path__ = paths
    except Exception:
        pass


def _load_note_module():
    """加载真实的 `app.services.note` 模块。

    优先走正常 import（整条真实依赖链）。只有当这个测试文件**单独**运行时
    才会因为 `app/__init__.py` 聚合导入重依赖而失败 —— 这时退回到
    「用真实的 app 包 + 只注册必要的空子包」的方式，并**不**污染 sys.modules：
    加载完成后把临时插入的条目全部还原。
    """
    import importlib.util

    # 1) 首选：真实 import（其他测试已经 import 过 app 时一定可用）
    try:
        import app.services.note as note_module  # noqa: PLC0415

        return note_module
    except Exception:
        pass

    # 2) 退化路径：单独运行本文件时
    inserted = []

    def _ensure(name: str, path: Path):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__package__ = name
            mod.__path__ = [str(path)]
            sys.modules[name] = mod
            inserted.append(name)

    for name, path in (
        ("app", BACKEND_DIR / "app"),
        ("app.services", BACKEND_DIR / "app" / "services"),
        ("app.utils", BACKEND_DIR / "app" / "utils"),
        ("app.db", BACKEND_DIR / "app" / "db"),
        ("app.enmus", BACKEND_DIR / "app" / "enmus"),
        ("app.exceptions", BACKEND_DIR / "app" / "exceptions"),
        ("app.models", BACKEND_DIR / "app" / "models"),
        ("app.downloaders", BACKEND_DIR / "app" / "downloaders"),
        ("app.validators", BACKEND_DIR / "app" / "validators"),
    ):
        if path.is_dir():
            _ensure(name, path)

    target = BACKEND_DIR / "app" / "services" / "note.py"
    mod_name = "app.services.note"
    previous = sys.modules.get(mod_name)

    spec = importlib.util.spec_from_file_location(mod_name, target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        if previous is not None:
            sys.modules[mod_name] = previous
        else:
            sys.modules.pop(mod_name, None)
        for name in inserted:
            sys.modules.pop(name, None)
    return mod


@pytest.fixture()
def note_mod(tmp_path, monkeypatch):
    """加载 note 模块，并把 NOTE_OUTPUT_DIR 指到临时目录。

    用一条**独立于 sys.modules 的包树**加载：`tests/test_whisper_models.py`
    会在模块级往 sys.modules 塞 `app` / `app.utils` 空包（`__path__ = []`），
    把后续所有 `app.*` 导入搞坏。与其逐个修补那些空包，不如在加载期间
    把 `app` 相关的 sys.modules 条目整个 snapshot → 替换成干净的 namespace 包
    → 加载完成后整体还原。这样对其它测试零污染、也不受它们污染。
    """
    notes = tmp_path / "note_results"
    notes.mkdir()
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'open_noter.db'}")

    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    try:
        mod = _load_note_module()
    finally:
        # 清掉本次插入的 app.*，再把原状态整体放回
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)

    monkeypatch.setattr(mod, "NOTE_OUTPUT_DIR", notes, raising=False)
    return mod, notes


# ---------------------------------------------------------------------------
# 1. 状态写盘失败：不能污染 .status.json
# ---------------------------------------------------------------------------
def test_status_write_failure_writes_sidecar_not_corrupt_status(note_mod, monkeypatch):
    """写盘失败时保留原状态文件，错误落到 .err 旁路文件。"""
    mod, notes = note_mod
    gen = mod.NoteGenerator.__new__(mod.NoteGenerator)

    task_id = "t-writefail"
    status_file = notes / f"{task_id}.status.json"
    # 先落一个正常状态
    status_file.write_text(json.dumps({"status": "PARSING"}), encoding="utf-8")

    # 只让「写 .tmp」失败，不影响后面写 .err。
    import pathlib

    real_open = pathlib.Path.open

    def flaky_open(self, *args, **kwargs):
        if str(self).endswith(".tmp"):
            raise OSError("simulated disk failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", flaky_open, raising=False)

    gen._update_status(task_id, "TRANSCRIBING")

    # 原文件必须仍然可解析，且保持旧状态（宁可旧一拍，不可写坏）
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["status"] == "PARSING"

    # 错误落到旁路文件
    err_file = notes / f"{task_id}.err"
    assert err_file.exists(), "应写出 .err 旁路文件"
    err_payload = json.loads(err_file.read_text(encoding="utf-8"))
    assert "simulated disk failure" in err_payload["error"]
    assert err_payload["attempted_status"] == "TRANSCRIBING"


def test_status_write_success_records_failed_at(note_mod):
    """FAILED 状态要落不可变 failed_at（监控 24h 统计靠它，而不是 mtime）。"""
    mod, notes = note_mod
    gen = mod.NoteGenerator.__new__(mod.NoteGenerator)
    task_id = "t-failed-at"
    gen._update_status(task_id, "FAILED", message="boom")
    payload = json.loads((notes / f"{task_id}.status.json").read_text(encoding="utf-8"))
    assert payload["status"] == "FAILED"
    assert payload["message"] == "boom"
    assert "failed_at" in payload
    datetime.fromisoformat(payload["failed_at"])


def test_status_write_success_keeps_user_id(note_mod):
    """后续状态更新不能丢掉首次写入的归属。"""
    mod, notes = note_mod
    gen = mod.NoteGenerator.__new__(mod.NoteGenerator)
    task_id = "t-owner"
    gen._update_status(task_id, "PENDING", user_id=42)
    gen._update_status(task_id, "SUMMARIZING")
    payload = json.loads((notes / f"{task_id}.status.json").read_text(encoding="utf-8"))
    assert payload["user_id"] == 42
    assert payload["status"] == "SUMMARIZING"


# ---------------------------------------------------------------------------
# 2. 截图失败不能把整篇笔记变 None
# ---------------------------------------------------------------------------
def test_insert_screenshots_failure_keeps_markdown(note_mod, monkeypatch):
    """单张截图失败只跳过该标记，其余内容照常返回（旧实现 return None）。"""
    mod, _ = note_mod
    gen = mod.NoteGenerator.__new__(mod.NoteGenerator)

    def boom(*a, **k):
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(mod, "generate_screenshot", boom, raising=False)

    markdown = "# 前言\n\n*Screenshot-00:05\n\n后记"
    result = gen._insert_screenshots(markdown, Path("/tmp/fake.mp4"))

    assert result is not None, "截图失败时不能返回 None"
    assert "前言" in result and "后记" in result
    # 残留标记被清掉，笔记保持可读
    assert "Screenshot-00:05" not in result


def test_insert_screenshots_success_replaces_markers(note_mod, monkeypatch):
    mod, _ = note_mod
    gen = mod.NoteGenerator.__new__(mod.NoteGenerator)

    def fake_shot(*a, **k):
        return "/tmp/shot.png"

    monkeypatch.setattr(mod, "generate_screenshot", fake_shot, raising=False)
    markdown = "# 前言\n\n*Screenshot-00:05\n\n后记"
    result = gen._insert_screenshots(markdown, Path("/tmp/fake.mp4"))
    assert "shot.png" in result
    assert "Screenshot-00:05" not in result


def test_post_process_never_returns_none(note_mod, monkeypatch):
    """后处理环节即使内部返回怪值，也不能把 markdown 变成 None。"""
    mod, _ = note_mod
    gen = mod.NoteGenerator.__new__(mod.NoteGenerator)

    audio_meta = types.SimpleNamespace(video_id="BV1xx", platform="bilibili")
    monkeypatch.setattr(
        gen, "_insert_screenshots", lambda md, vp: None, raising=False
    )
    result = gen._post_process_markdown(
        markdown="# 正常内容",
        video_path=Path("/tmp/fake.mp4"),
        formats=["screenshot"],
        audio_meta=audio_meta,
        platform="bilibili",
    )
    assert result == "# 正常内容"


# ---------------------------------------------------------------------------
# 3. video_tasks 状态列：迁移幂等
# ---------------------------------------------------------------------------
def _make_legacy_db(db_path: Path):
    """造一个「旧版本」的 video_tasks 表：没有 status/error/failed_at/updated_at。

    新建库时 create_all 已包含新列，所以必须手工建旧结构才能测迁移。
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE video_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id VARCHAR NOT NULL,
                platform VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL UNIQUE,
                user_id INTEGER,
                created_at DATETIME
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_migrate_task_status_is_idempotent(tmp_path, monkeypatch):
    """旧库升级：首次补 4 列，重复执行不应报错、也不应重复加列。"""
    db_path = tmp_path / "mig.db"
    _make_legacy_db(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(tmp_path / "note_results"))
    for name in list(sys.modules):
        if name.startswith("app.") or name == "migrate_task_status":
            sys.modules.pop(name, None)

    import importlib

    migrate = importlib.import_module("migrate_task_status")

    first = migrate.add_columns()
    assert first == 5, f"首次应新增 5 列，实际 {first}"

    second = migrate.add_columns()
    assert second == 0, "重复执行不应再加列"

    # 列确实存在
    from sqlalchemy import text

    from app.db.engine import get_engine

    with get_engine().connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(video_tasks)")).fetchall()}
    assert {"status", "error", "failed_at", "updated_at", "deleted_at"} <= cols


def test_migrate_on_fresh_db_is_noop(tmp_path, monkeypatch):
    """新库由 create_all 建出完整列，迁移应该是 no-op（幂等的一部分）。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(tmp_path / "note_results"))
    for name in list(sys.modules):
        if name.startswith("app.") or name == "migrate_task_status":
            sys.modules.pop(name, None)

    import importlib

    from app.db.init_db import init_db

    init_db()
    migrate = importlib.import_module("migrate_task_status")
    assert migrate.add_columns() == 0, "新库已含全部列，不应再加"


def test_migrate_backfill_from_status_files(tmp_path, monkeypatch):
    """迁移能从 .status.json 回填历史状态。"""
    notes = tmp_path / "note_results"
    notes.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bf.db'}")
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))
    for name in list(sys.modules):
        if name.startswith("app.") or name == "migrate_task_status":
            sys.modules.pop(name, None)

    import importlib

    from app.db.init_db import init_db
    from app.db.engine import SessionLocal
    from app.db.models.users import User  # noqa: F401
    from app.db.models.video_tasks import VideoTask

    init_db()
    migrate = importlib.import_module("migrate_task_status")
    migrate.add_columns()

    # 造一条 DB 记录 + 对应状态文件
    db = SessionLocal()
    try:
        db.add(VideoTask(video_id="v1", platform="bilibili", task_id="bf-1"))
        db.commit()
    finally:
        db.close()
    (notes / "bf-1.status.json").write_text(
        json.dumps({"status": "FAILED", "message": "转写失败", "failed_at": "2026-01-02T03:04:05+00:00"}),
        encoding="utf-8",
    )

    filled = migrate.backfill()
    assert filled == 1

    db = SessionLocal()
    try:
        row = db.query(VideoTask).filter_by(task_id="bf-1").first()
        assert row.status == "FAILED"
        assert row.error == "转写失败"
        assert row.failed_at is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. update_task_status 语义
# ---------------------------------------------------------------------------
def test_update_task_status_sets_and_clears_failure_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'st.db'}")
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.engine import SessionLocal
    from app.db.init_db import init_db
    from app.db.models.users import User  # noqa: F401
    from app.db.models.video_tasks import VideoTask
    from app.db.video_task_dao import update_task_status

    init_db()
    db = SessionLocal()
    try:
        db.add(VideoTask(video_id="v", platform="bilibili", task_id="s-1"))
        db.commit()
    finally:
        db.close()

    # 失败 → failed_at 落值
    assert update_task_status("s-1", "FAILED", message="boom") is True
    db = SessionLocal()
    try:
        row = db.query(VideoTask).filter_by(task_id="s-1").first()
        assert row.status == "FAILED"
        assert row.error == "boom"
        assert row.failed_at is not None
    finally:
        db.close()

    # 重试成功 → 失败痕迹清空
    assert update_task_status("s-1", "SUCCESS") is True
    db = SessionLocal()
    try:
        row = db.query(VideoTask).filter_by(task_id="s-1").first()
        assert row.status == "SUCCESS"
        assert row.error is None
        assert row.failed_at is None
    finally:
        db.close()

    # 不存在的 task_id：不新建行，返回 False
    assert update_task_status("does-not-exist", "SUCCESS") is False


def test_count_tasks_by_status_since(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cnt.db'}")
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.engine import SessionLocal
    from app.db.init_db import init_db
    from app.db.models.users import User  # noqa: F401
    from app.db.models.video_tasks import VideoTask
    from app.db.video_task_dao import count_tasks_by_status_since, update_task_status

    init_db()
    db = SessionLocal()
    try:
        for tid in ("c-1", "c-2", "c-3"):
            db.add(VideoTask(video_id=tid, platform="p", task_id=tid))
        db.commit()
    finally:
        db.close()

    update_task_status("c-1", "SUCCESS")
    update_task_status("c-2", "FAILED", message="x")
    update_task_status("c-3", "TRANSCRIBING")

    stats = count_tasks_by_status_since()
    assert stats["total"] == 3
    assert stats["success"] == 1
    assert stats["failed"] == 1
    assert stats["running"] == 1

    # since 在未来 → 不应命中任何 failed_at
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert count_tasks_by_status_since(since=future).get("failed_since", 0) == 0


# ---------------------------------------------------------------------------
# 5. 软删除：删任务不能物理删行，否则累计指标倒退
# ---------------------------------------------------------------------------
def test_delete_is_soft_and_keeps_cumulative(tmp_path, monkeypatch):
    """DELETE 任务后：DB 行仍在（deleted_at 有值），累计不倒退。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'soft.db'}")
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.engine import SessionLocal
    from app.db.init_db import init_db
    from app.db.models.users import User  # noqa: F401
    from app.db.models.video_tasks import VideoTask
    from app.db.video_task_dao import (
        count_tasks_by_status_since,
        delete_task_by_task_id,
        update_task_status,
    )

    init_db()
    db = SessionLocal()
    try:
        for tid in ("s-ok", "s-fail"):
            db.add(VideoTask(video_id=tid, platform="p", task_id=tid))
        db.commit()
    finally:
        db.close()

    update_task_status("s-ok", "SUCCESS")
    update_task_status("s-fail", "FAILED", message="x")

    before = count_tasks_by_status_since()
    assert before["all_including_deleted"] == 2
    assert before["deleted"] == 0

    assert delete_task_by_task_id("s-ok") is True

    after = count_tasks_by_status_since()
    # 当前态统计排除了软删除行
    assert after["success"] == 0
    assert after["failed"] == 1
    # 但累计包含它 —— 这是「不随删笔记倒退」的关键
    assert after["all_including_deleted"] == 2
    assert after["deleted"] == 1

    # 行还在（软删除而非物理删除）
    db = SessionLocal()
    try:
        row = db.query(VideoTask).filter_by(task_id="s-ok").first()
        assert row is not None
        assert row.deleted_at is not None
    finally:
        db.close()


def test_hard_delete_removes_row(tmp_path, monkeypatch):
    """soft=False 时仍然是物理删除（供运维彻底清理用）。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'hard.db'}")
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from app.db.engine import SessionLocal
    from app.db.init_db import init_db
    from app.db.models.users import User  # noqa: F401
    from app.db.models.video_tasks import VideoTask
    from app.db.video_task_dao import delete_task_by_task_id, count_tasks_by_status_since

    init_db()
    db = SessionLocal()
    try:
        db.add(VideoTask(video_id="h1", platform="p", task_id="h1"))
        db.commit()
    finally:
        db.close()

    assert delete_task_by_task_id("h1", soft=False) is True
    db = SessionLocal()
    try:
        assert db.query(VideoTask).filter_by(task_id="h1").first() is None
    finally:
        db.close()
    assert count_tasks_by_status_since()["all_including_deleted"] == 0
