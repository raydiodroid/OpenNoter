from app.db.model_dao import insert_model, get_all_models
from app.db.provider_dao import get_enabled_providers
from app.gpt.gpt_factory import GPTFactory
from app.gpt.provider.OpenAI_compatible_provider import OpenAICompatibleProvider
from app.models.model_config import ModelConfig
from app.services.provider import ProviderService


class ModelService:

    @staticmethod
    def _build_model_config(provider: dict) -> ModelConfig:
        return ModelConfig(
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            provider=provider["name"],
            model_name='',
            name=provider["name"],
        )

    @staticmethod
    def get_model_list(provider_id: int, verbose: bool = False):
        provider = ProviderService.get_provider_by_id(provider_id)
        if not provider:
            return []

        try:
            config = ModelService._build_model_config(provider)
            gpt = GPTFactory().from_config(config)
            models = gpt.list_models()
            if verbose:
                print(f"[{provider['name']}] 模型列表: {models}")
            return models
        except Exception as e:
            print(f"[{provider['name']}] 获取模型失败: {e}")
            return []

    @staticmethod
    def get_all_models(verbose: bool = False):
        try:
            raw_models = get_all_models()
            if verbose:
                print(f"所有模型列表: {raw_models}")
            return ModelService._format_models(raw_models)
        except Exception as e:
            print(f"获取所有模型失败: {e}")
            return []
    @staticmethod
    def get_all_models_safe(verbose: bool = False):
        try:
            raw_models = get_all_models()
            if verbose:
                print(f"所有模型列表: {raw_models}")
            return ModelService._format_models(raw_models)
        except Exception as e:
            print(f"获取所有模型失败: {e}")
            return []
    @staticmethod
    def _format_models(raw_models: list) -> list:
        """
        格式化模型列表
        """
        formatted = []
        for model in raw_models:
            formatted.append({
                "id": model.get("id"),
                "provider_id": model.get("provider_id"),
                "model_name": model.get("model_name"),
                "created_at": model.get("created_at", None),  # 如果有created_at字段
            })
        return formatted
    @staticmethod
    def get_all_models_by_id(provider_id: str, verbose: bool = False):
        try:
            provider = ProviderService.get_provider_by_id(provider_id)

            models = ModelService.get_model_list(provider["id"], verbose=verbose)

            model_list={

                "models": models
            }

            return model_list
        except Exception as e:
            print(f"[{provider_id}] 获取模型失败: {e}")
            return []
    @staticmethod
    def connect_test(api_key: str, base_url: str) -> bool:
        try:
            return OpenAICompatibleProvider.test_connection(api_key=api_key, base_url=base_url)
        except Exception as e:
            print(f"连接测试失败：{e}")
            return False

    @staticmethod
    def add_new_model(provider_id: int, model_name: str) -> bool:
        try:
            # 先查供应商是否存在
            provider = ProviderService.get_provider_by_id(provider_id)
            if not provider:
                print(f"供应商ID {provider_id} 不存在，无法添加模型")
                return False

            # 插入模型
            insert_model(provider_id=provider_id, model_name=model_name)
            print(f"模型 {model_name} 已成功添加到供应商ID {provider_id}")
            return True
        except Exception as e:
            print(f"添加模型失败: {e}")
            return False

if __name__ == '__main__':
    # 单个 Provider 测试
    print(ModelService.get_model_list(1, verbose=True))

    # 所有 Provider 模型测试
    # print(ModelService.get_all_models(verbose=True))
