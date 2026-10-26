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
    """返回 (provider_id, model_name)。

    缺省规则：provider 缺省时取第一个「已启用且有模型」的供应商；model 缺省时
    取该供应商下第一个已启用的模型。显式指定但不存在 / 未启用时立即抛错。

    ## BYOK 的 ``own:`` 前缀

    用户选择自己的模型时，``provider_id`` 形如 ``own:llm`` / ``own:default``。
    这类值**不是** ``providers`` 表里的记录，必须在这里短路掉，否则会被
    下面的存在性校验判成「供应商不存在」——前端下拉能选到、一提交就报错，
    错误信息还会误导人去查供应商配置。

    短路后只校验「该用户确实配了这个能力位的凭据」，然后把原值透传给
    ``credential_resolver`` 解析。
    """
    # ---- BYOK：用户自己的模型，不走站点 providers 表 ----
    if provider_id and provider_id.startswith("own:"):
        return _resolve_own_selection(provider_id, model_name, user_id)

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


def _resolve_own_selection(
    provider_id: str, model_name: Optional[str], user_id: Optional[int]
) -> Tuple[str, str]:
    """校验并透传 ``own:<capability>`` 形式的选择。

    只做两件事：
      1. 该用户确实配了这个能力位（或 ``default`` 兜底）的凭据；
      2. 模型名必填——用户自有端点的模型名无法从站点表推断。

    真正的凭据解密与端点选择交给 ``credential_resolver``，这里不重复。
    """
    from app.db.engine import SessionLocal
    from app.db.models.user_credentials import CAP_DEFAULT, UserCredential
    from app.services.byok_config import byok_config

    capability = provider_id.split(":", 1)[1].strip() or CAP_DEFAULT

    if user_id is None:
        raise ProviderError(
            "请先登录后再使用自有模型凭据", ProviderErrorEnum.NOT_FOUND
        )

    if not byok_config.is_byok_enabled():
        raise ProviderError(
            "当前站点未启用自带凭据功能", ProviderErrorEnum.NOT_FOUND
        )

    db = SessionLocal()
    try:
        wanted = [capability]
        if capability != CAP_DEFAULT:
            wanted.append(CAP_DEFAULT)
        found = (
            db.query(UserCredential)
            .filter(
                UserCredential.user_id == user_id,
                UserCredential.capability.in_(wanted),
                UserCredential.enabled.is_(True),
            )
            .count()
        )
    finally:
        db.close()

    if not found:
        raise ProviderError(
            "你的凭据不存在或已删除，请到「设置 → 模型配置 → 我的凭据」重新配置",
            ProviderErrorEnum.NOT_FOUND,
        )

    name = (model_name or "").strip()
    if not name:
        raise NoteError(
            "请指定模型名称（自有端点无法自动推断）",
            NoteErrorEnum.MODEL_NOT_FOUND,
        )

    return provider_id, name
