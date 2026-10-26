"""BYOK 用户凭据管理接口（每个用户管自己的）。

## 为什么用 ``/me/`` 前缀，而不是给 ``/providers`` 加参数

``/providers`` 是管理员资源（写接口全部 ``require_admin``）。路径语义一旦
重载，很容易写出「普通用户不小心调到管理接口」的 bug。独立命名空间让权限
边界在路由表上一目了然。

## 安全要点（对照既有实现）

- **加密**：``utils/credential_crypto``（AES-GCM + AAD 绑定 user+capability）
- **掩码**：响应体**永不**包含明文 key，只给 ``***后4位``
- **掩码污染防御**：前端回填的是掩码，保存时若值里含 ``*`` 则不覆盖原 key
  （与 ``ProviderService.update_provider`` 里既有做法一致）
- **SSRF**：``base_url`` 是不可信输入，保存与探活前都过 ``assert_safe_url``
- **归属**：所有查询强制带 ``user_id``，不接受客户端传入

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | ``/me/credentials`` | 我的凭据 + 各能力位当前用的是谁的 |
| PUT | ``/me/credentials/{capability}`` | 新建或更新（upsert，天然幂等）|
| DELETE | ``/me/credentials/{capability}`` | 删除，回到站点默认 |
| POST | ``/me/credentials/{capability}/test`` | 连通性测试（用我的 key 真发一次）|
| POST | ``/me/credentials/discover-models`` | 填完 url+key 后拉取可选模型列表 |
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models.user_credentials import (
    ALL_CAPABILITIES,
    CAP_DEFAULT,
    CAP_LLM,
    CAP_ASR,
    UserCredential,
)
from app.db.models.users import User
from app.services import credential_resolver
from app.services.auth import get_current_user
from app.services.byok_config import byok_config
from app.utils import credential_crypto
from app.utils.logger import get_logger
from app.utils.response import ResponseWrapper as R

logger = get_logger(__name__)

router = APIRouter()

# SSRF 守卫的超时/探测参数
_PROBE_TIMEOUT = 10.0
_DISCOVER_TIMEOUT = 15.0


# ---------------- 请求体 ----------------


class CredentialUpsertRequest(BaseModel):
    """PUT /me/credentials/{capability} 的请求体。

    三者全部必填：BYOK 的本质是换一整套「端点 + 密钥 + 模型」，
    只配其中一个没有意义（详见 ``db/models/user_credentials.py`` 的模块注释）。
    """

    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=1)
    model_name: str = Field(min_length=1, max_length=128)
    label: Optional[str] = Field(default=None, max_length=128)


class DiscoverModelsRequest(BaseModel):
    """还没保存时就有用：用户刚填完 url+key，想看看有哪些模型可选。"""

    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(min_length=1)


# ----------------  helpers  ----------------


def _require_byok_enabled() -> None:
    """``shared`` 模式下整个 BYOK 功能对用户不可见。

    返回 404 而不是 403：这个功能在 ``shared`` 模式下**不存在**，
    403 会暗示「有资源但你没权限」，反而暴露了功能形状。
    """
    if not byok_config.is_byok_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _validate_base_url(base_url: str) -> str:
    """校验并规范化用户填写的端点。

    SSRF 是这里唯一真正的安全面：用户能让服务器去请求任意地址。
    复用现成的 ``assert_safe_url``（``routers/provider.py`` 的 probe_endpoint 已用它），
    拦截私网 / 保留地址 / 非 http(s) scheme。
    """
    from app.utils.env_compat import getbool
    from app.utils.url_safety import UnsafeUrlError, assert_safe_url

    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="接口地址不能为空")

    # 与 probe_endpoint 同一开关：明确允许私网（如自建 Ollama）时才放行
    if not getbool("PROBE_ALLOW_PRIVATE", False):
        try:
            assert_safe_url(url, suffixes=())
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=f"接口地址不安全：{exc}") from exc
    return url


def _looks_masked(value: str) -> bool:
    """判断前端回传的是不是掩码值（含 ``*``）。

    与 ``ProviderService.update_provider`` 的防御一致：用户打开设置页时
    看到的是掩码，若他没重新输入直接保存，不能把掩码写进库。
    """
    return "*" in (value or "")


def _get_own(db: Session, user_id: int, capability: str) -> Optional[UserCredential]:
    return (
        db.query(UserCredential)
        .filter(
            UserCredential.user_id == user_id,
            UserCredential.capability == capability,
        )
        .first()
    )


def _check_capability(capability: str) -> str:
    cap = (capability or "").strip().lower()
    if cap not in ALL_CAPABILITIES:
        raise HTTPException(
            status_code=400,
            detail=f"未知的能力位: {capability}，可选值: {', '.join(ALL_CAPABILITIES)}",
        )
    return cap


# ----------------  端点  ----------------


@router.get("/me/credentials")
def list_my_credentials(user: User = Depends(get_current_user)):
    """我的凭据状态 + 各能力位当前用的是谁的。

    响应里带 ``mode``，前端据此决定显示什么 UI，不需要额外端点。
    ``shared`` 模式下也允许读（返回 mode=shared 且 list 为空），
    这样前端初始化逻辑统一，不用先探测模式再取数据。
    """
    try:
        status = credential_resolver.build_capability_status(user.id)
        status["credentials"] = (
            credential_resolver.list_user_credentials(user.id)
            if byok_config.is_byok_enabled()
            else []
        )
        return R.success(data=status)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"读取用户凭据失败: {exc}", exc_info=True)
        return R.error(msg="读取凭据失败")


@router.put("/me/credentials/{capability}")
def upsert_my_credential(
    capability: str,
    data: CredentialUpsertRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新建或更新某个能力位的凭据（upsert）。

    用 PUT + 路径参数而不是 POST + 列表：一个用户每种能力位只有一份凭据，
    upsert 语义更贴切，也省掉 id 管理。
    """
    _require_byok_enabled()
    cap = _check_capability(capability)

    if not byok_config.get()["allow_custom_base_url"]:
        # 站点禁止自定义端点时，拒绝保存（管理员可能只想让用户填 key 用内置端点）。
        # 当前阶段没有「内置端点白名单」，所以这条开关等价于「禁用 BYOK 写操作」。
        # 保留开关是为了将来加白名单时不用改 API 契约。
        raise HTTPException(
            status_code=403,
            detail="当前站点不允许自定义接口地址，请联系管理员",
        )

    base_url = _validate_base_url(data.base_url)
    model_name = (data.model_name or "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="模型名称不能为空")

    row = _get_own(db, user.id, cap)

    # ---- api_key：掩码回填时保留原值 ----
    if _looks_masked(data.api_key):
        if row is None:
            raise HTTPException(status_code=400, detail="请填写 API Key")
        # 不覆盖——用户没改 key，只改了其它字段
        sealed_key = row.api_key
    else:
        sealed_key = credential_crypto.seal(
            data.api_key.strip(), user_id=user.id, capability=cap
        )

    try:
        if row is None:
            row = UserCredential(
                id=uuid.uuid4().hex,
                user_id=user.id,
                capability=cap,
                base_url=base_url,
                api_key=sealed_key,
                model_name=model_name,
                label=(data.label or "").strip() or None,
            )
            db.add(row)
        else:
            row.base_url = base_url
            row.api_key = sealed_key
            row.model_name = model_name
            row.label = (data.label or "").strip() or None
            row.enabled = True
            row.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error(f"保存用户凭据失败 (user={user.id}, cap={cap}): {exc}", exc_info=True)
        return R.error(msg="保存凭据失败")

    logger.info(f"用户 {user.id} 更新了 {cap} 凭据（model={model_name}）")
    return R.success(
        data={"capability": cap, "configured": True},
        msg="凭据已保存",
    )


@router.delete("/me/credentials/{capability}")
def delete_my_credential(
    capability: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除某个能力位的凭据 → 该能力位回到站点默认。"""
    _require_byok_enabled()
    cap = _check_capability(capability)

    row = _get_own(db, user.id, cap)
    if row is None:
        return R.success(msg="凭据不存在（已是站点默认）")

    try:
        db.delete(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error(f"删除用户凭据失败: {exc}", exc_info=True)
        return R.error(msg="删除凭据失败")

    logger.info(f"用户 {user.id} 删除了 {cap} 凭据")
    return R.success(msg="已删除，该能力位将使用站点配置")


@router.post("/me/credentials/discover-models")
def discover_models(
    data: DiscoverModelsRequest,
    user: User = Depends(get_current_user),
):
    """用**尚未保存**的 url + key 拉取可选模型列表。

    复用现有能力：``GPTFactory.from_config`` 能用任意 (key, base_url) 造客户端，
    ``UniversalGPT.list_models()`` 直接打 ``/v1/models``。

    注意：不少自建 OpenAI 兼容网关不实现 ``/v1/models``（见
    ``services/model_fallback.py`` 的模块注释），所以**失败必须如实报错**，
    不能伪装成「成功的空列表」——那正是 ``model.py:106-135`` 记录过的坑。
    """
    _require_byok_enabled()
    base_url = _validate_base_url(data.base_url)

    import openai

    from app.gpt.gpt_factory import GPTFactory
    from app.models.model_config import ModelConfig

    try:
        config = ModelConfig(
            name="discover",
            provider="custom",
            api_key=data.api_key.strip(),
            base_url=base_url,
            model_name="",  # list_models 不需要具体模型
        )
        gpt = GPTFactory.from_config(config, timeout=_DISCOVER_TIMEOUT, max_retries=0)
        raw = gpt.list_models()
    except openai.AuthenticationError:
        raise HTTPException(status_code=400, detail="鉴权失败：API Key 无效或已过期")
    except openai.NotFoundError:
        raise HTTPException(
            status_code=400,
            detail="该端点没有 /models 接口，请手动填写模型名称",
        )
    except openai.APIConnectionError:
        raise HTTPException(status_code=400, detail="无法连接到该地址，请检查网络与接口地址")
    except openai.APITimeoutError:
        raise HTTPException(status_code=400, detail="拉取模型列表超时，请手动填写模型名称")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"拉取模型列表失败: {exc}")
        return R.error(
            msg=f"拉取模型列表失败：{type(exc).__name__}，请手动填写模型名称",
            code=400,
        )

    from app.services.model_fallback import normalize_models

    models = normalize_models(raw)
    names = [m.get("id") for m in models if m.get("id")]
    logger.info(f"用户 {user.id} 拉取到 {len(names)} 个模型 ({base_url})")
    return R.success(
        data={"models": names},
        msg=f"获取到 {len(names)} 个模型" if names else "该端点未返回任何模型，请手动填写",
    )


@router.post("/me/credentials/{capability}/test")
def test_my_credential(
    capability: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用**已保存**的凭据真发一次最小 completion，验证能否跑通。

    与 ``/providers/{id}/test`` 的区别：那个用站点 key（管理员专用），
    这个用当前用户自己的 key。开销参考既有实现：max_tokens=1、
    temperature=0，< 0.0001 美元、< 2s。
    """
    _require_byok_enabled()
    cap = _check_capability(capability)

    row = _get_own(db, user.id, cap)
    if row is None:
        # 允许 default 兜底（与 resolve() 的口径一致）
        row = _get_own(db, user.id, CAP_DEFAULT)
        if row is None:
            return R.error(msg="尚未配置该能力位的凭据", code=400)

    plain = credential_crypto.reveal(
        row.api_key, user_id=user.id, capability=row.capability
    )
    if not plain:
        return R.error(msg="凭据无法解密，请重新填写", code=400)

    started = time.time()
    try:
        from app.gpt.provider.OpenAI_compatible_provider import OpenAICompatibleProvider

        ok = OpenAICompatibleProvider.test_connection(
            api_key=plain,
            base_url=row.base_url,
            model=row.model_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"用户凭据连通性测试异常: {exc}")
        return R.error(msg=f"测试失败：{type(exc).__name__}", code=400)

    latency = int((time.time() - started) * 1000)
    if ok:
        return R.success(
            data={"ok": True, "latency_ms": latency, "model": row.model_name},
            msg="连接成功",
        )
    return R.error(msg="测试失败：请检查接口地址、API Key 与模型名称", code=400)
