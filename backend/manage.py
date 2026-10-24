"""OpenNoter 用户管理 CLI。

在容器内执行（工作目录 /app）：
    docker exec -it opennoter-backend python manage.py list
    docker exec -it opennoter-backend python manage.py reset-password admin@opennoter.local
    docker exec -it opennoter-backend python manage.py reset-password admin@opennoter.local -p NewPass1234
    docker exec -it opennoter-backend python manage.py create-user ops@example.com --admin
    docker exec -it opennoter-backend python manage.py disable ops@example.com
    docker exec -it opennoter-backend python manage.py enable ops@example.com
    docker exec -it opennoter-backend python manage.py promote ops@example.com
"""

import argparse
import secrets
import sys

from app.db.engine import SessionLocal
from app.db.models.users import User
from app.services.auth import hash_password


def _find(db, email: str) -> User:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user:
        print(f"未找到用户：{email}")
        sys.exit(1)
    return user


def cmd_list(args):
    db = SessionLocal()
    try:
        rows = db.query(User).order_by(User.id.asc()).all()
        if not rows:
            print("暂无用户")
            return
        print(f"{'ID':<4} {'邮箱':<32} {'角色':<6} {'状态':<6} {'密码':<6} {'OIDC':<6} 最后登录")
        for u in rows:
            print(
                f"{u.id:<4} {u.email:<32} "
                f"{'admin' if u.is_superuser else 'user':<6} "
                f"{'启用' if u.is_active else '停用':<6} "
                f"{'有' if u.hashed_password else '无':<6} "
                f"{'已绑' if u.oidc_sub else '未绑':<6} "
                f"{u.last_login_at.isoformat(sep=' ', timespec='seconds') if u.last_login_at else '-'}"
            )
    finally:
        db.close()


def cmd_reset_password(args):
    db = SessionLocal()
    try:
        user = _find(db, args.email)
        password = args.password or secrets.token_urlsafe(12)
        user.hashed_password = hash_password(password)
        user.is_active = True
        db.commit()
        print(f"已重置 {user.email} 的密码")
        print(f"  新密码：{password}")
        if not args.password:
            print("  （随机生成，仅显示这一次）")
    finally:
        db.close()


def cmd_create_user(args):
    db = SessionLocal()
    try:
        email = args.email.strip().lower()
        if db.query(User).filter(User.email == email).first():
            print(f"用户已存在：{email}")
            sys.exit(1)
        password = args.password or secrets.token_urlsafe(12)
        user = User(
            email=email,
            name=args.name or email.split("@")[0],
            hashed_password=hash_password(password),
            is_superuser=args.admin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"已创建用户 {user.email}（id={user.id}，{'管理员' if args.admin else '普通用户'}）")
        print(f"  初始密码：{password}")
        if not args.password:
            print("  （随机生成，仅显示这一次）")
    finally:
        db.close()


def _toggle_active(args, active: bool):
    db = SessionLocal()
    try:
        user = _find(db, args.email)
        user.is_active = active
        db.commit()
        print(f"{'已启用' if active else '已停用'}：{user.email}")
    finally:
        db.close()


def cmd_promote(args):
    db = SessionLocal()
    try:
        user = _find(db, args.email)
        user.is_superuser = True
        db.commit()
        print(f"已提升为管理员：{user.email}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="OpenNoter 用户管理")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出所有用户").set_defaults(func=cmd_list)

    p_reset = sub.add_parser("reset-password", help="重置密码（不传 -p 则随机生成）")
    p_reset.add_argument("email")
    p_reset.add_argument("-p", "--password")
    p_reset.set_defaults(func=cmd_reset_password)

    p_create = sub.add_parser("create-user", help="创建用户")
    p_create.add_argument("email")
    p_create.add_argument("-p", "--password")
    p_create.add_argument("-n", "--name")
    p_create.add_argument("--admin", action="store_true")
    p_create.set_defaults(func=cmd_create_user)

    p_disable = sub.add_parser("disable", help="停用账号")
    p_disable.add_argument("email")
    p_disable.set_defaults(func=lambda a: _toggle_active(a, False))

    p_enable = sub.add_parser("enable", help="启用账号")
    p_enable.add_argument("email")
    p_enable.set_defaults(func=lambda a: _toggle_active(a, True))

    p_promote = sub.add_parser("promote", help="提升为管理员")
    p_promote.add_argument("email")
    p_promote.set_defaults(func=cmd_promote)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
