"""BYOK 用户凭据的静态加密（AES-256-GCM + AAD 绑定 + 双密钥轮换）。

## 为什么需要一个独立模块，而不是复用 secret_box

``secret_box`` 用 HKDF 从 ``JWT_SECRET`` 派生单密钥，服务于四类密文：provider
key、平台 Cookie、静态签名。它有两个对 BYOK 致命的性质：

1. **不能轮换**：换 ``JWT_SECRET`` 会让上述四类全部解密失败（``.env.example``
   已文档化这一点）。对 Cookie 和 provider key 而言，代价是管理员自己重填；
   对 BYOK 而言，代价是 **N 个用户各自重填，而平台不掌握用户的 key、帮不上忙**。
2. **无绑定**：同一份密文可以被复制到数据库的另一行并正常解密。

所以 BYOK 单独一套密钥、单独一个模块，与 ``secret_box`` 完全解耦。

> ⚠️ **不要**把本模块改成读 ``OPENNOTER_SECRET_KEY``：那个变量是
> ``secret_box`` 的密钥来源，影响上面那四类密文。生产环境当前**未设置**它，
> 若为 BYOK 而新增，会导致现有 Cookie 与 provider key 全部解密失败。

## 两个设计点（都有业界依据）

**AAD 绑定**（借鉴 Dify 的 per-tenant 加密、LibreChat 的 ``seal(text, binding)``）：
把 ``f"{user_id}:{capability}"`` 作为 GCM 的附加认证数据。密文被复制到别的
用户/能力位时，解密直接抛 ``InvalidTag`` —— 挡住「改库里的 user_id 把自己
变成别人」这类水平越权。

**双密钥轮换**（借鉴 LobeChat 的 ``KEY_VAULTS_SECRET`` + ``..._FALLBACK_SECRET``）：
加密永远用 active；解密先试 active，失败再试 fallback。轮换 = 把当前密钥挪到
fallback、设新的 active —— 写入自动用新密钥、读取自动兼容旧密文，**零停机、
无需重加密存量**。这是六家参考项目里唯一可运维的轮换方案。

## 密钥来源（三档，优先前者）

1. ``OPENNOTER_CREDENTIAL_SECRET``     —— 推荐；与 JWT 完全解耦
2. ``OPENNOTER_CREDENTIAL_SECRET_OLD`` —— 轮换期间的 fallback（只用于解密）
3. 都没有 → 从 ``JWT_SECRET`` 派生      —— 零配置可用，与改造前行为一致

第 3 档是给「clone 下来直接跑」的开源用户的兜底：不要求他们为 BYOK 多配一个
环境变量。代价是轮换 ``JWT_SECRET`` 会连带 BYOK 凭据失效——这在文档里写明即可，
因为轮换 JWT 本身就是破坏性运维操作。
"""

from __future__ import annotations

import base64
import os
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 密文前缀。将来若更换算法，用新前缀双轨解密（参考 LibreChat 的 v1/v2/v3）。
PREFIX = "enc:v2:"

# AAD 的 domain separation 前缀。与密文内容分开，避免不同用途的 AAD 撞车。
_AAD_DOMAIN = b"opennoter-byok-credential-v1"

# HKDF 的 info（仅用于第 3 档「从 JWT_SECRET 派生」的回退路径）
_FALLBACK_KEY_INFO = b"opennoter-byok-credential-derivation-v1"

# 固定 salt。这里不需要随机 salt：输入是 48+ 字节的高熵密钥，
# 且派生是确定性的（否则无法解密）。
_FALLBACK_SALT = b"opennoter-byok-salt-v1"


# ---------------- 密钥派生 ----------------


