"""API Key 管理接口。

密钥的生命周期是一台状态机，而不是一个布尔：
    active ─┬─→ expired   （到期自动失效）
            ├─→ disabled  （临时停用，可恢复）
            └─→ revoked   （人工吊销 / 轮换 / 判定泄露，终态）

轮换（rotate）是「新建一把 → 旧的可选宽限期 → 旧的转 revoked」，
而不是直接删了旧的（GitHub regenerate / Stripe 7 天宽限 / AWS 2 把重叠）。

API Key **等价于该用户的认证口令**：它能做什么，用户就能做什么。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models.api_keys import (
    ApiKey,
    ApiKeyUsage,
    REVOKE_MANUAL,
    REVOKE_ROTATED,
    STATUS_ACTIVE,
    STATUS_REVOKED,
)
from app.db.models.users import User
from app.services import api_key_runtime
from app.services.auth import (
    as_utc,
    effective_key_status,
    generate_api_key,
    get_current_user,
)
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

logger = get_logger(__name__)

router = APIRouter()

# 可选有效期（天）。不传 = 永不过期（用户明确要求的默认）
EXPIRY_PRESETS = (7, 30, 90, 180, 365)


# ---------------- 请求模型 ----------------

class KeyCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    # 不传 = 永不过期
    expires_days: Optional[int] = Field(default=None, ge=1, le=3650)
    scopes: Optional[dict] = None
    allowed_ips: Optional[list] = None
    rate_limit_rpm: Optional[int] = Field(default=None, ge=1, le=100000)


class KeyUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # None = 不改；传 0 表示改成「永不过期」
    expires_days: Optional[int] = Field(default=None, ge=0, le=3650)
    scopes: Optional[dict] = None
    allowed_ips: Optional[list] = None
    rate_limit_rpm: Optional[int] = Field(default=None, ge=1, le=100000)


class RotateRequest(BaseModel):
    # 宽限期天数：0 = 立即吊销旧的（疑似泄露时应该用这个）
    grace_days: int = Field(default=0, ge=0, le=90)


class RevokeRequest(BaseModel):
    reason: str = REVOKE_MANUAL


# ---------------- 序列化 ----------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    """时间统一输出 ISO 字符串（UTC，带时区）。"""
    normalized = as_utc(value)
    return normalized.isoformat() if normalized else None


def _key_payload(key: ApiKey, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    status = effective_key_status(key, now)

    # SQLite 取出来的是 naive datetime，统一归一成 UTC 再比较
    expires_at = as_utc(key.expires_at)
    grace_expires_at = as_utc(key.grace_expires_at)

    # 距到期天数（None = 永不过期）
    expires_in_days = None
    if expires_at is not None:
        delta = expires_at - now
        expires_in_days = max(0, delta.days)

    # 轮换宽限期内：状态是 revoked，但此刻还能用
    in_grace = (
        status == STATUS_REVOKED
        and key.revoked_reason == REVOKE_ROTATED
        and grace_expires_at is not None
        and grace_expires_at > now
    )

    return {
        "id": key.id,
        "key_id": key.key_id,
        "name": key.name,
        "description": key.description,
        "prefix": key.prefix,
        "last_four": key.last_four,
        "scopes": key.scopes or {},
        "allowed_ips": key.allowed_ips or [],
        "rate_limit_rpm": key.rate_limit_rpm,
        "status": status,
        "revoked": status == STATUS_REVOKED,
        "revoked_at": _iso(key.revoked_at),
        "revoked_reason": key.revoked_reason,
        "in_rotation_grace": in_grace,
        "grace_expires_at": _iso(grace_expires_at),
        "expires_at": _iso(expires_at),
        "expires_in_days": expires_in_days,
        "not_before": _iso(key.not_before),
        "rotated_from_id": key.rotated_from_id,
        "last_used_at": _iso(key.last_used_at),
        "last_used_ip": key.last_used_ip,
        "last_used_endpoint": key.last_used_endpoint,
        "use_count": key.use_count or 0,
        "created_at": _iso(key.created_at),
        "last_rotated_at": _iso(key.last_rotated_at),
    }


def _owned_key(key_id: int, user: User, db: Session) -> ApiKey:
    key = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.user_id == user.id).first()
    if not key:
        raise HTTPException(status_code=404, detail="密钥不存在")
    return key


def _expires_at_from_days(days: Optional[int]) -> Optional[datetime]:
    if not days:
        return None
    return datetime.now(timezone.utc) + timedelta(days=days)


# ---------------- 列表 / 创建 ----------------

@router.get("/keys")
def list_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """列出当前用户的全部密钥。

    每项都带实时算出来的 status —— 过期是随时间推移才生效的，不能只信库里的值。
    """
    rows = db.query(ApiKey).filter(ApiKey.user_id == user.id).order_by(ApiKey.id.desc()).all()
    now = datetime.now(timezone.utc)
    return R.success(data=[_key_payload(k, now) for k in rows])


@router.post("/keys")
def create_key(
    data: KeyCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建密钥。明文只在这次响应里出现一次。"""
    generated = generate_api_key()

    key = ApiKey(
        user_id=user.id,
        name=(data.name or "").strip() or "未命名",
        description=(data.description or "").strip() or None,
        key_id=generated["key_id"],
        prefix=generated["prefix"],
        last_four=generated["last_four"],
        key_hash=generated["key_hash"],
        hash_algo=generated["hash_algo"],
        scopes=data.scopes or None,
        allowed_ips=data.allowed_ips or None,
        rate_limit_rpm=data.rate_limit_rpm,
        status=STATUS_ACTIVE,
        revoked=False,
        expires_at=_expires_at_from_days(data.expires_days),
        use_count=0,
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    payload = _key_payload(key)
    payload["key"] = generated["plain"]
    payload["usage_hint"] = f'export OPENNOTER_API_KEY="{generated["plain"]}"'
    return R.success(msg="密钥已创建，明文只显示这一次", data=payload)


# ---------------- 编辑 ----------------

@router.patch("/keys/{key_id}")
def update_key(
    key_id: int,
    data: KeyUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """编辑密钥的名称、用途、有效期、作用域、IP 白名单、限流。"""
    key = _owned_key(key_id, user, db)
    now = datetime.now(timezone.utc)
    if effective_key_status(key, now) == STATUS_REVOKED:
        raise HTTPException(status_code=409, detail="已吊销的密钥不能编辑，请先恢复")

    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        key.name = name
    if data.description is not None:
        key.description = data.description.strip() or None
    if data.expires_days is not None:
        # 0 = 改成永不过期
        key.expires_at = _expires_at_from_days(data.expires_days or None)
    if data.scopes is not None:
        key.scopes = data.scopes or None
    if data.allowed_ips is not None:
        key.allowed_ips = data.allowed_ips or None
    if data.rate_limit_rpm is not None:
        key.rate_limit_rpm = data.rate_limit_rpm

    db.commit()
    db.refresh(key)
    return R.success(msg="已保存", data=_key_payload(key))


# ---------------- 轮换 ----------------

@router.post("/keys/{key_id}/rotate")
def rotate_key(
    key_id: int,
    data: RotateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """轮换密钥：新建一把继承配置的，旧的按需进入宽限期后吊销。

    宽限期是为了「先部署新 key、验证没问题、再废弃旧 key」这种平滑迁移。
    但**疑似泄露时不要用宽限期** —— grace_days=0 立即吊销旧的。
    """
    old = _owned_key(key_id, user, db)
    now = datetime.now(timezone.utc)
    if effective_key_status(old, now) == STATUS_REVOKED:
        raise HTTPException(status_code=409, detail="已吊销的密钥不能轮换")

    generated = generate_api_key()
    new_key = ApiKey(
        user_id=user.id,
        name=old.name,
        description=old.description,
        key_id=generated["key_id"],
        prefix=generated["prefix"],
        last_four=generated["last_four"],
        key_hash=generated["key_hash"],
        hash_algo=generated["hash_algo"],
        scopes=old.scopes,
        allowed_ips=old.allowed_ips,
        rate_limit_rpm=old.rate_limit_rpm,
        status=STATUS_ACTIVE,
        revoked=False,
        # 继承原有效期里剩余的部分，而不是重新给一整段时间
        expires_at=old.expires_at,
        rotated_from_id=old.id,
        last_rotated_at=now,
        use_count=0,
    )
    db.add(new_key)

    old.revoked = True
    old.status = STATUS_REVOKED
    old.revoked_at = now
    old.revoked_reason = REVOKE_ROTATED
    old.grace_expires_at = (now + timedelta(days=data.grace_days)) if data.grace_days else None
    old.last_rotated_at = now

    db.commit()
    db.refresh(new_key)

    payload = _key_payload(new_key)
    payload["key"] = generated["plain"]
    payload["usage_hint"] = f'export OPENNOTER_API_KEY="{generated["plain"]}"'
    payload["rotated"] = {
        "old_key_id": old.key_id,
        "old_name": old.name,
        "grace_expires_at": old.grace_expires_at.isoformat() if old.grace_expires_at else None,
    }
    return R.success(msg="已轮换，新密钥明文只显示这一次", data=payload)


# ---------------- 克隆 ----------------

@router.post("/keys/{key_id}/clone")
def clone_key(
    key_id: int,
    data: KeyCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """克隆密钥：复制配置，不复制明文。"""
    source = _owned_key(key_id, user, db)
    generated = generate_api_key()

    new_key = ApiKey(
        user_id=user.id,
        name=(data.name or source.name).strip() or "未命名",
        description=data.description if data.description is not None else source.description,
        key_id=generated["key_id"],
        prefix=generated["prefix"],
        last_four=generated["last_four"],
        key_hash=generated["key_hash"],
        hash_algo=generated["hash_algo"],
        scopes=source.scopes,
        allowed_ips=source.allowed_ips,
        rate_limit_rpm=source.rate_limit_rpm,
        status=STATUS_ACTIVE,
        revoked=False,
        expires_at=_expires_at_from_days(data.expires_days) if data.expires_days else source.expires_at,
        use_count=0,
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    payload = _key_payload(new_key)
    payload["key"] = generated["plain"]
    payload["usage_hint"] = f'export OPENNOTER_API_KEY="{generated["plain"]}"'
    return R.success(msg="已克隆，明文只显示这一次", data=payload)


# ---------------- 吊销 / 恢复 ----------------

@router.post("/keys/{key_id}/revoke")
def revoke_key_action(
    key_id: int,
    data: RevokeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """吊销密钥。记录保留以供审计，所以这是可恢复的。"""
    key = _owned_key(key_id, user, db)
    now = datetime.now(timezone.utc)
    if effective_key_status(key, now) == STATUS_REVOKED:
        return R.success(msg="密钥已经是吊销状态", data=_key_payload(key))

    key.revoked = True
    key.status = STATUS_REVOKED
    key.revoked_at = now
    key.revoked_reason = data.reason or REVOKE_MANUAL
    key.grace_expires_at = None
    db.commit()
    db.refresh(key)
    # 限流计数一并清掉，别让旧配额影响新密钥
    api_key_runtime.rate_limiter.reset(key.id)
    return R.success(msg="已吊销", data=_key_payload(key))


@router.post("/keys/{key_id}/restore")
def restore_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """恢复被吊销的密钥（人工吊销的可以恢复；轮换/泄露导致的不可）。"""
    key = _owned_key(key_id, user, db)
    if key.status != STATUS_REVOKED:
        raise HTTPException(status_code=409, detail="该密钥不是吊销状态")

    if key.revoked_reason in (REVOKE_ROTATED, "leaked"):
        raise HTTPException(
            status_code=409,
            detail="因轮换或泄露而吊销的密钥不可恢复，请重新创建",
        )

    key.revoked = False
    key.status = STATUS_ACTIVE
    key.revoked_at = None
    key.revoked_reason = None
    db.commit()
    db.refresh(key)
    return R.success(msg="已恢复", data=_key_payload(key))


# ---------------- 用量 ----------------

@router.get("/keys/{key_id}/usage")
def key_usage(
    key_id: int,
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """这把密钥的调用统计与最近明细。

    回答的是「我实际用了什么」—— 有了这个才能反推该给哪些 key 收权限、
    哪些可以删掉。
    """
    key = _owned_key(key_id, user, db)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(ApiKeyUsage)
        .filter(ApiKeyUsage.key_id == key.id, ApiKeyUsage.created_at >= since)
        .order_by(ApiKeyUsage.created_at.desc())
        .limit(200)
        .all()
    )

    by_day: dict[str, int] = {}
    by_endpoint: dict[str, int] = {}
    for row in rows:
        day = row.created_at.date().isoformat() if row.created_at else "未知"
        by_day[day] = by_day.get(day, 0) + 1
        by_endpoint[row.path] = by_endpoint.get(row.path, 0) + 1

    return R.success(data={
        "key_id": key.key_id,
        "days": days,
        "total": len(rows),
        "by_day": [{"date": d, "count": c} for d, c in sorted(by_day.items())],
        "by_endpoint": [
            {"path": p, "count": c}
            for p, c in sorted(by_endpoint.items(), key=lambda kv: -kv[1])[:10]
        ],
        "recent": [
            {
                "method": r.method,
                "path": r.path,
                "status_code": r.status_code,
                "ip": r.ip,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows[:50]
        ],
    })


# ---------------- 删除 ----------------

@router.delete("/keys/{key_id}")
def delete_key(
    key_id: int,
    purge: bool = Query(default=False, description="true = 彻底删除记录；false = 吊销（默认）"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """吊销密钥（默认）。加 ?purge=true 则彻底删除这条记录。

    默认走吊销而不是真删，是为了让「谁在什么时候用过这把 key」留有记录。
    """
    key = _owned_key(key_id, user, db)

    if purge:
        # SQLite 默认不开启 foreign_keys，ON DELETE CASCADE 不会生效，
        # 用量明细要自己清，否则会留下孤儿行白占空间。
        db.query(ApiKeyUsage).filter(ApiKeyUsage.key_id == key_id).delete(
            synchronize_session=False
        )
        db.delete(key)
        db.commit()
        api_key_runtime.rate_limiter.reset(key_id)
        return R.success(msg="已彻底删除")

    now = datetime.now(timezone.utc)
    if key.status != STATUS_REVOKED:
        key.revoked = True
        key.status = STATUS_REVOKED
        key.revoked_at = now
        key.revoked_reason = REVOKE_MANUAL
        db.commit()
    api_key_runtime.rate_limiter.reset(key_id)
    return R.success(msg="已吊销", data=_key_payload(key))


# ---------------- 旧路径兼容 ----------------

@router.get("/auth/keys", include_in_schema=False)
def legacy_list_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """旧路径。列出密钥。"""
    return list_keys(user, db)


@router.post("/auth/keys", include_in_schema=False)
def legacy_create_key(
    data: KeyCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """旧路径。创建密钥。"""
    return create_key(data, user, db)


@router.delete("/auth/keys/{key_id}", include_in_schema=False)
def legacy_delete_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """旧路径。吊销密钥。"""
    return delete_key(key_id, False, user, db)


@router.get("/keys/{key_id}", include_in_schema=False)
def get_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """旧路径。读单个密钥。"""
    return R.success(data=_key_payload(_owned_key(key_id, user, db)))
