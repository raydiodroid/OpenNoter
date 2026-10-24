from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.exceptions.provider import ProviderError
from app.models.model_config import ModelConfig
from app.services.model import ModelService
from app.db.models.users import User
from app.services.auth import get_optional_user, require_admin
from app.utils.response import ResponseWrapper as R
from app.services.provider import ProviderService

router = APIRouter(dependencies=[Depends(get_optional_user)])

#  新增 type 字段
class ProviderRequest(BaseModel):
    name: str
    api_key: str
    base_url: str
    logo: Optional[str] = None
    type: str

class TestRequest(BaseModel):
    id: str
    # 可选：指定用哪个 model 跑连通性测试；不传则用该 provider 在 DB 里的第一个模型
    model: Optional[str] = None

class ProviderTestRequest(BaseModel):
    """POST /providers/{id}/test 的请求体：provider id 从路径拿。"""

    model: Optional[str] = None
class ProviderUpdateRequest(BaseModel):
    id: str
    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    logo: Optional[str] = None
    type: Optional[str] = None
    enabled:Optional[int] = None

class ProviderPatchRequest(BaseModel):
    """PATCH /providers/{id} 的请求体：id 取自路径，body 里不用再带。"""

    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    logo: Optional[str] = None
    type: Optional[str] = None
    enabled: Optional[int] = None


# ---------- 规范化端点（API 文档里展示的就是这一组） ----------

@router.get("/providers")
def list_providers():
    """列出全部供应商（api_key 已脱敏）。"""
    try:
        res = ProviderService.get_all_providers_safe()
        return R.success(data=res)
    except Exception as e:
        return R.error(msg=e)


@router.get("/providers/{id}")
def read_provider(id: str):
    """读取单个供应商。"""
    try:
        res = ProviderService.get_provider_by_id_safe(id)
        if not res:
            return R.error(msg="供应商不存在", code=404, status_code=404)
        return R.success(data=res)
    except Exception as e:
        return R.error(msg=e)


@router.post("/providers")
def create_provider(data: ProviderRequest, admin: User = Depends(require_admin)):
    """新增供应商。"""
    try:
        res = ProviderService.add_provider(
            name=data.name,
            api_key=data.api_key,
            base_url=data.base_url,
            logo=data.logo,
            type_=data.type
        )
        return R.success(msg='添加模型供应商成功',data=res)
    except Exception as e:
        return R.error(msg=e)


@router.patch("/providers/{id}")
def patch_provider(id: str, data: ProviderPatchRequest, admin: User = Depends(require_admin)):
    """局部更新供应商，id 取自路径。"""
    payload = data.model_dump(exclude_none=True)
    if not payload:
        return R.error(msg='请至少填写一个参数')

    updated_provider = ProviderService.update_provider(id=id, data=payload)
    if updated_provider:
        return R.success(msg='更新模型供应商成功', data=updated_provider)
    else:
        return R.error(msg='更新模型供应商失败')


@router.delete("/providers/{id}")
def remove_provider(id: str, admin: User = Depends(require_admin)):
    """删除供应商。"""
    try:
        if not ProviderService.delete_provider(id):
            return R.error(msg="供应商不存在", code=404, status_code=404)
        return R.success(msg="供应商已删除")
    except Exception as e:
        return R.error(msg=str(e))


@router.get("/providers/{id}/models")
def list_provider_models(id: str):
    """列出某个供应商下已启用的模型（读本地 DB）。"""
    try:
        models = ModelService.get_enabled_models_by_provider(id)
        return R.success(models, msg="获取启用模型成功")
    except Exception as e:
        return R.error(f"获取启用模型失败: {e}")


@router.get("/providers/{id}/remote-models")
def list_remote_models(id: str):
    """列出供应商远端提供的模型列表（走该供应商的 /models 接口）。"""
    try:
        models = ModelService.get_all_models_by_id(id)
        return R.success(models, msg="获取远端模型列表成功")
    except Exception as e:
        return R.error(f"获取远端模型失败: {e}")


