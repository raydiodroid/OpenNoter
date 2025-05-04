from kombu import uuid

from app.db.provider_dao import (
    insert_provider,
    init_provider_table,
    get_all_providers,
    get_provider_by_name,
    get_provider_by_id,
    update_provider,
    delete_provider, get_enabled_providers,
)
from app.gpt.gpt_factory import GPTFactory
from app.models.model_config import ModelConfig


class ProviderService:

    @staticmethod
    def serialize_provider(row: tuple) -> dict:
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "logo": row[2],
            "type": row[3],
            "api_key": row[4],
            "base_url": row[5],
            "enabled": row[6],
            "created_at": row[7],
        }
    @staticmethod
    def serialize_provider_safe(row: tuple) -> dict:
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "logo": row[2],
            "type": row[3],
            "api_key": ProviderService.mask_key(row[4]),
            "base_url": row[5],
            "enabled": row[6],
            "created_at": row[7],
        }
    @staticmethod
    def mask_key(key: str) -> str:
        if not key or len(key) < 8:
            return '*' * len(key)
        return key[:4] + '*' * (len(key) - 8) + key[-4:]
    @staticmethod
    def add_provider( name: str, api_key: str, base_url: str, logo: str, type_: str, enabled: int = 1):
        try:
            id = uuid().lower()
            logo='custom'
            return insert_provider(id, name, api_key, base_url, logo, type_, enabled)
        except Exception as  e:
            print('创建模式失败',e)

    @staticmethod
    def get_all_providers():
        rows = get_all_providers()
        return [ProviderService.serialize_provider(row) for row in rows] if rows else []
    @staticmethod
    def get_all_providers_safe():
        rows = get_all_providers()
        return [ProviderService.serialize_provider(row) for row in rows] if rows else []
    @staticmethod
    def get_provider_by_name(name: str):
        row = get_provider_by_name(name)
        return ProviderService.serialize_provider(row)

    @staticmethod
    def get_provider_by_id(id: str):  # 已改为 str 类型
        row = get_provider_by_id(id)
        return ProviderService.serialize_provider(row)

    @staticmethod
    def get_provider_by_id_safe(id: str):  # 已改为 str 类型
        row = get_provider_by_id(id)
        return ProviderService.serialize_provider_safe(row)
            # all_models.extend(provider['models'])

    @staticmethod
    def update_provider(id: str, data: dict):
        try:
        # 过滤掉空值
            filtered_data = {k: v for k, v in data.items() if v is not None and k != 'id'}
            print('更新模型供应商',filtered_data)
            return update_provider(id, **filtered_data)

        except Exception as e:
            print('更新模型供应商失败：',e)

    @staticmethod
    def delete_provider(id: str):
        return delete_provider(id)
