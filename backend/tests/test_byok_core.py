"""BYOK Step 1 的回归测试：配置模式、凭据加密、凭据解析。

覆盖三条最容易出错的性质：

1. ``shared`` 模式**短路**——保证既有部署升级后行为逐行不变；
2. AAD 绑定——密文搬到别的用户/能力位必须解不开；
3. "没配置"与"配置了但坏了"必须区分对待——后者绝不静默回退。

测试隔离手段沿用 ``test_provider_api_key_encryption.py`` 的 sys.modules 快照法：
每个用例拿一个全新的 ``app`` 包与临时 SQLite，避免模块级单例串状态。
"""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _install_app_namespace():
    if "app" in sys.modules:
        return
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(BACKEND_DIR / "app")]
    pkg.__package__ = "app"
    sys.modules["app"] = pkg


@pytest.fixture()
def byok_env(tmp_path, monkeypatch):
    """全新 app 包 + 临时库 + 临时 config 目录。"""
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'byok.db'}")
    monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "unit-test-byok-secret-0001")
    monkeypatch.setenv("OPENNOTER_JWT_SECRET", "unit-test-jwt-secret-0001")
    # 配置路径相对 cwd：切到 tmp_path，保证不碰仓库里的 config/
    monkeypatch.chdir(tmp_path)

    try:
        _install_app_namespace()
        from app.db.init_db import init_db

        init_db()
        yield tmp_path
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _seed(tmp_path):
    """建站点 provider + 两个用户。"""
    from app.db.engine import SessionLocal
    from app.db.models.providers import Provider
    from app.db.models.users import User

    db = SessionLocal()
    try:
        db.add(
            Provider(
                id="site-p",
                name="SiteGate",
                logo="custom",
                type="custom",
                api_key="sk-SITE-KEY",
                base_url="https://site.example/v1",
                enabled=1,
            )
        )
        db.add(User(id=1, email="a@example.com", is_superuser=False, is_active=True))
        db.add(User(id=2, email="b@example.com", is_superuser=False, is_active=True))
        db.commit()
    finally:
        db.close()


def _add_credential(user_id: int, capability: str, key="sk-USER-KEY", model="m1"):
    from app.db.engine import SessionLocal
    from app.db.models.user_credentials import UserCredential
    from app.utils import credential_crypto as cc

    db = SessionLocal()
    try:
        db.add(
            UserCredential(
                id=uuid.uuid4().hex,
                user_id=user_id,
                capability=capability,
                base_url="https://user.example/v1",
                api_key=cc.seal(key, user_id=user_id, capability=capability),
                model_name=model,
            )
        )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- 配置


class TestByokConfig:
    def test_default_is_shared_when_file_missing(self, byok_env):
        """文件不存在必须回落 shared——这是升级零破坏的前提。"""
        from app.services.byok_config import MODE_SHARED, ByokConfigManager

        mgr = ByokConfigManager(str(byok_env / "config" / "byok.json"))
        assert mgr.get_mode() == MODE_SHARED
        assert mgr.is_byok_enabled() is False
        assert mgr.requires_byok() is False

    def test_switch_takes_effect_immediately(self, byok_env):
        """三种模式必须支持随时切换，且切换后立刻生效（不走重启）。"""
        from app.services.byok_config import (
            MODE_OPTIONAL,
            MODE_REQUIRED,
            MODE_SHARED,
            ByokConfigManager,
        )

        mgr = ByokConfigManager(str(byok_env / "config" / "byok.json"))
        for mode in (MODE_OPTIONAL, MODE_REQUIRED, MODE_SHARED):
            mgr.update(mode=mode)
            assert mgr.get_mode() == mode
            # 新建实例（模拟另一个进程/请求）也应立刻看到
            assert ByokConfigManager(str(byok_env / "config" / "byok.json")).get_mode() == mode

    def test_invalid_mode_rejected_on_write(self, byok_env):
        from app.services.byok_config import ByokConfigManager

        mgr = ByokConfigManager(str(byok_env / "config" / "byok.json"))
        with pytest.raises(ValueError):
            mgr.update(mode="bogus")

    def test_corrupt_file_falls_back_to_default(self, byok_env):
        """配置文件损坏不该让服务起不来，也不该切到危险模式。"""
        from app.services.byok_config import MODE_SHARED, ByokConfigManager

        path = byok_env / "config" / "byok.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")

        assert ByokConfigManager(str(path)).get_mode() == MODE_SHARED


# ---------------------------------------------------------------- 加密


