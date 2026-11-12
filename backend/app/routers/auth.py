"""认证与凭据相关接口。

三条通道：
  - POST /login            本地邮箱+密码（双通道里的后备通道）
  - GET  /oidc/login      跳转外部 OIDC（默认 authentik）
  - API Key               通过 X-API-Key 调业务接口，由 services.auth 统一校验
"""

import os
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models.api_keys import ApiKey, ApiKeyUsage
from app.db.models.users import User
from app.db.models.video_tasks import VideoTask
from app.services import oidc
from app.services.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.utils.env_compat import getbool
from app.utils.response import ResponseWrapper as R

router = APIRouter()

# OIDC state 暂存（单进程内存足够；多副本部署需换 Redis）
_OIDC_STATES: dict[str, tuple[float, str, str]] = {}
_STATE_TTL = 600
_STATE_COOKIE = "opennoter_oidc_state"

from app.services.api_key_runtime import FixedWindowRateLimiter

_LOGIN_LIMITER = FixedWindowRateLimiter()


def _cookie_secure() -> bool:
    return getbool("COOKIE_SECURE", True)


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "opennoter_token",
        token,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_superuser": bool(user.is_superuser),
        "is_active": bool(user.is_active),
        "has_password": bool(user.hashed_password),
        "oidc_bound": bool(user.oidc_sub),
    }


# ---------------- 本地登录 ----------------

class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


@router.post("/login")
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # 只信任直连对端，不能相信用户可伪造的 X-Forwarded-For。
    peer = request.client.host if request.client else "unknown"
    for key, limit in (("peer:" + peer, 60), ("email:" + data.email.strip().lower(), 10)):
        if not _LOGIN_LIMITER.check(key, limit)[0]:
            raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后重试", headers={"Retry-After": "60"})
    user = db.query(User).filter(User.email == data.email.strip().lower()).first()
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    user.last_login_at = datetime.now(timezone.utc)
    token = create_access_token(user, db)
    db.commit()
    # 注意：R.success 返回的是独立的 JSONResponse，注入的 Response 参数改不到它，
    # 必须在返回值上设置 Cookie，否则浏览器拿不到会话。
    resp = R.success(data={"token": token, "user": _user_payload(user)})
    _set_auth_cookie(resp, token)
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    from app.services.auth import revoke_session
    revoke_session(request, db)
    db.commit()
    resp = R.success(msg="已退出")
    resp.delete_cookie("opennoter_token", path="/")
    return resp


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return R.success(data=_user_payload(user))


