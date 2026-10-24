"""一次性迁移：扩展 api_keys 表，新增 api_key_usage 表。

背景：2026-10-20 的 API Key 管理改造。旧表只有 9 列（name/prefix/key_hash/
last_used_at/expires_at/revoked/created_at），缺状态机、缺作用域、缺活动明细、
缺独立 key_id；哈希是 bcrypt（放在鉴权热路径上每次约 300ms，且带盐导致无法
按哈希直接索引查找）。

幂等，可重复执行。用法（容器内）：
    python migrate_api_keys.py

存量数据的处理原则：**不改变行为**。
  - 存量密钥继续有效，不强制加过期（突然让在跑的脚本失效是事故）
  - 存量密钥的 scopes 留空 = 等价于用户全部权限（与改造前一致）
  - 存量密钥的 hash_algo 记为 bcrypt，首次鉴权时透明重哈希成 SHA-256
"""

import sys

from sqlalchemy import text

from app.db.engine import SessionLocal, get_engine
from app.db.init_db import init_db

# 新增列：(列名, SQL 类型, 默认值字面量)
# 注意：SQLite 的 ALTER TABLE ADD COLUMN 不支持带约束的完整定义，
# 索引和唯一约束在下面用单独的语句补。
NEW_COLUMNS = [
    ("description", "TEXT", None),
    ("key_id", "TEXT", None),
    ("last_four", "TEXT", None),
    ("hash_algo", "TEXT", "'sha256'"),
    ("scopes", "JSON", None),
    ("allowed_ips", "JSON", None),
    ("rate_limit_rpm", "INTEGER", None),
    ("status", "TEXT", "'active'"),
    ("revoked_at", "TEXT", None),
    ("revoked_reason", "TEXT", None),
    ("grace_expires_at", "TEXT", None),
    ("not_before", "TEXT", None),
    ("rotated_from_id", "INTEGER", None),
    ("last_used_ip", "TEXT", None),
    ("last_used_endpoint", "TEXT", None),
    ("last_used_user_agent", "TEXT", None),
    ("use_count", "INTEGER", "0"),
    ("last_rotated_at", "TEXT", None),
]

INDEXES = [
    ("CREATE UNIQUE INDEX IF NOT EXISTS ix_api_keys_key_id ON api_keys (key_id)"),
    ("CREATE INDEX IF NOT EXISTS ix_api_keys_key_hash ON api_keys (key_hash)"),
]


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def main() -> int:
    engine = get_engine()

    print("[1/5] 创建缺失的表（api_key_usage 等）")
    init_db()

    print("[2/5] 扩展 api_keys 表")
    with engine.begin() as conn:
        existing = _columns(conn, "api_keys")
        added = 0
        for name, sql_type, default in NEW_COLUMNS:
            if name in existing:
                continue
            ddl = f"ALTER TABLE api_keys ADD COLUMN {name} {sql_type}"
            if default is not None:
                ddl += f" DEFAULT {default}"
            conn.execute(text(ddl))
            added += 1
            print(f"      + {name} {sql_type}")
        if added == 0:
            print("      所有列均已存在，跳过")

    print("[3/5] 建索引")
    with engine.begin() as conn:
        for ddl in INDEXES:
            conn.execute(text(ddl))
            print(f"      ok {ddl.split(' ON ')[0].replace('CREATE ', '')}")

    print("[4/5] 回填存量行")
    db = SessionLocal()
    try:
        # hash_algo 按哈希本身判断，不看新列的默认值。
        # ALTER TABLE ADD COLUMN ... DEFAULT 'sha256' 会把存量行也填成 'sha256'，
        # 但存量行的 key_hash 是 bcrypt（形如 $2b$12$...），必须标成 bcrypt，
        # 否则它们会被当成 SHA-256 去比对而全部失效。
        db.execute(text(
            "UPDATE api_keys SET hash_algo = 'bcrypt' WHERE key_hash LIKE '$2%'"
        ))
        db.execute(text(
            "UPDATE api_keys SET hash_algo = 'sha256' "
            "WHERE key_hash IS NOT NULL AND key_hash NOT LIKE '$2%'"
        ))
        # key_id：按 id 生成稳定值，保证唯一且可追溯
        rows = db.execute(text(
            "SELECT id FROM api_keys WHERE key_id IS NULL OR key_id = ''"
        )).fetchall()
        for (row_id,) in rows:
            db.execute(
                text("UPDATE api_keys SET key_id = :kid WHERE id = :rid"),
                {"kid": f"bk_legacy{row_id:06d}", "rid": row_id},
            )
        # 已吊销的存量行补上状态与原因
        db.execute(text(
            "UPDATE api_keys SET status = 'revoked', revoked_at = COALESCE(revoked_at, created_at), "
            "revoked_reason = COALESCE(revoked_reason, 'manual') WHERE revoked = 1"
        ))
        # 未吊销的补上 active
        db.execute(text(
            "UPDATE api_keys SET status = 'active' WHERE revoked = 0 AND (status IS NULL OR status = '')"
        ))
        # use_count 兜底
        db.execute(text("UPDATE api_keys SET use_count = 0 WHERE use_count IS NULL"))
        db.commit()
        print(f"      回填 key_id {len(rows)} 条")
    finally:
        db.close()

    print("[5/5] 校验")
    db = SessionLocal()
    try:
        cols = sorted(_columns(db.connection(), "api_keys"))
        print(f"      api_keys 现有 {len(cols)} 列")
        missing = [c for c, _, _ in NEW_COLUMNS if c not in cols]
        if missing:
            print(f"      警告：仍缺列 {missing}")
        total = db.execute(text("SELECT count(*) FROM api_keys")).scalar()
        by_algo = db.execute(
            text("SELECT hash_algo, count(*) FROM api_keys GROUP BY hash_algo")
        ).fetchall()
        print(f"      存量密钥 {total} 条，按哈希算法：{dict(by_algo)}")
        print("      提示：bcrypt 的存量密钥会在各自下一次成功鉴权时透明升级为 SHA-256")
    finally:
        db.close()

    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
