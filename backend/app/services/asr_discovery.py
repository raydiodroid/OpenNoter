"""Read an ASR endpoint's catalog without storing drafts or forwarding saved keys elsewhere."""
import requests

from app.db.engine import SessionLocal
from app.db.models.asr_providers import AsrProvider
from app.db.provider_dao import reveal_api_key
from app.services.asr_providers import AsrProviderService
from app.services.asr_model_catalog import transcription_models
from app.utils.env_compat import getbool
from app.utils.url_safety import safe_get, UnsafeUrlError


def discover_asr_models(base_url, api_key='', provider_id=None):
    data = {'base_url': base_url, 'api_key': api_key}
    # Reuse URL/key validation, but keep the request key in memory (validation seals its copy).
    AsrProviderService.validate(data)
    base = base_url.strip().rstrip('/')
    key = api_key.strip()
    if provider_id:
        AsrProviderService.get(provider_id)
        with SessionLocal() as db:
            row = db.get(AsrProvider, provider_id)
            if row is None:
                raise ValueError('转写供应商不存在')
            if row.engine != 'groq':
                raise ValueError('此内置引擎不提供在线模型列表')
            if not key and row.api_key:
                if base != row.base_url.strip().rstrip('/'):
                    raise ValueError('API 地址已改变，请填写新地址的 API Key 后再拉取')
                key = reveal_api_key(row.api_key)
                if not key:
                    raise ValueError('密钥无法解密，请重新配置 API Key')
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    response = None
    try:
        url = base + '/models'
        if getbool('PROBE_ALLOW_PRIVATE', False):
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
        else:
            # Zero redirects: never forward the Authorization header to another endpoint.
            response = safe_get(url, suffixes=(), max_redirects=0, headers=headers, timeout=10)
        if response.status_code in (401, 403):
            raise ValueError('鉴权失败，请检查 API Key 和模型列表访问权限')
        if response.status_code in (404, 405):
            raise ValueError('该服务不支持拉取模型列表，请手动填写模型名称')
        if not 200 <= response.status_code < 300:
            raise ValueError(f'拉取模型列表失败（HTTP {response.status_code}），请手动填写')
        payload = response.json()
    except UnsafeUrlError:
        raise ValueError('该地址不允许访问，请检查 API 地址或站点私网访问配置') from None
    except requests.RequestException:
        raise ValueError('无法拉取模型列表，请检查网络和接口地址，或手动填写') from None
    except (TypeError, requests.exceptions.JSONDecodeError):
        raise ValueError('该服务未返回有效的模型列表，请手动填写') from None
    finally:
        if response is not None:
            response.close()
    items = payload.get('data', payload.get('models')) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError('该服务未返回有效的模型列表，请手动填写')
    return {'models': transcription_models(items)}
