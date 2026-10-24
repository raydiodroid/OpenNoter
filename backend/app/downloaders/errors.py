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


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。

    已知的不可重试类型直接拒绝；其余按「可能是网络问题」处理——
    历史行为是任何异常都让任务直接 FAILED，改回「网络类异常重试」风险更低。
    """
    if isinstance(exc, (SizeLimitExceeded, DurationLimitExceeded)):
        return False
    if isinstance(exc, DownloadError):
        return exc.retryable
    return True
