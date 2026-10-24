"""YouTube 下载链路测试。

全部离线：yt-dlp 用 `_FakeYoutubeDL` 替身（按 opts 返回脚本化的 info / 抛脚本化的
异常），临时目录用 pytest 的 `tmp_path`，不触网。

覆盖的每一条都对应一个实测确认过的历史问题（见 `youtube_downloader.py` 模块注释）：

1. Netscape Cookie 落成 0600 临时 cookies.txt，传给 `cookiefile`，**下载结束后文件
   与临时目录都被删除**（B 站 `NamedTemporaryFile(delete=False)` 永不清理的坑）
2. Cookie 为空时不传 `cookiefile`（传空文件 yt-dlp 会直接报错）
3. 403 才触发 `web_embedded` 回退；默认**不**带 `extractor_args`
4. `quality` 真正映射到 `preferredquality`，且是纯数字字符串（带 'k' 会被 yt-dlp
   的 Python API 静默忽略）
5. 时长 / 体积超限在下载前抛 `DurationLimitExceeded` / `SizeLimitExceeded`
6. `skip_download=True` 不做上限判断、不写媒体文件
7. `download_video` 格式选择器 avc1 优先 + 返回前校验文件存在且非空
8. `max_filesize` 静默跳过（不抛异常、不产生文件）要转成 `SizeLimitExceeded`
9. 字幕片段是 dataclass（不是 dict）时也要能正确解析出时间轴
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional

import pytest

from app.downloaders import youtube_downloader as module
from app.downloaders.errors import (
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
    is_retryable,
)
from app.downloaders.limits import DownloadLimits
from app.downloaders.youtube_downloader import YoutubeDownloader

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"

COOKIE_TEXT = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1800000000\tSID\tsid-value\n"
    ".youtube.com\tTRUE\t/\tFALSE\t1800000000\tPREF\ttz=Asia/Shanghai\n"
)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _FakeYoutubeDL:
    """按 `script` 顺序返回 dict / 抛 Exception，并记录每次构造时的 opts。"""

    # 所有实例共享的脚本队列（每个 extract_info 消费一条）
    script: List[Any] = []
    constructed: List[dict] = []

    def __init__(self, opts, *args, **kwargs):
        self.opts = opts
        type(self).constructed.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=True):
        if _FakeYoutubeDL.script:
            item = _FakeYoutubeDL.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        # 脚本用尽时按真实行为兜底：
        # - skip_download 的调用不产生任何文件（元信息路径）
        # - 真实下载的调用会落一个非空媒体文件，让 _resolve_audio_path 能找到
        if self.opts.get("skip_download"):
            return _default_info(ext=None)
        return _materialize_media(self.opts)


def _default_info(**overrides) -> Dict[str, Any]:
    info = {
        "id": VIDEO_ID,
        "title": "Example",
        "duration": 213,
        "thumbnail": "https://example.invalid/t.jpg",
        "ext": "m4a",
        "tags": ["tag"],
        "filesize": 3_449_447,
        "formats": [{"format_id": "140"}],
        "requested_formats": [],
    }
    info.update(overrides)
    return info


def _materialize_media(opts: dict) -> Dict[str, Any]:
    """按 outtmpl 真的写一个非空媒体文件，并返回指向它的 info。"""
    outtmpl = opts.get("outtmpl") or "%(id)s.%(ext)s"
    # 只有音频后处理会产生最终 .mp3；视频合并保留 .mp4
    target_ext = "mp3" if opts.get("postprocessors") else "mp4"
    path = outtmpl.replace("%(id)s", VIDEO_ID).replace("%(ext)s", target_ext)
    pathlib.Path(path).write_bytes(b"media" * 256)
    return _default_info(
        ext=target_ext,
        filepath=path,
        requested_downloads=[{"filepath": path}],
    )


@pytest.fixture
def fake_ytdlp(monkeypatch):
    """替换 yt_dlp.YoutubeDL，并重置脚本队列。

    用法：`fake_ytdlp.script = [probe_info, saved_audio_info]`，
    或插入 `Exception(...)` 让对应的 extract_info 抛错。
    """
    _FakeYoutubeDL.constructed = []
    _FakeYoutubeDL.script = []

    def fake_ctor(opts, *args, **kwargs):
        return _FakeYoutubeDL(opts)

    monkeypatch.setattr(module.yt_dlp, "YoutubeDL", fake_ctor)
    return _FakeYoutubeDL


def _saved_file_info(tmp_path, *, name: str, size: int = 1024, **overrides) -> Dict[str, Any]:
    """构造「已经真实落盘」的 info：在 tmp_path 里写一个非空文件并指向它。"""
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    info = _default_info(
        filepath=str(path),
        requested_downloads=[{"filepath": str(path)}],
    )
    info.update(overrides)
    return info


@pytest.fixture
def limits(monkeypatch):
    """把下载器读到的 limits 固定成可控值（默认 200MB / 300s）。"""

    current = {"value": DownloadLimits(max_size_mb=200, max_duration_seconds=300)}

    def fake_limits(self):
        return current["value"]

    monkeypatch.setattr(YoutubeDownloader, "_current_limits", fake_limits)
    return current


@pytest.fixture
def no_proxy(monkeypatch):
    monkeypatch.setattr(module, "_apply_proxy", lambda opts: opts)


def _make_downloader(cookies: Optional[str] = "", **kwargs) -> YoutubeDownloader:
    """构造下载器，默认不读真实 Cookie 配置。"""
    return YoutubeDownloader(cookies=cookies, **kwargs)


def _downloaded_media_opts(fake_ytdlp) -> List[dict]:
    """取出所有「真的会写盘」的 opts（排除预检那次）。"""
    return [opts for opts in fake_ytdlp.constructed if not opts.get("skip_download")]


# ---------------------------------------------------------------------------
# 1. Cookie
# ---------------------------------------------------------------------------
class TestNetscapeCookie:
    def test_cookie_file_is_created_and_passed_to_ytdlp(self, fake_ytdlp, limits, no_proxy, tmp_path):
        seen = {}

        def fake_constructor(opts, *args, **kwargs):
            cookie_path = opts.get("cookiefile")
            if cookie_path:
                # 构造 YoutubeDL 时文件必须已经存在且可读（yt-dlp 在 __init__ 里就加载它）
                seen["path"] = cookie_path
                seen["exists_during"] = os.path.exists(cookie_path)
                seen["mode"] = os.stat(cookie_path).st_mode & 0o777
                seen["content"] = pathlib.Path(cookie_path).read_text(encoding="utf-8")
            return _FakeYoutubeDL(opts)

        fake_ytdlp.constructed = []
        fake_ytdlp.script = []
        module.yt_dlp.YoutubeDL = fake_constructor
        try:
            _make_downloader(cookies=COOKIE_TEXT).download(
                VIDEO_URL, output_dir=str(tmp_path), skip_download=True
            )
        finally:
            module.yt_dlp.YoutubeDL = fake_ytdlp

        assert seen["exists_during"] is True
        # Cookie 是凭据：必须 0600，不能给同机其他用户读
        assert seen["mode"] == 0o600
        assert "# Netscape HTTP Cookie File" in seen["content"]
        assert "SID\tsid-value" in seen["content"]

    def test_cookie_temp_file_is_removed_after_download(self, fake_ytdlp, limits, no_proxy, tmp_path):
        cookie_paths: List[str] = []

        def fake_constructor(opts, *args, **kwargs):
            if opts.get("cookiefile"):
                cookie_paths.append(opts["cookiefile"])
            return _FakeYoutubeDL(opts)

        fake_ytdlp.constructed = []
        fake_ytdlp.script = []
        module.yt_dlp.YoutubeDL = fake_constructor
        try:
            _make_downloader(cookies=COOKIE_TEXT).download(
                VIDEO_URL, output_dir=str(tmp_path), skip_download=True
            )
        finally:
            module.yt_dlp.YoutubeDL = fake_ytdlp

        assert cookie_paths, "cookiefile 没有被传给 yt-dlp"
        for path in cookie_paths:
            assert not os.path.exists(path), f"临时 Cookie 文件未被清理: {path}"
            temp_dir = os.path.dirname(path)
            assert not os.path.exists(temp_dir), f"临时目录未被清理: {temp_dir}"

    def test_cookie_cleaned_up_even_when_download_fails(self, fake_ytdlp, limits, no_proxy, tmp_path):
        cookie_paths: List[str] = []

        def fake_constructor(opts, *args, **kwargs):
            if opts.get("cookiefile"):
                cookie_paths.append(opts["cookiefile"])
            return _FakeYoutubeDL(opts)

        fake_ytdlp.constructed = []
        fake_ytdlp.script = [RuntimeError("boom")]
        module.yt_dlp.YoutubeDL = fake_constructor
        try:
            with pytest.raises(RuntimeError):
                _make_downloader(cookies=COOKIE_TEXT).download(
                    VIDEO_URL, output_dir=str(tmp_path), skip_download=True
                )
        finally:
            module.yt_dlp.YoutubeDL = fake_ytdlp

        assert cookie_paths
        for path in cookie_paths:
            assert not os.path.exists(path)

    def test_empty_cookie_means_no_cookiefile(self, fake_ytdlp, limits, no_proxy, tmp_path):
        _make_downloader(cookies=None).download(
            VIDEO_URL, output_dir=str(tmp_path), skip_download=True
        )
        assert _FakeYoutubeDL.constructed
        for opts in _FakeYoutubeDL.constructed:
            assert "cookiefile" not in opts

    def test_blank_cookie_string_means_no_cookiefile(self, fake_ytdlp, limits, no_proxy, tmp_path):
        _make_downloader(cookies="").download(
            VIDEO_URL, output_dir=str(tmp_path), skip_download=True
        )
        for opts in _FakeYoutubeDL.constructed:
            assert "cookiefile" not in opts

    def test_cookie_content_never_logged(self, fake_ytdlp, limits, no_proxy, tmp_path, caplog):
        weird_cookie = (
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t1800000000\tSID\tSUPER_SECRET_VALUE_123\n"
        )
        with caplog.at_level("DEBUG"):
            _make_downloader(cookies=weird_cookie).download(
                VIDEO_URL, output_dir=str(tmp_path), skip_download=True
            )
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert "SUPER_SECRET_VALUE_123" not in joined


# ---------------------------------------------------------------------------
# 2. 403 回退
# ---------------------------------------------------------------------------
class TestForbiddenFallback:
    def test_default_opts_do_not_pin_player_client(self, fake_ytdlp, limits, no_proxy, tmp_path):
        """默认必须不传 extractor_args：yt-dlp 的默认 client 列表随版本演进。"""
        _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path), skip_download=True)
        assert _FakeYoutubeDL.constructed
        for opts in _FakeYoutubeDL.constructed:
            assert "extractor_args" not in opts

    def test_403_triggers_single_web_embedded_retry(self, fake_ytdlp, limits, no_proxy, tmp_path):
        fake_ytdlp.script = [
            Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden"),
            _default_info(),
        ]
        result = _make_downloader().download(
            VIDEO_URL, output_dir=str(tmp_path), skip_download=True
        )
        # 第一次默认参数 + 第二次回退参数
        assert len(_FakeYoutubeDL.constructed) == 2
        assert "extractor_args" not in _FakeYoutubeDL.constructed[0]
        fallback = _FakeYoutubeDL.constructed[1]["extractor_args"]["youtube"]["player_client"]
        assert "web_embedded" in fallback
        # 回退成功就不该抛
        assert result.video_id == VIDEO_ID

    def test_403_variants_are_recognized(self):
        for message in (
            "ERROR: HTTP Error 403: Forbidden",
            "unable to download video data: HTTP Error 403",
            "403: Forbidden",
            "http error 403",
        ):
            assert module._is_forbidden_error(Exception(message)), message
        assert not module._is_forbidden_error(Exception("HTTP Error 404"))
        assert not module._is_forbidden_error(Exception("Requested format is not available"))

    def test_non_403_errors_are_not_retried(self, fake_ytdlp, limits, no_proxy, tmp_path):
        fake_ytdlp.script = [RuntimeError("some other failure")]
        with pytest.raises(RuntimeError):
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path), skip_download=True)
        assert len(_FakeYoutubeDL.constructed) == 1

    def test_403_after_fallback_raises_retryable_with_hint(self, fake_ytdlp, limits, no_proxy, tmp_path):
        fake_ytdlp.script = [
            Exception("HTTP Error 403: Forbidden"),
            Exception("HTTP Error 403: Forbidden"),
        ]
        with pytest.raises(RetryableDownloadError) as exc_info:
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path), skip_download=True)
        message = str(exc_info.value)
        # 提示里要说清 403 主因通常是 PO Token / 出口 IP，不一定是 Cookie
        assert "403" in message
        assert "出口 IP" in message or "出口IP" in message
        # 重试本身失败后仍然是可重试类型（网络类），交给任务级重试
        assert is_retryable(exc_info.value) is True


# ---------------------------------------------------------------------------
# 3. quality
# ---------------------------------------------------------------------------
class TestQuality:
    @pytest.mark.parametrize(
        ("quality", "expected_bitrate"),
        [("fast", "64"), ("medium", "128"), ("slow", "192")],
    )
    def test_quality_maps_to_preferredquality(self, fake_ytdlp, limits, no_proxy, tmp_path, quality, expected_bitrate):
        _make_downloader().download(
            VIDEO_URL, output_dir=str(tmp_path), quality=quality, skip_download=False
        )
        media_opts = _downloaded_media_opts(fake_ytdlp)
        assert media_opts, "没有产生真实下载的 opts"
        postprocessors = media_opts[-1]["postprocessors"]
        extract = [pp for pp in postprocessors if pp["key"] == "FFmpegExtractAudio"]
        assert extract, postprocessors
        assert extract[0]["preferredquality"] == expected_bitrate
        # yt-dlp 的 Python API 不会 strip('k')：必须传纯数字字符串
        assert extract[0]["preferredquality"].isdigit()

    def test_unknown_quality_falls_back_to_medium(self, fake_ytdlp, limits, no_proxy, tmp_path):
        _make_downloader().download(
            VIDEO_URL, output_dir=str(tmp_path), quality="ultra-hyper", skip_download=False
        )
        media_opts = _downloaded_media_opts(fake_ytdlp)
        extract = [pp for pp in media_opts[-1]["postprocessors"] if pp["key"] == "FFmpegExtractAudio"]
        assert extract[0]["preferredquality"] == "128"


# ---------------------------------------------------------------------------
# 4. 体积 / 时长上限
# ---------------------------------------------------------------------------
class TestLimits:
    def test_duration_over_limit_raises_before_download(self, fake_ytdlp, limits, no_proxy, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=200, max_duration_seconds=60)
        fake_ytdlp.script = [_default_info(duration=213)]
        with pytest.raises(DurationLimitExceeded):
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))
        # 预检一次就够，不应该真的去下载
        assert all(opts.get("skip_download") or "max_filesize" not in opts for opts in _FakeYoutubeDL.constructed)

    def test_size_over_limit_raises_before_download(self, fake_ytdlp, limits, no_proxy, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=100, max_duration_seconds=0)
        fake_ytdlp.script = [_default_info(filesize=200 * 1024 * 1024)]
        with pytest.raises(SizeLimitExceeded):
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))

    def test_size_limit_counts_requested_formats(self, fake_ytdlp, limits, no_proxy, tmp_path):
        """音视频合并下载时要累加多路流的体积，不能只看第一路。"""
        limits["value"] = DownloadLimits(max_size_mb=100, max_duration_seconds=0)
        fake_ytdlp.script = [
            _default_info(
                duration=60,
                formats=[{"format_id": "137"}, {"format_id": "140"}],
                requested_formats=[
                    {"format_id": "137", "filesize": 60 * 1024 * 1024},
                    {"format_id": "140", "filesize": 60 * 1024 * 1024},
                ],
            )
        ]
        with pytest.raises(SizeLimitExceeded):
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))

    def test_max_filesize_is_passed_to_real_download(self, fake_ytdlp, limits, no_proxy, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=200, max_duration_seconds=300)
        _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))
        media_opts = _downloaded_media_opts(fake_ytdlp)
        assert media_opts[-1]["max_filesize"] == 200 * 1024 * 1024

    def test_limits_are_not_applied_on_skip_download(self, fake_ytdlp, limits, no_proxy, tmp_path):
        """元信息路径不做上限判断（本来就只取元信息）。"""
        limits["value"] = DownloadLimits(max_size_mb=1, max_duration_seconds=1)
        fake_ytdlp.script = [
            _default_info(duration=99999, filesize=999 * 1024 * 1024)
        ]
        result = _make_downloader().download(
            VIDEO_URL, output_dir=str(tmp_path), skip_download=True
        )
        assert result.duration == 99999
        for opts in _FakeYoutubeDL.constructed:
            assert opts.get("skip_download") is True
            assert "max_filesize" not in opts

    def test_match_filter_rejects_before_format_selection(self, fake_ytdlp, limits, no_proxy, tmp_path):
        """max_filesize 是静默跳过，match_filter 才是格式选择前的硬拦截。"""
        limits["value"] = DownloadLimits(max_size_mb=100, max_duration_seconds=0)
        _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))
        media_opts = _downloaded_media_opts(fake_ytdlp)
        assert "match_filter" in media_opts[-1]

        match_filter = media_opts[-1]["match_filter"]
        over = _default_info(filesize=200 * 1024 * 1024)
        with pytest.raises(SizeLimitExceeded):
            match_filter(over, incomplete=False)

    def test_match_filter_checks_duration_even_when_incomplete(self, fake_ytdlp, limits, no_proxy, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=0, max_duration_seconds=60)
        # 预检信息里的 duration 必须在限内，否则会在预检就抛出来
        fake_ytdlp.script = [_default_info(duration=10)]
        _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))
        match_filter = _downloaded_media_opts(fake_ytdlp)[-1]["match_filter"]
        # incomplete 时 formats 还没解析，体积不可信，但 duration 可用
        with pytest.raises(DurationLimitExceeded):
            match_filter(_default_info(duration=9999), incomplete=True)

    def test_size_abort_without_file_raises_size_limit(self, fake_ytdlp, limits, no_proxy, tmp_path):
        """yt-dlp 因 max_filesize 跳过时是「静默成功」：没有文件也不能当成功。"""
        limits["value"] = DownloadLimits(max_size_mb=200, max_duration_seconds=300)
        # 预检返回体积正常，真实下载返回 requested_downloads 但没有落盘文件
        fake_ytdlp.script = [
            _default_info(duration=60, filesize=1024, requested_formats=[]),
            _default_info(
                duration=60,
                filesize=None,
                ext="m4a",
                filepath=None,
                requested_downloads=[{"format_id": "140", "filesize": 999 * 1024 * 1024}],
            ),
        ]
        with pytest.raises(SizeLimitExceeded):
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))

    def test_missing_file_without_size_hint_is_retryable(self, fake_ytdlp, limits, no_proxy, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=0, max_duration_seconds=0)
        fake_ytdlp.script = [
            _default_info(duration=60, filesize=None, requested_formats=[]),
            _default_info(
                duration=60,
                filepath=None,
                requested_downloads=[{"format_id": "140"}],
            ),
        ]
        with pytest.raises(RetryableDownloadError):
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# 5. skip_download
# ---------------------------------------------------------------------------
class TestSkipDownload:
    def test_skip_download_writes_no_media_file(self, fake_ytdlp, limits, no_proxy, tmp_path):
        result = _make_downloader().download(
            VIDEO_URL, output_dir=str(tmp_path), skip_download=True
        )
        assert list(tmp_path.iterdir()) == []
        assert result.file_path.endswith(".m4a")
        assert not os.path.exists(result.file_path)

    def test_skip_download_sets_ignore_no_formats_error(self, fake_ytdlp, limits, no_proxy, tmp_path):
        _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path), skip_download=True)
        assert _FakeYoutubeDL.constructed[0]["ignore_no_formats_error"] is True

    def test_metadata_result_fields(self, fake_ytdlp, limits, no_proxy, tmp_path):
        result = _make_downloader().download(
            VIDEO_URL, output_dir=str(tmp_path), skip_download=True
        )
        assert result.platform == "youtube"
        assert result.video_id == VIDEO_ID
        assert result.title == "Example"
        assert result.duration == 213
        assert result.cover_url == "https://example.invalid/t.jpg"
        assert result.raw_info == {"tags": ["tag"]}


# ---------------------------------------------------------------------------
# 6. 真实下载的落盘定位
# ---------------------------------------------------------------------------
class TestAudioDownloadResult:
    def test_finds_postprocessed_file_path(self, fake_ytdlp, limits, no_proxy, tmp_path):
        audio_file = tmp_path / f"{VIDEO_ID}.mp3"
        audio_file.write_bytes(b"x" * 1024)
        fake_ytdlp.script = [
            _default_info(duration=10, filesize=1024),
            _default_info(
                duration=10,
                ext="m4a",
                filepath=str(audio_file),
                requested_downloads=[{"filepath": str(audio_file)}],
            ),
        ]
        result = _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))
        assert result.file_path == str(audio_file)

    def test_finds_file_from_requested_downloads(self, fake_ytdlp, limits, no_proxy, tmp_path):
        """后处理后顶层 filepath 可能是 None，真实路径在 requested_downloads 里（实测）。"""
        audio_file = tmp_path / f"{VIDEO_ID}.mp3"
        audio_file.write_bytes(b"x" * 512)
        fake_ytdlp.script = [
            _default_info(duration=10, filesize=512),
            _default_info(
                duration=10,
                ext="m4a",
                filepath=None,
                requested_downloads=[{"filepath": str(audio_file)}],
            ),
        ]
        result = _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))
        assert result.file_path == str(audio_file)

    def test_empty_file_is_not_accepted(self, fake_ytdlp, limits, no_proxy, tmp_path):
        empty = tmp_path / f"{VIDEO_ID}.mp3"
        empty.write_bytes(b"")
        fake_ytdlp.script = [
            _default_info(duration=10, filesize=0),
            _default_info(filepath=str(empty), requested_downloads=[{"filepath": str(empty)}]),
        ]
        with pytest.raises(RetryableDownloadError):
            _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# 7. download_video
# ---------------------------------------------------------------------------
class TestDownloadVideo:
    def test_format_selector_prefers_avc1(self, fake_ytdlp, limits, no_proxy, tmp_path):
        video_file = tmp_path / f"{VIDEO_ID}.mp4"
        video_file.write_bytes(b"v" * 2048)
        fake_ytdlp.script = [
            _default_info(duration=10, formats=[{"format_id": "137"}]),
            _default_info(duration=10, ext="mp4", filepath=str(video_file)),
        ]
        _make_downloader().download_video(VIDEO_URL, output_dir=str(tmp_path))
        for opts in _FakeYoutubeDL.constructed:
            assert "avc1" in opts["format"]
            assert opts["format"].index("avc1") < 200
            assert opts["merge_output_format"] == "mp4"

    def test_returns_existing_nonempty_file(self, fake_ytdlp, limits, no_proxy, tmp_path):
        video_file = tmp_path / f"{VIDEO_ID}.mp4"
        video_file.write_bytes(b"v" * 4096)
        fake_ytdlp.script = [
            _default_info(duration=10, formats=[{"format_id": "137"}]),
            _default_info(duration=10, ext="mp4", filepath=str(video_file)),
        ]
        path = _make_downloader().download_video(VIDEO_URL, output_dir=str(tmp_path))
        assert path == str(video_file)
        assert os.path.getsize(path) > 0

    def test_cached_video_is_reused_without_calling_ytdlp(self, fake_ytdlp, limits, no_proxy, tmp_path):
        cached = tmp_path / f"{VIDEO_ID}.mp4"
        cached.write_bytes(b"v" * 128)
        path = _make_downloader().download_video(VIDEO_URL, output_dir=str(tmp_path))
        assert path == str(cached)
        assert _FakeYoutubeDL.constructed == []

    def test_missing_output_raises(self, fake_ytdlp, limits, no_proxy, tmp_path):
        fake_ytdlp.script = [
            _default_info(duration=10, formats=[{"format_id": "137"}]),
            _default_info(duration=10, ext="mp4", filepath=None, requested_downloads=[]),
        ]
        with pytest.raises(RetryableDownloadError):
            _make_downloader().download_video(VIDEO_URL, output_dir=str(tmp_path))

    def test_empty_output_raises(self, fake_ytdlp, limits, no_proxy, tmp_path):
        empty = tmp_path / f"{VIDEO_ID}.mp4"
        empty.write_bytes(b"")
        fake_ytdlp.script = [
            _default_info(duration=10, formats=[{"format_id": "137"}]),
            _default_info(duration=10, ext="mp4", filepath=str(empty)),
        ]
        with pytest.raises(RetryableDownloadError):
            _make_downloader().download_video(VIDEO_URL, output_dir=str(tmp_path))

    def test_video_over_size_limit_is_removed(self, fake_ytdlp, limits, no_proxy, tmp_path):
        """预检拿不到体积时，落盘后必须再校验一次真实体积并删掉超限文件。

        注意文件名故意不等于 `{video_id}.mp4`：那是 download_video 的缓存快路径，
        同名文件会被直接当成「已下载」返回，测不到下载后的体积校验。
        """
        limits["value"] = DownloadLimits(max_size_mb=1, max_duration_seconds=0)
        big = tmp_path / "merged-output.mp4"
        big.write_bytes(b"v" * (2 * 1024 * 1024))
        fake_ytdlp.script = [
            _default_info(
                duration=10,
                formats=[{"format_id": "137"}],
                filesize=None,
                requested_formats=[],
            ),
            _default_info(duration=10, ext="mp4", filepath=str(big), filesize=None),
        ]
        with pytest.raises(SizeLimitExceeded):
            _make_downloader().download_video(VIDEO_URL, output_dir=str(tmp_path))
        assert not big.exists(), "超限视频应当被删除"


# ---------------------------------------------------------------------------
# 8. 代理保留
# ---------------------------------------------------------------------------
class TestProxy:
    def test_proxy_is_still_applied(self, fake_ytdlp, limits, monkeypatch, tmp_path):
        captured = {}

        def fake_apply(opts):
            opts["proxy"] = "http://127.0.0.1:7890"
            captured["called"] = True
            return opts

        monkeypatch.setattr(module, "_apply_proxy", fake_apply)
        _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path), skip_download=True)
        assert captured.get("called") is True
        assert _FakeYoutubeDL.constructed[0]["proxy"] == "http://127.0.0.1:7890"


# ---------------------------------------------------------------------------
# 9. metadata-only 兼容（历史行为）
# ---------------------------------------------------------------------------
class TestMetadataOnly:
    def test_missing_ext_falls_back_to_m4a(self, fake_ytdlp, limits, no_proxy, tmp_path):
        fake_ytdlp.script = [_default_info(ext=None)]
        result = _make_downloader().download(
            VIDEO_URL, output_dir=str(tmp_path), skip_download=True
        )
        assert result.file_path.endswith(".m4a")
        assert "None" not in result.file_path

    def test_full_download_still_selects_an_audio_format(self, fake_ytdlp, limits, no_proxy, tmp_path):
        audio = tmp_path / f"{VIDEO_ID}.mp3"
        audio.write_bytes(b"a" * 256)
        fake_ytdlp.script = [
            _default_info(duration=10, filesize=256),
            _default_info(duration=10, filepath=str(audio)),
        ]
        _make_downloader().download(VIDEO_URL, output_dir=str(tmp_path))
        media_opts = _downloaded_media_opts(fake_ytdlp)
        assert "bestaudio" in media_opts[-1]["format"]
        assert "ignore_no_formats_error" not in media_opts[-1]
