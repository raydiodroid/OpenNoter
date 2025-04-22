from app.gpt.gpt_factory import GPTFactory
from app.models.model_config import ModelConfig
from app.services.provider import ProviderService


class ModelService:
    @staticmethod
    def get_model_list(provider_id: int):
        provider=ProviderService.get_provider_by_id(provider_id)
        if not provider:
            return []
        config=ModelConfig(
            api_key=provider.api_key,
            base_url=provider.base_url,
            provider=provider.name,
            model_name='',
            name=provider.name,
        )
        GPT=GPTFactory().from_config(config)
        return GPT.list_models()

if __name__ == '__main__':
    print(ModelService.get_model_list(1))