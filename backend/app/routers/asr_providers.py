from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services.auth import require_admin
from app.services.asr_providers import AsrProviderService
from app.utils.response import ResponseWrapper as R

router = APIRouter(prefix='/asr/providers', dependencies=[Depends(require_admin)])


class CreateAsrProvider(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    logo: str = 'cloud'
    base_url: str = Field(min_length=1, max_length=2000)
    api_key: str = Field(default='', max_length=4096)
    model_name: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class UpdateAsrProvider(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=100)
    logo: str | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4096)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None


def invoke(fn, *args):
    try:
        return R.success(data=fn(*args))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DiscoverAsrModels(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(min_length=1, max_length=2000)
    api_key: str = Field(default='', max_length=4096)
    provider_id: str | None = None


@router.post('/discover-models')
def discover_models(data: DiscoverAsrModels):
    from app.services.asr_discovery import discover_asr_models
    return invoke(discover_asr_models, data.base_url, data.api_key, data.provider_id)


@router.get('')
def list_providers():
    return invoke(AsrProviderService.list)


@router.post('')
def create_provider(data: CreateAsrProvider):
    return invoke(AsrProviderService.create, data.model_dump())


@router.patch('/{provider_id}')
def update_provider(provider_id: str, data: UpdateAsrProvider):
    return invoke(AsrProviderService.update, provider_id, data.model_dump(exclude_none=True))


@router.delete('/{provider_id}')
def delete_provider(provider_id: str):
    return invoke(AsrProviderService.delete, provider_id)
