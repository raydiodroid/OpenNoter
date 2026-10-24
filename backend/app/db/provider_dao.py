import json
import os
import sys
from app.db.models.providers import Provider
from app.utils.logger import get_logger
from app.db.engine import get_engine, Base, get_db

logger = get_logger(__name__)


def reveal_api_key(stored: str | None) -> str:
    """读库得到的 api_key → 明文。密文损坏/密钥不符时返回空串（不当密文用）。"""
    if not stored:
        return ""
    from app.utils import secret_box

    plain = secret_box.decrypt(stored)
    return plain if plain is not None else ""


def seal_api_key(plaintext: str | None) -> str:
    """写入前加密。已是密文则原样返回；空串不加密。"""
    if not plaintext:
        return ""
    from app.utils import secret_box

    if secret_box.is_encrypted(plaintext):
        return plaintext
    return secret_box.encrypt(plaintext)


def migrate_plaintext_api_keys() -> int:
    """把 providers.api_key 存量明文转成 enc:v1: 密文（幂等）。返回迁移条数。"""
    from app.utils import secret_box

    db = next(get_db())
    try:
        migrated = 0
        for row in db.query(Provider).all():
            raw = row.api_key or ""
            if not raw or secret_box.is_encrypted(raw):
                continue
            row.api_key = secret_box.encrypt(raw)
            migrated += 1
        if migrated:
            db.commit()
            logger.info(f"Migrated {migrated} provider api_key(s) plaintext → ciphertext")
        return migrated
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to migrate provider api_keys: {e}")
        raise
    finally:
        db.close()



def get_builtin_providers_path():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, 'builtin_providers.json')


def seed_default_providers():
    db = next(get_db())
    try:
        if db.query(Provider).count() > 0:
            logger.info("Providers already exist, skipping seed.")
            return

        json_path = get_builtin_providers_path()
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                providers = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read builtin_providers.json: {e}")
            return

        for p in providers:
            db.add(Provider(
                id=p['id'],
                name=p['name'],
                api_key=seal_api_key(p.get('api_key') or ''),
                base_url=p['base_url'],
                logo=p['logo'],
                type=p['type'],
                enabled=p.get('enabled', 1)
            ))
        db.commit()
        logger.info("Default providers seeded successfully.")
    except Exception as e:
        logger.error(f"Failed to seed default providers: {e}")
    finally:
        db.close()


def insert_provider(id: str, name: str, api_key: str, base_url: str, logo: str, type_: str, enabled: int = 1):
    db = next(get_db())
    try:
        provider = Provider(id=id, name=name, api_key=seal_api_key(api_key), base_url=base_url, logo=logo, type=type_, enabled=enabled)
        db.add(provider)
        db.commit()
        logger.info(f"Provider inserted successfully. id: {id}, name: {name}, type: {type_}")
        return id
    except Exception as e:
        logger.error(f"Failed to insert provider: {e}")
    finally:
        db.close()


def get_enabled_providers():
    db = next(get_db())
    try:
        return db.query(Provider).filter_by(enabled=1).all()
    finally:
        db.close()


def get_provider_by_name(name: str):
    db = next(get_db())
    try:
        return db.query(Provider).filter_by(name=name).first()
    finally:
        db.close()


def get_provider_by_id(id: str):
    db = next(get_db())
    try:
        return db.query(Provider).filter_by(id=id).first()
    finally:
        db.close()


def get_all_providers():
    db = next(get_db())
    try:
        return db.query(Provider).all()
    finally:
        db.close()


def update_provider(id: str, **kwargs):
    db = next(get_db())
    try:
        provider = db.query(Provider).filter_by(id=id).first()
        if not provider:
            logger.warning(f"Provider {id} not found for update.")
            return

        for key, value in kwargs.items():
            if hasattr(provider, key):
                if key == "api_key":
                    value = seal_api_key(value)
                setattr(provider, key, value)

        db.commit()
        logger.info(f"Provider updated successfully. id: {id}, updated_fields: {list(kwargs.keys())}")
    except Exception as e:
        logger.error(f"Failed to update provider: {e}")
    finally:
        db.close()


def delete_provider(id: str) -> bool:
    db = next(get_db())
    try:
        provider = db.query(Provider).filter_by(id=id).first()
        if not provider:
            return False
        db.delete(provider)
        db.commit()
        logger.info(f"Provider deleted successfully. id: {id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete provider: {e}")
        return False
    finally:
        db.close()