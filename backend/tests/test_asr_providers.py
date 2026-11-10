import json
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def registry(tmp_path, monkeypatch):
    saved = {k: v for k, v in sys.modules.items() if k == 'app' or k.startswith('app.')}
    for k in saved:
        sys.modules.pop(k, None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "asr.db"}')
    monkeypatch.setenv('OPENNOTER_JWT_SECRET', 'test-asr-secret')
    monkeypatch.setenv('OPENNOTER_AUTH_ENFORCE', 'true')
    from app.db.init_db import init_db
    init_db()
    from app.services.asr_providers import AsrProviderService
    yield AsrProviderService
    for k in list(sys.modules):
        if k == 'app' or k.startswith('app.'):
            sys.modules.pop(k, None)
    sys.modules.update(saved)


def create(registry, **kwargs):
    return registry.create(dict(name='Custom ASR', logo='cloud', base_url='https://asr.example/v1', model_name='custom-model', api_key='complete-secret-key', **kwargs))


def test_migrate_legacy_once_preserves_connection_and_active_behavior(registry):
    from app.db.engine import SessionLocal
    from app.db.models.providers import Provider
    from app.db.provider_dao import seal_api_key
    from app.services.transcriber_config_manager import TranscriberConfigManager
    TranscriberConfigManager().update_config('groq', groq_model='legacy-model')
    with SessionLocal.begin() as db:
        db.add(Provider(id='groq', name='Legacy', logo='groq', type='built-in', enabled=0,
                        base_url='https://legacy.example/v1', api_key=seal_api_key('legacy-secret')))
    entries = registry.list()
    assert len(entries) == 5
    legacy = registry.get('groq')
    assert legacy['enabled'] is True  # previous runtime ignored LLM enabled flag
    assert legacy['has_api_key'] and 'api_key' not in legacy
    cred = registry.credential('groq')
    assert (cred.base_url, cred.api_key, cred.model_name) == ('https://legacy.example/v1', 'legacy-secret', 'legacy-model')
    registry.update('groq', {'name': 'Renamed', 'model_name': 'new-model'})
    registry.seed()
    assert registry.get('groq')['name'] == 'Renamed'
    assert registry.credential('groq').model_name == 'new-model'


def test_custom_provider_is_encrypted_and_blank_key_preserved(registry):
    from app.db.engine import SessionLocal
    from app.db.models.asr_providers import AsrProvider
    from app.db.models.providers import Provider
    p = create(registry)
    assert p['id'].startswith('asr_') and not p['builtin'] and p['enabled']
    assert 'api_key' not in p
    with SessionLocal() as db:
        stored = db.get(AsrProvider, p['id']).api_key
        assert stored.startswith('enc:v1:') and 'complete-secret-key' not in stored
        assert db.get(Provider, p['id']) is None
    registry.update(p['id'], {'api_key': '', 'name': 'Renamed', 'logo': 'server'})
    assert registry.credential(p['id']).api_key == 'complete-secret-key'
    assert registry.get(p['id'])['logo'] == 'server'
    with pytest.raises(ValueError, match='掩码'):
        registry.update(p['id'], {'api_key': 'sk-****'})


def test_default_selection_and_disable_do_not_fall_back(registry, monkeypatch):
    from app.services.transcriber_config_manager import TranscriberConfigManager
    from app.services import credential_resolver as cr
    from app.routers.config import update_transcriber_config, TranscriberConfigRequest
    from fastapi import HTTPException
    p = create(registry)
    monkeypatch.setattr(cr.byok_config, 'get_asr_mode', lambda: 'shared')
    update_transcriber_config(TranscriberConfigRequest(transcriber_type=p['id']), SimpleNamespace(is_superuser=True))
    credential = cr.resolve_asr(None, online=True)
    assert (credential.api_key, credential.base_url, credential.model_name) == ('complete-secret-key', 'https://asr.example/v1', 'custom-model')
    with pytest.raises(ValueError, match='其他默认'):
        registry.delete(p['id'])
    registry.update(p['id'], {'enabled': False})
    assert TranscriberConfigManager().get_transcriber_type() == p['id']
    with pytest.raises(cr.CredentialBroken, match='停用'):
        cr.resolve_asr(None, online=True)
    with pytest.raises(HTTPException) as exc:
        update_transcriber_config(TranscriberConfigRequest(transcriber_type=p['id']), None)
    assert exc.value.status_code == 400
    update_transcriber_config(TranscriberConfigRequest(transcriber_type='bcut'), None)
    registry.delete(p['id'])
    assert not any(item['id'] == p['id'] for item in registry.list())


