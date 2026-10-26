"""F4 / F5 / O-1 / O-3 的回归测试：任务状态机的四条不变量。

## 锁住的行为

| 条目 | 不变量 |
|---|---|
| F4 | `generate()` 最多标到 SAVING；SUCCESS 只在**结果落盘之后**由 `save_and_mark_success` 写 |
| F4 | 落盘失败 → 任务标 FAILED（不是 SUCCESS），且不再出现"成功但无文件" |
| F4 | `get_task_status` 遇「状态文件 SUCCESS 但结果文件缺失」→ **SAVING**，禁止再铸 PENDING |
| F5 | 软删任务再轮询 → 404（`get_task_by_id` 默认排除 `deleted_at`） |
| O-1 | `list_tasks` 的 **DB 分支**必须有 `has_result`（文件分支一直有，DB 分支漏了） |
| O-3 | `_update_status` 必须真的把状态写进 DB（`_parse_iso` 曾是 NameError 被静默吞掉） |

## 为什么需要这些测试

F4 之前的顺序是「标 SUCCESS → 返回结果 → router 落盘」，中间窗口轮询会看到
"成功但无结果"；落盘一旦抛异常，状态会**永久**停在假 SUCCESS，前端一直转圈。

O-3 更隐蔽：`_update_status` 里的裸 `_parse_iso` 解析成 global（模块级没有），
抛 `NameError` 被 `logger.debug` 吞掉 → `update_task_status` **一次都没执行过**，
DB 里 status 恒 NULL。此前没有任何测试断言"`_update_status` 之后 DB 里有 status"。
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# 加载辅助（与 test_task_list_api / test_task_status_persistence 同风格：
# snapshot sys.modules 里的 app.* ，避免模块级 stub 互相干扰）
# ---------------------------------------------------------------------------
def _fresh_app_modules(tmp_path, monkeypatch, db_name="open_noter.db"):
    """把 `app.*` 从 sys.modules 清掉，并让引擎指向临时 sqlite。"""
    notes = tmp_path / "note_results"
    notes.mkdir(exist_ok=True)
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(notes))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / db_name}")
    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    return notes


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """干净的 `app.*` 模块树 + 临时 DB/note 目录，用完整体还原。"""
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    try:
        notes = _fresh_app_modules(tmp_path, monkeypatch)
        yield tmp_path, notes
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _init_db_with_task(task_id: str, *, user_id: int | None = 1, deleted: bool = False):
    """建表并插一条 video_tasks 行，返回 VideoTask 类。

    `user_id` 有 FK 约束（`ForeignKey("users.id")`，PRAGMA foreign_keys=ON），
    所以先确保对应用户行存在（id=1）。
    """
    from app.db.engine import SessionLocal
    from app.db.init_db import init_db
    from app.db.models.users import User
    from app.db.models.video_tasks import VideoTask

    init_db()
    db = SessionLocal()
    try:
        if user_id is not None and db.query(User).filter_by(id=user_id).first() is None:
            db.add(User(id=user_id, email=f"u{user_id}@example.com"))
            db.flush()
        row = VideoTask(video_id="v1", platform="bilibili", task_id=task_id, user_id=user_id)
        if deleted:
            from datetime import datetime, timezone

            row.deleted_at = datetime.now(timezone.utc)
        db.add(row)
        db.commit()
    finally:
        db.close()
    return VideoTask


# ---------------------------------------------------------------------------
# F5 · get_task_by_id 默认排除软删行
# ---------------------------------------------------------------------------
def test_get_task_by_id_excludes_soft_deleted(app_env):
    """默认查不到软删行；显式 include_deleted=True 才查得到。"""
    _init_db_with_task("alive")
    _init_db_with_task("gone", deleted=True)

    from app.db.video_task_dao import get_task_by_id

    assert get_task_by_id("alive") is not None
    assert get_task_by_id("gone") is None, "软删行默认不可见（否则已删任务会复活成 PENDING）"
    assert get_task_by_id("gone", include_deleted=True) is not None


def test_get_task_by_id_unknown_returns_none(app_env):
    assert __import__("app.db.video_task_dao", fromlist=["x"]).get_task_by_id("nope") is None


# ---------------------------------------------------------------------------
# F5 · 软删任务再轮询 → 404（不是 PENDING）
# ---------------------------------------------------------------------------
def test_soft_deleted_task_status_returns_404(app_env):
    """软删行保留 user_id，ACL 能过 —— 必须在"查 DB 有没有这行"时就被挡掉。"""
    _init_db_with_task("soft-gone", user_id=1, deleted=True)

    from fastapi import HTTPException

    from app.routers.note import get_task_status

    user = types.SimpleNamespace(id=1, is_superuser=False, is_active=True)

    with pytest.raises(HTTPException) as excinfo:
        get_task_status("soft-gone", user)
    assert excinfo.value.status_code == 404


def test_soft_deleted_task_not_in_list(app_env):
    """软删任务不出现在列表的 DB 分支。"""
    _init_db_with_task("alive", user_id=1)
    _init_db_with_task("soft-gone", user_id=1, deleted=True)

    from app.db.video_task_dao import list_tasks_for_user

    items, total = list_tasks_for_user(1)
    assert total == 1
    assert [i["task_id"] for i in items] == ["alive"]


# ---------------------------------------------------------------------------
# F4 · generate() 不标 SUCCESS；save_and_mark_success 先落盘再标
# ---------------------------------------------------------------------------
def _generator_with_fake_pipeline(monkeypatch, notes: Path):
    """造一个 generate() 能跑通的 NoteGenerator（下载/转写/GPT 全假）。"""
    from app.downloaders.limits import DownloadLimits
    from app.models.audio_model import AudioDownloadResult
    from app.models.transcriber_model import TranscriptResult, TranscriptSegment
    from app.services import note as note_mod

    media = notes / "audio.mp3"
    media.write_bytes(b"fake")
    audio = AudioDownloadResult(
        file_path=str(media),
        title="t",
        duration=1.0,
        cover_url=None,
        platform="bilibili",
        video_id="BV1test",
        raw_info={"tags": []},
        video_path=None,
    )
    transcript = TranscriptResult(
        language="zh",
        full_text="字幕",
        segments=[TranscriptSegment(start=0.0, end=1.0, text="字幕")],
    )

    class FakeDownloader:
        def download_subtitles(self, *a, **k):
            return None

        def download(self, *a, **k):
            return audio

        def download_video(self, *a, **k):
            return str(media)

    class FakeGPT:
        def summarize(self, source):
            return "# 笔记"

    class FakeTranscriber:
        def transcript(self, file_path=None, **kwargs):
            return transcript

    gen = note_mod.NoteGenerator.__new__(note_mod.NoteGenerator)
    gen.video_path = None
    gen.video_img_urls = []
    gen.limits = DownloadLimits(retry_count=0)
    gen.transcriber = FakeTranscriber()
    gen._get_downloader = lambda platform: FakeDownloader()
    gen._get_gpt = lambda model_name, provider_id: FakeGPT()
    gen._sleep_before_retry = lambda attempt: None
    monkeypatch.setattr(note_mod, "NOTE_OUTPUT_DIR", notes, raising=False)
    return note_mod, gen


def _read_status(notes: Path, task_id: str) -> dict:
    return json.loads((notes / f"{task_id}.status.json").read_text(encoding="utf-8"))


def test_generate_ends_at_saving_not_success(app_env, monkeypatch):
    """F4 核心：generate() 返回时状态最多是 SAVING（结果还没落盘）。"""
    tmp, notes = app_env
    _init_db_with_task("seed")  # 建表（generate 末尾会写 DB）
    note_mod, gen = _generator_with_fake_pipeline(monkeypatch, notes)

    result = gen.generate(
        video_url="https://www.bilibili.com/video/BV1test",
        platform="bilibili",
        task_id="f4-1",
        model_name="m",
        provider_id="p",
    )

    assert result is not None
    assert _read_status(notes, "f4-1")["status"] == "SAVING"


def test_save_and_mark_success_marks_success_after_write(app_env):
    """落盘成功 → SUCCESS；且落盘函数确实在标状态之前被调用。"""
    tmp, notes = app_env
    from app.services import note as note_mod

    gen = note_mod.NoteGenerator.__new__(note_mod.NoteGenerator)
    events = []

    def save_fn(task_id, note):
        events.append(("save", _read_status(notes, task_id)["status"]))
        (notes / f"{task_id}.json").write_text('{"markdown": "# x"}', encoding="utf-8")

    # 先造一个 SAVING 状态（模拟 generate() 的产出）
    gen._update_status("f4-2", note_mod.TaskStatus.SAVING)
    ok = gen.save_and_mark_success("f4-2", object(), save_fn)

    assert ok is True
    # 落盘那一刻状态还是 SAVING，之后才变 SUCCESS —— 顺序不可颠倒
    assert events == [("save", "SAVING")]
    assert _read_status(notes, "f4-2")["status"] == "SUCCESS"
    assert (notes / "f4-2.json").is_file()


def test_save_failure_marks_failed_not_success(app_env):
    """落盘失败必须标 FAILED，绝不能留下"SUCCESS 但无文件"的假成功。"""
    tmp, notes = app_env
    from app.services import note as note_mod

    gen = note_mod.NoteGenerator.__new__(note_mod.NoteGenerator)
    gen._update_status("f4-3", note_mod.TaskStatus.SAVING)

    def boom(task_id, note):
        raise OSError("disk full")

    ok = gen.save_and_mark_success("f4-3", object(), boom)

    assert ok is False
    payload = _read_status(notes, "f4-3")
    assert payload["status"] == "FAILED"
    assert not (notes / "f4-3.json").exists()


# ---------------------------------------------------------------------------
# F4 后半 · get_task_status：SUCCESS 缺文件 → SAVING（不是 PENDING）
# ---------------------------------------------------------------------------
def test_missing_result_file_returns_saving_not_pending(app_env):
    """状态文件说 SUCCESS、结果文件不存在 → 返回 SAVING，禁止 PENDING。"""
    tmp, notes = app_env
    (notes / "ghost.status.json").write_text(
        json.dumps({"status": "SUCCESS", "user_id": 1}), encoding="utf-8"
    )

    from app.routers.note import get_task_status

    user = types.SimpleNamespace(id=1, is_superuser=False, is_active=True)
    resp = get_task_status("ghost", user)
    payload = json.loads(bytes(resp.body))["data"]

    assert payload["status"] == "SAVING", payload
    assert payload["status"] != "PENDING"


def test_success_with_result_file_returns_result(app_env):
    """正常路径不受影响：SUCCESS + 结果文件存在 → 返回 result。"""
    tmp, notes = app_env
    (notes / "ok.status.json").write_text(
        json.dumps({"status": "SUCCESS", "user_id": 1}), encoding="utf-8"
    )
    (notes / "ok.json").write_text(json.dumps({"markdown": "# 正文"}), encoding="utf-8")

    from app.routers.note import get_task_status

    user = types.SimpleNamespace(id=1, is_superuser=False, is_active=True)
    resp = get_task_status("ok", user)
    payload = json.loads(bytes(resp.body))["data"]

    assert payload["status"] == "SUCCESS"
    assert payload["result"]["markdown"] == "# 正文"


# ---------------------------------------------------------------------------
# O-1 · list_tasks 的 DB 分支必须有 has_result
# ---------------------------------------------------------------------------
def test_db_branch_returns_has_result(app_env):
    """DB 分支的 item 必须带 has_result，且与文件真实存在一致。"""
    tmp, notes = app_env
    _init_db_with_task("with-body")
    _init_db_with_task("no-body")
    (notes / "with-body.json").write_text('{"markdown": "# x"}', encoding="utf-8")

    from app.db.video_task_dao import list_tasks_for_user

    items, total = list_tasks_for_user(1, limit=50, offset=0, note_dir=notes)
    assert total == 2
    by_id = {i["task_id"]: i for i in items}
    assert by_id["with-body"]["has_result"] is True
    assert by_id["no-body"]["has_result"] is False
    # 契约不能只有部分 item 有这个 key
    assert all("has_result" in i for i in items)


def test_db_branch_has_result_uses_note_dir_not_env(app_env, monkeypatch):
    """显式 note_dir 必须优先于 NOTE_OUTPUT_DIR 环境变量。

    路由层的测试 monkeypatch 的是模块属性 `mod.NOTE_OUTPUT_DIR`，**不会**
    同步到 env；只读 env 会让 DB 分支在测试里判错目录。
    """
    tmp, notes = app_env
    other = tmp / "elsewhere"
    other.mkdir()
    (other / "t1.json").write_text('{"markdown": "# x"}', encoding="utf-8")

    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(other))  # env 指向另一处
    _init_db_with_task("t1")

    from app.db.video_task_dao import list_tasks_for_user

    items, _ = list_tasks_for_user(1, note_dir=notes)  # 显式传入空目录
    assert items[0]["has_result"] is False, "显式 note_dir 必须被尊重"


def test_db_branch_unsafe_task_id_does_not_raise(app_env):
    """DB 里混进越界 task_id 时不能抛异常，按"没有结果"处理。"""
    tmp, notes = app_env
    _init_db_with_task("../etc/passwd")

    from app.db.video_task_dao import list_tasks_for_user

    items, _ = list_tasks_for_user(1, note_dir=notes)
    assert items == [] or items[0]["has_result"] is False


# ---------------------------------------------------------------------------
# O-3 · _update_status 必须真的写进 DB
# ---------------------------------------------------------------------------
def test_update_status_reaches_db(app_env):
    """回归：`_parse_iso` 曾是 NameError 被 except 静默吞掉，DB 同步从未执行。"""
    _init_db_with_task("sync-1")

    from app.db.engine import SessionLocal
    from app.db.models.video_tasks import VideoTask
    from app.services import note as note_mod

    gen = note_mod.NoteGenerator.__new__(note_mod.NoteGenerator)
    gen._update_status("sync-1", note_mod.TaskStatus.TRANSCRIBING)

    db = SessionLocal()
    try:
        row = db.query(VideoTask).filter_by(task_id="sync-1").first()
        assert row.status == "TRANSCRIBING", "状态没同步进 DB（_update_status 的 DB 分支失效）"
        assert row.updated_at is not None
    finally:
        db.close()


def test_update_status_reaches_db_for_failed_with_message(app_env):
    """FAILED 分支的 failed_at/message 也必须真的落到 DB。"""
    _init_db_with_task("sync-2")

    from app.db.engine import SessionLocal
    from app.db.models.video_tasks import VideoTask
    from app.services import note as note_mod

    gen = note_mod.NoteGenerator.__new__(note_mod.NoteGenerator)
    gen._update_status("sync-2", note_mod.TaskStatus.FAILED, message="磁盘满了")

    db = SessionLocal()
    try:
        row = db.query(VideoTask).filter_by(task_id="sync-2").first()
        assert row.status == "FAILED"
        assert row.error == "磁盘满了"
        assert row.failed_at is not None
    finally:
        db.close()


def test_update_status_db_failure_is_logged_as_warning(app_env, caplog):
    """DB 同步失败必须留 warning（+ traceback），不能再静默成 debug。"""
    import logging

    from app.services import note as note_mod

    gen = note_mod.NoteGenerator.__new__(note_mod.NoteGenerator)

    def boom(**kwargs):
        raise RuntimeError("db down")

    original = note_mod.update_task_status
    note_mod.update_task_status = boom
    try:
        with caplog.at_level(logging.WARNING, logger=note_mod.logger.name):
            gen._update_status("sync-3", note_mod.TaskStatus.PARSING)
    finally:
        note_mod.update_task_status = original

    assert any(
        "同步任务状态到 DB 失败" in rec.message and rec.levelno >= logging.WARNING
        for rec in caplog.records
    ), [r.message for r in caplog.records]
