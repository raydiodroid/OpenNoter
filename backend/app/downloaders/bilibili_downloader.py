"""B 站下载器：音频轨道、视频文件、字幕，统一走 yt-dlp。

设计要点（2026-10 重写，实测依据见 ``_ref/findings/bilibili_guest_probe.md``）：

1. **音频不再转码成 64kbps mp3**。B 站 DASH 源音轨只有三档：
   30280(≈204kbps) / 30232(≈103kbps) / 30216(≈44kbps)。原实现写死
   ``preferredquality='64'``，等于主动丢掉 2/3 的信息量。现在默认**直接保留
   ``.m4a`` 原轨**：whisper / faster-whisper 内部都会用 ffmpeg 重采样到 16kHz
   单声道，预转码纯属二次有损。

   ``quality`` 因此改成「允许的最低档位下限」（见 ``QUALITY_AUDIO_FLOOR``）：
   fast 允许退到 44k 档、medium 至少 103k 档、slow 至少 204k 档；同时始终
   优先选当前身份能拿到的最高档。极少数只吃 mp3 的在线 ASR 可以设
   ``BILIBILI_AUDIO_CONTAINER=mp3`` 回退到 FFmpegExtractAudio 转码，此时码率
   由 ``app.downloaders.base.audio_bitrate_kbps(quality)`` 决定。

2. **落盘文件名与 yt-dlp 对齐**。yt-dlp 的 ``BiliBiliIE`` 用
   ``id = f'{bvid}{format_field(part_id, None, "_p%d")}'`` 命名：单 P 是
   ``BV1xxxxxxxxx``，多 P（**含第 1 集**）是 ``BV1xxxxxxxxx_p1/_p2/...``。
   原实现用 ``extract_video_id`` 拼 ``{bvid}.mp4`` 做存在性判断，多 P 永远命中
   不了，每次重新下载。现在统一走 ``_resolve_output_stem()``，下载后用
   yt-dlp 回报的 ``info['id']`` / ``requested_downloads[].filepath`` 校准。

3. **临时 Cookie 文件必须清理**。原实现 ``NamedTemporaryFile(delete=False)``
   且没有任何 unlink，容器跑几周会堆出上千个含 SESSDATA 明文的文件。现在改成
   下载方法内的 contextmanager（``mkdtemp`` + ``finally`` rmtree），并在
   ``__init__`` 里只持有一个 Cookie 管理器、**用到时才读 Cookie、写文件**。

4. **视频优先 avc1**。原格式选择器会选中 AV1（``av01.*``），Safari 和部分
   播放器不认；OpenNoter 的视频主要用于浏览器预览和截图，所以优先 AVC。

5. **风控兜底**：所有 B 站请求头带 ``Referer`` + ``Origin``（实测 ``Origin``
   能让缺少 dm_* 的 playurl 请求从 412 变 200），另见 ``bilibili_dm_patch``。

6. **体积/时长上限**走 ``self.limits``：下载前用元信息预检（``filesize_approx``
   对 B 站足够准），yt-dlp 再传 ``max_filesize`` 兜底，下载后**必须**校验文件
   存在且非空——``max_filesize`` 超限时 yt-dlp 是静默不写文件、不抛异常，
   只看返回值会把「没下」当成「下好了」。

7. ``skip_download=True`` 只取元信息、不下载任何媒体，供 note.py 在「已有
   字幕、不需要截图」时省一次完整下载。
"""

import contextlib
import json
import logging
import os
import shutil
import tempfile
from abc import ABC
from typing import List, Optional, Sequence, Union

import yt_dlp

from app.downloaders.base import (
    Downloader,
    DownloadQuality,
    YDL_RETRY_OPTS,
    audio_bitrate_kbps,
    normalize_quality,
    ydl_audio_postprocessor,
)
from app.downloaders.bilibili_dm_patch import apply_bilibili_dm_img_patch
from app.downloaders.bilibili_subtitle import BilibiliSubtitleFetcher
from app.downloaders.errors import (
    DownloadError,
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
)
from app.models.notes_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.cookie_manager import CookieConfigManager
from app.utils.url_parser import extract_bilibili_p_number, extract_video_id

logger = logging.getLogger(__name__)

# 注入 dm_img_* / web_location 风控参数（上游 yt-dlp 已有 dm_* 时会自动跳过）。
# 打不打 patch 都不影响 import，失败只在日志里留一条。
apply_bilibili_dm_img_patch()

