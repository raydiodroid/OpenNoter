import sqlite3

from app.db.engine import DATABASE_URL


def _resolve_path() -> str:
    """从 DATABASE_URL 里取出 sqlite 文件路径，保持与 ORM 用同一个库。"""
    prefix = "sqlite:///"
    if DATABASE_URL.startswith(prefix):
        return DATABASE_URL[len(prefix):]
    return "open_noter.db"


def get_connection():
    return sqlite3.connect(_resolve_path())
