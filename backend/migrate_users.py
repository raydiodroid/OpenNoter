"""一次性迁移：建 users / api_keys 表，给 video_tasks 加 user_id 并回填。

幂等，可重复执行。用法（容器内）：
    python migrate_users.py

首位管理员：
    读取 ADMIN_EMAIL / ADMIN_PASSWORD 环境变量；
    未提供时用 admin@opennoter.local + 随机密码，并把密码打印到标准输出。
"""

import os
import secrets
import sys

from sqlalchemy import text

from app.db.engine import SessionLocal, get_engine
from app.db.init_db import init_db
from app.db.models.users import User
from app.services.auth import hash_password


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def main() -> int:
    engine = get_engine()

    print("[1/4] 创建缺失的表（users / api_keys / 其他）")
    init_db()

    with engine.begin() as conn:
        print("[2/4] 检查 video_tasks.user_id")
        if _column_exists(conn, "video_tasks", "user_id"):
            print("      已存在，跳过")
        else:
            conn.execute(text("ALTER TABLE video_tasks ADD COLUMN user_id INTEGER"))
            print("      已添加")

    db = SessionLocal()
    try:
        print("[3/4] 确保存在首位管理员")
        admin = db.query(User).filter(User.is_superuser.is_(True)).first()
        created_password = None
        if admin:
            print(f"      已存在管理员：{admin.email}")
        else:
            email = os.getenv("ADMIN_EMAIL", "admin@opennoter.local").strip().lower()
            password = os.getenv("ADMIN_PASSWORD", "").strip()
            if not password:
                password = secrets.token_urlsafe(12)
                created_password = password

            existing = db.query(User).filter(User.email == email).first()
            if existing:
                existing.is_superuser = True
                admin = existing
                print(f"      已将既有用户提升为管理员：{email}")
            else:
                admin = User(
                    email=email,
                    name="Administrator",
                    hashed_password=hash_password(password),
                    is_superuser=True,
                    is_active=True,
                )
                db.add(admin)
                print(f"      已创建管理员：{email}")

            db.commit()
            db.refresh(admin)

        print("[4/4] 回填存量任务的归属")
        result = db.execute(
            text("UPDATE video_tasks SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": admin.id},
        )
        db.commit()
        print(f"      回填 {result.rowcount} 条")

        if created_password:
            print("\n" + "=" * 56)
            print("  首位管理员初始密码（只显示这一次，请立即保存）")
            print(f"  邮箱：{admin.email}")
            print(f"  密码：{created_password}")
            print("=" * 56 + "\n")

        total = db.query(User).count()
        print(f"完成。当前用户数：{total}，管理员 id={admin.id}")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
