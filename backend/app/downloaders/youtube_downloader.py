"""YouTube 下载器。

历史问题（本次修复）：

1. **完全没读 Cookie**：策略层 `COOKIE_FORMATS["youtube"] = "netscape"` 已改成
   接受 Netscape cookies.txt，但下载器一个字节都没往 yt-dlp 传，年龄限制 /
   会员 / 部分 403 风控都解不了。现在从 `CookieConfigManager().get("youtube")`
   取规范化后的 cookies.txt 文本，落到 0600 临时文件传给 `cookiefile`，
   用完即删（`_cookie_file()` 上下文管理器，参考 B 站 `NamedTemporaryFile(delete=False)`
   永不清理的事故）。
2. **403 完全没有回退**：YouTube 媒体流被拒时直接失败。现在错误串含
   `HTTP Error 403` / `403: Forbidden` 时换一组更保守的 `player_client` 重试一次。
   **默认不传 `extractor_args`**：yt-dlp 的默认 client 列表随版本变化（且带 Cookie
   时自己就会把 web_embedded 列入候选），硬编码只会让它更差。
3. **quality 被忽略**：原来写死 `bestaudio`，现在码率走
   `app.downloaders.base.audio_bitrate_kbps()`（fast=64 / medium=128 / slow=192），
   后处理走 `ydl_audio_postprocessor()`。注意 `preferredquality` 必须是纯数字字符串：
   命令行会 `strip("k")`，Python API 不会，传 `"128k"` 会静默退化成编码器默认质量。
4. **没有任何体积/时长上限**：下载前先 `extract_info(download=False)` 拿
   `duration` / 选中格式的 `filesize(-_approx)`，超限直接抛
   `DurationLimitExceeded` / `SizeLimitExceeded`，不进入下载。
   `skip_download=True` 的元信息路径不做任何上限判断（本来就只取元信息）。
5. **download_video 会选中 4K 大文件**：格式选择器优先 avc1（浏览器兼容），
   加 `max_filesize`，返回前校验文件存在且非空。

`max_filesize` 有一个坑：yt-dlp 只在拿到 Content-Length 后、写第一个字节前查一次，
超限时 `return False` —— **不产生文件、不抛异常、不降级到下一个 format**。所以
不能只依赖它，下载后的落盘校验和下载前的体积预检都必须有，否则会出现
「返回成功但文件不存在」。

Cookie 的定位要说清楚：它只解决「需要账号的内容」（私有列表、年龄限制、会员视频）。
限速 / 风控类的 403 通常换 Cookie 也没用，优先考虑换出口 IP；而且用 Cookie 应对
bot 检查（"Sign in to confirm you're not a bot"）有封号风险。
"""

import contextlib
import logging
import os
import tempfile
from abc import ABC
from typing import Iterator, List, Optional, Union

import yt_dlp

from app.downloaders.base import Downloader, DownloadQuality, YDL_RETRY_OPTS
from app.downloaders.youtube_subtitle import YouTubeSubtitleFetcher
from app.models.notes_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult
from app.services.cookie_manager import CookieConfigManager
from app.services.proxy_config_manager import ProxyConfigManager
from app.utils.url_parser import extract_video_id

from app.downloaders.base import audio_bitrate_kbps, ydl_audio_postprocessor
from app.downloaders.errors import (
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
)
from app.downloaders.limits import get_download_limits

logger = logging.getLogger(__name__)

# 音频优先选 m4a：转写只需要音轨，m4a 的 AAC 比 webm/opus 更省 CPU。
# 后处理会按 quality 转成 mp3，所以这里只是「原始音轨」的选择器。
AUDIO_FORMAT_SELECTOR = "bestaudio[ext=m4a]/bestaudio/best"

# 视频格式：**优先 avc1**。OpenNoter 产出的视频用于浏览器预览和截图，
# AV1/VP9 在 Safari 和部分播放器里不认。fallback 链保留，避免没有 avc1 时直接失败。
VIDEO_FORMAT_SELECTOR = (
    "bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*[ext=mp4]+ba/best"
)

