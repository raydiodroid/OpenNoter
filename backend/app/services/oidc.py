"""OIDC 客户端：只做「拿身份」，不做授权判断。

用 pyjwt 的 PyJWKClient 校验 id_token 签名，用 httpx 走标准 discovery 流程，
不引入额外 OIDC 框架依赖。
"""

import os
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

_DISCOVERY_CACHE: dict = {"data": None, "exp": 0}
_JWKS_CLIENT: Optional[PyJWKClient] = None


def oidc_enabled() -> bool:
    return bool(
        os.getenv("OIDC_ISSUER", "").strip()
        and os.getenv("OIDC_CLIENT_ID", "").strip()
        and os.getenv("OIDC_CLIENT_SECRET", "").strip()
    )


def _issuer() -> str:
    return os.getenv("OIDC_ISSUER", "").strip().rstrip("/")


def redirect_uri() -> str:
    return os.getenv("OIDC_REDIRECT_URI", "").strip()


def discovery() -> dict:
    """带 10 分钟缓存的 discovery 文档。"""
    now = time.time()
    if _DISCOVERY_CACHE["data"] and _DISCOVERY_CACHE["exp"] > now:
        return _DISCOVERY_CACHE["data"]

    url = f"{_issuer()}/.well-known/openid-configuration"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _DISCOVERY_CACHE["data"] = data
    _DISCOVERY_CACHE["exp"] = now + 600
    return data


def build_authorize_url(state: str) -> str:
    conf = discovery()
    params = {
        "client_id": os.getenv("OIDC_CLIENT_ID", "").strip(),
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri(),
        "state": state,
    }
    return f"{conf['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """用 code 换 token，校验 id_token 签名，返回 claims。

    issuer 一律取 discovery 文档里的权威值，而不是环境变量里手写的字符串——
    authentik 的 issuer 结尾带斜杠，配置里少写一个字符就会 Invalid issuer。
    """
    conf = discovery()
    resp = httpx.post(
        conf["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "client_id": os.getenv("OIDC_CLIENT_ID", "").strip(),
            "client_secret": os.getenv("OIDC_CLIENT_SECRET", "").strip(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()

    id_token = tokens.get("id_token")
    if not id_token:
        raise ValueError("OIDC 响应缺少 id_token")

    global _JWKS_CLIENT
    if _JWKS_CLIENT is None:
        _JWKS_CLIENT = PyJWKClient(conf["jwks_uri"])

    signing_key = _JWKS_CLIENT.get_signing_key_from_jwt(id_token)

    # 先只验签名和有效期，拿到 claims 后再按 discovery 的 issuer 校验，
    # 避免因末尾斜杠之类的差异误判
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience=os.getenv("OIDC_CLIENT_ID", "").strip(),
        options={"verify_iss": False},
    )

    token_issuer = claims.get("iss", "")
    expected = conf.get("issuer", "")
    if token_issuer.rstrip("/") != expected.rstrip("/"):
        raise ValueError(f"issuer 不匹配：token={token_issuer} 期望={expected}")

    return claims
