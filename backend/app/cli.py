"""OpenNoter 运维 CLI。

## 为什么需要它

F39 之前，「用户表为空时第一个 OIDC 登录者自动成为管理员」。这在公网可达的
部署上可被抢注：谁先完成一次 OIDC 登录，谁就是超管。

现在该行为**默认关闭**（需显式 `OPENNOTER_OIDC_FIRST_USER_ADMIN=true`），
所以必须有一条**不经 HTTP** 的引导路径来创建第一个管理员 —— 否则新部署会
陷入「没有管理员 → 无法建用户 → 永远没有管理员」的死锁。

CLI 的价值在于：它只能在**服务器本机**执行（有文件系统权限才能跑），
因此「谁能执行」本身就是一道访问控制，不依赖任何网络暴露面。

## 用法

在 `backend/` 目录下：

    # 创建第一个管理员（交互式输入密码）
    python -m app.cli create-admin --email admin@example.com

    # 非交互（CI / 脚本）：密码从环境变量读，避免进 shell history
    OPENNOTER_ADMIN_PASSWORD='xxx' python -m app.cli create-admin --email a@b.c --from-env

    # 把某个已有用户提升为管理员
    python -m app.cli promote --email someone@example.com

    # 列出用户
    python -m app.cli list-users

## 安全约定

- 密码**不**接受命令行参数（会进 shell history / `ps` 输出）；
  只用交互式 `getpass` 或 `OPENNOTER_ADMIN_PASSWORD` 环境变量。
- 密码强度下限与后端 `/auth/me/password` 一致（8 位），但这里建议更长。
- 重复执行是安全的（同一个 email 已存在则提示并退出，不覆盖密码）。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# 允许 `python -m app.cli` 与 `python app/cli.py` 两种跑法
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIN_PASSWORD_LEN = 8


def _db():
    from app.db.engine import SessionLocal
    from app.db.init_db import init_db

    # 保证表结构存在（首次部署时用户表可能还没建）
    init_db()
    return SessionLocal()


def _normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not email or "@" not in email:
        raise SystemExit(f"✗ 邮箱格式不合法：{raw!r}")
    return email


def _read_password(from_env: bool) -> str:
    """读密码。不接受命令行参数（避免进 shell history / ps）。"""
    if from_env:
        pwd = os.getenv("OPENNOTER_ADMIN_PASSWORD", "")
        if not pwd:
            raise SystemExit(
                "✗ --from-env 需要设置 OPENNOTER_ADMIN_PASSWORD 环境变量",
            )
        return pwd

    pwd = getpass.getpass("请输入管理员密码（输入时不回显）：")
    if len(pwd) < MIN_PASSWORD_LEN:
        raise SystemExit(f"✗ 密码至少 {MIN_PASSWORD_LEN} 位")
    confirm = getpass.getpass("再次输入以确认：")
    if pwd != confirm:
        raise SystemExit("✗ 两次输入不一致")
    return pwd


def cmd_create_admin(args) -> int:
    from app.db.models.users import User
    from app.services.auth import hash_password

    email = _normalize_email(args.email)
    password = _read_password(args.from_env)
    if len(password) < MIN_PASSWORD_LEN:
        raise SystemExit(f"✗ 密码至少 {MIN_PASSWORD_LEN} 位")

    db = _db()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            print(f"✗ 用户已存在：{email}（id={existing.id}）")
            print("  若要提升权限请用：python -m app.cli promote --email", email)
            return 1

        user = User(
            email=email,
            name=args.name or email.split("@")[0],
            hashed_password=hash_password(password),
            is_superuser=True,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"✓ 已创建管理员：{email}（id={user.id}）")
        return 0
    finally:
        db.close()


def cmd_promote(args) -> int:
    from app.db.models.users import User

    email = _normalize_email(args.email)
    db = _db()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            print(f"✗ 用户不存在：{email}")
            return 1
        if user.is_superuser:
            print(f"· 已经是管理员：{email}（id={user.id}）")
            return 0
        user.is_superuser = True
        db.commit()
        print(f"✓ 已提升为管理员：{email}（id={user.id}）")
        return 0
    finally:
        db.close()


def cmd_list_users(args) -> int:
    from app.db.models.users import User

    db = _db()
    try:
        users = db.query(User).order_by(User.id).all()
        if not users:
            print("（暂无用户）")
            return 0
        print(f"{'ID':<6}{'邮箱':<38}{'管理员':<8}{'启用':<6}登录方式")
        for u in users:
            ways = []
            if u.hashed_password:
                ways.append("本地密码")
            if u.oidc_sub:
                ways.append("OIDC")
            print(
                f"{u.id:<6}{u.email:<38}"
                f"{'是' if u.is_superuser else '否':<8}"
                f"{'是' if u.is_active else '否':<6}"
                f"{'/'.join(ways) or '—'}"
            )
        return 0
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="OpenNoter 运维命令（引导管理员等）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-admin", help="创建第一个管理员")
    p_create.add_argument("--email", required=True)
    p_create.add_argument("--name", default=None)
    p_create.add_argument(
        "--from-env",
        action="store_true",
        help="从 OPENNOTER_ADMIN_PASSWORD 读密码（非交互）",
    )
    p_create.set_defaults(func=cmd_create_admin)

    p_promote = sub.add_parser("promote", help="把已有用户提升为管理员")
    p_promote.add_argument("--email", required=True)
    p_promote.set_defaults(func=cmd_promote)

    p_list = sub.add_parser("list-users", help="列出所有用户")
    p_list.set_defaults(func=cmd_list_users)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
