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

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# 历史数据库文件名，**按「新 → 旧」优先级排序**。
# 顺序很重要：同时存在多个时，必须优先用最新的那个（open_noter.db）。
# 曾经写成旧→新，导致双文件并存时迁走了更旧的 bili_note.db，
# 较新的数据被留在原地不用 —— 表现为「数据回退到更早的版本」。
_LEGACY_NAMES = ("open_noter.db", "bili_note.db")
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


def _find_legacy_dbs(cwd: Path, exclude: Path | None = None) -> list[Path]:
    """找出 cwd 下所有可用的历史库，按新→旧顺序返回。"""
    found: list[Path] = []
    for name in _LEGACY_NAMES:
        candidate = cwd / name
        if exclude is not None and candidate == exclude:
            continue
        if candidate.exists() and candidate.is_file() and _sqlite_ok(candidate):
            found.append(candidate)
    return found


def _log_dual_db_warning(found: list[Path]) -> None:
    _log().error(
        "检测到多个历史数据库并存：" + ", ".join(p.name for p in found) + "。"
        "自动迁移有选错风险，已中止。请人工确认哪个是最新的，"
        "把需要的那个复制到目标位置后重启（其余可归档）。"
    )


def _resolve_default_db_url() -> str:
    """解析默认 DATABASE_URL，必要时把旧位置的库搬到 data/ 并返回新 URL。

    只在环境变量**未显式设置**时调用——用户自定义 `DATABASE_URL` 一律尊重。
    """
    cwd = Path.cwd()
    target = cwd / _DEFAULT_DB_DIR / _DEFAULT_DB_NAME

    # 新位置已有可用库：直接用
    if target.exists() and _sqlite_ok(target):
        return f"sqlite:///{target}"

    found = _find_legacy_dbs(cwd, exclude=target)
    if len(found) > 1:
        # 双库并存：不猜，直接拒绝自动迁移，沿用最新的那个
        _log_dual_db_warning(found)
        _log().warning(f"暂时沿用较新的数据库：{found[0]}")
        return f"sqlite:///{found[0]}"

    if found:
        candidate = found[0]
        if _move_db(candidate, target):
            _log().info(
                f"已把历史数据库 {candidate.name} 迁移到 {_DEFAULT_DB_DIR}/{_DEFAULT_DB_NAME}"
            )
            return f"sqlite:///{target}"
        # 迁移失败：继续用旧文件，绝不静默新建空库
        _log().warning(f"迁移失败，沿用旧数据库位置：{candidate}")
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

    # 只在「目标是 data/ 目录下的默认库名」时接管。
    #
    # 为什么必须同时校验目录和文件名：只看文件名的话，
    # `sqlite:////tmp/open_noter.db`、`/mnt/x/open_noter.db` 这类完全无关的
    # 路径也会被塞入 cwd 的历史库，并把原文件**搬走**——那是数据错位，
    # 不是升级迁移。只有 compose 约定的 `<cwd>/data/open_noter.db`
    # （容器内即 /app/data/open_noter.db）才是我们要接管的场景。
    if target.name != _DEFAULT_DB_NAME:
        return url
    if target.parent.name != _DEFAULT_DB_DIR:
        return url

    cwd = Path.cwd()
    found = _find_legacy_dbs(cwd, exclude=target)

    if len(found) > 1:
        _log_dual_db_warning(found)
        _log().warning(f"放弃接管，沿用现有配置的 {target}")
        return url

    if not found:
        return url  # 无旧库可接管，走「全新安装」语义

    candidate = found[0]
    if _move_db(candidate, target):
        _log().info(
            f"检测到显式 DATABASE_URL 指向新库，已接管历史数据 "
            f"{candidate.name} → {target}"
        )
        return url

    # === N1: 接管失败必须回退到已校验的旧库，绝不能指向空目标 ===
    #
    # 失败仍 return url 的话，目标文件不存在，SQLAlchemy 首次连接会建一个
    # **空库**并连上去——用户看到「账号/供应商/笔记全丢了」，而真实数据
    # 还躺在 candidate 里没被使用。回退到 candidate 至少保住数据可用，
    # 同时日志明确告知需要人工处理。
    _log().error(
        f"接管历史数据失败（{candidate} → {target}）。"
        f"为避免创建空库，本次启动回退使用旧库 {candidate}。"
        f"请修复目标目录权限后重启，或手动复制：cp {candidate} {target}"
    )
    return f"sqlite:///{candidate}"


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


# ---------------------------------------------------------------------------
# SQLite PRAGMA（R5）
# ---------------------------------------------------------------------------
# 默认 journal_mode=DELETE 的语义：写事务进行时持有 RESERVED/EXCLUSIVE 锁，
# 其他连接的**读也会被阻塞**（直到 busy_timeout 超时 → database is locked）。
#
# 本项目的写模式恰好是最容易撞锁的那种：6 个任务线程池 worker 各自频繁
# `_update_status()`（每任务多次落盘+写 DB），同时用户端每 3 秒轮询、
# 每 30 秒拉列表。写密集 + 高频读 = delete 模式下的经典争用。
#
# WAL 的收益：
#   - **读不阻塞写、写不阻塞读**（读走快照，写进 WAL 文件）
#   - 写并发仍是单写者，但排队更短（只锁写）
#   - 崩溃恢复更快，且 `synchronous=NORMAL` 在 WAL 下是安全的
#
# 权衡：WAL 会在库旁多出 -wal / -shm 两个文件；备份时要一起带上
# （或用 `VACUUM INTO` / `.backup` 做一致性快照，见 README）。
if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        """每条新连接都设置。PRAGMA 是连接级的，不是数据库级的。"""
        try:
            cursor = dbapi_connection.cursor()
            # WAL 是持久化的（写进库头），但重复设置无害且能自愈
            cursor.execute("PRAGMA journal_mode=WAL")
            # 写锁等待上限：5s。默认 5000ms 但显式声明避免驱动差异
            cursor.execute("PRAGMA busy_timeout=5000")
            # WAL 下 NORMAL 安全（不会因断电损坏库），且避免每次提交 fsync
            cursor.execute("PRAGMA synchronous=NORMAL")
            # 外键约束默认关闭，必须显式打开（配额/归属表有关联）
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        except Exception:
            # 只读文件系统/特殊环境设置失败时不要炸启动，降级为默认行为
            pass


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
