"""Provider api_key 加密落库回归（复用 secret_box / Fernet）。"""

from __future__ import annotations

import sys
import types
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
def provider_db(tmp_path, monkeypatch):
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    monkeypatch.setenv("OPENNOTER_SECRET_KEY", "unit-test-provider-key-0001")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'providers.db'}")

    try:
        _install_app_namespace()
        from app.db.engine import Base, get_engine
        from app.db.models.providers import Provider  # noqa: F401

        Base.metadata.create_all(bind=get_engine())
        yield
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_insert_encrypts_api_key_on_disk(provider_db):
    from app.db.models.providers import Provider
    from app.db.engine import SessionLocal
    from app.db.provider_dao import insert_provider, reveal_api_key
    from app.utils import secret_box

    insert_provider(
        "p1", "Test", "sk-secret-value-123456", "https://example.com/v1", "custom", "custom", 1
    )

    db = SessionLocal()
    try:
        row = db.query(Provider).filter_by(id="p1").first()
        assert row is not None
        assert secret_box.is_encrypted(row.api_key)
        assert "sk-secret-value-123456" not in row.api_key
        assert reveal_api_key(row.api_key) == "sk-secret-value-123456"
    finally:
        db.close()


def test_update_encrypts_new_api_key(provider_db):
    from app.db.models.providers import Provider
    from app.db.engine import SessionLocal
    from app.db.provider_dao import insert_provider, update_provider, reveal_api_key
    from app.utils import secret_box

    insert_provider("p2", "T2", "old-key-aaaa", "https://example.com", "custom", "custom", 1)
    update_provider("p2", api_key="new-key-bbbb")

    db = SessionLocal()
    try:
        row = db.query(Provider).filter_by(id="p2").first()
        assert secret_box.is_encrypted(row.api_key)
        assert reveal_api_key(row.api_key) == "new-key-bbbb"
    finally:
        db.close()


def test_serialize_returns_plaintext(provider_db):
    from app.db.provider_dao import insert_provider, get_provider_by_id
    from app.services.provider import ProviderService

    insert_provider("p3", "T3", "plain-for-gpt", "https://example.com", "custom", "custom", 1)
    data = ProviderService.serialize_provider(get_provider_by_id("p3"))
    assert data["api_key"] == "plain-for-gpt"


def test_safe_serialize_masks_key(provider_db):
    from app.db.provider_dao import insert_provider, get_provider_by_id
    from app.services.provider import ProviderService

    insert_provider("p4", "T4", "abcdefghijklmnop", "https://example.com", "custom", "custom", 1)
    data = ProviderService.serialize_provider_safe(get_provider_by_id("p4"))
    assert data["api_key"] == "abcd********mnop"
    assert "efghijkl" not in data["api_key"]


def test_list_safe_does_not_leak_full_key(provider_db):
    from app.db.provider_dao import insert_provider
    from app.services.provider import ProviderService

    insert_provider("p5", "T5", "leak-me-not-xyz", "https://example.com", "custom", "custom", 1)
    rows = ProviderService.get_all_providers_safe()
    assert len(rows) == 1
    assert "leak-me-not-xyz" not in rows[0]["api_key"]
    assert "*" in rows[0]["api_key"]


def test_migrate_plaintext_api_keys(provider_db):
    from app.db.models.providers import Provider
    from app.db.engine import SessionLocal
    from app.db.provider_dao import migrate_plaintext_api_keys, reveal_api_key
    from app.utils import secret_box

    db = SessionLocal()
    try:
        db.add(
            Provider(
                id="legacy",
                name="Legacy",
                api_key="legacy-plain-key",
                base_url="https://example.com",
                logo="custom",
                type="custom",
                enabled=1,
            )
        )
        db.commit()
    finally:
        db.close()

    assert migrate_plaintext_api_keys() == 1
    assert migrate_plaintext_api_keys() == 0  # idempotent

    db = SessionLocal()
    try:
        row = db.query(Provider).filter_by(id="legacy").first()
        assert secret_box.is_encrypted(row.api_key)
        assert reveal_api_key(row.api_key) == "legacy-plain-key"
    finally:
        db.close()


def test_empty_api_key_not_encrypted(provider_db):
    from app.db.models.providers import Provider
    from app.db.engine import SessionLocal
    from app.db.provider_dao import insert_provider
    from app.utils import secret_box

    insert_provider("p6", "T6", "", "https://example.com", "custom", "custom", 1)
    db = SessionLocal()
    try:
        row = db.query(Provider).filter_by(id="p6").first()
        assert row.api_key == ""
        assert not secret_box.is_encrypted(row.api_key)
    finally:
        db.close()


def test_wrong_key_reveal_returns_empty(provider_db, monkeypatch):
    from app.db.provider_dao import insert_provider, reveal_api_key, get_provider_by_id

    insert_provider("p7", "T7", "will-be-lost", "https://example.com", "custom", "custom", 1)
    row = get_provider_by_id("p7")
    stored = row.api_key
    monkeypatch.setenv("OPENNOTER_SECRET_KEY", "totally-different-key")
    assert reveal_api_key(stored) == ""
