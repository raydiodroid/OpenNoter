"""认证与凭据：本地密码、JWT 会话、API Key 三条通道。

设计要点：
  - 密码用 bcrypt 存哈希，永不落明文（低熵人类密码，慢哈希有意义）
  - JWT 用 HS256，密钥来自环境变量（缺失时启动即报错，避免用默认值裸奔）
  - API Key 明文只返回一次，库里存 SHA-256 哈希 + 非秘密的展示信息
    （高熵随机串，慢哈希无益，见 hash_api_key 的说明）

API Key 的定位：**等价于该用户的认证口令**。它能做什么，用户就能做什么。
`get_current_user` 仍然返回 `User`，所有依赖它的接口（约 20 处 require_admin
调用点）一行都不用改。按 key 维度的能力（限流、IP 白名单、用量）通过
`request.state.api_key` 传递。
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models.api_keys import (
    ApiKey,
    REVOKE_ROTATED,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_EXPIRED,
    STATUS_REVOKED,
)
from app.db.models.users import User
from app.services import api_key_runtime
from app.utils.ip_allow import client_ip, ip_allowed
from app.utils.logger import get_logger

logger = get_logger(__name__)

JWT_ALG = "HS256"
API_KEY_PREFIX = "sk-bili-"
# 公开、非秘密的密钥 ID 前缀
API_KEY_ID_PREFIX = "bk_"

# 401 响应里附带的鉴权提示。只看到一句「未登录」的话，调用方（尤其是 agent）
# 根本不知道该怎么传凭据、key 从哪来，所以把该说的都说清楚。
AUTH_HINT = {
    "reason": "missing_or_invalid_credentials",
    "how_to_authenticate": [
        f"请求头 `x-api-key: {API_KEY_PREFIX}...`",
        f"请求头 `Authorization: Bearer {API_KEY_PREFIX}...`（与上一条等价）",
        "请求头 `Authorization: Bearer <JWT>` 或 `Cookie: opennoter_token=<JWT>`（网页登录态）",
    ],
    "how_to_get_a_key": "用网页账号登录后打开「设置 → API 密钥」页点「新建密钥」；明文只在创建时返回一次。",
    "usage_guide": "GET /api/ 返回完整的使用指引（免鉴权）。",
    "openapi": "GET /openapi.json 返回完整 OpenAPI schema（需凭据）。",
}


def _unauthorized(message: str, reason: Optional[str] = None) -> HTTPException:
    """构造带鉴权提示的 401。

    响应体形如 {"code":401,"msg":"未登录","data":{"hint":{...}}}，
    客户端能直接读出「该怎么带凭据、key 从哪拿、指引在哪」。
    """
    hint = dict(AUTH_HINT)
    if reason:
        hint["reason"] = reason
    return HTTPException(
        status_code=401,
        detail={"msg": message, "data": {"hint": hint}},
    )


def _jwt_secret() -> str:
    from app.utils.env_compat import getenv

    secret = (getenv("JWT_SECRET", "") or "").strip()
    if not secret:
        raise RuntimeError(
            "OPENNOTER_JWT_SECRET 未配置。请生成一个随机串写入 .env，"
            "例如：python -c \"import secrets;print(secrets.token_urlsafe(48))\""
        )
    return secret


def _jwt_ttl_hours() -> int:
    from app.utils.env_compat import getint

    return getint("JWT_TTL_HOURS", 168)  # 默认 7 天


# ---------- 密码 ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: Optional[str]) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------

def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": "admin" if user.is_superuser else "user",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=_jwt_ttl_hours())).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None


# ---------- API Key ----------

def hash_api_key(plain: str) -> str:
    """API Key 哈希：SHA-256 hex，**不加盐、不拉伸**。

    这是有意的取舍。bcrypt 这类慢哈希是为**低熵人类密码**设计的：它防的是
    「数据库泄露后被离线爆破」。而 API Key 是 32 字节随机数（256 bit 熵），
    离线爆破本就不可行，慢哈希在这里只买到 CPU 成本：

      - 每次鉴权约 300ms（bcrypt rounds=12），放在 API 热路径上不可接受
      - bcrypt 带盐 → 无法按哈希直接索引查找，鉴权只能靠 prefix 查出所有
        候选再逐个 checkpw

    改成确定性的 SHA-256 后，`key_hash` 建唯一索引，鉴权就是一次 O(1) 查表。
    """
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def verify_api_key(plain: str, key_hash: str, algo: str = "sha256") -> bool:
    """校验 API Key。algo=bcrypt 是存量密钥的过渡期分支。"""
    if algo == "bcrypt":
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), key_hash.encode("utf-8"))
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(hash_api_key(plain), key_hash or "")


def generate_api_key() -> dict:
    """生成一把新密钥。

    返回 dict：
      plain     —— 明文，只在创建响应里出现一次
      key_id    —— 公开、非秘密的稳定 ID（bk_ + 12 字符），日志关联与轮换链用
      prefix    —— 展示用前缀（sk-bili-xxxxxxxx）
      last_four —— 明文后 4 位，在密码管理器里肉眼核对
      key_hash  —— SHA-256 hex
      hash_algo —— "sha256"
    """
    raw = secrets.token_urlsafe(32)
    plain = f"{API_KEY_PREFIX}{raw}"
    return {
        "plain": plain,
        "key_id": API_KEY_ID_PREFIX + secrets.token_urlsafe(9)[:12],
        "prefix": plain[: len(API_KEY_PREFIX) + 8],
        "last_four": plain[-4:],
        "key_hash": hash_api_key(plain),
        "hash_algo": "sha256",
    }


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite 取出来的是 naive datetime，统一当 UTC 处理。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def effective_key_status(key: ApiKey, now: datetime) -> str:
    """算出密钥当前的**有效**状态。

    库里存的 status 可能是过期的（到期时间是随时间推移才生效的，不需要定时
    任务去改库），所以每次判断时按 expires_at 实时修正。
    """
    status = key.status or STATUS_ACTIVE
    if status in (STATUS_REVOKED, STATUS_DISABLED):
        return status
    expires_at = as_utc(key.expires_at)
    if expires_at and expires_at < now:
        return STATUS_EXPIRED
    return status


def _in_rotation_grace(key: ApiKey, now: datetime) -> bool:
    """轮换宽限期内，旧 key 仍然可用（但界面要标记「即将吊销」）。"""
    if key.revoked_reason != REVOKE_ROTATED:
        return False
    grace_until = as_utc(key.grace_expires_at)
    return bool(grace_until and grace_until > now)


def current_api_key_id(request: Request) -> Optional[int]:
    """取本次请求用的 API Key 的 id（网页登录态返回 None）。

    注意只暴露**标量**而不是 ORM 实例：鉴权依赖的 DB session 在响应返回前就关了，
    之后再去读实例属性会触发 DetachedInstanceError（expire_on_commit 之后
    访问属性会尝试 refresh），进而把本该成功的响应变成 500。
    """
    return getattr(request.state, "api_key_id", None)


def _find_legacy_key(credential: str, db: Session) -> Optional[ApiKey]:
    """存量 bcrypt 密钥只能靠 prefix 找候选再逐个验签（bcrypt 带盐，查不了哈希）。"""
    prefix = credential[: len(API_KEY_PREFIX) + 8]
    candidates = db.query(ApiKey).filter(ApiKey.prefix == prefix).all()
    for key in candidates:
        if (key.hash_algo or "bcrypt") != "bcrypt":
            continue
        if verify_api_key(credential, key.key_hash, "bcrypt"):
            return key
    return None


def _authenticate_api_key(request: Request, credential: str, db: Session) -> User:
    """API Key 鉴权主流程。"""
    if not credential.startswith(API_KEY_PREFIX):
        raise _unauthorized("API Key 格式不正确", reason="expected_prefix")

    now = datetime.now(timezone.utc)

    # 快路径：SHA-256 哈希唯一索引，直接查表
    key = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(credential)).first()
    if key is None:
        # 慢路径：存量 bcrypt 密钥
        key = _find_legacy_key(credential, db)
    if key is None:
        raise _unauthorized("API Key 无效", reason="invalid")

    # 存量密钥首次鉴权成功 → 透明重哈希，之后就走快路径了
    if (key.hash_algo or "sha256") != "sha256":
        key.key_hash = hash_api_key(credential)
        key.hash_algo = "sha256"
        if not key.key_id:
            key.key_id = API_KEY_ID_PREFIX + secrets.token_urlsafe(9)[:12]
        if not key.last_four:
            key.last_four = credential[-4:]
        db.commit()
        logger.info(f"API Key {key.id} 已透明升级为 SHA-256 哈希")

    # ---- 生命周期 ----
    status = effective_key_status(key, now)
    if status == STATUS_REVOKED:
        if _in_rotation_grace(key, now):
            pass  # 轮换宽限期内仍放行
        else:
            raise _unauthorized("API Key 已吊销", reason="revoked")
    elif status == STATUS_DISABLED:
        raise _unauthorized("API Key 已停用", reason="disabled")
    elif status == STATUS_EXPIRED:
        raise _unauthorized("API Key 已过期", reason="expired")

    not_before = as_utc(key.not_before)
    if not_before and not_before > now:
        raise _unauthorized("API Key 尚未生效", reason="not_yet_valid")

    # ---- IP 白名单 ----
    source_ip = client_ip(request)
    if not ip_allowed(source_ip, key.allowed_ips):
        logger.warning(f"API Key {key.id} 被拒绝：来源 IP {source_ip} 不在允许列表内")
        raise HTTPException(
            status_code=403,
            detail=f"来源 IP {source_ip or '未知'} 不在该密钥的允许列表内",
        )

    # ---- 限流 ----
    if key.rate_limit_rpm:
        allowed, remaining = api_key_runtime.rate_limiter.check(key.id, key.rate_limit_rpm)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"超过限流上限（{key.rate_limit_rpm} 次/分钟）",
                headers={"Retry-After": str(api_key_runtime.RATE_LIMIT_WINDOW)},
            )

    # ---- 归属用户 ----
    user = db.query(User).filter(User.id == key.user_id).first()
    if not user or not user.is_active:
        raise _unauthorized("账号已停用", reason="account_disabled")

    # ---- 活动记录（攒批落盘，不在请求路径上写库） ----
    endpoint = f"{request.method} {request.url.path}"
    api_key_runtime.activity_tracker.record(
        key.id, source_ip, endpoint, request.headers.get("user-agent", "")
    )
    _flush_activity_if_due(db)

    request.state.api_key_id = key.id
    request.state.auth_kind = "api_key"
    return user


def _flush_activity_if_due(db: Session) -> None:
    """到点就把缓冲里的活动记录落库（一次事务写多把 key）。"""
    if not api_key_runtime.activity_tracker.due_for_flush():
        return
    items = api_key_runtime.activity_tracker.drain()
    if not items:
        return
    now = datetime.now(timezone.utc)
    try:
        for key_id, entry in items:
            row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
            if not row:
                continue
            row.last_used_at = now
            row.last_used_ip = entry.get("ip")
            row.last_used_endpoint = entry.get("endpoint")
            row.last_used_user_agent = (entry.get("user_agent") or "")[:255]
            row.use_count = (row.use_count or 0) + int(entry.get("count", 0))
        db.commit()
    except Exception as e:
        logger.warning(f"写入 API Key 活动记录失败: {e}")


def flush_all_activity(db: Session) -> None:
    """进程退出前把缓冲刷干净（lifespan shutdown 时调）。"""
    items = api_key_runtime.activity_tracker.drain()
    if not items:
        return
    now = datetime.now(timezone.utc)
    for key_id, entry in items:
        try:
            row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
            if not row:
                continue
            row.last_used_at = now
            row.last_used_ip = entry.get("ip")
            row.last_used_endpoint = entry.get("endpoint")
            row.last_used_user_agent = (entry.get("user_agent") or "")[:255]
            row.use_count = (row.use_count or 0) + int(entry.get("count", 0))
            db.commit()
        except Exception as e:
            logger.warning(f"退出前写入 API Key 活动记录失败: {e}")


# ---------- FastAPI 依赖 ----------

def _extract_credentials(request: Request) -> tuple[str, str]:
    """返回 (类型, 凭据)：api_key / bearer / cookie / 空。

    API Key 有两个等价入口：`x-api-key` 头和 `Authorization: Bearer <key>`。
    后者是 OpenAI / Anthropic / GitHub 等 API 的通行做法，脚本和 agent 框架
    默认都会往 Authorization 里塞凭据，只认 x-api-key 会让它们直接 401。
    两者用 `sk-bili-` 前缀区分，不会和 JWT 混淆。
    """
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        return "api_key", api_key

    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith(API_KEY_PREFIX):
            return "api_key", token
        return "bearer", token

    cookie = request.cookies.get("opennoter_token", "").strip()
    if cookie:
        return "cookie", cookie

    return "", ""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """统一入口：API Key / Bearer / Cookie 任一有效即可。

    API Key **等价于该用户的认证口令** —— 它能做什么，用户就能做什么。
    按 key 维度的能力（限流、IP 白名单）在这里就地校验，命中结果通过
    `request.state.api_key` 传给中间件（用量日志要拿到 key 与最终状态码）。

    401 会在响应体里附带鉴权提示（怎么带凭据、去哪拿 key、指引地址），
    免得调用方只看到一句「未登录」却不知道怎么传。
    """
    kind, credential = _extract_credentials(request)
    if not kind:
        raise _unauthorized("未登录")

    if kind == "api_key":
        return _authenticate_api_key(request, credential, db)

    payload = decode_access_token(credential)
    if not payload:
        raise _unauthorized("登录已失效", reason="expired")

    user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not user or not user.is_active:
        raise _unauthorized("账号不存在或已停用", reason="account_disabled")
    request.state.api_key_id = None
    request.state.auth_kind = "session"
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def auth_enforced() -> bool:
    """是否强制认证。

    渐进上线的开关：前端登录页就绪前保持关闭，业务接口照旧可用；
    打开后所有挂了 get_optional_user 的接口都会要求有效凭据。
    """
    from app.utils.env_compat import getbool

    return getbool("AUTH_ENFORCE", False)


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """有凭据就解析，没凭据时按开关决定放行还是 401。

    这样同一个依赖既能支撑「渐进上线」（未强制时透传），
    又能在开关打开后当作 get_current_user 用；业务侧还能拿到 user_id 做归属。
    """
    try:
        return get_current_user(request, db)
    except HTTPException:
        if auth_enforced():
            raise
        return None

