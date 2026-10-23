"""数据库引擎与连接会话。

## 默认路径与历史迁移

默认 SQLite 文件位置随版本演进了两次，这里自动处理，**用户不需要手动 mv**：

| 版本 | 默认路径 | 说明 |
|---|---|---|
| ≤ 2026-10 | `bili_note.db`（相对 cwd） | 旧品牌名 |
| 2026-10 ~ | `open_noter.db`（相对 cwd） | 更名后，仍在 backend/ 根 |
| 当前 | `/app/data/open_noter.db`（data 卷） | 与代码分离，避免整目录挂载 |

迁移策略（只在「新位置不存在」时触发，幂等、失败不丢数据）：

1. **同名换位置**：`open_noter.db` → `data/open_noter.db`
2. **旧品牌名**：`bili_note.db` → `open_noter.db`（可能还在旧位置）

两步都做「复制 → 校验 → 再删源」，而不是直接 `rename`：
容器里 `data/` 可能是不同文件系统的挂载点，`rename` 会抛 `EXDEV`。
复制后先 `PRAGMA integrity_check` 确认可读，再删源文件；任何一步失败都回退到旧路径继续用。
"""

import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# 历史遗留名（按优先级从旧到新）
_LEGACY_NAMES = ("bili_note.db", "open_noter.db")
_DEFAULT_DB_NAME = "open_noter.db"
# 新默认目录：与代码分离，容器里挂到 data 卷
_DEFAULT_DB_DIR = "data"


def _log():
    """延迟拿 logger（避免 import 期循环依赖）。"""
    try:
        from app.utils.logger import get_logger

        return get_logger(__name__)
    except Exception:
        import logging

        return logging.getLogger(__name__)


def _sqlite_ok(path: Path) -> bool:
    """确认是能打开的 SQLite 文件（防把半截文件当库）。"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row and str(row[0]).lower() == "ok")
        finally:
            conn.close()
    except Exception:
        return False


def _move_db(src: Path, dst: Path) -> bool:
    """把库从 src 搬到 dst。成功返回 True。

    用「复制 → 校验 → 删源」而非 rename：
    `data/` 在容器里常是独立挂载点，跨文件系统 rename 会 EXDEV。
    """
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if not _sqlite_ok(dst):
            # 复制体不可用：删掉，保留源文件
            try:
                dst.unlink()
            except OSError:
                pass
            return False
        try:
            src.unlink()
        except OSError:
            # 源删不掉不影响使用；下次启动会看到「新库已存在」而跳过
            pass
        return True
    except Exception as exc:
        _log().warning(f"数据库迁移失败（{src} → {dst}）：{exc}")
        try:
            if dst.exists() and not _sqlite_ok(dst):
                dst.unlink()
        except OSError:
            pass
        return False


def _resolve_default_db_url() -> str:
    """解析默认 DATABASE_URL，必要时把旧位置的库搬到 data/ 并返回新 URL。

    只在环境变量**未显式设置**时调用——用户自定义 `DATABASE_URL` 一律尊重。
    """
    cwd = Path.cwd()
    target = cwd / _DEFAULT_DB_DIR / _DEFAULT_DB_NAME

    # 新位置已有可用库：直接用
    if target.exists() and _sqlite_ok(target):
        return f"sqlite:///{target}"

    # 依次找历史位置（cwd 根、cwd/bili_note.db）
    for name in _LEGACY_NAMES:
        candidate = cwd / name
        if candidate.exists() and candidate.is_file():
            if _move_db(candidate, target):
                _log().info(
                    f"已把历史数据库 {candidate.name} 迁移到 {_DEFAULT_DB_DIR}/{_DEFAULT_DB_NAME}"
                )
                return f"sqlite:///{target}"
            else:
                # 迁移失败：继续用旧文件，绝不静默新建空库
                _log().warning(f"沿用旧数据库位置：{candidate}")
                return f"sqlite:///{candidate}"

    # 全新安装：指向新位置（首次连接时 SQLite 会创建空库）
    return f"sqlite:///{target}"


def _maybe_adopt_legacy_for_explicit_url(url: str) -> str:
    """显式 DATABASE_URL 指向「全新 sqlite 文件」时，尝试接管旧位置的数据。

    这是 Docker 部署的关键场景：compose 里固定写
    `DATABASE_URL=sqlite:////app/data/open_noter.db`，于是 `_resolve_default_db_url()`
    根本不会被调用——但老用户的库还在 `/app/open_noter.db`（代码根目录）。
    如果不处理，升级后就会看到一个空库，表现为「数据全丢了」。

    安全边界（只在同时满足时接管，绝不覆盖已有数据）：
      1. URL 是 sqlite
      2. 目标文件**不存在**（或存在但不是有效 SQLite，即空壳）
      3. cwd 下存在可用的历史库（open_noter.db / bili_note.db）

    非 sqlite（Postgres 等）与自定义路径一律不碰。
    """
    if not url.startswith("sqlite"):
        return url

    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return url
    raw_path = url[len(prefix) :]
    # sqlite:////app/x 会剩下 /app/x（绝对路径）；sqlite:///x 剩下 x（相对）
    target = Path(raw_path)

    if target.exists() and _sqlite_ok(target):
        return url  # 目标已有真库：不动

    # 只在「目标就是 data/ 下的默认库名」时接管。
    # 若是用户自己指到别处的库（如 /mnt/mydb.sqlite），说明那是另一个数据集，
    # 不该把代码目录里的库塞过去。
    if target.name != _DEFAULT_DB_NAME:
        return url

    cwd = Path.cwd()
    for name in _LEGACY_NAMES:
        candidate = cwd / name
        if candidate == target:
            continue  # 同一路径，无需搬
        if candidate.exists() and candidate.is_file() and _sqlite_ok(candidate):
            if _move_db(candidate, target):
                _log().info(
                    f"检测到显式 DATABASE_URL 指向新库，已接管历史数据 "
                    f"{candidate.name} → {target}"
                )
            else:
                _log().warning(
                    f"接管历史数据失败，请手动把 {candidate} 复制到 {target}"
                )
            break

    return url


_explicit_url = os.getenv("DATABASE_URL", "").strip()
if _explicit_url:
    DATABASE_URL = _maybe_adopt_legacy_for_explicit_url(_explicit_url)
else:
    DATABASE_URL = _resolve_default_db_url()

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