def _hkdf(secret: str) -> bytes:
    """从字符串密钥派生 32 字节 AES key。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FALLBACK_SALT,
        info=_FALLBACK_KEY_INFO,
    )
    return hkdf.derive(secret.encode("utf-8"))


def _secret_candidates() -> list[bytes]:
    """按优先级返回可用于解密的密钥列表。

    - 第 1 个永远用于加密
    - 全部用于解密（支持轮换）
    """
    from app.utils.env_compat import getenv

    keys: list[bytes] = []

    active = (getenv("CREDENTIAL_SECRET", "") or "").strip()
    if active:
        keys.append(_hkdf(active))

    old = (getenv("CREDENTIAL_SECRET_OLD", "") or "").strip()
    if old:
        keys.append(_hkdf(old))

    if not keys:
        # 兜底：与改造前一致，从 JWT_SECRET 派生。
        # 用 domain-separated info，保证与 secret_box 派生出的 key 不同。
        jwt_secret = (getenv("JWT_SECRET", "") or "").strip()
        if not jwt_secret:
            raise RuntimeError(
                "缺少 BYOK 凭据加密密钥：请设置 OPENNOTER_CREDENTIAL_SECRET"
                "（或至少设置 OPENNOTER_JWT_SECRET 作为回退）。"
                '例如：python -c "import secrets;print(secrets.token_urlsafe(48))"'
            )
        keys.append(_hkdf(jwt_secret))

    return keys


def _aad(user_id: int, capability: str) -> bytes:
    """附加认证数据：把密文绑定到 (用户, 能力位)。"""
    return _AAD_DOMAIN + b"|" + f"{int(user_id)}:{capability}".encode("utf-8")


# ---------------- 对外接口 ----------------


def seal(plaintext: str, *, user_id: int, capability: str) -> str:
    """加密并绑定到 ``(user_id, capability)``。

    返回 ``enc:v2:<base64(nonce||ciphertext)>``。空串原样返回（不产生密文）。

    :raises RuntimeError: 没有任何可用密钥
    """
    if not plaintext:
        return plaintext

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _secret_candidates()[0]
    nonce = os.urandom(12)  # GCM 推荐 12 字节
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _aad(user_id, capability))
    payload = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    return PREFIX + payload


def reveal(envelope: Optional[str], *, user_id: int, capability: str) -> Optional[str]:
    """解密。返回 ``None`` 表示「解不开」——调用方按「凭据不可用」处理。

    **返回 None 而不是抛异常**是刻意的：调用方（``credential_resolver``）要按
    「配置了但坏了 → 明确报错」的分支处理，而不是让整个请求 500。

    依次尝试 active / fallback 密钥（支持轮换）。AAD 不匹配、密文被篡改、
    密钥全不对，都返回 None。
    """
    if not envelope:
        return None
    if not envelope.startswith(PREFIX):
        # 非本模块产出的密文（历史明文 / secret_box 的 enc:v1:）。
        # 不猜测、不降级——BYOK 凭据只可能来自本模块。
        logger.warning("BYOK 凭据格式不是 enc:v2:，拒绝解密")
        return None

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        raw = base64.urlsafe_b64decode(envelope[len(PREFIX):].encode("ascii"))
    except Exception:
        logger.warning("BYOK 凭据 base64 解码失败")
        return None

    if len(raw) <= 12:
        logger.warning("BYOK 凭据长度异常")
        return None

    nonce, ct = raw[:12], raw[12:]
    ad = _aad(user_id, capability)

    try:
        candidates = _secret_candidates()
    except RuntimeError as exc:
        logger.error(f"无法解密 BYOK 凭据：{exc}")
        return None

    for key in candidates:
        try:
            return AESGCM(key).decrypt(nonce, ct, ad).decode("utf-8")
        except (InvalidTag, ValueError):
            continue  # 试下一把（轮换兼容）

    # 全部失败：可能是密钥全换过，或密文被搬运到别的用户/能力位（AAD 不符）
    logger.error(
        f"BYOK 凭据解密失败（user_id={user_id}, capability={capability}）："
        "密钥不匹配或密文被移动"
    )
    return None


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and value.startswith(PREFIX)
