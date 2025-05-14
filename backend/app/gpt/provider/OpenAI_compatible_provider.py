from typing import Optional, Union

from openai import OpenAI

class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str, model: Union[str, None]=None):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    @property
    def get_client(self):
        return self.client

    @staticmethod
    def test_connection(api_key: str, base_url: str) -> bool:
        print(api_key)
        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            client.models.list()
            return True
        except Exception as e:
            print(f"Error connecting to OpenAI API: {e}")
            return False