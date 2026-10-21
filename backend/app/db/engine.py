import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

_LEGACY_DB_NAME = "bili_note.db"
_DEFAULT_DB_NAME = "open_noter.db"


def _migrate_legacy_sqlite(default_path: str) -> str:
    """把旧库文件名迁移成新名字（BiliNote → OpenNoter 改名）。

    只处理「默认路径」这一种情况，且只在**新文件不存在、旧文件存在**时重命名，
    所以对已迁移过的库、自定义 DATABASE_URL 都不产生影响。
    失败时退回旧路径继续用，绝不静默造一个空库。
    """
    new_file = Path(default_path)
    legacy_file = new_file.with_name(_LEGACY_DB_NAME)
    if new_file.exists() or not legacy_file.exists():
        return default_path
    try:
        legacy_file.rename(new_file)
        try:
            from app.utils.logger import get_logger

            get_logger(__name__).info(
                f"检测到旧数据库 {_LEGACY_DB_NAME}，已重命名为 {_DEFAULT_DB_NAME}"
            )
        except Exception:
            pass
        return default_path
    except OSError:
        # 重命名失败（权限/占用）：继续用旧库，别让用户丢数据
        return str(legacy_file)


_default_db_path = os.getenv("DATABASE_URL", "")
if not _default_db_path:
    _default_db_path = "sqlite:///" + _migrate_legacy_sqlite(_DEFAULT_DB_NAME)

# 默认 SQLite，如果想换 PostgreSQL 或 MySQL，可以直接改 .env
DATABASE_URL = _default_db_path

# SQLite 需要特定连接参数，其他数据库不需要
engine_args = {}
if DATABASE_URL.startswith("sqlite"):
    engine_args["connect_args"] = {"check_same_thread": False}

_pool_args = {}
if not DATABASE_URL.startswith("sqlite"):
    _pool_args = {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "pool_pre_ping": True,
    }

engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("SQLALCHEMY_ECHO", "false").lower() == "true",
    **engine_args,
    **_pool_args,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_engine():
    return engine


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()