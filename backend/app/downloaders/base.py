import logging
import re
from abc import ABC, abstractmethod
from os import getenv
from typing import TYPE_CHECKING, Optional

# DownloadQuality 保持模块级 import 并 re-export：历史代码里有
# `from app.downloaders.base import Downloader, DownloadQuality, ...` 这种写法，
# 删掉会破坏调用方。
from app.enmus.note_enums import DownloadQuality  # noqa: F401  (re-export)

if TYPE_CHECKING:
    # 仅类型标注用途。模块级 import 这两个会让 tests/test_ydl_retry_opts.py
    # 这类「把 base.py 单独加载进来做语法级检查」的测试踩到 app 包初始化顺序问题。
    from app.models.notes_model import AudioDownloadResult
    from app.models.transcriber_model import TranscriptResult

logger = logging.getLogger(__name__)

# 前端「快速 / 标准 / 高质量」对应的音频目标码率（kbps）。
#
# 历史坑：这里原本是 {"fast": "32", "medium": "64", "slow": "128"}，塞进
# `self.quality` 之后再也没人读过 —— B 站写死 preferredquality='64'、YouTube
# 写死 bestaudio、抖音写死 music 音轨。前端三个选项纯装饰。
#
# 现在把它做成真正生效的映射：音频只喂给 Whisper 转写，而 whisper 内部会重采样到
# 16kHz，所以码率的边际收益有限，128k 已经足够；64k 是"省流量"，192k 留给需要
# 保留更多高频细节（音乐类视频、需要语气判断的场景）。
AUDIO_BITRATE_KBPS: dict[str, int] = {
    "fast": 64,
    "medium": 128,
    "slow": 192,
}

# 兼容旧代码里对 QUALITY_MAP 的引用；新代码请用 AUDIO_BITRATE_KBPS。
QUALITY_MAP = {key: str(value) for key, value in AUDIO_BITRATE_KBPS.items()}

# 转写场景下音频一律降为单声道：whisper 只吃单声道，立体声纯属浪费一半带宽。
AUDIO_CHANNELS = 1

# yt-dlp 的 `retries` 默认值(10)是命令行参数解析器给的，Python API 不套用它：
# 不显式设置时 HttpFD 拿到的是 `self.params.get('retries')` == None，而
# `RetryManager.__init__` 做的是 `self.retries = _retries or 0`——也就是
# 一次都不重试。任何一次网络抖动（例如 B 站 CDN
# upos-sz-mirror*.bilivideo.com 读超时）都会让整个笔记任务直接失败。
#
# 这里的值偏保守：笔记任务是用户在前台等的，重试太多不如早点失败让用户重来。
YDL_RETRY_OPTS = {
    "retries": 3,
    "fragment_retries": 3,
    "socket_timeout": 30,
}

# 输出文件名里不允许出现的字符。yt-dlp 会把 title 塞进文件名，
# B 站标题常带 '|' '/' ':' 这些，Windows 上直接创建失败。
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename_component(value: str, *, fallback: str = "video") -> str:
    """把任意字符串裁成可安全用于文件名的片段。"""
    cleaned = _UNSAFE_FILENAME_RE.sub("_", (value or "").strip())
    cleaned = cleaned.strip(" .")
    # 防止 "." / ".." 这类会穿越目录的名字
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned[:120]


def normalize_quality(value) -> str:
    """把前端/枚举传来的 quality 归一成 'fast' | 'medium' | 'slow'。

    不 import DownloadQuality：`str, Enum` 的实例本身就是 str，`.value` 拿到的
    也是同一个字符串，所以直接按字符串判断即可，顺带让 base.py 保持零 app 依赖。
    """
    text = getattr(value, "value", value)
    text = str(text or "").strip().lower()
    return text if text in AUDIO_BITRATE_KBPS else "medium"


def audio_bitrate_kbps(value) -> int:
    return AUDIO_BITRATE_KBPS[normalize_quality(value)]


def ydl_audio_postprocessor(quality, *, container: str = "mp3") -> list[dict]:
    """给 yt-dlp 用的音频后处理配置，码率跟随 quality 而不是写死。"""
    return [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": container,
            "preferredquality": str(audio_bitrate_kbps(quality)),
        }
    ]