def test_own_credentials_override_disabled_site_provider(registry, monkeypatch):
    from app.services import credential_resolver as cr
    p = create(registry, enabled=False)
    monkeypatch.setattr(cr.byok_config, 'get_asr_mode', lambda: 'optional')
    monkeypatch.setattr(cr, '_load_credential', lambda *args: SimpleNamespace(capability='asr', api_key='sealed', base_url='https://own.example/v1', model_name='own-model'))
    monkeypatch.setattr(cr.credential_crypto, 'reveal', lambda *args, **kw: 'own-key')
    result = cr.resolve_asr(1, online=True, provider_id=p['id'])
    assert (result.api_key, result.base_url, result.model_name, result.source) == ('own-key', 'https://own.example/v1', 'own-model', 'own')


def test_disabled_local_engine_is_rejected_before_loading_runtime(registry, monkeypatch):
    from app.transcriber import transcriber_provider as tp
    registry.update('fast-whisper', {'enabled': False})
    monkeypatch.setattr(tp, 'get_whisper_transcriber', lambda *args, **kwargs: pytest.fail('Disabled runtime loaded'))
    with pytest.raises(ValueError, match='停用'):
        tp.get_transcriber('fast-whisper', require_ready=True)


def test_custom_provider_is_forwarded_to_actual_transcriber(registry, monkeypatch):
    from app.transcriber import transcriber_provider as tp
    from app.services import credential_resolver as cr
    monkeypatch.setattr(cr.byok_config, 'get_asr_mode', lambda: 'shared')
    monkeypatch.setattr(tp, 'GroqTranscriber', lambda credential: credential)
    p = create(registry)
    credential = tp.get_transcriber(p['id'], require_ready=True)
    assert credential.api_key == 'complete-secret-key' and credential.model_name == 'custom-model'


@pytest.mark.parametrize('url', ['ftp://example.com', 'https://', 'https://u:secret@example.com/v1', 'https://example.com/v1?key=secret', 'https://example.com/#secret'])
def test_invalid_endpoints_never_persist(registry, url):
    with pytest.raises(ValueError):
        registry.create(dict(name='Bad', logo='cloud', base_url=url, model_name='model'))
    assert len(registry.list()) == 5


def test_admin_only_api_and_secret_free_responses(registry):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers.asr_providers import router
    from app.services.auth import get_current_user
    app = FastAPI()
    app.include_router(router, prefix='/api')
    client = TestClient(app)
    body = dict(name='Cloud', base_url='https://api.example/v1', model_name='whisper', api_key='top-secret-key', logo='cloud')
    assert client.post('/api/asr/providers', json=body).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_superuser=False)
    assert client.post('/api/asr/providers', json=body).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_superuser=True)
    response = client.post('/api/asr/providers', json=body)
    assert response.status_code == 200
    p = response.json()['data']
    assert p['has_api_key'] is True and 'top-secret' not in response.text
    assert client.patch('/api/asr/providers/'+p['id'], json={'enabled': False}).json()['data']['enabled'] is False
    assert client.patch('/api/asr/providers/'+p['id'], json={'builtin': True}).status_code == 422
    assert client.delete('/api/asr/providers/groq').status_code == 400
    assert client.delete('/api/asr/providers/'+p['id']).status_code == 200
    assert client.patch('/api/asr/providers/missing', json={'name': 'x'}).status_code == 400
