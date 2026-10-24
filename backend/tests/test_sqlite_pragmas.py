"""SQLite PRAGMA 配置的回归测试（R5）。

## 为什么需要 WAL

默认 `journal_mode=DELETE`：写事务持有 RESERVED→EXCLUSIVE 锁，期间
**其他连接的读也会被阻塞**，直到 `busy_timeout` 超时 → `database is locked`。

本项目的写模式最容易撞锁：6 个任务 worker 各自频繁 `_update_status()`，
同时用户端每 3 秒轮询 + 每 30 秒拉列表。写密集 + 高频读 = 经典争用。

WAL 让读走快照（不阻塞写）、写只锁 WAL 文件（不阻塞读）。

## foreign_keys 的连带影响

SQLite 默认**不启用**外键约束（历史兼容原因）。开启后，
`video_tasks.user_id` 引用不存在的用户会直接 `IntegrityError` ——
这是更严谨的行为，但也意味着测试必须真的建出关联记录。
生产库已验证零外键违规（PRAGMA foreign_key_check 返回空）。
"""

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
def sqlite_engine(tmp_path, monkeypatch):
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'pragma.db'}")
    try:
        _install_app_namespace()
        from app.db import engine as eng

        yield eng
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _pragma(engine, name: str):
    from sqlalchemy import text

    with engine.connect() as conn:
        return conn.execute(text(f"PRAGMA {name}")).fetchone()[0]


def test_journal_mode_is_wal(sqlite_engine):
    assert str(_pragma(sqlite_engine.engine, "journal_mode")).lower() == "wal"


def test_busy_timeout_is_set(sqlite_engine):
    """防止写锁竞争时立刻抛 locked；5s 给足重试窗口。"""
    assert int(_pragma(sqlite_engine.engine, "busy_timeout")) >= 5000


def test_synchronous_is_normal(sqlite_engine):
    """WAL 下 NORMAL 是安全的，且避免每次提交 fsync。0=OFF 1=NORMAL 2=FULL 3=EXTRA"""
    assert int(_pragma(sqlite_engine.engine, "synchronous")) == 1


def test_foreign_keys_enabled(sqlite_engine):
    """外键默认关闭，必须显式开；关掉会静默产生孤儿记录。"""
    assert int(_pragma(sqlite_engine.engine, "foreign_keys")) == 1


def test_foreign_key_actually_rejects_orphan(sqlite_engine):
    """开启后写入不存在的 user_id 必须被拒绝（证明约束真的生效）。"""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    eng = sqlite_engine
    from app.db.engine import Base
    from app.db.models.users import User  # noqa: F401
    from app.db.models.video_tasks import VideoTask  # noqa: F401

    Base.metadata.create_all(bind=eng.engine)

    with pytest.raises(IntegrityError):
        with eng.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO video_tasks (video_id, platform, task_id, user_id) "
                    "VALUES ('v', 'bilibili', 't', 99999)"
                )
            )


def test_wal_files_created(sqlite_engine, tmp_path):
    """WAL 模式会在库旁生成 -wal / -shm（备份时要一起处理）。"""
    from sqlalchemy import text

    with sqlite_engine.engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))

    files = {p.name for p in tmp_path.iterdir()}
    assert any(f.endswith("-wal") for f in files), f"未生成 -wal 文件: {files}"
