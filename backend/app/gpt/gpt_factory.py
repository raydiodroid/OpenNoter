from typing import Optional

from app.gpt.base import GPT
from app.gpt.provider.OpenAI_compatible_provider import OpenAICompatibleProvider
from app.gpt.universal_gpt import UniversalGPT
from app.models.model_config import ModelConfig


class GPTFactory:
    @staticmethod
    def from_config(config: ModelConfig, *, timeout: Optional[float] = None,
                    max_retries: Optional[int] = None) -> GPT:
        """按配置构造 GPT 客户端。

        `timeout` / `max_retries` 透传给 OpenAI SDK。**不传时用 SDK 默认值**
        （`timeout=600s`、`max_retries=2`）——对笔记生成这种长任务合适，对
        `/chat/ask` 这种「用户在前台等」的接口则不可接受：前端 axios 60s
        就放弃了，后端还会按 600s × 3 次尝试把线程占住（实测最坏 4 轮工具调用
        × 3 次 × 600s ≈ 2 小时）。所以 chat 链路必须显式传短超时，
        见 `app/services/chat_service.py`。
        """
        client = OpenAICompatibleProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=timeout,
            max_retries=max_retries,
        ).get_client
        return UniversalGPT(client=client, model=config.model_name)