def ydl_audio_postprocessor_args(quality) -> list[str]:
    """自定义 ffmpeg 后处理时用的 -ac / -b:a 参数（单声道 + 目标码率）。"""
    return ["-ac", str(AUDIO_CHANNELS), "-b:a", f"{audio_bitrate_kbps(quality)}k"]


class Downloader(ABC):
    """所有平台下载器的基类。

    约定：

    - `download()` 返回音频（或仅元信息），`download_video()` 返回视频文件路径。
    - `skip_download=True` 时**不得**下载任何媒体文件，只返回元信息，用于「已经有
      字幕、不需要音视频」的场景。历史上 B 站/抖音没实现这个参数，导致 note.py
      那条「省一次完整下载」的捷径抛 TypeError 被吞掉后回退全量下载。
    - 体积/时长上限统一走 `self.limits`，超限抛 `SizeLimitExceeded` /
      `DurationLimitExceeded`，不要静默截断。
    """

    def __init__(self, user_id: Optional[int] = None):
        self.cache_data = getenv('DATA_DIR')
        # BYOK：任务归属用户。下载器在读平台 Cookie 时用它决定「用谁的登录态」。
        # None = 匿名（AUTH_ENFORCE=0 的部署），一律用站点 Cookie。
        self.user_id: Optional[int] = user_id

    def resolve_platform_cookie(self, platform: str) -> Optional[str]:
        """读某平台该用的 Cookie（BYOK 三层解析）。

        子类应当在自己的 ``_read_cookie`` / ``_load_cookie`` 里调这个方法替代
        直接读 ``CookieConfigManager``，这样才能拿到「用户自己的 Cookie」。

        返回 None 表示没有可用 Cookie —— **调用方必须继续执行**（降级到
        无 Cookie 的普通画质），不要当成错误。
        """
        from app.services.credential_resolver import resolve_cookie

        try:
            return resolve_cookie(self.user_id, platform)
        except Exception as exc:  # noqa: BLE001 - Cookie 读失败不该阻断下载
            logger.warning("解析 %s Cookie 失败，按无 Cookie 处理: %s", platform, exc)
            return None

    @property
    def limits(self):
        """每次现读环境变量，不缓存（否则改 env 要重启进程才生效）。

        延迟 import：`tests/test_ydl_retry_opts.py` 会把 base.py 单独加载进来
        做语法级检查，此时 `app.downloaders` 这个包可能还不存在。
        """
        from app.downloaders.limits import get_download_limits

        return get_download_limits()

    # ------------------------------------------------------------------
    # 必须由子类实现
    # ------------------------------------------------------------------
    @abstractmethod
    def download(
        self,
        video_url: str,
        output_dir: str = None,
        quality="medium",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> "AudioDownloadResult":
        """下载音频（或只取元信息）。

        :param video_url: 资源链接
        :param output_dir: 输出路径，默认 data 目录
        :param quality: 音频质量 fast | medium | slow
        :param need_video: 调用方是否还需要视频文件（子类可据此减少重复下载）
        :param skip_download: 只取元信息，不下载任何媒体
        :return: AudioDownloadResult
        """
        raise NotImplementedError

    @abstractmethod
    def download_video(self, video_url: str, output_dir: Optional[str] = None) -> str:
        """下载视频，返回本地视频文件路径。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 可选实现
    # ------------------------------------------------------------------
    def download_subtitles(
        self,
        video_url: str,
        output_dir: str = None,
        langs: list = None,
    ) -> "Optional[TranscriptResult]":
        """
        尝试获取平台字幕（人工字幕或自动生成字幕）

        :param video_url: 视频链接
        :param output_dir: 输出路径
        :param langs: 优先语言列表，如 ['zh-Hans', 'zh', 'en']
        :return: TranscriptResult 或 None（无字幕时）
        """
        return None

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _resolve_output_dir(self, output_dir: Optional[str]) -> str:
        """统一解析输出目录并确保存在。"""
        import os

        resolved = output_dir or self.cache_data
        if not resolved:
            from app.utils.path_helper import get_data_dir

            resolved = get_data_dir()
        os.makedirs(resolved, exist_ok=True)
        return resolved
