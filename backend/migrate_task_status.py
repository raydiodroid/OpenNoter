"""给 video_tasks 增加任务状态列，给 users 增加配额列。

**为什么需要**：任务历史目前只存在 `note_results/*.status.json` 里，
删除任务会把 `.status.json` 物理删掉，于是「累计任务」「24h 失败」这类
监控指标会随用户删笔记而倒退（审计结论：监控指标可被普通用户操作篡改）。
DB 里另有一张 video_tasks 表但它只有 id/video_id/platform/task_id/created_at，
没有 status，因此补不上这个缺口。

本迁移把状态落到 DB，让监控有不可篡改的历史来源（扫描文件仍保留为兜底）。

**幂等**：可重复执行；列已存在就跳过。
**回填**：按现有 `.status.json` 回填历史（status 取 JSON 里的值，
failed_at 取文件内 failed_at 或 mtime）。回填只填空值，不覆盖已有数据。

用法（容器内 / 本机 backend 目录）：
    python migrate_task_status.py            # 迁移 + 回填
    python migrate_task_status.py --no-backfill   # 只加列，不回填
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.db.engine import get_engine
from app.db.init_db import init_db
from app.utils.logger import get_logger

logger = get_logger(__name__)

# (列名, SQLite 类型)
NEW_COLUMNS = [
    ("status", "VARCHAR(32)"),
    ("error", "VARCHAR(512)"),
    ("failed_at", "DATETIME"),
    ("updated_at", "DATETIME"),
    # 软删除标记：删笔记只打时间戳，不物理删行，否则累计指标会倒退
    ("deleted_at", "DATETIME"),
]

# 用户表：多用户配额字段（NULL = 跟随全局默认，0 = 不限制）
USER_COLUMNS = [
    ("max_concurrent_tasks", "INTEGER"),
    ("daily_task_limit", "INTEGER"),
    ("storage_quota_mb", "INTEGER"),
]


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _note_dir() -> Path:
    raw = os.getenv("NOTE_OUTPUT_DIR", "note_results")
    return Path(raw).expanduser().resolve()


def _iter_status_files(root: Path):
    if not root.exists():
        return
    for f in sorted(root.glob("*.status.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        task_id = f.name[: -len(".status.json")]
        yield task_id, data, f


def _ts(value) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def add_columns() -> int:
    """给 video_tasks 补列。返回新增列数（0 表示已是最新）。"""
    engine = get_engine()
    added = 0
    with engine.begin() as conn:
        # users 表的配额列（表不存在时跳过——它由 init_db 创建，
        # 这里不该替它建表；老库可能还没跑到那一步）
        if _table_exists(conn, "users"):
            for name, coltype in USER_COLUMNS:
                if _column_exists(conn, "users", name):
                    logger.info(f"users.{name} 已存在，跳过")
                    continue
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {coltype}"))
                logger.info(f"users.{name} 已添加 ({coltype})")
                added += 1
        else:
            logger.warning("users 表不存在，跳过配额列迁移（请先执行 init_db）")

        for name, coltype in NEW_COLUMNS:
            if _column_exists(conn, "video_tasks", name):
                logger.info(f"video_tasks.{name} 已存在，跳过")
                continue
            conn.execute(text(f"ALTER TABLE video_tasks ADD COLUMN {name} {coltype}"))
            logger.info(f"video_tasks.{name} 已添加 ({coltype})")
            added += 1


        # 索引：监控按 status / failed_at / updated_at 过滤
        try:
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_video_tasks_status ON video_tasks (status)")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_video_tasks_failed_at ON video_tasks (failed_at)")
            )
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_video_tasks_deleted_at ON video_tasks (deleted_at)")
            )
        except Exception as exc:  # 索引失败不影响主流程
            logger.warning(f"创建索引失败（忽略）：{exc}")

    return added


def backfill(force: bool = False) -> int:
    """把现有 .status.json 回填进 DB。只填空值，除非 force。"""
    root = _note_dir()
    if not root.exists():
        logger.warning(f"任务目录不存在，跳过回填：{root}")
        return 0

    engine = get_engine()
    filled = 0
    with engine.begin() as conn:
        for task_id, data, path in _iter_status_files(root):
            row = conn.execute(
                text("SELECT id, status FROM video_tasks WHERE task_id = :tid"), {"tid": task_id}
            ).fetchone()
            if row is None:
                # 状态文件存在但 DB 无记录（历史/被删过）：跳过，避免凭空造行
                continue
            if row[1] and not force:
                continue

            status = str(data.get("status") or "").upper() or None
            error = (data.get("message") or None)
            if error is not None:
                error = str(error)[:512]
            failed_at = _ts(data.get("failed_at"))
            if failed_at is None and status == "FAILED":
                try:
                    failed_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    failed_at = None
            updated_at = None
            try:
                updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                pass

            conn.execute(
                text(
                    "UPDATE video_tasks SET status = COALESCE(status, :st), "
                    "error = COALESCE(error, :err), "
                    "failed_at = COALESCE(failed_at, :fat), "
                    "updated_at = COALESCE(updated_at, :uat) WHERE task_id = :tid"
                ),
                {
                    "st": status,
                    "err": error,
                    "fat": failed_at,
                    "uat": updated_at,
                    "tid": task_id,
                },
            )
            filled += 1

    logger.info(f"回填完成：{filled} 条")
    return filled


def main() -> int:
    parser = argparse.ArgumentParser(description="给 video_tasks 增加任务状态列并回填")
    parser.add_argument("--no-backfill", action="store_true", help="只加列，不从状态文件回填")
    parser.add_argument("--force", action="store_true", help="回填时覆盖已有值")
    args = parser.parse_args()

    print("[1/3] 确保基础表存在")
    init_db()

    print("[2/3] 给 video_tasks 补列")
    added = add_columns()
    print(f"      新增 {added} 列" if added else "      已是最新，无需变更")

    if args.no_backfill:
        print("[3/3] 跳过回填（--no-backfill）")
        return 0

    print(f"[3/3] 从 {_note_dir()} 回填历史状态")
    filled = backfill(force=args.force)
    print(f"      回填 {filled} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