# 403 通常是 YouTube 对媒体流的风控（PO Token / 出口 IP / 登录态）。
# 出现后换一组更保守的客户端重试一次；**不要**把它写死成默认值 ——
# yt-dlp 自己的默认 client 列表随版本演进，而且带 Cookie 时本来就会把
# web_embedded 列入候选，硬编码反而可能更差。
_403_FALLBACK_PLAYER_CLIENTS = ["web_embedded", "tv_samsung", "web"]

_FORBIDDEN_MARKERS = ("http error 403", "403: forbidden")


def _apply_proxy(ydl_opts: dict) -> dict:
    """YouTube 在国内需要代理。配置了全局代理就塞进 yt-dlp opts。"""
    proxy = ProxyConfigManager().get_proxy_url()
    if proxy:
        ydl_opts["proxy"] = proxy
        logger.info(f"yt-dlp 走代理: {proxy}")
    return ydl_opts


def _is_forbidden_error(exc: BaseException) -> bool:
    """识别 YouTube 的 HTTP 403 拒绝。大小写不敏感，兼容两种上游报错格式。"""
    text = str(exc).lower()
    return any(marker in text for marker in _FORBIDDEN_MARKERS)


def _format_size_bytes(fmt: Optional[dict]) -> Optional[int]:
    """取单个格式的大小：精确值优先，其次近似值。"""
    if not fmt:
        return None
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        try:
            if value:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _selected_size_bytes(info: Optional[dict]) -> Optional[int]:
    """汇总「即将/已经下载」的格式大小。

    yt-dlp 的 info 有三种形态，都要能读到体积：
    1. 单流：`filesize` / `filesize_approx` 在 info 顶层；
    2. 已选格式（download=False 的预检）：`requested_formats` 在 info 顶层，多个流要累加；
    3. 下载被 max_filesize 中止后：真实格式信息在 `requested_downloads[0]` 里。

    只要有一路流大小未知就返回 None —— 拿部分数据求和会误报，宁可不判，
    交给 `max_filesize` 与下载后的真实体积校验。
    """
    if not info:
        return None

    def _sum_formats(formats: Optional[list]) -> Optional[int]:
        if not formats:
            return None
        sizes = [_format_size_bytes(fmt) for fmt in formats]
        known = [size for size in sizes if size]
        if known and len(known) == len(sizes):
            return sum(known)
        return None

    requested = info.get("requested_formats") or []
    if requested:
        return _sum_formats(requested)

    for download in info.get("requested_downloads") or []:
        if not isinstance(download, dict):
            continue
        size = _sum_formats(download.get("requested_formats"))
        if size is not None:
            return size
        size = _format_size_bytes(download)
        if size is not None:
            return size

    return _format_size_bytes(info)


def _candidate_file_paths(info: Optional[dict]) -> List[str]:
    """收集 info 里所有可能指向「已落盘文件」的路径，按可信度排序。

    yt-dlp 对「单文件下载」「合并下载」「后处理过」三种情况的落盘信息位置不同：
    顶层的 `filepath` 有时为 None，真实路径藏在 `requested_downloads[]` 里。
    """
    if not info:
        return []

    paths: List[str] = []

    def _add(path) -> None:
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)

    for key in ("filepath", "_filename", "filename"):
        _add(info.get(key))
    for download in info.get("requested_downloads") or []:
        if not isinstance(download, dict):
            continue
        for key in ("filepath", "_filename", "filename"):
            _add(download.get(key))
        for fmt in download.get("requested_formats") or []:
            if isinstance(fmt, dict):
                _add(fmt.get("filepath"))
    for fmt in info.get("requested_formats") or []:
        if isinstance(fmt, dict):
            _add(fmt.get("filepath"))
    return paths


def _first_existing_file(paths: List[str]) -> Optional[str]:
    """返回第一个「存在且非空」的文件路径。空文件视为下载残骸。"""
    for path in paths:
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
        except OSError:
            continue
    return None


class _YtDlpLogger:
    """把 yt-dlp 的输出接进标准 logging，避免它直接写 stderr。"""

    def debug(self, msg: str) -> None:
        logger.debug(msg)

    def info(self, msg: str) -> None:
        logger.debug(msg)

    def warning(self, msg: str) -> None:
        logger.warning(msg)

    def error(self, msg: str) -> None:
        logger.error(msg)


