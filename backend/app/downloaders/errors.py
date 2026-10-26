"""下载器公共异常。

这些异常故意分成两类：
- 可重试（网络抖动、CDN 5xx）→ 调用方按指数退避重试
- 不可重试（超大小限制、超时长限制、私密/下架）→ 立刻失败，重试也没用

`Downloader.download` 的调用方（note.py）靠 `is_retryable()` 决定要不要再试一次。
"""

from __future__ import annotations


class DownloadError(RuntimeError):
    """下载失败基类。"""

    retryable = False


class RetryableDownloadError(DownloadError):
    """值得重试的失败：网络抖动、临时 5xx、CDN 鉴权过期。"""

    retryable = True


class SizeLimitExceeded(DownloadError):
    """媒体体积超过配置上限。

    镜像 astrbot_plugin_link_resolver 的 SizeLimitExceeded 语义：超限即终止，
    B 站那边还可以捕获它来降画质重下，所以这里必须是一个独立异常类型。
    """

    retryable = False


class DurationLimitExceeded(DownloadError):
    """视频时长超过配置上限。"""

    retryable = False


# 值得重试的**非业务**异常（网络层瞬时故障）。
#
# 用显式白名单而不是「其余都重试」：旧实现 `return True` 意味着
# `AttributeError`（代码 bug）、`KeyError`（平台返回结构变了）、
# `json.JSONDecodeError`（坏缓存）都会被当成网络抖动重试 2~3 次 ——
# 结果是白烧时间、配额与 CDN 流量，且**掩盖了真正的 bug**（日志里只剩
# 「重试 2/2 后失败」，看不到原始 AttributeError）。
#
# 判断标准：这些异常的共同点是「同一请求换个时刻可能成功」。
# 代码缺陷/数据损坏不具备这个性质。
_RETRYABLE_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,          # 内建：socket 超时
    ConnectionError,       # 内建：ConnectionReset/BrokenPipe 等父类
)


def _retryable_by_type(exc: BaseException) -> bool:
    """按类型判断是否属于「网络瞬时故障」白名单。

    requests/httpx/openai 的异常都**不继承**内建 ConnectionError，
    所以必须按各自 SDK 的类型再判一遍。这里用延迟 import：这几个依赖
    在某些极简部署里可能不存在，缺了不该让模块导入失败。
    """
    if isinstance(exc, _RETRYABLE_NETWORK_ERRORS):
        return True

    # requests（yt-dlp / 各下载器底层常用）
    try:
        import requests

        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
    except ImportError:
        pass

    # httpx
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
    except ImportError:
        pass

    # openai（Groq 也在用；其 APIConnectionError 不继承 httpx 异常）
    try:
        import openai

        if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
            return True
    except (ImportError, AttributeError):
        pass

    return False


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。

    判定顺序：
      1. 业务异常（`DownloadError` 子类）→ 看它自己的 `retryable` 标记；
      2. 已知的网络瞬时故障（白名单）→ 重试；
      3. **其余一律不重试**。

    第 3 条是 F25 的修复点：旧实现在这里 `return True`，把
    `AttributeError` / `KeyError` / `JSONDecodeError` 这类**确定性失败**
    也重试了，既浪费配额，又把真正的 bug 埋在「重试后失败」的日志里。
    """
    if isinstance(exc, (SizeLimitExceeded, DurationLimitExceeded)):
        return False
    if isinstance(exc, DownloadError):
        return exc.retryable
    return _retryable_by_type(exc)