@router.post("/me/password")
def set_password(
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """为当前账号设置/修改本地密码（OIDC 用户也可借此开通后备登录）。"""
    new_password = (payload.get("password") or "").strip()
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    if len(new_password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="密码的 UTF-8 长度不能超过 72 字节")

    current = (payload.get("current_password") or "").strip()
    if user.hashed_password and not verify_password(current, user.hashed_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")

    user.hashed_password = hash_password(new_password)
    from app.services.auth import revoke_user_sessions
    revoke_user_sessions(user, db)
    db.commit()
    resp = R.success(msg="密码已更新，请重新登录")
    resp.delete_cookie("opennoter_token", path="/")
    return resp


# ---------------- OIDC ----------------

@router.get("/oidc/enabled")
def oidc_enabled():
    return R.success(data={"enabled": oidc.oidc_enabled(), "issuer": os.getenv("OIDC_ISSUER", "")})


@router.get("/config")
def auth_config():
    """前端启动时读一次：决定是否需要走登录流程、登录页显示哪些入口。"""
    from app.services.auth import auth_enforced

    return R.success(data={
        "enforced": auth_enforced(),
        "oidc_enabled": oidc.oidc_enabled(),
        "issuer": os.getenv("OIDC_ISSUER", ""),
    })


@router.get("/oidc/login")
def oidc_login():
    if not oidc.oidc_enabled():
        raise HTTPException(status_code=400, detail="未配置 OIDC")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    now = time.time()
    for key, (ts, _, _) in list(_OIDC_STATES.items()):
        if ts < now - _STATE_TTL:
            _OIDC_STATES.pop(key, None)
    if len(_OIDC_STATES) >= 10000:
        raise HTTPException(status_code=429, detail="登录请求过多，请稍后重试")
    _OIDC_STATES[state] = (now, nonce, verifier)

    response = RedirectResponse(oidc.build_authorize_url(state, nonce, verifier), status_code=302)
    response.set_cookie(_STATE_COOKIE, state, max_age=_STATE_TTL, httponly=True,
                        secure=_cookie_secure(), samesite="lax", path="/api/auth")
    return response


@router.get("/oidc/callback")
@router.get("/callback")  # 兼容 authentik 里登记的短路径
def oidc_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    """回调：code 换 id_token → 建/找本地用户 → 发本系统 JWT。"""
    if not code or not state:
        raise HTTPException(status_code=400, detail="回调参数缺失")
    browser_state = request.cookies.get(_STATE_COOKIE, "")
    if not browser_state or not secrets.compare_digest(state, browser_state):
        raise HTTPException(status_code=400, detail="登录会话不匹配，请重新登录")
    pending = _OIDC_STATES.pop(state, None)
    if pending is None or time.time() - pending[0] > _STATE_TTL:
        raise HTTPException(status_code=400, detail="state 校验失败或已过期")

    try:
        claims = oidc.exchange_code(code, nonce=pending[1], verifier=pending[2])
    except Exception as exc:  # 网络/验签/解析都可能失败
        raise HTTPException(status_code=400, detail="OIDC 校验失败，请重新登录") from exc

    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    if not sub or not email:
        raise HTTPException(status_code=400, detail="OIDC 返回的身份信息不完整")

    issuer = os.getenv("OIDC_ISSUER", "").strip().rstrip("/")
    user = db.query(User).filter(User.oidc_sub == sub, User.oidc_issuer.in_([issuer, issuer + "/"])).first()
    if not user:
        if claims.get("email_verified") is not True:
            raise HTTPException(status_code=403, detail="请先在身份服务中验证邮箱")
        # 同邮箱已存在则绑定，避免重复建号
        user = db.query(User).filter(User.email == email).first()
        if user:
            if user.oidc_sub:
                raise HTTPException(status_code=403, detail="该邮箱已绑定其他身份，请联系管理员")
            user.oidc_sub = sub
            user.oidc_issuer = os.getenv("OIDC_ISSUER", "")
        else:
            # ------------------------------------------------------------
            # 首个用户是否自动成为管理员（F39）
            # ------------------------------------------------------------
            # 旧行为：「用户表为空 → 第一个 OIDC 登录的人就是超管」。
            # 这在**公网可达**的部署上是可被抢注的：谁先完成一次 OIDC 登录，
            # 谁就拿到管理员。竞态窗口 = 从服务上线到真正的管理员首次登录之间。
            #
            # 现在改为**默认关闭**，需要显式开启才保留旧行为：
            #
            #   OPENNOTER_OIDC_FIRST_USER_ADMIN=true
            #
            # 推荐做法（默认路径）：
            #   1) 首次部署先不开放公网；
            #   2) 用 `python -m app.cli create-admin` 或直接写 DB 建第一个管理员；
            #   3) 之后再开放。
            is_first = db.query(User).count() == 0
            allow_first_admin = getbool("OIDC_FIRST_USER_ADMIN", False)
            user = User(
                email=email,
                name=claims.get("name") or claims.get("preferred_username"),
                oidc_sub=sub,
                oidc_issuer=os.getenv("OIDC_ISSUER", ""),
                is_superuser=is_first and allow_first_admin,
                is_active=True,
            )
            db.add(user)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    user.last_login_at = datetime.now(timezone.utc)
    db.flush()
    token = create_access_token(user, db)
    db.commit()
    db.refresh(user)
    from app.utils.env_compat import getenv

    target = getenv("POST_LOGIN_REDIRECT", "/")
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie(_STATE_COOKIE, path="/api/auth")
    _set_auth_cookie(response, token)
    return response


# ---------------- API Keys ----------------
# 密钥管理接口已迁到 app/routers/api_keys.py（REST 风格：/api/keys）。
# 旧路径 /api/auth/keys 由那边以兼容别名提供。

# ---------------- 管理员 ----------------

@router.get("/admin/users")
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.id.asc()).all()
    return R.success(data=[
        {
            **_user_payload(u),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        }
        for u in rows
    ])


@router.patch("/admin/users/{user_id}")
def admin_update_user(
    user_id: int,
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id and payload.get("is_active") is False:
        raise HTTPException(status_code=400, detail="不能停用自己")
    if user.id == admin.id and payload.get("is_superuser") is False:
        raise HTTPException(status_code=400, detail="不能取消自己的管理员身份")

    if "is_active" in payload:
        user.is_active = bool(payload["is_active"])
    if "is_superuser" in payload:
        user.is_superuser = bool(payload["is_superuser"])

    if "is_active" in payload or "is_superuser" in payload:
        from app.services.auth import revoke_user_sessions
        revoke_user_sessions(user, db)

    db.commit()
    return R.success(data=_user_payload(user))


@router.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """删除用户。

    账号与他的 API 密钥一起消失（密钥等价于该用户的口令，留着等于留后门）；
    历史任务保留，只解除归属 —— 任务与产物是已经生成的笔记，不随账号一起删。
    删掉的是当前管理员以外的人，所以系统里始终至少留着一个管理员。
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    # 密钥与它的用量明细先清掉 —— api_keys.user_id 虽然声明了 ondelete=CASCADE，
    # 但 SQLite 默认不执行外键约束，级联不会真的发生。
    key_ids = [row[0] for row in db.query(ApiKey.id).filter(ApiKey.user_id == user_id).all()]
    if key_ids:
        db.query(ApiKeyUsage).filter(ApiKeyUsage.key_id.in_(key_ids)).delete(
            synchronize_session=False
        )
    db.query(ApiKey).filter(ApiKey.user_id == user_id).delete(synchronize_session=False)

    # 历史任务保留，把归属摘掉
    db.query(VideoTask).filter(VideoTask.user_id == user_id).update(
        {VideoTask.user_id: None}, synchronize_session=False
    )

    # Preserve the submission ledger without retaining a dangling user reference.
    from app.db.models.task_attempts import TaskAttempt
    db.query(TaskAttempt).filter(TaskAttempt.user_id == user_id).update(
        {TaskAttempt.user_id: None}, synchronize_session=False
    )
    db.delete(user)
    db.commit()
    return R.success(msg="用户已删除")
