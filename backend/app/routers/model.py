from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db.models.users import User
from app.services.model import ModelService
from app.services.auth import get_optional_user, require_admin
from app.utils.response import ResponseWrapper as R
router = APIRouter(dependencies=[Depends(get_optional_user)])
modelService = ModelService()
class CreateModelRequest(BaseModel):
    provider_id: str
    model_name: str

# 返回体：模型信息
class ModelItem(BaseModel):
    id: int
    model_name: str


# ---------- 规范化端点（API 文档里展示的就是这一组） ----------

@router.get("/models")
def list_models(user: User | None = Depends(get_optional_user)):
    """列出可选模型：站点模型 + 当前用户 BYOK 模型。

    BYOK 之后，模型下拉必须能选到「用户自己配的模型」——否则用户填了
    ``model_name`` 却在前端看不到它（这是设计评审里发现的核心逻辑断点）。

    返回项带 ``source`` 字段：
      - ``shared``：站点 providers 表里的模型（管理员配置）
      - ``own``   ：当前用户凭据里的模型（``id`` 形如 ``own:llm``）

    ``provider_id`` 用 ``own:<capability>`` 字符串前缀承载，避免改动现有
    API 契约（它本来就是 str），也避免给 ``user_credentials`` 造一套 id 空间。
    """
    try:
        models = modelService.get_all_models(True)

        # 给站点模型打上来源标记。老前端不认识这个字段也不会坏。
        for item in models:
            item.setdefault("source", "shared")

        if user is not None:
            models.extend(_own_models(user))

        return R.success(models, msg="获取模型列表成功")
    except Exception as e:
        return R.error(e)


def _own_models(user: User) -> list[dict]:
    """把当前用户凭据里的模型包装成与站点模型同形的条目。

    只暴露 ``llm`` 能力位——ASR 不走模型下拉。
    解密失败的凭据**不**出现在列表里（它本来也用不了），
    但仍会通过 ``/api/me/credentials`` 的 ``broken`` 标记提示用户重填。
    """
    from app.services.byok_config import byok_config

    if not byok_config.is_byok_enabled():
        return []

    from app.db.engine import SessionLocal
    from app.db.models.user_credentials import CAP_ASR, CAP_DEFAULT, CAP_LLM, UserCredential
    from app.utils import credential_crypto

    out: list[dict] = []
    db = SessionLocal()
    try:
        rows = (
            db.query(UserCredential)
            .filter(
                UserCredential.user_id == user.id,
                UserCredential.enabled.is_(True),
                UserCredential.capability.in_((CAP_LLM, CAP_DEFAULT)),
            )
            .all()
        )
        for row in rows:
            # 只收能解密的（解不开的留在设置页让用户重填）
            if not credential_crypto.reveal(
                row.api_key, user_id=user.id, capability=row.capability
            ):
                continue
            out.append(
                {
                    # 用字符串 id 表示来源：own:llm / own:default
                    "id": f"own:{row.capability}",
                    "provider_id": f"own:{row.capability}",
                    "model_name": row.model_name,
                    "source": "own",
                    "label": row.label,
                }
            )
    finally:
        db.close()

    # 同一个模型名只保留一条（default 与 llm 都配了同名模型时）
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in out:
        if item["model_name"] in seen:
            continue
        seen.add(item["model_name"])
        deduped.append(item)
    return deduped


@router.get("/models/{model_id}")
def get_model(model_id: int):
    """读取单个模型。"""
    for model in modelService.get_all_models():
        if model.get("id") == model_id:
            return R.success(model)
    return R.error(msg="模型不存在", code=404, status_code=404)


@router.post("/models")
def create_model(data: CreateModelRequest, admin: User = Depends(require_admin)):
    """为某个供应商新增一个模型。"""
    success = ModelService.add_new_model(data.provider_id, data.model_name)
    if not success:
        return R.error("模型添加失败")
    return R.success(msg="模型添加成功")


@router.delete("/models/{model_id}")
def delete_model(model_id: int, admin: User = Depends(require_admin)):
    """删除模型。

    之前这里是 GET /models/delete/{model_id} —— 用 GET 删数据。爬虫、浏览器预取、
    任何「扫一遍链接」的工具都可能把它当成普通 GET 打过来。新接口用 DELETE，
    旧路径已下线。
    """
    try:
        success = modelService.delete_model_by_id(model_id)
        if success:
            return R.success(msg="模型删除成功")
        else:
            return R.error(msg="模型不存在或删除失败", code=404, status_code=404)
    except Exception as e:
        return R.error(f"删除模型失败: {e}")


# ---------- 旧路径（保留可用，但不出现在 API 文档里） ----------

@router.get("/model_list", include_in_schema=False)
def model_list():
    """旧路径，等价于 GET /api/models。"""
    return list_models()


@router.get("/model_list/{provider_id}", include_in_schema=False)
def model_list_by_provider(provider_id):
    """旧路径，等价于 GET /api/models?provider_id=...。"""
    return R.success(modelService.get_all_models_by_id(provider_id))


@router.get("/model_enable/{provider_id}", include_in_schema=False)
def get_enabled_models_by_provider(provider_id: str):
    """旧路径，等价于 GET /api/providers/{id}/models。"""
    try:
        models = ModelService.get_enabled_models_by_provider(provider_id)
        return R.success(models, msg="获取启用模型成功")
    except Exception as e:
        return R.error(f"获取启用模型失败: {e}")
