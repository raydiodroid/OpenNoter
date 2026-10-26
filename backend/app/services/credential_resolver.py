"""凭据解析：一次上游调用到底该用谁的 key。

## 为什么要有这个模块

改造前，三条链路（笔记生成 / 聊天 / 转写）各自调用
``ProviderService.get_provider_by_id(provider_id)`` 去查全局表。BYOK 之后
「用谁的凭据」变成一个需要判断的事：查用户自己的？回退站点的？按模式报错？

如果让每条链路各写一套判断，口径必然分叉（历史上 ``monitor.py`` 用
``id != "groq"`` 猜转写供应商就是这么坏掉的）。所以收敛成**唯一入口**：
所有需要上游凭据的地方都调 ``resolve()``，不得另写分支。

## 行为矩阵（与 BYOK-PLAN-v4 §1 一致）

| 场景 | shared | optional | required |
|---|---|---|---|
| 未配凭据 | 用站点 | 用站点 | **报错** |
| 已配凭据 | 忽略用户，用站点 | 用用户 | 用用户 |
| 已配但解密失败 | 用站点 | **报错** | **报错** |
| 已配但 key 被上游拒绝 | 用站点 | **报错** | **报错** |

## 两条关键原则

**原则 1：``shared`` 模式短路**
第一行就是「不是 optional/required 就直接走站点」，保证 ``BYOK_MODE=shared``
时走的分支与改造前**逐行相同**。这是对既有部署的零破坏承诺。

**原则 2：「没配置」和「配置了但坏了」必须区分对待**
- 没配置 → 按模式决定（回退 or 报错）
- 配置了但坏了 → **一律报错，绝不静默回退**

静默回退会让用户以为在用自己的额度，实际在烧站点的钱，内容流向也变了。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.db.engine import SessionLocal
from app.db.models.user_credentials import (
    ALL_CAPABILITIES,
    CAP_ASR,
    CAP_DEFAULT,
    CAP_LLM,
    UserCredential,
)
from app.services.byok_config import MODE_SHARED, byok_config
from app.utils import credential_crypto
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CredentialRequired(Exception):
    """``required`` 模式下用户未配置凭据。

    接口层应转成 4xx 并把引导写进错误信息（不要 500）。
    """


class CredentialBroken(Exception):
    """用户配置了凭据，但它不可用（解密失败 / 被上游拒绝）。

    刻意与 ``CredentialRequired`` 分开：前者是「还没配」，后者是「配了但坏了」，
    前者可以给引导链接，后者要给排障信息。
    """


@dataclass(frozen=True)
class Credential:
    """一次上游调用所需的完整凭据。端点与密钥成对，不可拆开使用。"""

    api_key: str
    base_url: str
    model_name: Optional[str]
    source: str  # "own" | "shared"

    def __repr__(self) -> str:
        # dataclass 默认 repr 会把 api_key 打进日志——那是凭据泄漏。
        return (
            f"Credential(source={self.source!r}, base_url={self.base_url!r}, "
            f"model={self.model_name!r}, api_key=***)"
        )


# ---------------- 内部辅助 ----------------


def _load_credential(user_id: int, capability: str) -> Optional[UserCredential]:
    """按优先级取用户凭据：精确匹配 → default。"""
    wanted = [capability]
    if capability != CAP_DEFAULT:
        wanted.append(CAP_DEFAULT)

    db = SessionLocal()
    try:
        for cap in wanted:
            row = (
                db.query(UserCredential)
                .filter(
                    UserCredential.user_id == user_id,
                    UserCredential.capability == cap,
                    UserCredential.enabled.is_(True),
                )
                .first()
            )
            if row is not None:
                # 只返回标量快照：session 关闭后再读 ORM 实例属性会
                # DetachedInstanceError（既有代码里踩过这个坑）。
                return _Snapshot(
                    capability=row.capability,
                    base_url=row.base_url,
                    api_key=row.api_key,
                    model_name=row.model_name,
                    label=row.label,
                )
        return None
    finally:
        db.close()


@dataclass(frozen=True)
class _Snapshot:
    capability: str
    base_url: str
    api_key: str
    model_name: str
    label: Optional[str]


def _from_site(
    site_provider_id: Optional[str], model_name: Optional[str]
) -> Credential:
    """站点共享凭据。端点与密钥同样成对来自同一行 providers。"""
    from app.services.provider import ProviderService

    provider = ProviderService.get_provider_by_id(site_provider_id)
    if not provider:
        from app.enmus.exception import ProviderErrorEnum
        from app.exceptions.provider import ProviderError

        raise ProviderError(
            code=ProviderErrorEnum.NOT_FOUND.code,
            message=ProviderErrorEnum.NOT_FOUND.message,
        )

    return Credential(
        api_key=provider["api_key"],
        base_url=provider["base_url"],
        model_name=model_name,
        source="shared",
    )


# ---------------- 对外唯一入口 ----------------


def resolve(
    user_id: Optional[int],
    capability: str = CAP_LLM,
    *,
    model_name: Optional[str] = None,
    site_provider_id: Optional[str] = None,
) -> Credential:
    """解析本次调用用谁的凭据。

    :param user_id: 任务/请求的归属用户。``None``（匿名）一律走站点。
    :param capability: ``llm`` / ``asr`` / ``default``
    :param model_name: 调用方显式指定的模型名；为空时用凭据里存的
    :param site_provider_id: 回退站点时用哪个 provider
    :raises CredentialRequired: ``required`` 模式且用户未配置
    :raises CredentialBroken: 用户配置了但不可用
    """
    mode = byok_config.get_mode()

    # ---- 原则 1：不是 BYOK 模式 → 与改造前完全一致 ----
    if mode == MODE_SHARED or user_id is None:
        return _from_site(site_provider_id, model_name)

    own = _load_credential(int(user_id), capability)

    # ---- 没配置 ----
    if own is None:
        if mode == "required":
            raise CredentialRequired(
                "当前站点要求使用你自己的模型凭据，但你还没有配置。"
                "请到「设置 → 模型配置 → 我的凭据」里填写接口地址、API Key 与模型名称。"
            )
        return _from_site(site_provider_id, model_name)

    # ---- 配置了：解密。坏了一律报错（原则 2）----
    api_key = credential_crypto.reveal(
        own.api_key, user_id=int(user_id), capability=own.capability
    )
    if not api_key:
        raise CredentialBroken(
            "你配置的模型凭据无法解密（可能是站点加密密钥已更换）。"
            "请到「设置 → 模型配置 → 我的凭据」重新填写。"
        )

    return Credential(
        api_key=api_key,
        base_url=own.base_url,
        model_name=model_name or own.model_name,
        source="own",
    )


# ---------------- 供接口层使用的小工具 ----------------


def mask_secret(value: Optional[str]) -> str:
    """掩码展示：只留后 4 位。参考 OpenRouter 的 ``sk-...AbCd``。"""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "***" + value[-4:]


def list_user_credentials(user_id: int) -> list[dict]:
    """列出某用户的凭据（密钥掩码，永不回传明文）。

    响应里带 ``mode`` 是为了让前端知道该显示什么 UI——不需要额外端点。
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(UserCredential)
            .filter(UserCredential.user_id == user_id)
            .order_by(UserCredential.capability)
            .all()
        )
        items = []
        for row in rows:
            plain = credential_crypto.reveal(
                row.api_key, user_id=user_id, capability=row.capability
            )
            items.append(
                {
                    "capability": row.capability,
                    "configured": bool(plain),
                    # 解密失败时不回传任何片段，前端应提示"重新填写"
                    "masked_key": mask_secret(plain) if plain else "",
                    "base_url": row.base_url,
                    "model_name": row.model_name,
                    "label": row.label,
                    "broken": bool(row.api_key) and not plain,
                }
            )
        return items
    finally:
        db.close()


