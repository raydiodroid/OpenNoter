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
    provider_id: Optional[str], model_name: Optional[str]
) -> Tuple[str, str]:
    """返回 (provider_id, model_name)。

    缺省规则：provider 缺省时取第一个「已启用且有模型」的供应商；model 缺省时
    取该供应商下第一个已启用的模型。显式指定但不存在 / 未启用时立即抛错。
    """
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
        return provider_id, available[0]["model_name"]

    if not models:
        raise NoteError(
            NoteErrorEnum.NO_AVAILABLE_MODEL.message, NoteErrorEnum.NO_AVAILABLE_MODEL
        )

    if model_name:
        hit = next((m for m in models if m["model_name"] == model_name), None)
        if hit is None:
            raise NoteError(
                NoteErrorEnum.MODEL_NOT_FOUND.message, NoteErrorEnum.MODEL_NOT_FOUND
            )
        return hit["provider_id"], model_name

    first = models[0]
    return first["provider_id"], first["model_name"]
