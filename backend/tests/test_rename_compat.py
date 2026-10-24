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
def _fresh_engine(tmp_path):
    """在指定目录下重新 import engine（触发路径解析 + 迁移）。"""
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)
        _install_app_namespace()
        from app.db import engine as eng

        return eng
    finally:
        os.chdir(cwd)


def _make_db(path: Path, email: str = "a@b.c") -> None:
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    try:
        c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        c.execute("INSERT INTO users (email) VALUES (?)", (email,))
        c.commit()
    finally:
        c.close()


def test_legacy_db_moves_into_data_dir(tmp_path, monkeypatch):
    """旧位置（cwd 根）的库应自动搬到 data/，且数据不丢。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    old = tmp_path / "open_noter.db"
    _make_db(old, "old@test.com")

    eng = _fresh_engine(tmp_path)

    new = tmp_path / "data" / "open_noter.db"
    assert new.exists(), "应迁移到 data/ 下"
    assert not old.exists(), "源文件应被移走"
    assert str(new) in eng.DATABASE_URL

    # 数据完整
    import sqlite3

    c = sqlite3.connect(str(new))
    try:
        rows = list(c.execute("SELECT email FROM users"))
    finally:
        c.close()
    assert rows == [("old@test.com",)]


def test_legacy_brand_name_is_migrated(tmp_path, monkeypatch):
    """更早的品牌名 bili_note.db 也要能迁到 data/open_noter.db。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    old = tmp_path / "bili_note.db"
    _make_db(old, "ancient@test.com")

    eng = _fresh_engine(tmp_path)

    new = tmp_path / "data" / "open_noter.db"
    assert new.exists()
    assert str(new) in eng.DATABASE_URL


def test_existing_new_db_wins(tmp_path, monkeypatch):
    """新位置已有可用库时，不动旧文件（幂等 / 避免覆盖更新的数据）。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    new = tmp_path / "data" / "open_noter.db"
    _make_db(new, "current@test.com")
    old = tmp_path / "open_noter.db"
    _make_db(old, "stale@test.com")

    eng = _fresh_engine(tmp_path)

    assert str(new) in eng.DATABASE_URL
    assert old.exists(), "新库已存在时不该动旧文件"
    c = __import__("sqlite3").connect(str(new))
    try:
        assert list(c.execute("SELECT email FROM users")) == [("current@test.com",)]
    finally:
        c.close()


def test_corrupt_file_is_not_migrated(tmp_path, monkeypatch):
    """坏文件不能被当成数据库搬走（否则会把好库覆盖成坏库）。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    bad = tmp_path / "open_noter.db"
    bad.write_bytes(b"this is not sqlite")

    _fresh_engine(tmp_path)

    new = tmp_path / "data" / "open_noter.db"
    # 迁移被拒：要么没生成新库，要么新库不是这个坏内容
    assert not new.exists() or new.read_bytes() != b"this is not sqlite"


def test_explicit_database_url_is_respected(tmp_path, monkeypatch):
    """用户显式配置 DATABASE_URL 时完全尊重，不做任何迁移。"""
    custom = tmp_path / "custom" / "mydb.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{custom}")
    legacy = tmp_path / "open_noter.db"
    _make_db(legacy, "should-not-move@test.com")

    eng = _fresh_engine(tmp_path)

    assert eng.DATABASE_URL == f"sqlite:///{custom}"
    assert legacy.exists(), "显式配置时不该搬动旧库"