# B 站请求必带的头。实测：
#   - 缺 Referer：部分 CDN 403
#   - 缺 Origin：playurl 在 dm 参数不完整时 412（带上 Origin 就能 200）
BILIBILI_HTTP_HEADERS = {
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
}

# B 站 DASH 音频三档：204k / 103k / 44k。按「从高到低」排列，格式选择器按这个
# 顺序取第一个可用的，因此正常情况下永远拿最高档。
AUDIO_FORMAT_TIERS: tuple = ("30280", "30232", "30216")

# 前端 quality -> 允许的最低音频档位（不转码，只决定「能退到多低」）。
#   对应关系依据实测码率：30280≈204kbps / 30232≈103kbps / 30216≈44kbps，
#   与 base.audio_bitrate_kbps() 给出的 64 / 128 / 192 目标并不要求相等——
#   B 站没有这两个码率的音轨，硬转码只会二次损失，所以这里只做「下限」约束。
QUALITY_AUDIO_FLOOR: dict = {
    "fast": "30216",
    "medium": "30232",
    "slow": "30280",
}

# 视频格式选择器：优先 H.264（AVC），Safari / 部分播放器不认 AV1 和 HEVC。
# 保留完整 fallback 链，避免老视频（没有 avc1 mp4 流）直接失败。
VIDEO_FORMAT_SELECTOR = (
    "bv*[vcodec^=avc1][ext=mp4]/bv*[ext=mp4]/bestvideo+bestaudio/best"
)

# 音频容器开关：默认 m4a（保留原轨）。唯一的例外是只接受 mp3 的在线 ASR。
AUDIO_CONTAINER_ENV = "BILIBILI_AUDIO_CONTAINER"
SUPPORTED_AUDIO_CONTAINERS = ("m4a", "mp3")

# 存在性探测时允许的媒体扩展名（按优先级）。
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".flv")
AUDIO_EXTS = (".m4a", ".mp3", ".aac", ".opus", ".flac", ".wav")

# yt-dlp 私有字段 / 大块结构，不要塞进 AudioDownloadResult.raw_info：
#   - 以 __ 开头的是内部状态（可能含不可 JSON 序列化的后处理对象）
#   - formats / requested_downloads 体积大，下游没人用
_RAW_INFO_DROP_KEYS = frozenset(
    {
        "formats",
        "requested_downloads",
        "requested_formats",
        "requested_subtitles",
        "entries",
        "automatic_captions",
        "subtitles",
    }
)


