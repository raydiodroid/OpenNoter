from fastapi import APIRouter
from pydantic import BaseModel

from app.services.model import ModelService
from app.utils.response import ResponseWrapper as R
router = APIRouter()
modelService = ModelService()
class CreateModelRequest(BaseModel):
    provider_id: str
    model_name: str

# 返回体：模型信息
class ModelItem(BaseModel):
    id: int
    model_name: str
@router.get("/model_list")
def model_list():
    try:
        return R.success(modelService.get_all_models(True),msg="获取模型列表成功")
    except Exception as e:
        return R.error(e)

@router.get("/model_list/{provider_id}")
def model_list(provider_id):
    try:
        return R.success(modelService.get_all_models_by_id(provider_id))
    except Exception as e:
        return R.error(e)

@router.post("/models")
def create_model(data: CreateModelRequest):
    success = ModelService.add_new_model(data.provider_id, data.model_name)
    if not success:
        raise R.error("模型添加失败")
    return R.success(msg="模型添加成功")

