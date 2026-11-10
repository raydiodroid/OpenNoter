"""ASR inventory, separate from LLM providers; secrets never leave safe DTOs."""
from threading import RLock
from uuid import uuid4
from urllib.parse import urlsplit

from app.db.engine import SessionLocal
from app.db.models.asr_providers import AsrProvider
from app.db.models.providers import Provider
from app.db.provider_dao import seal_api_key, reveal_api_key
from app.services.transcriber_config_manager import TranscriberConfigManager

LOCK = RLock()
LOGOS = {'openai', 'groq', 'cloud', 'server', 'audio', 'whisper', 'apple', 'bcut', 'kuaishou'}
BUILTINS = (
    ('fast-whisper', 'Faster Whisper（本地）', 'whisper'),
    ('bcut', '必剪（在线）', 'bcut'),
    ('kuaishou', '快手（在线）', 'kuaishou'),
    ('groq', 'OpenAI 兼容端点', 'groq'),
    ('mlx-whisper', 'MLX Whisper（仅 macOS）', 'apple'),
)


class AsrProviderService:
    @staticmethod
    def seed():
        with LOCK, SessionLocal.begin() as db:
            for id_, name, logo in BUILTINS:
                if db.get(AsrProvider, id_) is not None:
                    continue
                row = AsrProvider(id=id_, name=name, engine=id_, logo=logo, builtin=True)
                if id_ == 'groq':
                    legacy = db.get(Provider, 'groq')
                    row.model_name = TranscriberConfigManager().get_config()['groq_model']
                    if legacy:
                        row.base_url = legacy.base_url
                        row.api_key = seal_api_key(legacy.api_key)
                        # Legacy execution ignored the LLM slot's enabled flag.
                        row.name = 'Groq' if 'groq.com' in (legacy.base_url or '') else '自建端点（OpenAI 兼容）'
                        row.logo = 'groq' if 'groq.com' in (legacy.base_url or '') else 'server'
                    else:
                        row.base_url = 'https://api.groq.com/openai/v1'
                db.add(row)

    @staticmethod
    def dto(row):
        return {k: getattr(row, k) for k in ('id', 'name', 'engine', 'logo', 'base_url', 'model_name', 'enabled', 'builtin')} | {'has_api_key': bool(row.api_key)}

    @classmethod
    def list(cls):
        cls.seed()
        with SessionLocal() as db:
            rows = {r.id: cls.dto(r) for r in db.query(AsrProvider).order_by(AsrProvider.id).all()}
            return [rows.pop(id_) for id_, _, _ in BUILTINS] + list(rows.values())

    @classmethod
    def get(cls, id_):
        cls.seed()
        with SessionLocal() as db:
            row = db.get(AsrProvider, id_)
            if row is None:
                raise ValueError('转写供应商不存在')
            return cls.dto(row)

    @classmethod
    def require_enabled(cls, id_):
        row = cls.get(id_)
        if not row['enabled']:
            raise ValueError('当前转写供应商已停用，请启用或选择其他默认供应商')
        return row

    @staticmethod
    def validate(data):
        for key in ('name', 'base_url', 'model_name', 'api_key'):
            if key in data:
                data[key] = data[key].strip()
        if 'name' in data and not data['name']:
            raise ValueError('请填写供应商名称')
        if 'logo' in data and data['logo'] not in LOGOS:
            raise ValueError('不支持的供应商图标')
        if 'base_url' in data:
            url = urlsplit(data['base_url'])
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('API 地址必须是有效的 HTTP(S) 地址，不能包含账号、密码、查询参数或片段')
        if 'model_name' in data and not data['model_name']:
            raise ValueError('请填写转写模型名称')
        # Empty input preserves the stored key; masked strings are never credentials.
        if 'api_key' in data:
            if not data['api_key']:
                data.pop('api_key')
            elif '*' in data['api_key']:
                raise ValueError('请输入完整的新 API Key，不能保存掩码')
            else:
                data['api_key'] = seal_api_key(data['api_key'])

    @classmethod
    def create(cls, data):
        data = dict(data)
        cls.validate(data)
        cls.seed()
        with LOCK, SessionLocal.begin() as db:
            row = AsrProvider(id='asr_' + uuid4().hex, engine='groq', builtin=False, **data)
            db.add(row)
            db.flush()
            return cls.dto(row)

    @classmethod
    def update(cls, id_, data):
        data = dict(data)
        cls.validate(data)
        cls.seed()
        with LOCK, SessionLocal.begin() as db:
            row = db.get(AsrProvider, id_)
            if row is None:
                raise ValueError('转写供应商不存在')
            if row.engine != 'groq' and {'base_url', 'api_key', 'model_name'} & data.keys():
                raise ValueError('此内置引擎不支持自定义连接参数')
            for key, value in data.items():
                setattr(row, key, value)
            db.flush()
            return cls.dto(row)

    @classmethod
    def delete(cls, id_):
        cls.seed()
        with LOCK, SessionLocal.begin() as db:
            row = db.get(AsrProvider, id_)
            if row is None:
                raise ValueError('转写供应商不存在')
            if row.builtin:
                raise ValueError('内置引擎不能删除，可使用开关停用')
            if TranscriberConfigManager().get_transcriber_type() == id_:
                raise ValueError('请先选择其他默认供应商，再删除当前供应商')
            db.delete(row)

    @classmethod
    def credential(cls, id_):
        from app.services.credential_resolver import Credential, CredentialBroken
        cls.seed()
        with SessionLocal() as db:
            row = db.get(AsrProvider, id_)
            if row is None or not row.enabled or row.engine != 'groq':
                raise CredentialBroken('在线转写供应商不存在或已停用')
            key = reveal_api_key(row.api_key)
            if row.api_key and not key:
                raise CredentialBroken('转写供应商密钥无法解密，请重新配置')
            return Credential(api_key=key, base_url=row.base_url, model_name=row.model_name, source='shared')
