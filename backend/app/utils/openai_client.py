"""统一构造 OpenAI 兼容客户端：注入全局代理 + 校验 api_key。

为什么要这一层：
  - 代理：openai SDK 默认只认进程级 HTTP_PROXY 环境变量，桌面端用户在 UI 里
    填的代理需要显式塞进 httpx.Client 才生效。
  - api_key 校验：空 key 会让 httpx 拼出非法 header `Bearer `，抛出
    `httpx.LocalProtocolError: Illegal header value b'Bearer '` 这种天书报错。
    在入口挡掉，给用户「xxx 的 API Key 未配置」这种能看懂的提示。
"""
from typing import Optional

from openai import OpenAI

from app.services.proxy_config_manager import ProxyConfigManager
from app.utils.logger import get_logger

logger = get_logger(__name__)


def build_openai_client(
    api_key: Optional[str],
    base_url: Optional[str],
    *,
    key_label: str = "API Key",
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> OpenAI:
    """构造 OpenAI 客户端。api_key 为空直接抛清晰错误；代理已配置则注入。

    key_label 用于错误提示，例如 "Groq 的 API Key" / "OpenAI 供应商的 API Key"。

    ## timeout 与 max_retries 为什么要显式传

    SDK 默认 `timeout=600s` + `max_retries=2`：单次 create 最坏 3×600s。
    对笔记生成（后台长任务）无所谓，对 `/chat/ask`（用户在前台等）完全不可
    接受 —— 前端 60s 就放弃了，后端还会把线程占住。调用方应显式传短超时。

    ## 代理分支不能覆盖 timeout

    历史实现在代理分支里写 `httpx.Client(timeout=timeout or 600.0)`：即使传了
    `timeout=45`，只要用户配了代理，`http_client` 的 600s 仍会生效（httpx
    Client 的 timeout 优先于请求参数之外的值）。这里改成代理分支也尊重
    `timeout`，只把「未显式传」的情况回落到 600s。
    """
    if not api_key or not str(api_key).strip():
        raise ValueError(f"{key_label} 未配置，请先在「设置」里填写后再使用")

    kwargs = {"api_key": str(api_key).strip(), "base_url": base_url}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_retries is not None:
        kwargs["max_retries"] = max_retries

    proxy_url = ProxyConfigManager().get_proxy_url()
    if proxy_url:
        # 延迟 import httpx：仅在确实要走代理时才需要
        import httpx
        # 与 kwargs 中的 timeout 保持一致；未指定时回落到 600s（SDK 默认）
        client_timeout = timeout if timeout is not None else 600.0
        kwargs["http_client"] = httpx.Client(proxy=proxy_url, timeout=client_timeout)
        logger.info(f"OpenAI 客户端走代理: {proxy_url}")

    return OpenAI(**kwargs)
