"""生成笔记时的模型选择与校验。

调用方可以显式给 provider_id + model_name，也可以只给一个，甚至都不给。
不校验的话，错的 provider_id 会一路走到异步任务里才炸：提交接口先返回
200 + task_id，调用方只能轮询一次才发现「供应商不存在/未保存」。这里把校验
提前到提交时，让参数错误当场以 4xx 返回。
"""

from typing import Optional, Tuple

from app.db.model_dao import get_all_models
from app.db.provider_dao import get_provider_by_id
from app.enmus.exception import NoteErrorEnum, ProviderErrorEnum
from app.exceptions.note import NoteError
from app.exceptions.provider import ProviderError


def resolve_model_selection(
    provider_id: Optional[str],
    model_name: Optional[str],
    *,
    user_id: Optional[int] = None,
) -> Tuple[str, str]:
    """Explicit source > personal automatic choice > persisted site default.

    Unconfigured legacy sites use their first enabled model in stable ID order.
    A configured default that becomes unavailable fails instead of changing source.
    """
    from app.services.byok_config import byok_config
    from app.services.credential_resolver import CredentialRequired, _load_credential
    from app.services.model_defaults import model_defaults

    if provider_id and provider_id.startswith("own:"):
        return _resolve_own_selection(provider_id, model_name, user_id)

    mode = byok_config.get_mode()
    if user_id is not None and not provider_id and not model_name and mode != "shared":
        own = _load_credential(user_id, "llm")
        if own is not None:
            return _resolve_own_selection("own:" + own.capability, None, user_id)
    if mode == "required" and user_id is not None:
        raise CredentialRequired("当前策略要求选择个人 AI 模型，请先配置个人凭据")

    if not provider_id and not model_name:
        default = model_defaults.get()
        provider_id, model_name = default["provider_id"], default["model_name"]

    # get_all_models 只返回「已启用供应商」名下的模型，正好是可用集合
    models = get_all_models()

    if provider_id:
        provider = get_provider_by_id(provider_id)
        if provider is None or not provider.enabled:
            raise ProviderError(
                ProviderErrorEnum.NOT_FOUND.message, ProviderErrorEnum.NOT_FOUND
            )
        available = [m for m in models if m["provider_id"] == provider_id]
        if not available:
            raise NoteError(
                NoteErrorEnum.NO_AVAILABLE_MODEL.message, NoteErrorEnum.NO_AVAILABLE_MODEL
            )
        if model_name:
            if not any(m["model_name"] == model_name for m in available):
                raise NoteError(
                    NoteErrorEnum.MODEL_NOT_FOUND.message, NoteErrorEnum.MODEL_NOT_FOUND
                )
            return provider_id, model_name
        default = model_defaults.get()
        if default["provider_id"] == provider_id:
            if not any(m["model_name"] == default["model_name"] for m in available):
                raise NoteError("默认模型已不可用，请重新选择", NoteErrorEnum.MODEL_NOT_FOUND)
            return provider_id, default["model_name"]
        return provider_id, available[0]["model_name"]

    if not models:
        raise NoteError(
            NoteErrorEnum.NO_AVAILABLE_MODEL.message, NoteErrorEnum.NO_AVAILABLE_MODEL
        )

    if model_name:
        hits = [m for m in models if m["model_name"] == model_name]
        if len({m["provider_id"] for m in hits}) > 1:
            raise NoteError("多个供应商提供同名模型，请同时选择供应商", NoteErrorEnum.MODEL_NOT_FOUND)
        hit = hits[0] if hits else None
        if hit is None:
            raise NoteError(
                NoteErrorEnum.MODEL_NOT_FOUND.message, NoteErrorEnum.MODEL_NOT_FOUND
            )
        return hit["provider_id"], model_name

    first = models[0]
    return first["provider_id"], first["model_name"]


def _resolve_own_selection(
    provider_id: str, model_name: Optional[str], user_id: Optional[int]
) -> Tuple[str, str]:
    """校验并透传 ``own:<capability>`` 形式的选择。

    只做两件事：
      1. 该用户确实配了这个能力位（或 ``default`` 兜底）的凭据；
      2. 模型名必填——用户自有端点的模型名无法从站点表推断。

    真正的凭据解密与端点选择交给 ``credential_resolver``，这里不重复。
    """
    from app.services.credential_resolver import resolve
    capability = provider_id.split(":", 1)[1]
    if capability not in ("llm", "default"):
        raise ProviderError("请选择笔记与问答凭据，转写凭据不能用作 AI 模型", ProviderErrorEnum.NOT_FOUND)
    credential = resolve(user_id, capability, model_name=model_name, source="own")
    if not credential.model_name:
        raise NoteError("请先配置个人模型名称", NoteErrorEnum.MODEL_NOT_FOUND)
    return provider_id, credential.model_name
