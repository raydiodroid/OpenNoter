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
def list_models():
    """列出全部已启用供应商下的模型。"""
    try:
        return R.success(modelService.get_all_models(True), msg="获取模型列表成功")
    except Exception as e:
        return R.error(e)


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