def build_capability_status(user_id: Optional[int]) -> dict:
    """给前端初始化用：每个能力位当前用的是「我的」还是「站点」。"""
    mode = byok_config.get()
    status: dict = {
        "mode": mode["mode"],
        "allow_custom_base_url": mode["allow_custom_base_url"],
        "byok_enabled": mode["mode"] != MODE_SHARED,
        "capabilities": [],
    }

    configured = {}
    if user_id is not None:
        for item in list_user_credentials(int(user_id)):
            configured[item["capability"]] = item

    # 对外只暴露 llm / asr 两个能力位；default 是它们的兜底，不单独展示
    for cap in (CAP_LLM, CAP_ASR):
        own = configured.get(cap) or configured.get(CAP_DEFAULT)
        status["capabilities"].append(
            {
                "capability": cap,
                # own 只有在真正配置了（且能解密）时才是 own
                "source": "own" if (own and own["configured"]) else "shared",
                "configured": bool(own and own["configured"]),
                "masked_key": (own or {}).get("masked_key", ""),
                "base_url": (own or {}).get("base_url"),
                "model_name": (own or {}).get("model_name"),
                "broken": bool(own and own.get("broken")),
            }
        )
    return status


__all__ = [
    "Credential",
    "CredentialRequired",
    "CredentialBroken",
    "resolve",
    "list_user_credentials",
    "build_capability_status",
    "mask_secret",
    "ALL_CAPABILITIES",
]