class TestCredentialCrypto:
    def test_roundtrip(self, byok_env):
        from app.utils import credential_crypto as cc

        ct = cc.seal("sk-real-secret", user_id=1, capability="llm")
        assert ct.startswith("enc:v2:")
        assert cc.reveal(ct, user_id=1, capability="llm") == "sk-real-secret"

    def test_ciphertext_is_bound_to_user(self, byok_env):
        """AAD 绑定：把密文行复制给另一个用户，必须解不开。"""
        from app.utils import credential_crypto as cc

        ct = cc.seal("sk-real-secret", user_id=1, capability="llm")
        assert cc.reveal(ct, user_id=2, capability="llm") is None

    def test_ciphertext_is_bound_to_capability(self, byok_env):
        from app.utils import credential_crypto as cc

        ct = cc.seal("sk-real-secret", user_id=1, capability="llm")
        assert cc.reveal(ct, user_id=1, capability="asr") is None

    def test_tampered_ciphertext_rejected(self, byok_env):
        from app.utils import credential_crypto as cc

        ct = cc.seal("sk-real-secret", user_id=1, capability="llm")
        tampered = ct[:-6] + "AAAAAA"
        assert cc.reveal(tampered, user_id=1, capability="llm") is None

    def test_rotation_keeps_old_ciphertext_readable(self, byok_env, monkeypatch):
        """轮换：新密钥设 active、旧密钥设 fallback → 旧密文仍可解。"""
        from app.utils import credential_crypto as cc

        monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "secret-old-aaaaaaaaaaaa")
        old_ct = cc.seal("sk-old", user_id=1, capability="llm")

        monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "secret-new-bbbbbbbbbbbb")
        monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET_OLD", "secret-old-aaaaaaaaaaaa")

        assert cc.reveal(old_ct, user_id=1, capability="llm") == "sk-old"
        new_ct = cc.seal("sk-new", user_id=1, capability="llm")
        assert cc.reveal(new_ct, user_id=1, capability="llm") == "sk-new"

    def test_wrong_prefix_rejected(self, byok_env):
        """secret_box 的 enc:v1: 密文不能被当成 BYOK 凭据解开。"""
        from app.utils import credential_crypto as cc

        assert cc.reveal("enc:v1:whatever", user_id=1, capability="llm") is None


# ---------------------------------------------------------------- 解析


class TestCredentialResolver:
    def test_shared_mode_short_circuits(self, byok_env):
        """shared 模式下即便用户配了凭据也走站点——保证零行为变化。"""
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve

        _seed(byok_env)
        _add_credential(1, "default")
        byok_config.update(mode="shared")

        cred = resolve(1, "llm", site_provider_id="site-p")
        assert cred.source == "shared"
        assert cred.api_key == "sk-SITE-KEY"

    def test_optional_uses_own_when_configured(self, byok_env):
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve

        _seed(byok_env)
        _add_credential(1, "default")
        byok_config.update(mode="optional")

        cred = resolve(1, "llm", site_provider_id="site-p")
        assert cred.source == "own"
        assert cred.api_key == "sk-USER-KEY"
        assert cred.base_url == "https://user.example/v1"

    def test_optional_falls_back_to_site_when_missing(self, byok_env):
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve

        _seed(byok_env)
        byok_config.update(mode="optional")

        cred = resolve(2, "llm", site_provider_id="site-p")
        assert cred.source == "shared"

    def test_required_raises_when_missing(self, byok_env):
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import CredentialRequired, resolve

        _seed(byok_env)
        byok_config.update(mode="required")

        with pytest.raises(CredentialRequired):
            resolve(2, "llm", site_provider_id="site-p")

    def test_broken_credential_never_silently_falls_back(self, byok_env, monkeypatch):
        """原则 2：配了但坏了 → 报错，不能静默用站点额度。

        这是最要紧的一条——静默回退会让部署者以为用户在自付，实际在烧站点的钱。
        """
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import CredentialBroken, resolve

        _seed(byok_env)
        _add_credential(1, "default")

        # 换掉密钥，让既有密文解不开
        monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "brand-new-secret-cccccccc")
        byok_config.update(mode="optional")

        with pytest.raises(CredentialBroken):
            resolve(1, "llm", site_provider_id="site-p")

    def test_capability_falls_back_to_default(self, byok_env):
        """只配了 default 时，llm 和 asr 都应该能用它。"""
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve

        _seed(byok_env)
        _add_credential(1, "default")
        byok_config.update(mode="optional")

        assert resolve(1, "llm", site_provider_id="site-p").source == "own"
        assert resolve(1, "asr", site_provider_id="site-p").source == "own"

    def test_capability_specific_overrides_default(self, byok_env):
        """配了 asr 专用凭据时，asr 用自己的、llm 用 default。"""
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve

        _seed(byok_env)
        _add_credential(1, "default", key="sk-DEFAULT", model="m-default")
        _add_credential(1, "asr", key="sk-ASR", model="m-asr")
        byok_config.update(mode="optional")

        assert resolve(1, "llm", site_provider_id="site-p").api_key == "sk-DEFAULT"
        assert resolve(1, "asr", site_provider_id="site-p").api_key == "sk-ASR"

    def test_anonymous_always_uses_site(self, byok_env):
        """user_id=None（匿名）必须走站点，不能崩。"""
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve

        _seed(byok_env)
        byok_config.update(mode="optional")

        cred = resolve(None, "llm", site_provider_id="site-p")
        assert cred.source == "shared"

    def test_credential_repr_masks_key(self, byok_env):
        """日志里绝不能出现明文 key。"""
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve

        _seed(byok_env)
        _add_credential(1, "default")
        byok_config.update(mode="optional")

        cred = resolve(1, "llm", site_provider_id="site-p")
        assert "sk-USER-KEY" not in repr(cred)
        assert "api_key=***" in repr(cred)
