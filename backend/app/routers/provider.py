from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.utils.response import ResponseWrapper as R
from app.services.provider import ProviderService

router = APIRouter()

# ✅ 新增 type 字段
class ProviderRequest(BaseModel):
    name: str
    api_key: str
    base_url: str
    logo: str
    type: str

class ProviderUpdateRequest(BaseModel):
    id: int
    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    logo: Optional[str] = None
    type: Optional[str] = None

@router.post("/add_provider")
def add_provider(data: ProviderRequest):
    try:
        ProviderService.add_provider(
            name=data.name,
            api_key=data.api_key,
            base_url=data.base_url,
            logo=data.logo,
            type_=data.type
        )
        return R.success(msg='添加模型供应商成功')
    except Exception as e:
        return R.error(msg=e)

@router.get("/get_all_providers")
def get_all_providers():
    try:
        res = ProviderService.get_all_providers()
        return R.success(data=res)
    except Exception as e:
        return R.error(msg=e)

@router.get("/get_provider_by_id/{id}")
def get_provider_by_id(id: int):
    try:
        res = ProviderService.get_provider_by_id(id)
        return R.success(data=res)
    except Exception as e:
        return R.error(msg=e)

@router.get("/get_provider_by_name/{name}")
def get_provider_by_name(name: str):
    try:
        res = ProviderService.get_provider_by_name(name)
        return R.success(data=res)
    except Exception as e:
        return R.error(msg=e)

@router.post("/update_provider/")
def update_provider(data: ProviderUpdateRequest):
    try:
        if all(
            field is None
            for field in [data.name, data.api_key, data.base_url, data.logo, data.type]
        ):
            return R.error(msg='请至少填写一个参数')

        ProviderService.update_provider(
            id=data.id,
            name=data.name or '',
            api_key=data.api_key or '',
            base_url=data.base_url or '',
            logo=data.logo or '',
            type_=data.type or ''
        )
        return R.success(msg='更新模型供应商成功')
    except Exception as e:
        return R.error(msg=e)