def test_fresh_install_points_at_data_dir(tmp_path, monkeypatch):
    """全新安装：指向 data/ 下，首次连接时由 SQLite 建空库。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    eng = _fresh_engine(tmp_path)

    assert str(tmp_path / "data" / "open_noter.db") in eng.DATABASE_URL


def test_explicit_url_adopts_legacy_when_target_missing(tmp_path, monkeypatch):
    """容器场景：compose 显式写 DATABASE_URL 指向 data/，也不能丢旧库。

    这是最容易踩的坑——显式配置会让「默认路径迁移」整段跳过，
    升级后看到一个空库，表现为「数据全丢」。
    """
    target = tmp_path / "data" / "open_noter.db"
    legacy = tmp_path / "open_noter.db"
    _make_db(legacy, "prod@test.com")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")

    eng = _fresh_engine(tmp_path)

    assert target.exists(), "应接管旧库到 data/"
    assert not legacy.exists(), "源文件应被移走"
    c = __import__("sqlite3").connect(str(target))
    try:
        assert list(c.execute("SELECT email FROM users")) == [("prod@test.com",)]
    finally:
        c.close()


def test_explicit_url_never_overwrites_existing_db(tmp_path, monkeypatch):
    """目标已是有数据的真库时，绝不覆盖（防误删新数据）。"""
    target = tmp_path / "data" / "open_noter.db"
    _make_db(target, "new@test.com")
    legacy = tmp_path / "open_noter.db"
    _make_db(legacy, "old@test.com")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")

    _fresh_engine(tmp_path)

    c = __import__("sqlite3").connect(str(target))
    try:
        assert list(c.execute("SELECT email FROM users")) == [("new@test.com",)]
    finally:
        c.close()
    assert legacy.exists(), "目标已有数据时不该动旧库"


def test_non_sqlite_url_is_untouched(tmp_path, monkeypatch):
    """非 sqlite URL 不做任何本地文件操作（直接测 URL 处理函数，免装 PG 驱动）。"""
    sys.modules.pop("app.db.engine", None)
    _install_app_namespace()
    from app.db import engine as eng_mod

    legacy = tmp_path / "open_noter.db"
    _make_db(legacy, "x@test.com")
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        url = "postgresql://user:pass@db:5432/opennoter"
        out = eng_mod._maybe_adopt_legacy_for_explicit_url(url)
        assert out == url, "非 sqlite 应原样返回"
        assert legacy.exists(), "非 sqlite 时不该动本地文件"
    finally:
        os.chdir(cwd)


# ---------------------------------------------------------------------------
# 复审报告 N1/N2/N3：迁移失败面与边界
# ---------------------------------------------------------------------------
def test_dual_legacy_db_is_not_auto_migrated(tmp_path, monkeypatch):
    """N2：两个历史库并存时拒绝自动迁移，且沿用的是**较新**的那个。

    旧实现按「旧→新」顺序扫描，会迁走 bili_note.db 而把较新的
    open_noter.db 留在原地 —— 用户看到数据回退到更早版本。
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    old_brand = tmp_path / "bili_note.db"
    newer = tmp_path / "open_noter.db"
    _make_db(old_brand, "OLD@test.com")
    _make_db(newer, "NEWER@test.com")

    eng = _fresh_engine(tmp_path)

    # 不迁移：两个文件都留在原处
    assert old_brand.exists() and newer.exists()
    # 且使用的是较新的那个
    assert str(newer) in eng.DATABASE_URL
    c = __import__("sqlite3").connect(str(newer))
    try:
        assert list(c.execute("SELECT email FROM users")) == [("NEWER@test.com",)]
    finally:
        c.close()


def test_adoption_requires_data_dir(tmp_path, monkeypatch):
    """N3：非 data/ 目录的同名目标不应被接管（避免把 cwd 库搬到无关路径）。"""
    legacy = tmp_path / "open_noter.db"
    _make_db(legacy, "prod@test.com")
    other_dir = tmp_path / "somewhere_else"
    target = other_dir / "open_noter.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")

    _fresh_engine(tmp_path)

    assert legacy.exists(), "旧库不该被搬走"
    assert not target.exists(), "无关路径不该被写入"


def test_adoption_failure_falls_back_to_legacy(tmp_path, monkeypatch):
    """N1：接管失败必须回退旧库，绝不能指向一个会被建成空库的目标。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    legacy = tmp_path / "open_noter.db"
    _make_db(legacy, "precious@test.com")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    target = data_dir / "open_noter.db"
    # 让复制必失败：目标位置被一个**目录**占住
    target.mkdir()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")

    eng = _fresh_engine(tmp_path)

    # 不能指向那个"目录"（会被当空库建）
    assert eng.DATABASE_URL != f"sqlite:///{target}"
    # 应回退到旧库，且数据仍可读
    assert str(legacy) in eng.DATABASE_URL
    c = __import__("sqlite3").connect(str(legacy))
    try:
        assert list(c.execute("SELECT email FROM users")) == [("precious@test.com",)]
    finally:
        c.close()


def test_legacy_priority_is_newest_first(tmp_path, monkeypatch):
    """N2 配套：_LEGACY_NAMES 必须新→旧排序。"""
    sys.modules.pop("app.db.engine", None)
    _install_app_namespace()
    from app.db import engine as eng_mod

    assert eng_mod._LEGACY_NAMES[0] == "open_noter.db", "最新的库名必须排第一"
    assert eng_mod._LEGACY_NAMES[-1] == "bili_note.db"