class _YtDlpLogger:
    """把 yt-dlp 的屏幕输出接进项目 logger，并保留一份消息供事后判断。

    为什么要留消息：``max_filesize`` 超限时 yt-dlp **不抛异常、不写文件**，只打
    一行 ``File is larger than max-filesize``，调用方只能靠这句话区分「超限」
    和「其它原因没下下来」。

    同时 ``quiet=True`` + logger 的组合还能避免 yt-dlp 的进度条直接往 stdout
    打，符合「日志统一走 logger」的约定。
    """

    _MAX_KEPT_MESSAGES = 400

    def __init__(self, base_logger: logging.Logger, sink: Optional[list] = None):
        self._logger = base_logger
        self.messages: list = sink if sink is not None else []

    def _keep(self, message) -> str:
        text = message if isinstance(message, str) else str(message)
        self.messages.append(text)
        if len(self.messages) > self._MAX_KEPT_MESSAGES:
            del self.messages[: len(self.messages) // 2]
        return text

    def debug(self, message):
        self._logger.debug("%s", self._keep(message))

    def info(self, message):
        self.debug(message)

    def warning(self, message):
        self._logger.warning("%s", self._keep(message))

    def error(self, message):
        self._logger.error("%s", self._keep(message))


class BilibiliDownloader(Downloader, ABC):
    def __init__(self):
        super().__init__()
        # 只持有管理器，不在 __init__ 里读 Cookie、更不落临时文件——
        # 下载器实例本身是按任务新建的（app.services.constant.get_downloader），
        # 但「构造即写盘」仍然是错误的（import 期/测试期都会留下垃圾文件）。
        self._cookie_mgr = CookieConfigManager()
        # 无 Cookie 也能正常解析/下载 360P–1080P，只是拿不到 1080P+ / AI 字幕。
        logger.debug("BilibiliDownloader 初始化完成（无 Cookie 也可下载普通画质）")

    # ------------------------------------------------------------------
    # Cookie（用到才读、写成临时 Netscape 文件、用完必删）
    # ------------------------------------------------------------------
    def _read_cookie(self) -> Optional[str]:
        """每次现读 Cookie（后台改配置后下一个任务即生效）。"""
        try:
            return self._cookie_mgr.get("bilibili")
        except Exception as exc:  # 配置文件损坏不该阻断下载
            logger.warning("读取 B站 Cookie 失败，将按无 Cookie 处理: %s", exc)
            return None

    @contextlib.contextmanager
    def _temporary_cookiefile(self):
        """把 Cookie 写成临时 Netscape cookies.txt，退出上下文时**一定**删掉。

        权限 0600；目录用 ``mkdtemp`` 而不是 ``NamedTemporaryFile(delete=False)``，
        保证无论正常结束还是异常退出，``finally`` 都能把整目录清掉。

        :return: 生成器 yield ``cookies.txt`` 路径；无 Cookie 时 yield ``None``
            （不要传空文件给 yt-dlp，那会直接报 cookie 解析错误）。
        """
        cookie = self._read_cookie()
        if not cookie:
            logger.debug("B站未配置 Cookie，跳过 Netscape Cookie 文件（不影响普通画质下载）")
            yield None
            return

        tmpdir = tempfile.mkdtemp(prefix="bili_cookie_")
        path = os.path.join(tmpdir, "cookies.txt")
        try:
            lines = ["# Netscape HTTP Cookie File\n"]
            for pair in cookie.split("; "):
                if "=" not in pair:
                    continue
                key, value = pair.split("=", 1)
                if not key.strip():
                    continue
                lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key.strip()}\t{value.strip()}\n")
            # 600 权限：文件里有 SESSDATA / bili_jct 这类登录凭据。
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            logger.debug("已生成临时 B站 Cookie 文件（%d 条，0600 权限，用完即删）", len(lines) - 1)
            yield path
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 输出文件名规则（yt-dlp 的 id 规则）
    # ------------------------------------------------------------------
    def _resolve_output_stem(self, video_url: str, part_id: Optional[int] = None) -> str:
        """推算 yt-dlp 会落盘的文件名主干（不含扩展名）。

        规则对齐 ``yt_dlp.extractor.bilibili.BiliBiliIE._real_extract``：
        ``id = f'{bvid}{format_field(part_id, None, "_p%d")}'``

        - 单 P 视频：``BV1xxxxxxxxx``
        - 多 P 视频的任意一集（**包含第 1 集**）：``BV1xxxxxxxxx_p1`` /
          ``BV1xxxxxxxxx_p2`` ...

        注意：URL 不带 ``?p=`` 时，离线无法判断「这是单 P 还是多 P 的第 1 集」，
        此时返回单 P 名字，调用方应把 :meth:`_candidate_output_stems` 的候选列表
        一起用于存在性探测，下载后再以 yt-dlp 回报的 ``info['id']`` 为准。

        :raises ValueError: URL 里提取不到 BV 号
        """
        bvid = extract_video_id(video_url, "bilibili")
        if not bvid:
            raise ValueError(f"无法从链接提取 B 站视频 ID: {video_url}")
        if part_id is None:
            part_id = extract_bilibili_p_number(video_url)
        return f"{bvid}_p{part_id}" if part_id else bvid

    def _candidate_output_stems(self, video_url: str) -> List[str]:
        """下载前做存在性探测用的候选主干，顺序即优先级。

        URL 无 ``?p=`` 时同时给出「单 P」和「多 P 第 1 集」两种可能，覆盖
        yt-dlp 的两种落盘形态；多 P 的其它集必须靠 URL 里的 ``?p=N`` 指定。

        这里只解析一次 BVID：``extract_video_id`` 对 b23.tv 短链会发 HTTP 请求，
        每个候选各调一次等于把短链解析做两遍。
        """
        bvid = extract_video_id(video_url, "bilibili")
        if not bvid:
            return []
        part_id = extract_bilibili_p_number(video_url)
        if part_id:
            return [f"{bvid}_p{part_id}"]
        return [bvid, f"{bvid}_p1"]

    @staticmethod
    def _find_existing_file(output_dir: str, stems: Sequence[str], exts: Sequence[str]) -> Optional[str]:
        """在候选主干 × 候选扩展名里找第一个存在且非空的文件。"""
        for stem in stems:
            if not stem:
                continue
            for ext in exts:
                path = os.path.join(output_dir, f"{stem}{ext}")
                try:
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        return path
                except OSError:
                    continue
        return None

    # ------------------------------------------------------------------
    # yt-dlp 选项
    # ------------------------------------------------------------------
    @staticmethod
    def _audio_container() -> str:
        raw = (os.getenv(AUDIO_CONTAINER_ENV) or "m4a").strip().lower().lstrip(".")
        if raw not in SUPPORTED_AUDIO_CONTAINERS:
            logger.warning(
                "%s=%r 不是支持的音频容器（%s），回退 m4a（保留原轨）",
                AUDIO_CONTAINER_ENV,
                raw,
                "/".join(SUPPORTED_AUDIO_CONTAINERS),
            )
            return "m4a"
        return raw

    @staticmethod
    def _audio_format_selector(quality) -> str:
        """quality 只决定「最低可接受档位」，永远优先最高档。

        fast   -> 30280/30232/30216/bestaudio
        medium -> 30280/30232/bestaudio
        slow   -> 30280/bestaudio
        """
        floor = QUALITY_AUDIO_FLOOR[normalize_quality(quality)]
        allowed = AUDIO_FORMAT_TIERS[: AUDIO_FORMAT_TIERS.index(floor) + 1]
        return "/".join([*allowed, "bestaudio"])

    def _audio_postprocessors(self, quality, container: str) -> list:
        """默认直接保留原轨（不加后处理）；只有显式要求 mp3 时才转码。"""
        if container == "m4a":
            return []
        return ydl_audio_postprocessor(quality, container=container)

    def _build_ydl_opts(
        self,
        *,
        format_selector: str,
        outtmpl: str,
        messages: list,
        cookiefile: Optional[str] = None,
        skip_download: bool = False,
        postprocessors: Optional[list] = None,
        merge_output_format: Optional[str] = None,
        max_filesize: Optional[int] = None,
    ) -> dict:
        """所有下载/取信息路径共用的 yt-dlp 选项。

        ``**YDL_RETRY_OPTS`` 必须保留：yt-dlp 的 Python API 不会套用 CLI 的
        ``--retries 10`` 默认值，不显式传就是 0 次重试（见 tests/test_ydl_retry_opts.py）。
        """
        ydl_opts = {
            **YDL_RETRY_OPTS,
            "format": format_selector,
            "outtmpl": outtmpl,
            "http_headers": dict(BILIBILI_HTTP_HEADERS),
            "noplaylist": True,
            "quiet": True,
            "noprogress": True,
            "logger": _YtDlpLogger(logger, messages),
        }
        if cookiefile:
            ydl_opts["cookiefile"] = cookiefile
        if skip_download:
            # 只取元信息：不下载任何媒体；格式缺失（yt-dlp 落后于 B 站改版）时
            # 退化成「没有格式」而不是把整个任务带崩。
            ydl_opts["skip_download"] = True
            ydl_opts["ignore_no_formats_error"] = True
        if max_filesize:
            ydl_opts["max_filesize"] = max_filesize
        if postprocessors:
            ydl_opts["postprocessors"] = postprocessors
        if merge_output_format:
            ydl_opts["merge_output_format"] = merge_output_format
        return ydl_opts

    # ------------------------------------------------------------------
    # 元信息 / 上限 / 错误分类
    # ------------------------------------------------------------------
    @staticmethod
    def _unwrap_playlist(info: dict) -> dict:
        """拿到真正的视频条目。

        正常情况下 ``noplaylist=True`` 已经保证不会拿到播放列表；但合集/活动页
        这类 URL 仍可能返回 playlist 包装，这里取第一条兜底。
        """
        if not isinstance(info, dict):
            return {}
        if info.get("_type") == "playlist" or (
            info.get("entries") and not info.get("formats") and info.get("_type") != "video"
        ):
            entries = [entry for entry in (info.get("entries") or []) if entry]
            if entries:
                return entries[0]
        return info

    def _enforce_limits(self, info: dict) -> None:
        """下载前的体积/时长预检。

        B 站 DASH 只有 ``filesize_approx``（tbr × duration），实测足够准；
        ``tbr`` 缺失时拿不到近似值，交给下载后的文件校验兜底。
        """
        limits = self.limits
        duration = info.get("duration")
        if limits.duration_exceeds(duration):
            raise DurationLimitExceeded(
                f"视频时长 {float(duration):.0f} 秒超过上限 {limits.max_duration_seconds} 秒"
            )

        size = info.get("filesize") or info.get("filesize_approx")
        if limits.size_exceeds(size):
            raise SizeLimitExceeded(
                f"媒体体积约 {int(size) / 1024 / 1024:.1f}MB 超过上限 {limits.max_size_mb}MB"
            )

    def _missing_output_error(
        self,
        *,
        kind: str,
        messages: Sequence[str],
        info: dict,
        candidates: Sequence[str],
    ) -> Exception:
        """yt-dlp 没有产出文件时，判断是「超限」还是「真失败」。

        ``max_filesize`` 超限是静默的（不抛异常、不写文件），所以只能靠
        yt-dlp 的消息文本和元信息里的体积近似值来判断。
        """
        for message in messages:
            text = str(message).lower()
            if "max-filesize" in text or "max_filesize" in text:
                return SizeLimitExceeded(
                    f"{kind}超过大小上限 {self.limits.max_size_mb}MB，已中止下载"
                )

        limits = self.limits
        size = info.get("filesize") or info.get("filesize_approx")
        if limits.size_exceeds(size):
            return SizeLimitExceeded(
                f"{kind}体积约 {int(size) / 1024 / 1024:.1f}MB 超过上限 {limits.max_size_mb}MB"
            )

        tried = "、".join(os.path.basename(path) for path in candidates) or "（无候选路径）"
        return RetryableDownloadError(f"{kind}下载失败：未找到输出文件（已尝试 {tried}）")

    @staticmethod
    def _translate_ydl_error(exc: BaseException) -> Exception:
        """把 yt-dlp 的异常翻译成 OpenNoter 的下载异常。

        - 明显不可重试的（视频不存在/私密/需要登录/URL 不支持）→ ``DownloadError``
        - 其余（CDN 抖动、read timeout、CDN 鉴权过期导致的 403）→ 可重试

        刻意**不**把 ``HTTP Error 403`` 当成永久失败：B 站 CDN 的直链带签名，
        签名过期时就是 403，重试一次通常就能拿到新地址（``errors.py`` 里也把
        「CDN 鉴权过期」列为可重试）。真正的「需要登录」类错误有自己的文案
        （login required / member only），不会落到 403 上。
        """
        if isinstance(exc, DownloadError):
            return exc
        text = str(exc).lower()
        fatal_markers = (
            "video unavailable",
            "video is private",
            "private video",
            "has been deleted",
            "deleted by the uploader",
            "removed by the uploader",
            "http error 404",
            "unsupported url",
            "login required",
            "member only",
            "this video is only available for",
        )
        message = f"B站下载失败: {exc}"
        if any(marker in text for marker in fatal_markers):
            return DownloadError(message)
        return RetryableDownloadError(message)

    @staticmethod
    def _downloaded_filepath(info: dict) -> Optional[str]:
        """从 yt-dlp 结果里取实际落盘路径（最权威的来源）。"""
        if not isinstance(info, dict):
            return None
        direct = info.get("filepath")
        if direct and os.path.isfile(direct):
            return direct
        for download in info.get("requested_downloads") or []:
            path = download.get("filepath") if isinstance(download, dict) else None
            if path and os.path.isfile(path):
                return path
        return None

    @staticmethod
    def _expected_path(output_dir: str, stem: str, ext: str) -> str:
        return os.path.join(output_dir, f"{stem}.{ext}")

    @staticmethod
    def _sanitize_raw_info(info: dict) -> dict:
        """裁掉 yt-dlp 私有字段和大块结构，保证能 JSON 序列化。

        下游（note.py 的音频缓存、chat_tools / vector_store）只读
        title/uploader/description/tags/webpage_url 等顶层字段；而
        ``info['__postprocessors']`` 里可能带后处理对象，直接 json.dumps 会抛
        TypeError 把任务带崩。
        """
        if not isinstance(info, dict):
            return {}
        raw = {
            key: value
            for key, value in info.items()
            if not str(key).startswith("__") and key not in _RAW_INFO_DROP_KEYS
        }
        try:
            json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            raw = {
                key: value
                for key, value in raw.items()
                if isinstance(value, (str, int, float, bool, list, dict, type(None)))
            }
        return raw

    # ------------------------------------------------------------------
    # 音频下载
    # ------------------------------------------------------------------
    def download(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
        quality: DownloadQuality = "medium",
        need_video: Optional[bool] = False,
        skip_download: bool = False,
    ) -> AudioDownloadResult:
        """下载音频（默认保留原始 m4a 音轨），或只取元信息。

        :param skip_download: True 时只取元信息、不下载任何媒体，
            ``file_path`` 是约定路径（不保证存在），供 note.py 走「已有字幕」捷径。
        """
        output_dir = self._resolve_output_dir(output_dir)
        outtmpl = os.path.join(output_dir, "%(id)s.%(ext)s")
        container = self._audio_container()
        format_selector = self._audio_format_selector(quality)
        postprocessors = self._audio_postprocessors(quality, container)

        logger.info(
            "B站音频下载开始（quality=%s，参考码率 %dkbps，容器=%s，格式=%s，skip_download=%s）",
            normalize_quality(quality),
            audio_bitrate_kbps(quality),
            container,
            format_selector,
            skip_download,
        )

        messages: list = []
        with self._temporary_cookiefile() as cookiefile:
            # 只取元信息时不设 max_filesize：这一步本来就不下载任何媒体，
            # 设了反而会让「视频很大但已有字幕」的任务无谓失败。
            max_filesize = None if skip_download else self.limits.max_size_bytes
            ydl_opts = self._build_ydl_opts(
                format_selector=format_selector,
                outtmpl=outtmpl,
                messages=messages,
                cookiefile=cookiefile,
                skip_download=skip_download,
                postprocessors=postprocessors,
                max_filesize=max_filesize,
            )
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    meta = self._unwrap_playlist(ydl.extract_info(video_url, download=False))
                    if not skip_download:
                        self._enforce_limits(meta)
                        # 复用已取到的元信息直接下载，避免二次请求 playurl。
                        info = ydl.process_ie_result(meta, download=True) or meta
                    else:
                        info = meta
            except (SizeLimitExceeded, DurationLimitExceeded):
                raise
            except Exception as exc:
                raise self._translate_ydl_error(exc) from exc

        video_id = info.get("id") or meta.get("id")
        title = info.get("title") or meta.get("title")
        duration = info.get("duration") or meta.get("duration") or 0
        cover_url = info.get("thumbnail") or meta.get("thumbnail")

        # 实际落盘扩展名：转码模式看容器，否则看 yt-dlp 报的 ext。
        actual_path = None if skip_download else self._downloaded_filepath(info)
        if skip_download:
            stem = video_id or self._resolve_output_stem(video_url)
            audio_path = self._expected_path(output_dir, stem, container)
        else:
            candidates = [
                self._expected_path(output_dir, video_id or "", container),
                self._expected_path(output_dir, video_id or "", info.get("ext") or container),
            ]
            audio_path = actual_path or self._find_existing_file(
                output_dir,
                [video_id or ""] + self._candidate_output_stems(video_url),
                [f".{container}", *AUDIO_EXTS],
            )
            if not audio_path or not self._is_valid_media_file(audio_path):
                raise self._missing_output_error(
                    kind="音频",
                    messages=messages,
                    info=info,
                    candidates=candidates,
                )
            logger.info("B站音频落盘: %s (%d 字节)", audio_path, os.path.getsize(audio_path))

        return AudioDownloadResult(
            file_path=audio_path,
            title=title,
            duration=duration,
            cover_url=cover_url,
            platform="bilibili",
            video_id=video_id,
            raw_info=self._sanitize_raw_info(info),
            video_path=None,  # 音频路径不包含视频文件
        )

    @staticmethod
    def _is_valid_media_file(path: Optional[str]) -> bool:
        """存在且非空——空文件通常是下载中途失败留下的残骸。"""
        if not path:
            return False
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    # ------------------------------------------------------------------
    # 视频下载
    # ------------------------------------------------------------------
    def download_video(
        self,
        video_url: str,
        output_dir: Union[str, None] = None,
    ) -> str:
        """下载视频，返回视频文件路径（优先 H.264，便于浏览器预览/截图）。"""
        output_dir = self._resolve_output_dir(output_dir)

        # 先按统一命名规则找已存在的成品，避免重复下载（多 P 的关键修复点）。
        existing = self._find_existing_file(
            output_dir, self._candidate_output_stems(video_url), VIDEO_EXTS
        )
        if existing:
            logger.info("B站视频已存在，跳过下载: %s", existing)
            return existing

        outtmpl = os.path.join(output_dir, "%(id)s.%(ext)s")
        messages: list = []
        with self._temporary_cookiefile() as cookiefile:
            ydl_opts = self._build_ydl_opts(
                format_selector=VIDEO_FORMAT_SELECTOR,
                outtmpl=outtmpl,
                messages=messages,
                cookiefile=cookiefile,
                merge_output_format="mp4",
                max_filesize=self.limits.max_size_bytes,
            )
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    meta = self._unwrap_playlist(ydl.extract_info(video_url, download=False))
                    self._enforce_limits(meta)
                    info = ydl.process_ie_result(meta, download=True) or meta
            except (SizeLimitExceeded, DurationLimitExceeded):
                raise
            except Exception as exc:
                raise self._translate_ydl_error(exc) from exc

        video_id = info.get("id") or meta.get("id")
        actual_path = self._downloaded_filepath(info)
        if actual_path and self._is_valid_media_file(actual_path):
            logger.info("B站视频落盘: %s (%d 字节)", actual_path, os.path.getsize(actual_path))
            return actual_path

        # yt-dlp 有时把合并结果写在 info['filepath'] 之外，退回到按 id 拼路径。
        fallback_stems = [video_id or "", *self._candidate_output_stems(video_url)]
        candidates = [
            self._expected_path(output_dir, stem, ext)
            for stem in fallback_stems
            for ext in VIDEO_EXTS
            if stem
        ]
        fallback = self._find_existing_file(output_dir, fallback_stems, VIDEO_EXTS)
        if fallback and self._is_valid_media_file(fallback):
            logger.info("B站视频落盘: %s (%d 字节)", fallback, os.path.getsize(fallback))
            return fallback

        raise self._missing_output_error(
            kind="视频",
            messages=messages,
            info=info,
            candidates=candidates,
        )

    def delete_video(self, video_path: str) -> str:
        """删除视频文件。"""
        if os.path.exists(video_path):
            os.remove(video_path)
            return f"视频文件已删除: {video_path}"
        return f"视频文件未找到: {video_path}"

    # ------------------------------------------------------------------
    # 字幕
    # ------------------------------------------------------------------
    def download_subtitles(
        self, video_url: str, output_dir: str = None, langs: List[str] = None
    ) -> Optional[TranscriptResult]:
        """尝试获取 B 站视频字幕（官方 player API 优先，yt-dlp 兜底）。"""
        # 1) 优先走 B 站官方 player API（直拉，无需下视频；AI 字幕需 SESSDATA cookie）
        try:
            result = BilibiliSubtitleFetcher().fetch_subtitles(video_url)
            if result and result.segments:
                return result
        except Exception as e:
            logger.warning(f"player API 直拉字幕异常，回退到 yt-dlp: {e}")

        # 2) Fallback：yt-dlp 路径（更脆弱，遇到签名/Cookie 问题失败概率较高）
        output_dir = self._resolve_output_dir(output_dir)

        if langs is None:
            langs = ["zh-Hans", "zh", "zh-CN", "ai-zh", "en", "en-US"]

        try:
            stem = self._resolve_output_stem(video_url)
        except ValueError:
            stem = ""

        messages: list = []
        with self._temporary_cookiefile() as cookiefile:
            ydl_opts = {
                **YDL_RETRY_OPTS,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": langs,
                "subtitlesformat": "srt/json3/best",
                "skip_download": True,
                "ignore_no_formats_error": True,
                "outtmpl": os.path.join(output_dir, f"{stem or '%(id)s'}.%(ext)s"),
                "http_headers": dict(BILIBILI_HTTP_HEADERS),
                "noplaylist": True,
                "quiet": True,
                "noprogress": True,
                "logger": _YtDlpLogger(logger, messages),
            }
            if cookiefile:
                ydl_opts["cookiefile"] = cookiefile

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = self._unwrap_playlist(ydl.extract_info(video_url, download=True))

                subtitles = info.get("requested_subtitles") or {}
                if not subtitles:
                    logger.info("B站视频 %s 没有可用字幕", info.get("id") or stem)
                    return None

                # 按优先级查找字幕
                detected_lang = None
                sub_info = None
                for lang in langs:
                    if lang in subtitles:
                        detected_lang = lang
                        sub_info = subtitles[lang]
                        break

                # 如果按优先级没找到，取第一个可用的（排除弹幕）
                if not detected_lang:
                    for lang, info_item in subtitles.items():
                        if lang != "danmaku":  # 排除弹幕
                            detected_lang = lang
                            sub_info = info_item
                            break

                if not sub_info:
                    logger.info("B站视频 %s 没有可用字幕（排除弹幕）", info.get("id") or stem)
                    return None

                # 检查是否有内嵌数据（yt-dlp 有时直接返回字幕内容）
                if sub_info.get("data"):
                    logger.info("直接从返回数据解析字幕: %s", detected_lang)
                    return self._parse_srt_content(sub_info["data"], detected_lang)

                # 字幕文件路径：优先 yt-dlp 报的 filepath，否则按统一命名规则推算
                subtitle_file = sub_info.get("filepath")
                if not subtitle_file or not os.path.isfile(subtitle_file):
                    subtitle_file = self._locate_subtitle_file(
                        output_dir,
                        info,
                        stem,
                        detected_lang,
                        sub_info.get("ext") or "srt",
                    )
                if not subtitle_file:
                    logger.info("字幕文件不存在（lang=%s）", detected_lang)
                    return None

                ext = sub_info.get("ext") or os.path.splitext(subtitle_file)[1].lstrip(".")
                if ext == "json3":
                    return self._parse_json3_subtitle(subtitle_file, detected_lang)
                with open(subtitle_file, "r", encoding="utf-8") as f:
                    return self._parse_srt_content(f.read(), detected_lang)

            except Exception as e:
                logger.warning(f"获取B站字幕失败: {e}")
                return None

    def _locate_subtitle_file(
        self,
        output_dir: str,
        info: dict,
        stem: str,
        lang: str,
        ext: str,
    ) -> Optional[str]:
        """按 yt-dlp 的落盘规则找字幕文件。

        字幕文件名的 base 来自 ``prepare_filename(info, 'subtitle')``，也就是
        ``{stem}.{lang}.{ext}``；这里同时考虑 URL 推算的 stem 和 yt-dlp 回报的
        id，覆盖「URL 没带 ?p= 但实际是多 P」的情况。
        """
        stems = [stem]
        actual_id = info.get("id")
        if actual_id and actual_id not in stems:
            stems.append(actual_id)
        for candidate_stem in stems:
            if not candidate_stem:
                continue
            for candidate_ext in {ext, "srt", "json3", "vtt", "json"}:
                path = os.path.join(output_dir, f"{candidate_stem}.{lang}.{candidate_ext}")
                if os.path.isfile(path):
                    return path
        logger.debug("字幕文件未找到（stem 候选=%s, lang=%s, ext=%s）", stems, lang, ext)
        return None

    def _parse_srt_content(self, srt_content: str, language: str) -> Optional[TranscriptResult]:
        """解析 SRT 格式字幕内容。"""
        import re

        try:
            segments = []
            # SRT 格式: 序号\n时间戳\n文本\n\n
            pattern = r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n\d+\n|$)"
            matches = re.findall(pattern, srt_content, re.DOTALL)

            for _idx, start_time, end_time, text in matches:
                text = text.strip()
                if not text:
                    continue

                # 转换时间格式 00:00:00,000 -> 秒
                def time_to_seconds(t):
                    parts = t.replace(",", ".").split(":")
                    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

                segments.append(
                    TranscriptSegment(
                        start=time_to_seconds(start_time),
                        end=time_to_seconds(end_time),
                        text=text,
                    )
                )

            if not segments:
                return None

            full_text = " ".join(seg.text for seg in segments)
            logger.info("成功解析B站SRT字幕，共 %d 段", len(segments))
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={"source": "bilibili_subtitle", "format": "srt"},
            )

        except Exception as e:
            logger.warning(f"解析SRT字幕失败: {e}")
            return None

    def _parse_json3_subtitle(
        self, subtitle_file: str, language: str
    ) -> Optional[TranscriptResult]:
        """解析 json3 格式字幕文件。"""
        try:
            with open(subtitle_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            segments = []
            for event in data.get("events", []):
                # json3 格式中时间单位是毫秒
                start_ms = event.get("tStartMs", 0)
                duration_ms = event.get("dDurationMs", 0)
                text = "".join(seg.get("utf8", "") for seg in event.get("segs", [])).strip()
                if text:  # 只添加非空文本
                    segments.append(
                        TranscriptSegment(
                            start=start_ms / 1000.0,
                            end=(start_ms + duration_ms) / 1000.0,
                            text=text,
                        )
                    )

            if not segments:
                return None

            full_text = " ".join(seg.text for seg in segments)
            logger.info("成功解析B站字幕，共 %d 段", len(segments))
            return TranscriptResult(
                language=language,
                full_text=full_text,
                segments=segments,
                raw={"source": "bilibili_subtitle", "file": subtitle_file},
            )

        except Exception as e:
            logger.warning(f"解析字幕文件失败: {e}")
            return None
