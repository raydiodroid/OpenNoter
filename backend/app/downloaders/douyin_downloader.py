"""抖音下载器：免登录解析（x-tt-argus）+ 字段修复 + 流式下载。

这一版是对旧实现的整体重写。旧实现的几个致命问题（都是实测确认过的）：

1. **解析走不通**：依赖 a_bogus + msToken + WebSign 三件套，但详情接口现在
   只需要 `x-tt-argus: 1` 这个头（见 `douyin_api.py` 的模块注释）。旧代码在这里
   403 / 0 字节，等于整个抖音链路是死的。
2. **音频取错源**：旧代码下 `aweme_detail.music.play_url.uri`，那是 BGM/原声，
   `is_use_music=True` 的作品拿到的是别人的配乐，转写内容完全错。现在改成
   「下视频流，用 ffmpeg `-vn` 抽音轨」。
3. **时长单位错**：`video.duration` 是毫秒，旧代码当秒用，11.1s 的视频记成
   185 分 23 秒。
4. **标题取错字段**：`item_title` 实测是空串，真实标题在 `desc`。
5. **封面会 KeyError**：`cover_original_scale` 在图文/分享页里不存在，且旧代码的
   else 分支访问顶层 `video` 键（顶层没有这个键）。
6. **视频地址带水印**：旧代码用 `download_addr`（720x720 正方裁剪 + 重编码 + 水印），
   现在优先 `play_addr`（无水印）。
7. **下载层没有 timeout / 全量进内存 / 只取候选第一条**：现在流式写盘、
   `timeout=(5, 30)`、候选地址逐个回退、有体积/时长上限。

字段语义和网络请求都在 `douyin_api.py`，本文件只管「落盘 + 转码 + 组装结果」。

关于 `need_video=True`：调用方（note.py）在需要截图/视频理解时会先调
`download_video()` 再调 `download()`。这里不会重复下载视频，而是直接复用它
（同一个 `{aweme_id}.mp4` 命名规则），既省带宽也避免出现两份不同步的文件。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

import requests

from app.downloaders.base import Downloader, audio_bitrate_kbps, sanitize_filename_component
from app.downloaders.douyin_api import (
    DESKTOP_USER_AGENT,
    WEB_REFERER,
    DouyinAPI,
    DouyinMedia,
)
from app.downloaders.errors import (
    DownloadError,
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
)
from app.enmus.note_enums import DownloadQuality
from app.models.audio_model import AudioDownloadResult
from app.services.cookie_manager import CookieConfigManager
from app.utils.logger import get_logger
from app.utils.url_parser import extract_video_id

logger = get_logger(__name__)

# 流式写盘的块大小：1MB。抖音单个作品通常是 1–100MB，这个粒度既不会让内存
# 峰值跳变，也不会频繁 syscall。
CHUNK_SIZE = 1024 * 1024

# ffmpeg 抽音轨的超时（秒）。一段 5 分钟的视频在普通机器上 10 秒内能转完，
# 这里留足余量；卡死时要能失败而不是挂住任务队列。
FFMPEG_TIMEOUT = 300

# 下载时额外带的头。CDN（douyinvod.com）对 Referer 不严格，但带上更稳（实测
# 不带也能 200，带上是为了减少被当成脚本流量的机会）。
_DOWNLOAD_HEADERS = {
    "User-Agent": DESKTOP_USER_AGENT,
    "Referer": WEB_REFERER,
    "Accept": "*/*",
}

# ISO BMFF（mp4 / m4a / mov）box 的魔数特征：第 4–8 字节是 b"ftyp"。
# 实测抖音 CDN 的真实响应头是 `\x00\x00\x00 ftypisom`。
_ISO_BMFF_FTYP_OFFSET = 4
_ISO_BMFF_FTYP = b"ftyp"
# 判魔数需要读的字节数：box size(4) + "ftyp"(4) + 之后随便一点。
_MAGIC_READ_BYTES = 64


class DouyinDownloader(Downloader):
    """抖音下载器。

    :param cookie: 可选的抖音 Cookie（`name=value; ...`）。默认从
        `CookieConfigManager` 现读（工厂每次任务新建实例，所以改配置后下个任务生效）。
        没有 Cookie 也能工作：ttwid 由 `DouyinAPI` 自行引导。
    :param api: 注入的 `DouyinAPI` 实例（测试用）。
    :param http: 注入的网络实现（默认 `requests`）。下载 CDN 也用它，测试里
        传假实现就能完全离线。
    """

    def __init__(
        self,
        cookie: Optional[str] = None,
        *,
        api: Optional[DouyinAPI] = None,
        http: Any = None,
        user_id: Optional[int] = None,
    ):
        super().__init__(user_id=user_id)
        self._http = http if http is not None else requests
        self._cookie = cookie if cookie is not None else self._load_cookie()
        self._api = api if api is not None else DouyinAPI(cookie=self._cookie, http=self._http)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def download(
        self,
        video_url: str,
        output_dir: str = None,
        quality: DownloadQuality = "medium",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> AudioDownloadResult:
        """下载音频（或只取元信息）。

        :param skip_download: 只取元信息、不写任何文件。note.py 在「已有字幕、
            不需要截图」时走这条路径。
        :param need_video: 调用方是否还需要视频文件。为 True 时复用
            `download_video()` 已经下好的视频，不重复下载。
        """
        aweme_id = self._resolve_aweme_id(video_url)
        media = self._api.fetch_media(aweme_id)

        output_dir = self._resolve_output_dir(output_dir)

        if skip_download:
            # 只取元信息时不落任何媒体，体积/时长上限对它没有意义：
            # 在这里因为「视频太大」直接失败，反而会让 note.py 回退到完整下载，
            # 然后在下一次调用里才真的撞上限（多一次网络请求 + 一条误导性的告警）。
            logger.info("抖音：仅提取元信息（skip_download=True，不写任何文件）")
            return self._build_result(media, file_path=self._audio_path(output_dir, media.aweme_id))

        self._assert_limits(media)

        if media.is_image_post:
            audio_path = self._prepare_image_post_audio(media, output_dir, quality)
            return self._build_result(media, file_path=audio_path)

        video_path = self._video_path(output_dir, media.aweme_id)
        if self._is_usable_file(video_path):
            # 音频始终从视频流抽（music.play_url 是 BGM，不是视频音轨），所以
            # 视频文件是必要中间产物。note.py 在需要截图时会先调 download_video()，
            # 那次已经下好了；这里命中就不再重复下载。
            logger.info("抖音：复用已存在的视频文件 %s（need_video=%s）", video_path, bool(need_video))
        else:
            self._download_to_file(media.video_candidates, video_path, what="视频")

        audio_path = self._extract_audio(video_path, self._audio_path(output_dir, media.aweme_id), quality)
        return self._build_result(media, file_path=audio_path, video_path=None)

    def download_video(self, video_url: str, output_dir: Optional[str] = None) -> str:
        """下载视频（含水印判断与体积上限），返回本地路径。"""
        aweme_id = self._resolve_aweme_id(video_url)
        output_dir = self._resolve_output_dir(output_dir)

        # 文件名只由 aweme_id 决定，所以「已经下过」可以在**发请求之前**就判掉。
        # 注意这里不能只判存在：0 字节残文件必须重下。
        cached_path = self._video_path(output_dir, aweme_id)
        if self._is_usable_file(cached_path):
            logger.info("抖音：视频已存在，跳过解析与下载 %s", cached_path)
            return cached_path

        media = self._api.fetch_media(aweme_id)
        self._assert_limits(media)
        video_path = self._video_path(output_dir, media.aweme_id)
        if self._is_usable_file(video_path):
            logger.info("抖音：视频已存在，跳过下载 %s", video_path)
            return video_path

        if media.is_image_post:
            # 图文作品没有视频轨。扩展名保持 mp4 会让调用方（截图/视频理解）
            # 拿到一个无法解码的文件，所以明确报错而不是给个坏路径。
            raise DownloadError("该抖音作品是图文作品，没有可下载的视频文件")

        self._download_to_file(media.video_candidates, video_path, what="视频")
        return video_path

    # ------------------------------------------------------------------
    # 元信息 / 结果组装
    # ------------------------------------------------------------------
    def _build_result(
        self,
        media: DouyinMedia,
        *,
        file_path: str,
        video_path: Optional[str] = None,
    ) -> AudioDownloadResult:
        raw_info: Dict[str, Any] = {
            "tags": list(media.tags),
            "description": media.desc,
            "uploader": media.author,
            "webpage_url": media.webpage_url,
            "title": media.title,
            "view_count": _stat(media.statistics, "play_count"),
            "like_count": _stat(media.statistics, "digg_count"),
            "comment_count": _stat(media.statistics, "comment_count"),
            "is_image_post": media.is_image_post,
            "cover_url": media.cover_url,
        }
        # note.py 的 `_raise_if_skipped_by_limit` 靠 raw_info 里的
        # filesize / filesize_approx 区分「被上限跳过」和「网络中断」。抖音没有
        # 走 yt-dlp，这个信息只能我们自己填（详情里的 data_size，字节）。
        if media.estimated_size:
            raw_info["filesize_approx"] = media.estimated_size
            raw_info["filesize"] = media.estimated_size
        return AudioDownloadResult(
            file_path=file_path,
            title=media.title,
            duration=media.duration,
            cover_url=media.cover_url,
            platform="douyin",
            video_id=media.aweme_id,
            raw_info=raw_info,
            video_path=video_path,
        )

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------
    # 命名规则只有这一处：download / download_video / 存在性检查三处共用，
    # 否则「已存在就跳过」永远不命中（B 站那边就踩过这个坑）。
    # 用 aweme_id 而不是标题：标题会变（作者改文案），用标题命名会让同一作品
    # 反复下载，还会因为含 `#` / emoji 在不同文件系统上行为不一致。
    # 参数是 aweme_id 而不是 DouyinMedia，这样「只凭 URL 拼路径」也能用——
    # download_video 需要在发解析请求之前就检查缓存。
    def _video_path(self, output_dir: str, aweme_id: str) -> str:
        return os.path.join(output_dir, f"{self._stem(aweme_id)}.mp4")

    def _audio_path(self, output_dir: str, aweme_id: str) -> str:
        return os.path.join(output_dir, f"{self._stem(aweme_id)}.mp3")

    def _image_post_source_path(self, output_dir: str, aweme_id: str) -> str:
        """图文作品的音频源文件（M4A）。"""
        return os.path.join(output_dir, f"{self._stem(aweme_id)}.m4a")

    @staticmethod
    def _stem(aweme_id: str) -> str:
        return sanitize_filename_component(aweme_id, fallback="douyin")

    @staticmethod
    def _is_usable_file(path: str) -> bool:
        """存在且非空才算可用（下载中断会留下 0 字节残文件）。"""
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------
    def _download_to_file(self, candidates: List[str], target_path: str, *, what: str) -> str:
        """按候选顺序流式下载到 `target_path`，失败自动换下一个候选。

        先把数据写到 `.part` 再原子改名：中途失败不会留下一个「看起来已下载完成」
        的半截文件（否则下次进来会命中存在性检查直接返回坏文件）。
        """
        if not candidates:
            raise DownloadError(f"抖音作品没有可用的{what}地址")

        errors: List[str] = []
        for index, url in enumerate(candidates):
            try:
                self._stream_download(url, target_path)
            except SizeLimitExceeded:
                raise
            except (RetryableDownloadError, DownloadError) as exc:
                errors.append(f"候选 {index + 1}/{len(candidates)}: {exc}")
                logger.warning("抖音%s下载失败，换下一个候选：%s", what, exc)
                continue
            logger.info("抖音%s下载完成：%s", what, target_path)
            return target_path

        raise RetryableDownloadError(
            f"抖音{what}下载失败（{len(candidates)} 个候选地址都不行）：{'; '.join(errors[:3])}"
        )

    def _stream_download(self, url: str, target_path: str) -> None:
        """单个 URL 的流式下载：边下边写，超限立刻中止。"""
        part_path = f"{target_path}.part"
        limit = self.limits.max_size_bytes

        try:
            response = self._http.get(
                url,
                headers=_DOWNLOAD_HEADERS,
                timeout=self._api.timeout,
                stream=True,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise RetryableDownloadError(f"网络异常 {exc.__class__.__name__}") from exc

        try:
            status = getattr(response, "status_code", 0)
            if status != 200:
                raise RetryableDownloadError(f"HTTP {status}")

            declared_size = _content_length(response)
            if limit and declared_size and declared_size > limit:
                raise SizeLimitExceeded(
                    f"抖音媒体体积 {declared_size / 1024 / 1024:.1f}MB 超过上限 "
                    f"{limit / 1024 / 1024:.0f}MB"
                )

            written = 0
            head = b""
            with open(part_path, "wb") as handle:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    # Content-Length 可能缺失（分块传输），所以边写边核对。
                    if limit and written > limit:
                        raise SizeLimitExceeded(
                            f"抖音媒体体积超过上限 {limit / 1024 / 1024:.0f}MB"
                        )
                    # 只有第一块需要留样做魔数校验。CDN 在权限/风控问题上有时候
                    # 会回 200 + 一小段 HTML/JSON（而不是 0 字节），那东西一旦
                    # 落盘，下一轮存在性检查会认为「已经下载好了」。
                    if len(head) < _MAGIC_READ_BYTES:
                        head += chunk[: _MAGIC_READ_BYTES - len(head)]
                    handle.write(chunk)

            if written == 0:
                # CDN 也会用「200 + 0 字节」表达权限/风控问题，和详情接口一样
                # 当成可重试信号，换下一个候选。
                raise RetryableDownloadError("CDN 返回空响应（200 + 0 字节）")

            if not _looks_like_media(head):
                raise RetryableDownloadError("CDN 返回的不是媒体文件（疑似风控页面）")

            os.replace(part_path, target_path)
        except SizeLimitExceeded:
            _safe_remove(part_path)
            raise
        except BaseException:
            _safe_remove(part_path)
            raise
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    # ------------------------------------------------------------------
    # 图文作品的音频
    # ------------------------------------------------------------------
    def _prepare_image_post_audio(self, media: DouyinMedia, output_dir: str, quality) -> str:
        """图文作品：下载它的音频文件（不是 BGM，就是这条作品自己的声音）。

        实测图文作品的 `video.play_addr` 指向一个 M4A（与 `music.play_url.uri`
        逐字节相同），没有视频轨。ffmpeg 照常能 `-vn` 抽出 mp3，所以复用同一条
        转码路径，只是输入扩展名不同。
        """
        source_path = self._image_post_source_path(output_dir, media.aweme_id)
        if not self._is_usable_file(source_path):
            self._download_to_file(media.audio_candidates, source_path, what="音频")

        audio_path = self._audio_path(output_dir, media.aweme_id)
        return self._extract_audio(source_path, audio_path, quality)

    # ------------------------------------------------------------------
    # ffmpeg 抽音轨
    # ------------------------------------------------------------------
    def _extract_audio(self, video_path: str, audio_path: str, quality) -> str:
        """用 ffmpeg 从视频/音频容器里抽音轨。

        参数与 `base.ydl_audio_postprocessor_args` 的语义保持一致（单声道 + 目标
        码率），但这里必须手写：抖音没有 yt-dlp 这条路径。单声道是因为 whisper
        只吃单声道，立体声纯属浪费一半带宽。
        """
        bitrate = audio_bitrate_kbps(quality)
        command = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vn",                     # 丢弃视频轨
            "-acodec", "libmp3lame",
            "-ac", "1",                # 单声道，与 base.AUDIO_CHANNELS 一致
            "-b:a", f"{bitrate}k",
            audio_path,
        ]
        logger.info("抖音：ffmpeg 抽取音轨 -> %s（%dkbps 单声道）", audio_path, bitrate)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=FFMPEG_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as exc:
            raise DownloadError("未找到 ffmpeg，无法从抖音视频中抽取音频") from exc
        except subprocess.TimeoutExpired as exc:
            raise RetryableDownloadError("ffmpeg 抽取音轨超时") from exc

        if result.returncode != 0 or not self._is_usable_file(audio_path):
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore")[-400:]
            raise DownloadError(f"ffmpeg 抽取音轨失败：{stderr or '输出文件为空'}")
        return audio_path

    # ------------------------------------------------------------------
    # 限制 / 工具
    # ------------------------------------------------------------------
    def _assert_limits(self, media: DouyinMedia) -> None:
        """下载前先拒绝超限作品（解析阶段就能拿到时长和体积估算）。"""
        limits = self.limits
        if limits.duration_exceeds(media.duration):
            raise DurationLimitExceeded(
                f"抖音视频时长 {media.duration:.0f} 秒超过上限 {limits.max_duration_seconds} 秒"
            )
        if limits.size_exceeds(media.estimated_size):
            raise SizeLimitExceeded(
                f"抖音视频体积约 {(media.estimated_size or 0) / 1024 / 1024:.1f}MB "
                f"超过上限 {limits.max_size_mb}MB"
            )

    def _resolve_aweme_id(self, video_url: str) -> str:
        aweme_id = extract_video_id(video_url, "douyin")
        if not aweme_id:
            raise DownloadError(f"无法从链接中解析抖音作品 ID：{video_url}")
        return aweme_id

    def _load_cookie(self) -> Optional[str]:
        """现读 Cookie；读不到不报错（抖音免登录可用）。

        BYOK：从 staticmethod 改成实例方法，才能拿到 ``self.user_id``
        去解析「用户自己的 Cookie」。所有调用点都是 ``self._load_cookie()``，
        调用方无需改动。
        """
        return self.resolve_platform_cookie("douyin")

    # 说明：这里没有单独的代理解析逻辑。requests 默认 trust_env=True，会读
    # HTTP_PROXY / HTTPS_PROXY / ALL_PROXY；UI 里配置的代理由 config 路由在保存时
    # 调 `ProxyConfigManager.apply_to_env()` 同步到环境变量，所以这条路已经通了。


def _stat(statistics: Dict[str, Any], key: str) -> Optional[int]:
    """取统计数据（详情接口和分享页给的字段不一致，缺就返回 None）。"""
    if not isinstance(statistics, dict):
        return None
    value = statistics.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _content_length(response: Any) -> Optional[int]:
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Content-Length") or headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _looks_like_media(head: bytes) -> bool:
    """判断响应开头的字节像不像媒体容器。

    抖音 CDN 的真实响应是 ISO BMFF（mp4 / m4a），特征是第 4–8 字节为 `ftyp`。
    这里只做「不像就拒绝」的粗筛：一是防止把风控 HTML/JSON 落盘成 .mp4
    （那会让下一轮的存在性检查误判为已下载），二是比解析 Content-Type 更可靠
    —— 实测 CDN 有时干脆不给 Content-Type。
    """
    if len(head) < _ISO_BMFF_FTYP_OFFSET + len(_ISO_BMFF_FTYP):
        return False
    return head[_ISO_BMFF_FTYP_OFFSET : _ISO_BMFF_FTYP_OFFSET + len(_ISO_BMFF_FTYP)] == _ISO_BMFF_FTYP


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.debug("清理临时文件失败：%s", path)
