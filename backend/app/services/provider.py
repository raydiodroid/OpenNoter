from app.db.provider_dao import (
    insert_provider,
    init_provider_table,
    get_all_providers,
    get_provider_by_name,
    get_provider_by_id,
    update_provider,
    delete_provider,
)

class ProviderService:

    @staticmethod
    def add_provider(name: str, api_key: str, base_url: str, logo: str, type_: str):
        return insert_provider(name, api_key, base_url, logo, type_)

    @staticmethod
    def get_all_providers():
        provider_list = []
        provider = get_all_providers()

        for i in provider:
            provider_list.append({
                "id": i[0],
                "name": i[1],
                "logo": i[2],
                "type": i[3],         # ✅ 加上类型
                "api_key": i[4],
                "base_url": i[5],
            })
        return provider_list

    @staticmethod
    def get_provider_by_name(name: str):
        return get_provider_by_name(name)

    @staticmethod
    def get_provider_by_id(id: int):
        return get_provider_by_id(id)

    @staticmethod
    def update_provider(
        id: int,
        name: str,
        api_key: str,
        base_url: str,
        logo: str,
        type_: str
    ):
        return update_provider(id, name, api_key, base_url, logo, type_)

    @staticmethod
    def delete_provider(id: int):
        return delete_provider(id)