"""P2：进程退出 / 重启时标记进行中任务为 FAILED。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def note_dir(tmp_path, monkeypatch):
    d = tmp_path / "note_results"
    d.mkdir()
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(d))
    # note 模块在 import 时读了 NOTE_OUTPUT_DIR；测试里直接传 note_dir 给 API
    return d


def _write_status(note_dir: Path, task_id: str, status: str, **extra):
    path = note_dir / f"{task_id}.status.json"
    data = {"status": status, **extra}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_fail_interrupted_marks_in_progress_only(note_dir, monkeypatch):
    from app.services import task_interrupt as mod
    from app.services.note import NoteGenerator

    # 避免触达真实 DB：桩掉 update_task_status
    monkeypatch.setattr(
        "app.services.note.update_task_status",
        lambda **kwargs: None,
    )

    _write_status(note_dir, "a", "PENDING")
    _write_status(note_dir, "b", "TRANSCRIBING")
    _write_status(note_dir, "c", "SUCCESS")
    _write_status(note_dir, "d", "FAILED", message="already")

    n = mod.fail_interrupted_tasks(reason="test interrupt", note_dir=note_dir)
    assert n == 2

    a = json.loads((note_dir / "a.status.json").read_text(encoding="utf-8"))
    b = json.loads((note_dir / "b.status.json").read_text(encoding="utf-8"))
    c = json.loads((note_dir / "c.status.json").read_text(encoding="utf-8"))
    d = json.loads((note_dir / "d.status.json").read_text(encoding="utf-8"))

    assert a["status"] == "FAILED"
    assert "test interrupt" in a["message"]
    assert b["status"] == "FAILED"
    assert c["status"] == "SUCCESS"
    assert d["status"] == "FAILED"
    assert d["message"] == "already"


def test_fail_interrupted_empty_dir(note_dir):
    from app.services import task_interrupt as mod

    assert mod.fail_interrupted_tasks(reason="x", note_dir=note_dir) == 0


def test_fail_interrupted_missing_dir(tmp_path):
    from app.services import task_interrupt as mod

    missing = tmp_path / "nope"
    assert mod.fail_interrupted_tasks(reason="x", note_dir=missing) == 0