class YoutubeDownloader(Downloader, ABC):
    def __init__(self, cookies: Optional[str] = None, user_id: Optional[int] = None):
        """
        :param cookies: Netscape cookies.txt 文本。留空时现读（见 `_read_stored_cookies`）
            （`services/constant.py` 的工厂每次任务新建实例，所以读一次就是最新的）。
            显式传入主要给测试和调用方复用，生产走现读路径。
        :param user_id: BYOK —— 任务归属用户，用于解析「用户自己的 Cookie」。
        """
        super().__init__(user_id=user_id)
        self._cookies = cookies if cookies is not None else self._read_stored_cookies()

    # ------------------------------------------------------------------
    # Cookie
    # ------------------------------------------------------------------
    def _read_stored_cookies(self) -> Optional[str]:
        """读取 YouTube 的 Netscape Cookie 文本。

        策略层（`app/services/cookie_policy.py`）已经做了格式校验与规范化：
        `COOKIE_FORMATS["youtube"] == "netscape"`，非 cookies.txt 格式会在保存时
        就被拒绝。这里只负责取用，不再二次解析。

        BYOK：从 staticmethod 改成实例方法，才能拿到 ``self.user_id``。
        无可用 Cookie 时返回 None，调用方按「不用 cookiefile」继续。
        """
        cookie_text = self.resolve_platform_cookie("youtube")

        if not cookie_text:
            logger.debug("YouTube Cookie 未配置：本次不使用 cookiefile")
            return None

        # 日志里只允许出现「条目数」，绝不允许出现 Cookie 内容。
        try:
            from app.services.cookie_policy import cookie_entry_count

            count = cookie_entry_count("youtube", cookie_text)
            logger.info(f"YouTube Cookie 已加载（{count} 条），将写入临时 cookies.txt")
        except Exception:  # pragma: no cover - 计数失败不影响主流程
            logger.info("YouTube Cookie 已加载，将写入临时 cookies.txt")

        # 安全提示：Cookie 只解锁「需要账号的内容」，不能解决限速/风控。
        if "__Secure-" not in cookie_text:
            logger.debug(
                "YouTube Cookie 里没有 __Secure- 前缀项，可能不是登录态导出的"
                "（无痕窗口登录 → 访问 robots.txt → 导出 → 关闭窗口）"
            )
        return cookie_text

    @contextlib.contextmanager
    def _cookie_file(self) -> Iterator[Optional[str]]:
        """把 Netscape Cookie 写成 0600 临时文件，退出时确保删除。

        用 `TemporaryDirectory` 而不是 `NamedTemporaryFile(delete=False)`：
        后者没有 `__del__` / `atexit`，容器跑几周会堆出成百上千个含 SESSDATA
        的明文文件（B 站下载器踩过这个坑，这里不重蹈覆辙）。
        """
        if not self._cookies:
            # Cookie 为空时**不传 cookiefile**：传空文件 yt-dlp 会直接报错。
            yield None
            return

        # TemporaryDirectory 对象手动管理，配合 finally 保证任何异常路径都清理。
        temp_dir = tempfile.TemporaryDirectory(prefix="opennoter-yt-cookie-")
        cookie_path = os.path.join(temp_dir.name, "cookies.txt")
        try:
            # 0600：Cookie 是凭据，不能给同机其他用户读。
            fd = os.open(cookie_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                text = self._cookies
                handle.write(text if text.endswith("\n") else text + "\n")
            logger.debug(f"YouTube 临时 Cookie 文件已生成: {cookie_path}")
            yield cookie_path
        finally:
            # yt-dlp 退出时会把 jar 回写到 cookiefile，所以这里在 yield 之后删。
            temp_dir.cleanup()
            logger.debug(f"YouTube 临时 Cookie 文件已清理: {cookie_path}")

    # ------------------------------------------------------------------
    # 输出目录
    # ------------------------------------------------------------------
    def _prepare_output_dir(self, output_dir: Optional[str]) -> str:
        """统一解析输出目录（基类负责 data 目录兜底 + makedirs）。"""
        return self._resolve_output_dir(output_dir)

    # ------------------------------------------------------------------
    # 上限
    # ------------------------------------------------------------------
    def _current_limits(self):
        """取下载上限（每次现读环境变量，改配置无需重启）。"""
        limits = getattr(self, "limits", None)
        return limits if limits is not None else get_download_limits()

    def _check_media_limits(self, info: Optional[dict]) -> None:
        """下载前做体积/时长预检。

        只在「真的拿到了格式列表」时判断：`extract_info(download=False)` 正常返回
        的 YouTube 视频一定有 formats；没有 formats 说明这次解析并不完整（或调用方
        传的是测试替身），此时判断上限只会误杀。体积/时长最终还有下载后校验兜底。
        """
        if not info:
            return
        limits = self._current_limits()
        if limits is None:
            return
        if not info.get("formats"):
            return

        self._raise_if_over_limits(info, limits)

    def _raise_if_over_limits(self, info: dict, limits) -> None:
        """时长 / 体积超限时抛对应异常。被预检和 `match_filter` 共用。"""
        duration = info.get("duration")
        if limits.duration_exceeds(duration):
            minutes = int(duration) // 60
            raise DurationLimitExceeded(
                f"视频时长 {minutes} 分 {int(duration) % 60} 秒，"
                f"超过 {limits.max_duration_seconds} 秒上限"
                f"（可用环境变量 DOWNLOAD_MAX_DURATION_SECONDS 调整）"
            )

        size = _selected_size_bytes(info)
        if limits.size_exceeds(size):
            raise SizeLimitExceeded(
                f"视频体积约 {int(size) / 1024 / 1024:.1f}MB，"
                f"超过 {limits.max_size_mb}MB 上限"
                f"（可用环境变量 DOWNLOAD_MAX_SIZE_MB 调整）"
            )

    def _make_match_filter(self):
        """构造 yt-dlp 的 `match_filter`，在**格式选择之前**拒绝超限视频。

        为什么不能只靠 `max_filesize`（实测）：
        - 它只在拿到 Content-Length 后、写第一个字节前查一次，没有 Content-Length
          时完全失效；
        - 超限时 `return False`：不产生文件、**不抛异常**、不降级到下一个 format，
          退出码仍是成功。只靠它会得到「返回成功但文件不存在」。
        `match_filter` 抛出的异常会被 yt-dlp 原样向上传播（已实测），所以直接抛
        我们自己的 `SizeLimitExceeded` / `DurationLimitExceeded`，调用方据此
        给出可读的中文失败原因，而不是任务级重试（这两个异常不可重试）。
        """
        limits = self._current_limits()

        def match_filter(info_dict, incomplete=False):
            if limits is None:
                return None
            # incomplete 表示 formats 还没解析完，此时体积信息不可信；
            # 但 duration 在这是可用的，仍可以判时长。
            if not incomplete and info_dict.get("formats"):
                self._raise_if_over_limits(info_dict, limits)
            elif limits.duration_exceeds(info_dict.get("duration")):
                duration = info_dict.get("duration") or 0
                raise DurationLimitExceeded(
                    f"视频时长 {int(duration) // 60} 分 {int(duration) % 60} 秒，"
                    f"超过 {limits.max_duration_seconds} 秒上限"
                    f"（可用环境变量 DOWNLOAD_MAX_DURATION_SECONDS 调整）"
                )
            return None

        # 没有任何上限时不必挂 filter，省掉每条信息一次回调。
        if limits is None or (
            not limits.max_size_bytes and limits.max_duration_seconds <= 0
        ):
            return None
        return match_filter

    # ------------------------------------------------------------------
    # yt-dlp 调用
    # ------------------------------------------------------------------
    @staticmethod
    def _build_ydl_opts(
        *,
        format_selector: str,
        outtmpl: str,
        cookiefile: Optional[str] = None,
        postprocessors: Optional[list] = None,
        skip_download: bool = False,
        max_filesize: Optional[int] = None,
        merge_output_format: Optional[str] = None,
        match_filter=None,
    ) -> dict:
        ydl_opts = {
            **YDL_RETRY_OPTS,
            "format": format_selector,
            "outtmpl": outtmpl,
            "noplaylist": True,
            # 服务器场景下 yt-dlp 的进度条/提示没有意义，统一接进 logging。
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "logger": _YtDlpLogger(),
        }
        if cookiefile:
            ydl_opts["cookiefile"] = cookiefile
        if postprocessors:
            ydl_opts["postprocessors"] = postprocessors
        if merge_output_format:
            ydl_opts["merge_output_format"] = merge_output_format
        if match_filter is not None:
            # 在格式选择之前按 duration/filesize 拒绝超限视频。
            # max_filesize 是「静默跳过」，只靠它会得到「成功但没文件」。
            ydl_opts["match_filter"] = match_filter
        if skip_download:
            ydl_opts["skip_download"] = True
            # 只取元信息时并不需要媒体流。yt-dlp 版本落后于 YouTube player 时，
            # nsig 解析失败会导致所有音视频格式被丢弃，此时格式选择会抛
            # "Requested format is not available"，把一个已经拿到字幕的任务带崩。
            ydl_opts["ignore_no_formats_error"] = True
        elif max_filesize:
            # 防御性上限：预检拿不到体积时的最后一道拦截。
            # 注意它只在有 Content-Length 且超限时生效，且是「静默跳过」，
            # 所以下载后必须校验文件（见 _resolve_audio_path）。
            ydl_opts["max_filesize"] = max_filesize
        return ydl_opts

    @staticmethod
    def _extract(video_url: str, ydl_opts: dict, *, download: bool) -> Optional[dict]:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(video_url, download=download)

    def _extract_with_403_fallback(
        self, video_url: str, ydl_opts: dict, *, download: bool
    ) -> Optional[dict]:
        """先按默认参数取，遇到 403 才换保守的 player_client 重试一次。"""
        try:
            return self._extract(video_url, ydl_opts, download=download)
        except Exception as exc:
            if not _is_forbidden_error(exc):
                raise

            logger.warning(
                "YouTube 媒体流返回 403（%s），换更保守的 player_client 组合重试一次",
                str(exc).splitlines()[0][:200],
            )
            fallback_opts = dict(ydl_opts)
            fallback_opts["extractor_args"] = {
                "youtube": {"player_client": list(_403_FALLBACK_PLAYER_CLIENTS)}
            }
            try:
                return self._extract(video_url, fallback_opts, download=download)
            except Exception as retry_exc:
                raise RetryableDownloadError(
                    "YouTube 拒绝了媒体流（HTTP 403），已用 web_embedded 等兼容客户端"
                    "重试一次仍然失败。403 的主因通常是 PO Token / 出口 IP 风控，"
                    "不一定是 Cookie；建议依次排查：更新 yt-dlp、检查 Cookie 是否"
                    "为登录态导出、更换出口 IP。"
                    f"原始错误：{str(retry_exc).splitlines()[0][:300]}"
                ) from retry_exc

    # ------------------------------------------------------------------
    # 结果组装
    # ------------------------------------------------------------------
    def _resolve_audio_path(
        self,
        info: Optional[dict],
        output_dir: str,
        *,
        skip_download: bool,
    ) -> str:
        """定位音频落盘路径。

        - `skip_download`：不要求文件存在，按约定返回（`ext=None` 时回退 m4a，
          避免拼出 `xxx.None`）。
        - 真实下载：必须找到存在且非空的文件；找不到时要区分「超限被静默跳过」
          和「其他异常」。
        """
        info = info or {}
        video_id = info.get("id") or "audio"
        ext = info.get("ext") or "m4a"

        if skip_download:
            return os.path.join(output_dir, f"{video_id}.{ext}")

        existing = _first_existing_file(_candidate_file_paths(info))
        if existing:
            limits = self._current_limits()
            if limits is not None:
                # 先取体积再判断/删除：删除之后再 getsize 会抛 FileNotFoundError。
                size = os.path.getsize(existing)
                if limits.size_exceeds(size):
                    removed = self._remove_quietly(existing)
                    raise SizeLimitExceeded(
                        f"下载完成的音频约 {size / 1024 / 1024:.1f}MB，"
                        f"超过 {limits.max_size_mb}MB 上限（已删除{removed}）"
                        f"（可用环境变量 DOWNLOAD_MAX_SIZE_MB 调整）"
                    )
            return existing

        # max_filesize 超限时 yt-dlp 是「静默成功」：不抛异常、不产生文件。
        # 此时 info 里有 requested_downloads / filepath 这些键，但文件不存在。
        #
        # 判断「是超限跳过还是下载中断」只能看**声明体积**：只有 info 里确实报了
        # 超过上限的 filesize/filesize_approx 才判 SizeLimitExceeded，
        # 否则一律当成可重试的中断（把网络问题误判成超限会让用户永远重试失败）。
        limits = self._current_limits()
        declared_size = _selected_size_bytes(info)
        if limits is not None and limits.size_exceeds(declared_size):
            raise SizeLimitExceeded(
                f"音频约 {int(declared_size) / 1024 / 1024:.1f}MB，"
                f"超过 {limits.max_size_mb}MB 大小上限，yt-dlp 已跳过下载"
                f"（可用环境变量 DOWNLOAD_MAX_SIZE_MB 调整）"
            )

        download_attempted = (
            "requested_downloads" in info or "filepath" in info or "requested_formats" in info
        )
        if not download_attempted:
            # 拿不到任何落盘信息（例如调用方传的替身只返回元信息）：沿用历史行为，
            # 按约定路径返回，由下游的文件校验兜底。
            logger.warning(f"yt-dlp 未返回落盘信息，按约定路径返回: {video_id}.{ext}")
            return os.path.join(output_dir, f"{video_id}.{ext}")

        raise RetryableDownloadError(
            f"yt-dlp 报告下载结束但找不到音频文件（video_id={video_id}），"
            f"可能是下载被中断，稍后重试"
        )

    @staticmethod
    def _remove_quietly(path: str) -> str:
        try:
            os.remove(path)
            return ""
        except OSError:
            return "失败"

    @staticmethod
    def _build_result(info: Optional[dict], audio_path: str) -> AudioDownloadResult:
        info = info or {}
        return AudioDownloadResult(
            file_path=audio_path,
            title=info.get("title"),
            duration=info.get("duration") or 0,
            cover_url=info.get("thumbnail"),
            platform="youtube",
            video_id=info.get("id"),
            raw_info={"tags": info.get("tags")},
            video_path=None,
        )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def download(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
        quality: DownloadQuality = "medium",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> AudioDownloadResult:
        output_dir = self._prepare_output_dir(output_dir)
        outtmpl = os.path.join(output_dir, "%(id)s.%(ext)s")
        limits = self._current_limits()
        max_filesize = limits.max_size_bytes if limits is not None else None

        with self._cookie_file() as cookiefile:
            if skip_download:
                # 元信息路径：不传 max_filesize、不做时长/体积判断
                # （本来就只取元信息，用下载上限去拒绝它是误杀）。
                probe_opts = self._build_ydl_opts(
                    format_selector=AUDIO_FORMAT_SELECTOR,
                    outtmpl=outtmpl,
                    cookiefile=cookiefile,
                    skip_download=True,
                )
                _apply_proxy(probe_opts)
                info = self._extract_with_403_fallback(
                    video_url, probe_opts, download=False
                )
                logger.info("YouTube 元信息提取完成（未下载音视频）")
                return self._build_result(
                    info, self._resolve_audio_path(info, output_dir, skip_download=True)
                )

            # 1) 预检：拿 duration / 选中格式体积，超限在下载前就拒绝。
            probe_opts = self._build_ydl_opts(
                format_selector=AUDIO_FORMAT_SELECTOR,
                outtmpl=outtmpl,
                cookiefile=cookiefile,
            )
            _apply_proxy(probe_opts)
            probe_info = self._extract_with_403_fallback(
                video_url, probe_opts, download=False
            )
            self._check_media_limits(probe_info)

            # 2) 真正下载音频，并按 quality 转码（quality 生效点）。
            #    match_filter 在格式选择前再拦一次：预检拿不到体积时它兜底，
            #    而 max_filesize 是「静默跳过」不能单独依赖。
            download_opts = self._build_ydl_opts(
                format_selector=AUDIO_FORMAT_SELECTOR,
                outtmpl=outtmpl,
                cookiefile=cookiefile,
                postprocessors=ydl_audio_postprocessor(quality),
                max_filesize=max_filesize,
                match_filter=self._make_match_filter(),
            )
            _apply_proxy(download_opts)
            logger.info(
                f"YouTube 下载音频（quality={quality}，"
                f"目标码率 {audio_bitrate_kbps(quality)}kbps）"
            )
            info = self._extract_with_403_fallback(
                video_url, download_opts, download=True
            )

        audio_path = self._resolve_audio_path(info, output_dir, skip_download=False)
        logger.info(f"YouTube 音频下载完成: {audio_path}")
        return self._build_result(info, audio_path)

    def download_video(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
    ) -> str:
        """下载视频（优先 avc1），返回校验过的本地视频文件路径。"""
        output_dir = self._prepare_output_dir(output_dir)
        outtmpl = os.path.join(output_dir, "%(id)s.%(ext)s")

        expected_path: Optional[str] = None
        video_id = extract_video_id(video_url, "youtube")
        if video_id:
            expected_path = os.path.join(output_dir, f"{video_id}.mp4")
            if os.path.isfile(expected_path) and os.path.getsize(expected_path) > 0:
                logger.info(f"YouTube 视频已存在，跳过下载: {expected_path}")
                return expected_path

        limits = self._current_limits()
        max_filesize = limits.max_size_bytes if limits is not None else None

        with self._cookie_file() as cookiefile:
            # 预检：4K 大文件在这里就被拒，而不是下完才发现超限。
            probe_opts = self._build_ydl_opts(
                format_selector=VIDEO_FORMAT_SELECTOR,
                outtmpl=outtmpl,
                cookiefile=cookiefile,
                merge_output_format="mp4",
            )
            _apply_proxy(probe_opts)
            probe_info = self._extract_with_403_fallback(
                video_url, probe_opts, download=False
            )
            self._check_media_limits(probe_info)

            download_opts = self._build_ydl_opts(
                format_selector=VIDEO_FORMAT_SELECTOR,
                outtmpl=outtmpl,
                cookiefile=cookiefile,
                max_filesize=max_filesize,
                merge_output_format="mp4",
                match_filter=self._make_match_filter(),
            )
            _apply_proxy(download_opts)
            logger.info("YouTube 开始下载视频（优先 avc1）")
            info = self._extract_with_403_fallback(
                video_url, download_opts, download=True
            )

        candidates = [expected_path] if expected_path else []
        candidates.extend(_candidate_file_paths(info))
        video_path = _first_existing_file([path for path in candidates if path])
        if not video_path:
            # 同音频路径：只有 info 明确报了超限体积才算 SizeLimitExceeded，
            # 否则按「下载中断」处理（可重试）。
            declared_size = _selected_size_bytes(info)
            if limits is not None and limits.size_exceeds(declared_size):
                raise SizeLimitExceeded(
                    f"视频约 {int(declared_size) / 1024 / 1024:.1f}MB，"
                    f"超过 {limits.max_size_mb}MB 大小上限，yt-dlp 已跳过下载"
                    f"（可用环境变量 DOWNLOAD_MAX_SIZE_MB 调整）"
                )
            raise RetryableDownloadError(
                "YouTube 视频下载结束后找不到文件，可能是下载被中断，稍后重试"
            )

        size = os.path.getsize(video_path)
        if limits is not None and limits.size_exceeds(size):
            self._remove_quietly(video_path)
            raise SizeLimitExceeded(
                f"下载的视频约 {size / 1024 / 1024:.1f}MB，"
                f"超过 {limits.max_size_mb}MB 上限"
                f"（可用环境变量 DOWNLOAD_MAX_SIZE_MB 调整）"
            )

        logger.info(f"YouTube 视频下载完成: {video_path}（{size / 1024 / 1024:.1f}MB）")
        return video_path

    def download_subtitles(
        self, video_url: str, output_dir: str = None, langs: List[str] = None
    ) -> Optional[TranscriptResult]:
        """
        通过 YouTube InnerTube API 直接获取字幕（优先人工字幕，其次自动生成）。
        比 yt_dlp 方式更轻量，无需写临时文件到磁盘。

        :param video_url: 视频链接
        :param output_dir: 未使用（保留接口兼容）
        :param langs: 优先语言列表
        :return: TranscriptResult 或 None
        """
        if langs is None:
            langs = ["zh-Hans", "zh", "zh-CN", "zh-TW", "en", "en-US", "ja"]

        video_id = extract_video_id(video_url, "youtube")
        if not video_id:
            logger.warning(f"YouTube 链接解析不出 video_id，跳过字幕获取: {video_url}")
            return None

        fetcher = YouTubeSubtitleFetcher()
        logger.info(f"尝试获取 YouTube 字幕，video_id={video_id}, langs={langs}")
        return fetcher.fetch_subtitles(video_id, langs)
