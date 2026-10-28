"""用户自带上游 Cookie（BYOK 的 Cookie 部分）接口。

## 为什么 Cookie 单独一组端点，不混进 ``/me/credentials``

两者维度不同：

- 凭据（``user_credentials``）是「**按能力位**」的：llm / asr / default；
- Cookie（``user_cookies``）是「**按平台**」的：bilibili / douyin / kuaishou / youtube。

强行合并会让请求体/响应体都变成一堆可选字段。分开更清晰，也方便前端
分两个区块渲染。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | ``/me/cookies`` | 各平台 Cookie 状态（永不返回内容）|
| PUT | ``/me/cookies/{platform}`` | 保存某平台 Cookie |
| DELETE | ``/me/cookies/{platform}`` | 删除，回到站点/无 Cookie |

## 校验

Cookie 是不可信输入，且**会被拼进 HTTP 头或 Netscape 文件**，所以必须：

1. 平台名在白名单内（``COOKIE_PLATFORMS``）；
2. 内容过 ``filter_downloader_cookies``——它会按平台格式（header / netscape）
   校验并剔除不在 allowlist 里的字段。这既是格式校验，也是安全过滤：
   下游会把它原样发给平台，多余的字段可能带来意外的副作用。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models.user_cookies import SUPPORTED_COOKIE_PLATFORMS, UserCookie, cookie_capability
from app.db.models.users import User
from app.services import credential_resolver
from app.services.auth import get_current_user
from app.services.byok_config import byok_config
from app.utils import credential_crypto
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

logger = get_logger(__name__)

router = APIRouter()


class CookieUpsertRequest(BaseModel):
    cookie: str = Field(min_length=1, max_length=32768)


def _require_byok_enabled() -> None:
    """``shared`` 模式下该功能不存在 → 404（不是 403，避免暗示功能形状）。"""
    if not byok_config.is_byok_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _check_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p not in SUPPORTED_COOKIE_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的平台: {platform}，可选: {', '.join(SUPPORTED_COOKIE_PLATFORMS)}",
        )
    return p


def _get_own(db: Session, user_id: int, platform: str) -> UserCookie | None:
    return (
        db.query(UserCookie)
        .filter(UserCookie.user_id == user_id, UserCookie.platform == platform)
        .first()
    )


@router.get("/me/cookies")
def list_my_cookies(user: User = Depends(get_current_user)):
    """各平台 Cookie 的配置状态。

    **永不返回 Cookie 内容**，只给「配了没 / 几条 / 归属（我的还是站点的）」。

    响应里带 ``mode``，前端据此决定是否显示该区块（与凭据接口一致）。
    """
    try:
        status = credential_resolver.build_cookie_status(user.id)
        return R.success(data=status)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"读取用户 Cookie 状态失败: {exc}", exc_info=True)
        return R.error(msg="读取 Cookie 状态失败")


@router.put("/me/cookies/{platform}")
def upsert_my_cookie(
    platform: str,
    data: CookieUpsertRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存某平台的 Cookie。

    内容会过 ``filter_downloader_cookies``：按平台格式校验并剔除白名单外的字段。
    过滤后为空说明格式完全不对，直接 400（否则存进去也用不了）。
    """
    _require_byok_enabled()
    plat = _check_platform(platform)

    from app.services.cookie_policy import cookie_format, filter_downloader_cookies

    try:
        filtered = filter_downloader_cookies(plat, data.cookie)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Cookie 格式不正确：{exc}") from exc

    if not filtered:
        hint = (
            "Cookie 为空或格式不正确。"
            + ("请粘贴浏览器 cookies.txt 的内容。" if cookie_format(plat) == "netscape" else
               "请粘贴形如 name=value; name2=value2 的请求头 Cookie。")
        )
        raise HTTPException(status_code=400, detail=hint)

    row = _get_own(db, user.id, plat)
    sealed = credential_crypto.seal(
        filtered, user_id=user.id, capability=cookie_capability(plat)
    )

    try:
        if row is None:
            db.add(
                UserCookie(
                    id=uuid.uuid4().hex,
                    user_id=user.id,
                    platform=plat,
                    cookie=sealed,
                )
            )
        else:
            row.cookie = sealed
            row.enabled = True
            row.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error(f"保存用户 Cookie 失败 (user={user.id}, platform={plat}): {exc}", exc_info=True)
        return R.error(msg="保存 Cookie 失败")

    # 日志里**绝不**出现 Cookie 内容，只记平台与用户
    logger.info(f"用户 {user.id} 更新了 {plat} Cookie")
    return R.success(msg="Cookie 已保存", data={"platform": plat, "configured": True})


@router.delete("/me/cookies/{platform}")
def delete_my_cookie(
    platform: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除某平台 Cookie → 回退到站点 Cookie（若有），否则按无 Cookie 处理。"""
    _require_byok_enabled()
    plat = _check_platform(platform)

    row = _get_own(db, user.id, plat)
    if row is None:
        return R.success(msg="Cookie 不存在（已是站点/无 Cookie）")

    try:
        db.delete(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error(f"删除用户 Cookie 失败: {exc}", exc_info=True)
        return R.error(msg="删除 Cookie 失败")

    logger.info(f"用户 {user.id} 删除了 {plat} Cookie")
    return R.success(msg="已删除")
