"""敏感配置的静态加密（Fernet / AES-128-CBC + HMAC）。

## 为什么需要

平台 Cookie（尤其 B 站的 `SESSDATA`）本质是**长期登录凭证**——
拿到它等于拿到账号。此前它们以明文 JSON 存在 `config/downloader.json`，
虽然文件权限是 0600，但：

- 备份/打包/mount 到容器时会被一起带走
- `docker inspect` 之外任何能读该目录的进程都能读到
- 日志/调试时容易误输出

所以落盘前必须加密。

## 密钥从哪来

**复用 `JWT_SECRET`**，经 HKDF 派生出独立的加密密钥。理由：

1. 生产环境必配（没有它登录直接报错），不引入新配置项
2. 派生保证「签名密钥」与「加密密钥」不共用同一份字节
3. 换 secret 会同时作废旧 token 与旧密文——语义一致，符合直觉

若确实想分离，可设 `OPENNOTER_SECRET_KEY` 覆盖（云 KMS 场景）。

## 兼容策略

密文带前缀 `enc:v1:`，读取时：
- 有前缀 → 解密，失败则返回 None（不把密文当明文用）
- 无前缀 → 视为**历史明文**，正常返回，并在下次写入时自动转为密文

这样存量数据不需要迁移脚本，也不会因为密钥不符而突然全丢。
"""

from __future__ import annotations

import base64
import os
from typing import Optional

PREFIX = "enc:v1:"

# HKDF 的 info 参数：把「同一个 JWT_SECRET」派生成不同用途的密钥
_KEY_INFO = b"opennoter-config-encryption-v1"


def _derived_key() -> bytes:
    """从 JWT_SECRET（或 SECRET_KEY）派生 32 字节密钥。"""
    from app.utils.env_compat import getenv

    # 优先用专用密钥；否则回退到 JWT_SECRET
    secret = (getenv("SECRET_KEY", "") or "").strip()
    if not secret:
        secret = (getenv("JWT_SECRET", "") or "").strip()
    if not secret:
        raise RuntimeError(
            "缺少加密密钥：请设置 OPENNOTER_SECRET_KEY 或 OPENNOTER_JWT_SECRET。\n"
            "例如：python -c \"import secrets;print(secrets.token_urlsafe(48))\""
        )

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"opennoter-salt-v1",
        info=_KEY_INFO,
    )
    return hkdf.derive(secret.encode("utf-8"))


def _fernet():
    """构造 Fernet 实例（AES-128-CBC + HMAC-SHA256）。"""
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(_derived_key()))


def encrypt(plaintext: str) -> str:
    """加密字符串，返回 `enc:v1:<token>`。空串原样返回（不该产生密文）。"""
    if not plaintext:
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(value: str) -> Optional[str]:
    """解密。非密文（历史明文）原样返回；密文损坏/密钥不符返回 None。

    返回 None 而不是抛异常，是为了让调用方能「当作没配置」
    并提示用户重新填写，而不是让整个接口 500。
    """
    if not value:
        return value
    if not value.startswith(PREFIX):
        return value  # 历史明文，兼容

    token = value[len(PREFIX) :]
    try:
        from cryptography.fernet import InvalidToken

        try:
            return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            return None
    except Exception:
        return None


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(PREFIX)


def looks_like_plaintext_secret(value: str) -> bool:
    """粗判一个值是否像「未加密的敏感内容」（用于迁移告警）。"""
    if not value or is_encrypted(value):
        return False
    # 平台 Cookie 的典型特征
    return any(k in value for k in ("SESSDATA", "bili_jct", "sessionid", "cookies.txt"))
