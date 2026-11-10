"""BYOK Step 3 的集成测试：模型列表合并 + ``own:`` 前缀选择 + 笔记链路。

覆盖设计评审时发现的核心逻辑断点：

- 用户配了凭据后，他的模型必须能出现在 ``GET /models`` 里；
- 选了 ``own:`` 前缀的模型，不能被「供应商不存在」的既有校验拦掉；
- ``run_note_task`` 必须能把 ``user_id`` 带到 ``NoteGenerator``
  （contextvars 在线程池里不传播，只能显式传参）。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'byok_it.db'}")
    monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "unit-test-it-secret-0001")
    monkeypatch.setenv("OPENNOTER_JWT_SECRET", "unit-test-it-jwt-0001")
    monkeypatch.chdir(tmp_path)

    from sqlalchemy import text

    from app.db.engine import get_engine
    from app.db.init_db import init_db

    init_db()
    # users 的配额列由 migrate_task_status.py 添加，create_all 不管（既有约定）
    with get_engine().begin() as conn:
        existing = {r[1] for r in conn.execute(text("PRAGMA table_info(users)"))}
        for col in ("max_concurrent_tasks", "daily_task_limit", "storage_quota_mb"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} INTEGER"))

    from app.db.engine import SessionLocal
    from app.db.models.models import Model
    from app.db.models.providers import Provider
    from app.db.models.users import User

    # 幂等插入：模块级 engine 在进程内复用，同一 tmp 库可能被多个用例碰到
    db = SessionLocal()
    try:
        if db.query(Provider).filter(Provider.id == "site-p").first() is None:
            db.add(
                Provider(
                    id="site-p",
                    name="SiteGate",
                    logo="custom",
                    type="custom",
                    api_key="sk-SITE",
                    base_url="https://site.example/v1",
                    enabled=1,
                )
            )
        if db.query(Model).filter(Model.model_name == "site-model-a").first() is None:
            db.add(Model(provider_id="site-p", model_name="site-model-a"))
        if db.query(User).filter(User.id == 1).first() is None:
            db.add(
                User(id=1, email="alice@example.com", is_superuser=False, is_active=True)
            )
        db.commit()
    finally:
        db.close()

    from app.services.byok_config import byok_config

    byok_config.update(mode="optional")
    _clear_credentials()
    yield tmp_path


def _clear_credentials():
    """清空凭据表。

    模块级 SQLAlchemy engine 在同一个 pytest 进程内复用，同一个 tmp_path
    下的用例会互相看到对方写的凭据。每个用例开始前清一次，保证隔离。
    """
    from app.db.engine import SessionLocal
    from app.db.models.user_credentials import UserCredential

    db = SessionLocal()
    try:
        db.query(UserCredential).delete()
        db.commit()
    finally:
        db.close()


def _add_own_credential(user_id=1, capability="llm", model="my-model-x"):
    import uuid

    from app.db.engine import SessionLocal
    from app.db.models.user_credentials import UserCredential
    from app.utils import credential_crypto as cc

    db = SessionLocal()
    try:
        row = (
            db.query(UserCredential)
            .filter(
                UserCredential.user_id == user_id,
                UserCredential.capability == capability,
            )
            .first()
        )
        sealed = cc.seal("sk-USER", user_id=user_id, capability=capability)
        if row is None:
            db.add(
                UserCredential(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    capability=capability,
                    base_url="https://api.deepseek.com/v1",
                    api_key=sealed,
                    model_name=model,
                )
            )
        else:
            row.api_key = sealed
            row.model_name = model
        db.commit()
    finally:
        db.close()


class TestModelListMerge:
    def test_own_model_appears_with_source(self, env):
        """用户配了自己的模型 → GET /models 里必须能选到它。"""
        from app.db.engine import SessionLocal
        from app.db.models.users import User
        from app.routers.model import list_models

        _add_own_credential(model="my-model-x")

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == 1).first()
        finally:
            db.close()

        resp = list_models(user=user)
        import json

        payload = json.loads(resp.body)
        items = payload["data"]

        names = {i["model_name"]: i for i in items}
        assert "site-model-a" in names, "站点模型应保留"
        assert "my-model-x" in names, "用户自有模型应出现"

        assert names["site-model-a"]["source"] == "shared"
        assert names["my-model-x"]["source"] == "own"
        assert names["my-model-x"]["provider_id"] == "own:llm"

    def test_no_own_models_in_shared_mode(self, env):
        """shared 模式下不返回用户模型（功能关闭）。"""
        from app.db.engine import SessionLocal
        from app.db.models.users import User
        from app.routers.model import list_models
        from app.services.byok_config import byok_config

        _add_own_credential(model="my-model-x")
        byok_config.update(mode="shared")

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == 1).first()
        finally:
            db.close()

        import json

        items = json.loads(list_models(user=user).body)["data"]
        assert all(i.get("source") != "own" for i in items)

    def test_broken_credential_not_listed(self, env, monkeypatch):
        """解密失败的凭据不能出现在下拉里（它本来也用不了）。"""
        from app.db.engine import SessionLocal
        from app.db.models.users import User
        from app.routers.model import list_models

        _add_own_credential(model="my-model-x")
        monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "completely-new-secret-zzz")

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == 1).first()
        finally:
            db.close()

        import json

        items = json.loads(list_models(user=user).body)["data"]
        assert all(i.get("source") != "own" for i in items)


class TestOwnPrefixSelection:
    def test_own_prefix_accepted(self, env):
        """own:llm 不能被「供应商不存在」拦掉——这是最关键的短路。"""
        from app.services.model_selection import resolve_model_selection

        _add_own_credential(capability="llm")

        provider_id, model_name = resolve_model_selection(
            "own:llm", "my-model-x", user_id=1
        )
        assert provider_id == "own:llm"
        assert model_name == "my-model-x"

    def test_own_prefix_works_with_default_fallback(self, env):
        """只配了 default 时，own:llm 也应被接受。"""
        from app.services.model_selection import resolve_model_selection

        _add_own_credential(capability="default")

        provider_id, _ = resolve_model_selection("own:llm", "m", user_id=1)
        assert provider_id == "own:llm"

    def test_own_prefix_without_credential_rejected(self, env):
        """未配置凭据的用户选 own: 模型 → 明确报错。

        用一个**没有凭据**的独立用户，避免受其它用例写入的 default 凭据影响
        （模块级 DB 单例在进程内跨用例共享）。
        """
        from app.db.engine import SessionLocal
        from app.db.models.users import User
        from app.exceptions.provider import ProviderError
        from app.services.model_selection import resolve_model_selection

        db = SessionLocal()
        try:
            if db.query(User).filter(User.id == 99).first() is None:
                db.add(
                    User(id=99, email="nobody@example.com", is_superuser=False, is_active=True)
                )
                db.commit()
        finally:
            db.close()

        from app.services.credential_resolver import CredentialRequired
        with pytest.raises(CredentialRequired):
            resolve_model_selection("own:llm", "m", user_id=99)

    def test_own_prefix_uses_saved_model_when_omitted(self, env):
        from app.exceptions.note import NoteError
        from app.services.model_selection import resolve_model_selection

        _add_own_credential(capability="llm")
        assert resolve_model_selection("own:llm", "", user_id=1) == ("own:llm", "my-model-x")

    def test_site_model_still_works(self, env):
        """BYOK 不能打破原有的站点模型选择。"""
        from app.services.model_selection import resolve_model_selection

        provider_id, model_name = resolve_model_selection("site-p", "site-model-a")
        assert provider_id == "site-p"
        assert model_name == "site-model-a"


class TestNoteGeneratorCredential:
    def test_get_gpt_uses_own_credential(self, env):
        """NoteGenerator 带 user_id 时，_get_gpt 应解析到用户凭据。"""
        from app.services.note import NoteGenerator

        _add_own_credential(capability="llm", model="my-model-x")

        gen = NoteGenerator.__new__(NoteGenerator)  # 跳过重量级 __init__
        gen.user_id = 1

        captured = {}

        class FakeGPT:
            pass

        def _fake_from_config(config, **kw):
            captured["api_key"] = config.api_key
            captured["base_url"] = config.base_url
            captured["model_name"] = config.model_name
            return FakeGPT()

        import app.services.note as note_mod

        original = note_mod.GPTFactory
        try:
            class FakeFactory:
                @staticmethod
                def from_config(config, **kw):
                    return _fake_from_config(config, **kw)

            note_mod.GPTFactory = FakeFactory
            gen._get_gpt("my-model-x", "own:llm")
        finally:
            note_mod.GPTFactory = original

        assert captured["api_key"] == "sk-USER"
        assert captured["base_url"] == "https://api.deepseek.com/v1"
        assert captured["model_name"] == "my-model-x"

    def test_get_gpt_uses_site_credential_in_shared_mode(self, env):
        from app.services.byok_config import byok_config
        from app.services.note import NoteGenerator

        _add_own_credential()
        byok_config.update(mode="shared")

        gen = NoteGenerator.__new__(NoteGenerator)
        gen.user_id = 1

        captured = {}

        import app.services.note as note_mod

        original = note_mod.GPTFactory
        try:
            class FakeFactory:
                @staticmethod
                def from_config(config, **kw):
                    captured["api_key"] = config.api_key
                    captured["base_url"] = config.base_url
                    return object()

            note_mod.GPTFactory = FakeFactory
            gen._get_gpt("site-model-a", "site-p")
        finally:
            note_mod.GPTFactory = original

        assert captured["api_key"] == "sk-SITE"
        assert captured["base_url"] == "https://site.example/v1"


class TestRunNoteTaskSignature:
    def test_run_note_task_accepts_user_id(self, env):
        """run_note_task 必须接收 user_id——contextvars 在线程池里传不过去。"""
        import inspect

        from app.routers.note import run_note_task

        params = inspect.signature(run_note_task).parameters
        assert "user_id" in params


class TestSelectionPolicies:
    def test_site_default_is_used_and_invalid_default_never_falls_back(self, env):
        from app.services.model_defaults import model_defaults
        from app.services.model_selection import resolve_model_selection
        from app.db.engine import SessionLocal
        from app.db.models.models import Model
        with SessionLocal.begin() as db:
            db.add(Model(provider_id='site-p', model_name='second-model'))
        model_defaults.set('site-p', 'second-model')
        assert resolve_model_selection(None, None) == ('site-p', 'second-model')
        with SessionLocal.begin() as db:
            db.query(Model).filter_by(model_name='second-model').delete()
        with pytest.raises(Exception):
            resolve_model_selection(None, None)

    def test_explicit_site_and_own_keep_complete_credential_pairs(self, env):
        from app.services.credential_resolver import resolve, CredentialRequired
        from app.services.model_selection import resolve_model_selection
        from app.services.byok_config import byok_config
        _add_own_credential(model='my-model')
        assert resolve_model_selection(None, None, user_id=1) == ('own:llm', 'my-model')
        assert resolve_model_selection('site-p', 'site-model-a', user_id=1) == ('site-p', 'site-model-a')
        shared = resolve(1, model_name='site-model-a', site_provider_id='site-p', source='shared')
        own = resolve(1, model_name='my-model', source='own')
        assert shared.source == 'shared' and shared.base_url == 'https://site.example/v1'
        assert own.source == 'own' and own.base_url != shared.base_url
        byok_config.update(model_mode='required')
        with pytest.raises(CredentialRequired):
            resolve_model_selection('site-p', 'site-model-a', user_id=1)
        with pytest.raises(CredentialRequired):
            resolve(1, model_name='site-model-a', site_provider_id='site-p', source='shared')

    def test_asr_mode_and_default_fallback_are_independent(self, env):
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve_asr, CredentialRequired, build_capability_status
        _add_own_credential(capability='default', model='common-model')
        byok_config.update(model_mode='shared', asr_mode='required', asr_use_default=True)
        assert resolve_asr(1, online=True).model_name == 'common-model'
        assert build_capability_status(1)['capabilities'][0]['source'] == 'shared'
        assert build_capability_status(1)['capabilities'][1]['source'] == 'own'
        byok_config.update(asr_use_default=False)
        with pytest.raises(CredentialRequired):
            resolve_asr(1, online=True)
        assert resolve_asr(1, online=False) is None
        _add_own_credential(capability='asr', model='speech-model')
        assert resolve_asr(1, online=True).model_name == 'speech-model'

    def test_url_allowlist_applies_to_existing_credentials(self, env):
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve, CredentialBroken
        _add_own_credential()
        own = resolve(1, source='own')
        byok_config.update(allow_custom_base_url=False, allowed_base_urls=['https://different.example/v1'])
        with pytest.raises(CredentialBroken):
            resolve(1, source='own')
        byok_config.update(allowed_base_urls=[own.base_url + '/'])
        assert resolve(1, source='own').base_url == own.base_url

    def test_cookie_fallback_can_be_disabled(self, env, monkeypatch):
        from app.services.byok_config import byok_config
        from app.services import credential_resolver as cr
        monkeypatch.setattr(cr, '_load_user_cookie', lambda *args: None)
        monkeypatch.setattr(cr, '_site_cookie', lambda *args: 'site-cookie')
        byok_config.update(cookie_mode='optional', allow_shared_cookie_fallback=False)
        assert cr.resolve_cookie(1, 'bilibili') is None
        byok_config.update(allow_shared_cookie_fallback=True)
        assert cr.resolve_cookie(1, 'bilibili') == 'site-cookie'

    def test_legacy_policy_keeps_effective_modes_during_partial_update(self, env):
        from app.services.byok_config import ByokConfigManager
        import json
        path = env / 'legacy.json'
        path.write_text(json.dumps({'model_mode': 'required', 'cookie_mode': 'optional'}))
        cfg = ByokConfigManager(str(path))
        assert cfg.get_asr_mode() == 'required'
        cfg.update(model_mode='shared')
        assert cfg.get_asr_mode() == 'required'
        assert cfg.get_mode() == 'shared'

    def test_default_api_permissions_and_no_secret_disclosure(self, env):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        from app.routers.model import router
        from app.services.auth import get_current_user, get_optional_user
        app = FastAPI()
        app.include_router(router, prefix='/api')
        client = TestClient(app)
        normal = SimpleNamespace(id=1, is_superuser=False)
        app.dependency_overrides[get_optional_user] = lambda: normal
        app.dependency_overrides[get_current_user] = lambda: normal
        body = {'provider_id': 'site-p', 'model_name': 'site-model-a'}
        assert client.patch('/api/model_defaults', json=body).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_superuser=True)
        response = client.patch('/api/model_defaults', json=body)
        assert response.status_code == 200
        assert client.get('/api/model_defaults').json()['data']['model_name'] == 'site-model-a'
        assert client.patch('/api/model_defaults', json={'provider_id': 'own:llm', 'model_name': 'x'}).status_code == 400
        status = client.get('/api/selection_status')
        assert status.status_code == 200
        assert status.json()['data']['llm']['source'] == 'shared'
        assert 'sk-SITE' not in status.text and 'api_key' not in status.text

    def test_ambiguous_model_name_requires_provider_and_asr_slot_is_not_llm(self, env):
        from app.db.engine import SessionLocal
        from app.db.models.providers import Provider
        from app.db.models.models import Model
        from app.services.model_selection import resolve_model_selection
        with SessionLocal.begin() as db:
            db.add(Provider(id='other-p', name='Other', logo='custom', type='custom', api_key='', base_url='https://other.example/v1', enabled=1))
            db.add(Model(provider_id='other-p', model_name='site-model-a'))
        with pytest.raises(Exception, match='多个供应商'):
            resolve_model_selection(None, 'site-model-a')
        with pytest.raises(Exception, match='转写凭据'):
            resolve_model_selection('own:asr', 'speech', user_id=1)

    def test_discovery_obeys_allowlist_before_network_access(self, env, monkeypatch):
        from app.routers.me_credentials import _validate_base_url
        from app.services.byok_config import byok_config
        from fastapi import HTTPException
        monkeypatch.setenv('OPENNOTER_PROBE_ALLOW_PRIVATE', '1')
        byok_config.update(allow_custom_base_url=False, allowed_base_urls=['https://allowed.example/v1'])
        assert _validate_base_url('https://allowed.example/v1/') == 'https://allowed.example/v1'
        with pytest.raises(HTTPException) as exc:
            _validate_base_url('https://denied.example/v1')
        assert exc.value.status_code == 403
