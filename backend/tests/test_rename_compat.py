"""改名 BiliNote → OpenNoter 的兼容性测试。

覆盖两类「静默破坏」风险：
  1. 环境变量前缀从 BILINOTE_ 改成 OPENNOTER_：旧 .env 必须仍能读到
  2. SQLite 文件名从 bili_note.db 改成 open_noter.db：旧库必须被自动迁移
"""

import os
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _install_app_namespace():
    """跳过 app/__init__.py 的聚合导入（会拉 chromadb 等重依赖）。"""
    if "app" in sys.modules:
        return
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(BACKEND_DIR / "app")]
    pkg.__package__ = "app"
    sys.modules["app"] = pkg


# ---------------------------------------------------------------------------
# 环境变量兼容
# ---------------------------------------------------------------------------
def test_new_prefix_wins(monkeypatch):
    _install_app_namespace()
    from app.utils.env_compat import getenv

    monkeypatch.setenv("OPENNOTER_FOO", "new")
    monkeypatch.setenv("BILINOTE_FOO", "old")
    assert getenv("FOO") == "new"


def test_legacy_prefix_fallback(monkeypatch):
    _install_app_namespace()
    from app.utils.env_compat import getenv

    monkeypatch.delenv("OPENNOTER_BAR", raising=False)
    monkeypatch.setenv("BILINOTE_BAR", "legacy")
    assert getenv("BAR") == "legacy"


def test_default_when_absent(monkeypatch):
    _install_app_namespace()
    from app.utils.env_compat import getenv

    monkeypatch.delenv("OPENNOTER_BAZ", raising=False)
    monkeypatch.delenv("BILINOTE_BAZ", raising=False)
    assert getenv("BAZ", "fallback") == "fallback"
    assert getenv("BAZ") is None


def test_getint_and_getbool_compat(monkeypatch):
    _install_app_namespace()
    from app.utils.env_compat import getbool, getint

    # 旧前缀也要能解析成正确类型
    monkeypatch.delenv("OPENNOTER_NUM", raising=False)
    monkeypatch.setenv("BILINOTE_NUM", "42")
    assert getint("NUM", 0) == 42

    monkeypatch.delenv("OPENNOTER_FLAG", raising=False)
    monkeypatch.setenv("BILINOTE_FLAG", "1")
    assert getbool("FLAG", False) is True

    # 非法值回落默认
    monkeypatch.setenv("OPENNOTER_BAD", "abc")
    assert getint("BAD", 7) == 7


def test_empty_new_value_is_honoured(monkeypatch):
    """新变量存在但为空串时，不应回退到旧值（显式置空是合法意图）。"""
    _install_app_namespace()
    from app.utils.env_compat import getenv

    monkeypatch.setenv("OPENNOTER_EMPTY", "")
    monkeypatch.setenv("BILINOTE_EMPTY", "legacy")
    assert getenv("EMPTY") == ""


# ---------------------------------------------------------------------------
# 数据库文件自动迁移
# ---------------------------------------------------------------------------
def test_legacy_db_is_renamed(tmp_path, monkeypatch):
    """旧库存在、新库不存在时，启动应自动改名而不是新建空库。"""
    old = tmp_path / "bili_note.db"
    old.write_bytes(b"SQLite format 3\x00fake-legacy-db")
    new = tmp_path / "open_noter.db"

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)
    _install_app_namespace()

    from app.db.engine import _migrate_legacy_sqlite

    result = _migrate_legacy_sqlite("open_noter.db")

    assert result == "open_noter.db"
    assert new.exists(), "旧库应被重命名为新名字"
    assert not old.exists()
    assert new.read_bytes().startswith(b"SQLite format 3")


def test_existing_new_db_is_untouched(tmp_path):
    """新库已存在时不覆盖、不迁移（幂等）。"""
    old = tmp_path / "bili_note.db"
    old.write_bytes(b"legacy")
    new = tmp_path / "open_noter.db"
    new.write_bytes(b"current")

    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)
    _install_app_namespace()

    from app.db.engine import _migrate_legacy_sqlite

    assert _migrate_legacy_sqlite(str(new)) == str(new)
    assert new.read_bytes() == b"current"
    assert old.read_bytes() == b"legacy", "新库已存在时不应动旧库"


def test_no_legacy_db_is_noop(tmp_path):
    """没有旧库时保持默认路径。"""
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)
    _install_app_namespace()

    from app.db.engine import _migrate_legacy_sqlite

    target = tmp_path / "open_noter.db"
    assert _migrate_legacy_sqlite(str(target)) == str(target)
    assert not target.exists()