@router.post("/providers/{id}/test")
def test_provider(id: str, data: ProviderTestRequest = ProviderTestRequest()):
    """连通性测试：真实发一次 chat completion。"""
    ModelService().connect_test(id, model=data.model)
    return R.success(msg='连接成功')


# ---------- 旧路径（保留可用，但不出现在 API 文档里） ----------

@router.post("/add_provider", include_in_schema=False)
def add_provider(data: ProviderRequest, admin: User = Depends(require_admin)):
    """旧路径，等价于 POST /api/providers。"""
    return create_provider(data, admin)


@router.get("/get_all_providers", include_in_schema=False)
def get_all_providers():
    """旧路径，等价于 GET /api/providers。"""
    return list_providers()


@router.get("/get_provider_by_id/{id}", include_in_schema=False)
def get_provider_by_id(id: str):
    """旧路径，等价于 GET /api/providers/{id}。"""
    return read_provider(id)


@router.post("/update_provider", include_in_schema=False)
def update_provider(data: ProviderUpdateRequest, admin: User = Depends(require_admin)):
    """旧路径，等价于 PATCH /api/providers/{id}。"""
    payload = data.model_dump(exclude_none=True, exclude={"id"})
    if not payload:
        return R.error(msg='请至少填写一个参数')
    return patch_provider(data.id, ProviderPatchRequest(**payload), admin)


@router.post('/connect_test', include_in_schema=False)
def gpt_connect_test(data: TestRequest):
    """旧路径，等价于 POST /api/providers/{id}/test。"""
    return test_provider(data.id, data)


class EndpointProbeRequest(BaseModel):
    base_url: str
    api_key: Optional[str] = ""


@router.post('/probe_endpoint')
def probe_endpoint(data: EndpointProbeRequest, admin: User = Depends(require_admin)):
    """轻量探活：GET {base_url}/models。

    与 connect_test 的区别：不发起 chat completion，因此适用于 LLM、ASR、
    Embedding 等任意 OpenAI 兼容端点，也不消耗 token。返回耗时与模型数量，
    让配置页能直接判断「这个地址到底通不通」。

    出站前复用 assert_safe_url（suffixes=()：只拦 scheme / 内网 IP，不套
    图片 CDN 白名单），缩管理员内网探测面。本机 Ollama 等私网端点需显式
    打开 OPENNOTER_PROBE_ALLOW_PRIVATE=true。
    """
    import time

    import requests

    from app.utils.env_compat import getbool
    from app.utils.url_safety import UnsafeUrlError, assert_safe_url

    base = (data.base_url or "").strip().rstrip("/")
    if not base:
        return R.success(data={"ok": False, "latency": 0, "message": "未配置地址"})

    url = base + "/models"
    if not getbool("PROBE_ALLOW_PRIVATE", False):
        try:
            # suffixes=()：不限制域名，只做协议 + 私网/保留地址拦截
            assert_safe_url(url, suffixes=())
        except UnsafeUrlError as exc:
            return R.success(data={
                "ok": False,
                "latency": 0,
                "message": f"地址不安全：{exc}",
            })

    headers = {"Authorization": f"Bearer {data.api_key}"} if data.api_key else {}
    started = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        latency = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            return R.success(data={
                "ok": False,
                "latency": latency,
                "status": resp.status_code,
                "message": (resp.text or f"HTTP {resp.status_code}")[:120],
            })
        payload = resp.json()
        items = payload.get("data") if isinstance(payload, dict) else None
        return R.success(data={
            "ok": True,
            "latency": latency,
            "status": resp.status_code,
            "count": len(items) if isinstance(items, list) else 0,
        })
    except Exception as exc:  # 网络错误、超时、非 JSON 响应
        return R.success(data={
            "ok": False,
            "latency": 0,
            "message": str(exc)[:120],
        })
