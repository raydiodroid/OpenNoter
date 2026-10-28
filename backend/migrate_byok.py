"""BYOK 迁移：建 user_credentials / user_cookies 表，并做升级自检。

幂等，可重复执行。用法（容器内）：
    python migrate_byok.py
    python migrate_byok.py --check     # 只检查不建表

## 为什么这个脚本比其它迁移简单

BYOK 只**新增表**，不改任何既有表结构，所以 ``init_db()`` 里的
``Base.metadata.create_all()`` 就能把表建好——这是 ``create_all`` 唯一
靠得住的场景（它只建缺失的表，绝不 ALTER 已存在的表）。

所以本脚本的价值不在「建表」，而在**升级自检**：

1. 确认两张新表真的建出来了；
2. 确认存量数据不会被误伤（providers / users 行数不变）；
3. 检查加密密钥是否可用——**这是最容易在升级时踩的坑**：
   BYOK 凭据用 ``OPENNOTER_CREDENTIAL_SECRET``（未设则回退 ``JWT_SECRET``）
   派生密钥。如果部署者在升级时顺手换了 ``JWT_SECRET`` 而没设专用密钥，
   已存的 BYOK 凭据会全部解不开（表现为用户「突然要重填」）。
   脚本对空表不报错（新部署本来就没数据），但会对「有数据却解不开」明确告警。

## 为什么不自动改 config/byok.json

BYOK 模式是**部署决策**，不该被迁移脚本悄悄写成某个值。表建好后默认行为是
``shared``（文件不存在时的默认值），与升级前完全一致——这是刻意的零破坏。
"""

import argparse
import sys

from sqlalchemy import text

from app.db.engine import SessionLocal, get_engine
from app.db.init_db import init_db

REQUIRED_TABLES = ("user_credentials", "user_cookies")


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).fetchone()
    return row is not None


def _count(conn, table: str) -> int:
    try:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    except Exception:  # noqa: BLE001
        return -1


def _check_encryption() -> tuple[bool, str]:
    """验证 BYOK 加密密钥可用。

    只做「能不能派生密钥」的检查，不碰真实凭据——脚本不该解密用户数据。
    """
    try:
        from app.utils import credential_crypto

        # 走一次完整的加解密回路（用假数据 + 不落库）
        probe = "migrate-byok-selfcheck"
        sealed = credential_crypto.seal(probe, user_id=0, capability="selfcheck")
        ok = credential_crypto.reveal(sealed, user_id=0, capability="selfcheck") == probe
        return (ok, "" if ok else "加密回路自检失败")
    except RuntimeError as exc:
        # 缺少密钥（既无 CREDENTIAL_SECRET 也无 JWT_SECRET）
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="BYOK 迁移与升级自检")
    parser.add_argument(
        "--check", action="store_true", help="只检查，不建表"
    )
    args = parser.parse_args()

    engine = get_engine()

    if not args.check:
        print("[1/4] 创建缺失的表（含 user_credentials / user_cookies）")
        init_db()
    else:
        print("[1/4] --check 模式：跳过建表")

    rc = 0

    with engine.connect() as conn:
        print("[2/4] 检查 BYOK 表")
        for table in REQUIRED_TABLES:
            exists = _table_exists(conn, table)
            print(f"      {table}: {'已存在' if exists else '**缺失**'}")
            if not exists:
                rc = 1

        print("[3/4] 检查存量数据未被影响")
        for table in ("providers", "users"):
            if _table_exists(conn, table):
                print(f"      {table}: {_count(conn, table)} 行")
            else:
                print(f"      {table}: 不存在（空库？）")

    print("[4/4] 检查 BYOK 加密密钥")
    ok, detail = _check_encryption()
    if ok:
        print("      加密回路自检通过")
    else:
        print(f"      **失败**：{detail}")
        print("      修复：设置 OPENNOTER_CREDENTIAL_SECRET（或确保 OPENNOTER_JWT_SECRET 未变）")
        # 加密不可用一律算失败（含 --check），否则 CI / 部署脚本会把
        # 「密钥没配好」当成成功，问题留到用户点开设置页才暴露。
        rc = 1

    # 有数据的用户表 + 加密不可用 → 已有的 BYOK 凭据会解不开，必须告警
    if not ok:
        db = SessionLocal()
        try:
            from app.db.models.user_cookies import UserCookie
            from app.db.models.user_credentials import UserCredential

            n_cred = db.query(UserCredential).count()
            n_cookie = db.query(UserCookie).count()
        except Exception:  # noqa: BLE001
            n_cred = n_cookie = 0
        finally:
            db.close()

        if n_cred or n_cookie:
            print(
                f"\n  ⚠️  已存在 {n_cred} 条凭据 / {n_cookie} 条 Cookie，但当前密钥解不开它们。"
                "\n     用户会看到「凭据无法解密，请重新填写」。"
                "\n     若你刚换过密钥，请把旧值写入 OPENNOTER_CREDENTIAL_SECRET_OLD 以兼容。"
            )

    print()
    if rc == 0:
        print("迁移完成。BYOK 默认模式为 shared（与升级前行为一致），")
        print("可在「设置 → 模型配置」或 config/byok.json 中切换。")
    else:
        print("迁移存在未通过项，请按上面的提示处理。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
