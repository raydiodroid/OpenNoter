"""下载相关的运行期配置（体积上限 / 时长上限 / 重试预算）。

对照 astrbot_plugin_link_resolver 的做法：它在 `general_settings` 里配
`max_video_size_mb`、在两个平台的 settings 里配 `max_duration_seconds`，
超限时**在下载前就拒绝**，而不是下完再判断。

OpenNoter 之前完全没有这类限制，一个 3 小时 4K 视频能直接打满磁盘。这里补上，
并支持用环境变量覆盖，避免又要动前端才能调。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 默认值与插件对齐：200MB / 300s。0 表示不限制。
DEFAULT_MAX_SIZE_MB = 200
DEFAULT_MAX_DURATION_SECONDS = 300
DEFAULT_RETRY_COUNT = 2

_ENV_MAX_SIZE = "DOWNLOAD_MAX_SIZE_MB"
_ENV_MAX_DURATION = "DOWNLOAD_MAX_DURATION_SECONDS"
_ENV_RETRY = "DOWNLOAD_RETRY_COUNT"


def _read_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("环境变量 %s=%r 不是整数，回退默认值 %d", name, raw, default)
        return default
    return max(minimum, value)


@dataclass(frozen=True)
class DownloadLimits:
    max_size_mb: int = DEFAULT_MAX_SIZE_MB
    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS
    retry_count: int = DEFAULT_RETRY_COUNT

    @property
    def max_size_bytes(self) -> Optional[int]:
        """返回字节上限；0 或负数表示不限制。"""
        if self.max_size_mb <= 0:
            return None
        return self.max_size_mb * 1024 * 1024

    def size_exceeds(self, size_bytes: Optional[int]) -> bool:
        limit = self.max_size_bytes
        if not limit or not size_bytes:
            return False
        return size_bytes > limit

    def duration_exceeds(self, duration_seconds: Optional[float]) -> bool:
        if self.max_duration_seconds <= 0 or not duration_seconds:
            return False
        return duration_seconds > self.max_duration_seconds


def get_download_limits() -> DownloadLimits:
    """每次现读环境变量。

    不做模块级缓存：否则测试里改 env 不生效（`SUPPORT_PLATFORM_MAP` 在 import
    期读 cookie 导致的「改配置要重启」就是这么来的，这里不重蹈覆辙）。
    """
    return DownloadLimits(
        max_size_mb=_read_int(_ENV_MAX_SIZE, DEFAULT_MAX_SIZE_MB),
        max_duration_seconds=_read_int(_ENV_MAX_DURATION, DEFAULT_MAX_DURATION_SECONDS),
        retry_count=_read_int(_ENV_RETRY, DEFAULT_RETRY_COUNT),
    )
