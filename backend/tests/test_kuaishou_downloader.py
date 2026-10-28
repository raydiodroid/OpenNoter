"""快手下载链路测试。

全部离线：`requests` 用假实现替身（记录调用参数并按脚本返回），ffmpeg 用假的
`subprocess.run`，CookieConfigManager 用 monkeypatch 顶掉。不触网、不需要 ffmpeg。

覆盖的历史问题（见 `kuaishou_downloader.py` / `kuaishou_helper/kuaishou.py` 注释）：

1. 所有 requests 调用都带 `timeout=(5, 30)`（历史实现一个都没有，对端不响应就挂死任务）
2. `print` 改 logger，且不把整个 GraphQL 响应（几十 KB）打进日志
3. `cfm = CookieConfigManager()` 模块级单例改成每次现读（改 Cookie 后不必重启）
4. `cookies` 为 None 时不能 `AttributeError: 'NoneType' has no attribute 'strip'`
5. 补 `skip_download` 参数（历史传这个参数直接 TypeError，导致「省流量」优化失效）
6. 补体积上限：流式写入过程中超限立即中断并删除半成品
7. `download_video` 返回前校验文件存在且非空（历史可能返回不存在的 mp4）
8. `photo.duration` 是毫秒，要归一成秒（否则 11 秒视频被判成 3 小时，被时长上限拒掉）
9. `photo.caption` 可能是 None（历史 `.strip()` 直接 AttributeError）
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from app.downloaders import kuaishou_downloader as downloader_module
from app.downloaders import kuaishou_helper
from app.downloaders.errors import (
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
    is_retryable,
)
from app.downloaders.kuaishou_downloader import KuaiShouDownloader
from app.downloaders.kuaishou_helper.kuaishou import (
    KuaiShou,
    KuaishouError,
    extract_photo_id,
    normalize_duration_seconds,
)
from app.downloaders.limits import DownloadLimits

VIDEO_ID = "3xdqx42tk6mcxvi"
VIDEO_URL = f"https://www.kuaishou.com/short-video/{VIDEO_ID}"
SHARE_TEXT = f"某段分享文案 {VIDEO_URL} 更多文字"


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, *, status_code: int = 200, url: str = "", json_data=None,
                 cookies: Optional[Dict[str, str]] = None, content: bytes = b"",
                 chunks: Optional[List[bytes]] = None, text: str = ""):
        self.status_code = status_code
        self.url = url
        self._json = json_data
        self.cookies = _FakeCookies(cookies or {})
        self.content = content
        self.text = text
        self._chunks = list(chunks or [])

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeCookies:
    def __init__(self, data: Dict[str, str]):
        self._data = dict(data)

    def get_dict(self) -> Dict[str, str]:
        return dict(self._data)


class _FakeRequests:
    """记录所有调用（含 timeout），按 URL 匹配脚本化响应。"""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.get_responses: List[Any] = []
        self.post_responses: List[Any] = []
        self.raise_on_get = None
        self.raise_on_post = None

    def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        if self.raise_on_get is not None:
            raise self.raise_on_get
        if self.get_responses:
            return self.get_responses.pop(0)
        return _FakeResponse(url=url)

    def post(self, url=None, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if self.raise_on_post is not None:
            raise self.raise_on_post
        if self.post_responses:
            return self.post_responses.pop(0)
        return _FakeResponse(json_data={})


def _detail_payload(
    *,
    photo_id: str = VIDEO_ID,
    duration: int = 11000,
    caption: str = "测试标题",
    photo_url: str = "https://cdn.example.invalid/v.mp4",
    cover: str = "https://cdn.example.invalid/c.jpg",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "visionVideoDetail": {
            "status": 1,
            "photo": {
                "id": photo_id,
                "duration": duration,
                "caption": caption,
                "coverUrl": cover,
                "photoUrl": photo_url,
            },
        },
        "tags": [{"name": name} for name in (tags or ["生活", "搞笑"])],
    }


@pytest.fixture
def fake_requests(monkeypatch):
    fake = _FakeRequests()
    monkeypatch.setattr(kuaishou_helper.kuaishou, "requests", fake)
    monkeypatch.setattr(downloader_module, "requests", fake)
    return fake


@pytest.fixture
def stored_cookie(monkeypatch):
    """顶掉 Cookie 解析入口：默认返回一段 header Cookie。

    BYOK 之后生产代码不再直接调 ``CookieConfigManager().get()``，而是走
    ``credential_resolver.resolve_cookie(user_id, platform)``（它内部先看
    用户自己的 Cookie、再回退站点）。所以这里 mock 的层级也随之更新——
    断言仍然检查「传给解析层的平台名是否正确」。
    """
    state = {"value": "did=web_abc; kpf=PC_WEB; clientid=3"}

    def _fake_resolve_cookie(user_id, platform):
        assert platform == "kuaishou"
        return state["value"]

    import app.services.credential_resolver as _cr

    monkeypatch.setattr(_cr, "resolve_cookie", _fake_resolve_cookie, raising=False)
    return state


@pytest.fixture
def limits(monkeypatch):
    current = {"value": DownloadLimits(max_size_mb=200, max_duration_seconds=300)}

    def fake_limits(self):
        return current["value"]

    monkeypatch.setattr(KuaiShouDownloader, "_limits_now", fake_limits)
    return current


@pytest.fixture
def no_ffmpeg(monkeypatch):
    """不让测试真的跑 ffmpeg：按目标路径写一个非空文件。"""
    recorded = {}

    def fake_run(args, **kwargs):
        recorded["args"] = list(args)
        recorded["kwargs"] = kwargs
        # ffmpeg 调用形如 [... , mp3_path]，最后一个是输出
        out_path = args[-1]
        pathlib.Path(out_path).write_bytes(b"mp3" * 128)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(downloader_module.subprocess, "run", fake_run)
    return recorded


def _make_downloader() -> KuaiShouDownloader:
    return KuaiShouDownloader()


def _setup_detail(fake_requests, payload=None):
    fake_requests.get_responses = [
        _FakeResponse(url=f"https://www.kuaishou.com/short-video/{VIDEO_ID}")
    ]
    fake_requests.post_responses = [
        _FakeResponse(json_data={"data": payload if payload is not None else _detail_payload()})
    ]


# ---------------------------------------------------------------------------
# 1. timeout
# ---------------------------------------------------------------------------
class TestRequestTimeouts:
    def test_short_link_redirect_has_timeout(self, fake_requests, stored_cookie):
        # get_photo_id 用到的 GET 必须带 timeout
        fake_requests.get_responses = [
            _FakeResponse(url=f"https://www.kuaishou.com/short-video/{VIDEO_ID}")
        ]
        KuaiShou().get_photo_id("https://v.kuaishou.com/abc")
        assert fake_requests.calls
        assert fake_requests.calls[0]["timeout"] == (5, 30)

    def test_anonymous_cookie_homepage_get_has_timeout(self, fake_requests, stored_cookie):
        stored_cookie["value"] = None  # 逼它走「访问首页拿匿名 Cookie」
        fake_requests.get_responses = [
            _FakeResponse(url="https://www.kuaishou.com/", cookies={"did": "web_x", "kpf": "PC"})
        ]
        cookies = KuaiShou().get_temp_cookies()
        assert cookies == "did=web_x; kpf=PC"
        assert fake_requests.calls[0]["timeout"] == (5, 30)

    def test_graphql_post_has_timeout(self, fake_requests, stored_cookie):
        fake_requests.get_responses = [
            _FakeResponse(url=f"https://www.kuaishou.com/short-video/{VIDEO_ID}")
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        KuaiShou().run(SHARE_TEXT)
        posts = [c for c in fake_requests.calls if c["method"] == "POST"]
        assert posts
        assert posts[0]["timeout"] == (5, 30)

    def test_mp4_download_has_timeout(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        _setup_detail(fake_requests)
        fake_requests.get_responses.append(
            _FakeResponse(url="https://cdn.example.invalid/v.mp4", chunks=[b"x" * 1024])
        )
        _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        mp4_calls = [c for c in fake_requests.calls if c.get("stream")]
        assert mp4_calls
        assert mp4_calls[0]["timeout"] == (5, 30)


# ---------------------------------------------------------------------------
# 2. Cookie
# ---------------------------------------------------------------------------
class TestCookieHandling:
    def test_cookie_is_read_fresh_each_call(self, fake_requests, stored_cookie):
        """没有模块级单例：改配置后下一次调用立刻生效。"""
        fake_requests.get_responses = [
            _FakeResponse(url=f"https://www.kuaishou.com/short-video/{VIDEO_ID}"),
            _FakeResponse(url=f"https://www.kuaishou.com/short-video/{VIDEO_ID}"),
        ]
        fake_requests.post_responses = [
            _FakeResponse(json_data={"data": _detail_payload()}),
            _FakeResponse(json_data={"data": _detail_payload()}),
        ]
        KuaiShou().run(SHARE_TEXT)
        first_cookie = [c for c in fake_requests.calls if c["method"] == "POST"][0]["headers"]["Cookie"]

        stored_cookie["value"] = "did=web_NEW; kpf=PC_WEB"
        KuaiShou().run(SHARE_TEXT)
        posts = [c for c in fake_requests.calls if c["method"] == "POST"]
        assert first_cookie != posts[-1]["headers"]["Cookie"]
        assert "did=web_NEW" in posts[-1]["headers"]["Cookie"]

    def test_no_cookie_at_all_raises_clear_error(self, fake_requests, stored_cookie):
        """历史 bug：只 logger.error 不 return，随后 cookies.strip() AttributeError。"""
        stored_cookie["value"] = None
        fake_requests.get_responses = [_FakeResponse(url="https://www.kuaishou.com/", cookies={})]
        with pytest.raises(KuaishouError) as exc_info:
            KuaiShou().run(SHARE_TEXT)
        message = str(exc_info.value)
        assert "Cookie" in message
        # 不能是 AttributeError 原样冒出来
        assert "NoneType" not in message

    def test_cookie_error_is_not_retryable(self):
        assert is_retryable(KuaishouError("x")) is False


# ---------------------------------------------------------------------------
# 3. 日志 / print
# ---------------------------------------------------------------------------
class TestLogging:
    def test_no_print_and_no_bulk_payload_in_logs(self, fake_requests, stored_cookie, capsys, caplog):
        _setup_detail(fake_requests)
        with caplog.at_level("DEBUG"):
            KuaiShou().run(SHARE_TEXT)
        captured = capsys.readouterr()
        assert captured.out == "", f"仍有 print 输出: {captured.out!r}"
        assert captured.err == ""
        joined = "\n".join(record.getMessage() for record in caplog.records)
        # 不该把整个响应体打出来
        assert "visionVideoDetail" not in joined
        assert "cdn.example.invalid" not in joined


# ---------------------------------------------------------------------------
# 4. photo id / 链接解析
# ---------------------------------------------------------------------------
class TestPhotoIdParsing:
    @pytest.mark.parametrize(
        "url",
        [
            f"https://www.kuaishou.com/short-video/{VIDEO_ID}",
            f"https://www.kuaishou.com/short-video/{VIDEO_ID}?cc=share_copylink",
            f"https://m.gifshow.com/fw/photo/{VIDEO_ID}",
            f"https://www.kuaishou.com/photo/{VIDEO_ID}",
            f"https://www.kuaishou.com/?photoId={VIDEO_ID}",
        ],
    )
    def test_extract_photo_id_shapes(self, url):
        assert extract_photo_id(url) == VIDEO_ID

    def test_extract_photo_id_garbage(self):
        assert extract_photo_id("https://www.kuaishou.com/") is None
        assert extract_photo_id("") is None
        assert extract_photo_id(None) is None

    def test_share_text_without_link_raises_clear_error(self, fake_requests, stored_cookie):
        with pytest.raises(KuaishouError):
            KuaiShou().run("这段文案里没有链接")

    def test_redirect_to_mobile_page_is_recognized(self, fake_requests, stored_cookie):
        fake_requests.get_responses = [
            _FakeResponse(url=f"https://m.gifshow.com/fw/photo/{VIDEO_ID}")
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        KuaiShou().run("https://v.kuaishou.com/abc")
        post = [c for c in fake_requests.calls if c["method"] == "POST"][0]
        assert post["json"]["variables"]["photoId"] == VIDEO_ID


# ---------------------------------------------------------------------------
# 5. duration 单位
# ---------------------------------------------------------------------------
class TestDurationUnit:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(11000, 11.0), (185423, 185.423), (500, 500.0), (0, 0.0), (None, 0.0), ("", 0.0)],
    )
    def test_normalize(self, raw, expected):
        assert normalize_duration_seconds(raw) == expected

    def test_download_reports_seconds_not_milliseconds(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            _FakeResponse(chunks=[b"x" * 1024]),
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload(duration=11000)})]
        result = _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        assert result.duration == pytest.approx(11.0)

    def test_long_video_rejected_by_duration_limit(self, fake_requests, stored_cookie, limits, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=200, max_duration_seconds=60)
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [
            _FakeResponse(json_data={"data": _detail_payload(duration=185_000)})
        ]
        with pytest.raises(DurationLimitExceeded):
            _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# 6. skip_download
# ---------------------------------------------------------------------------
class TestSkipDownload:
    def test_skip_download_downloads_no_media(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        result = _make_downloader().download(
            SHARE_TEXT, output_dir=str(tmp_path), skip_download=True
        )
        assert list(tmp_path.iterdir()) == []
        assert result.video_id == VIDEO_ID
        assert result.title == "测试标题"
        assert result.duration == pytest.approx(11.0)
        assert result.platform == "kuaishou"
        # 不应该下载 mp4
        assert not [c for c in fake_requests.calls if c.get("stream")]

    def test_skip_download_accepts_quality_and_need_video(self, fake_requests, stored_cookie, limits, tmp_path):
        """签名必须和基类一致（历史 TypeError 的根因）。"""
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        _make_downloader().download(
            SHARE_TEXT,
            output_dir=str(tmp_path),
            quality="slow",
            need_video=True,
            skip_download=True,
        )


# ---------------------------------------------------------------------------
# 7. 体积上限
# ---------------------------------------------------------------------------
class TestSizeLimit:
    def test_download_aborts_when_exceeding_limit(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=1, max_duration_seconds=300)
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            # 2MB 分两块写，第二块会超过 1MB 上限
            _FakeResponse(chunks=[b"x" * (1024 * 1024), b"x" * (1024 * 1024)]),
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        with pytest.raises(SizeLimitExceeded):
            _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        # 半成品必须删掉
        assert list(tmp_path.iterdir()) == []

    def test_partial_file_removed_on_limit(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        limits["value"] = DownloadLimits(max_size_mb=1, max_duration_seconds=300)
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            _FakeResponse(chunks=[b"x" * (2 * 1024 * 1024)]),
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        with pytest.raises(SizeLimitExceeded):
            _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        assert not (tmp_path / f"{VIDEO_ID}.mp4").exists()


# ---------------------------------------------------------------------------
# 8. 音频码率 / 转换
# ---------------------------------------------------------------------------
class TestAudioConversion:
    @pytest.mark.parametrize(("quality", "bitrate"), [("fast", "64k"), ("medium", "128k"), ("slow", "192k")])
    def test_ffmpeg_bitrate_follows_quality(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path, quality, bitrate):
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            _FakeResponse(chunks=[b"x" * 1024]),
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path), quality=quality)
        args = no_ffmpeg["args"]
        assert bitrate in args, args
        # 单声道：whisper 只吃单声道
        assert "-ac" in args and "1" in args
        # 只抽音轨，不要视频
        assert "-vn" in args


# ---------------------------------------------------------------------------
# 9. download_video 校验
# ---------------------------------------------------------------------------
class TestDownloadVideo:
    def test_returns_path_when_file_exists(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            _FakeResponse(chunks=[b"x" * 1024]),
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        path = _make_downloader().download_video(SHARE_TEXT, output_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert os.path.getsize(path) > 0

    def test_raises_when_download_skipped_mp4(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        """已有 mp3 时 download() 会提前返回；此时 mp4 可能不存在，必须校验。"""
        # 预置 mp3 让 download() 命中缓存提前返回（不下载 mp4）
        (tmp_path / f"{VIDEO_ID}.mp3").write_bytes(b"mp3" * 64)
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        with pytest.raises(RetryableDownloadError):
            _make_downloader().download_video(SHARE_TEXT, output_dir=str(tmp_path))

    def test_reuses_existing_mp3(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        mp3_path = tmp_path / f"{VIDEO_ID}.mp3"
        mp3_path.write_bytes(b"mp3" * 64)
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload()})]
        result = _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        assert result.file_path == str(mp3_path)
        # 不应再下载 mp4
        assert not [c for c in fake_requests.calls if c.get("stream")]


# ---------------------------------------------------------------------------
# 10. 字段容错
# ---------------------------------------------------------------------------
class TestFieldRobustness:
    def test_none_caption_does_not_crash(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            _FakeResponse(chunks=[b"x" * 512]),
        ]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": _detail_payload(caption=None)})]
        result = _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        assert result.title == ""

    def test_caption_newlines_are_cleaned(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            _FakeResponse(chunks=[b"x" * 512]),
        ]
        fake_requests.post_responses = [
            _FakeResponse(json_data={"data": _detail_payload(caption="第一行\n第二行")})
        ]
        result = _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        assert result.title == "第一行第二行"

    def test_missing_photo_raises_clear_error(self, fake_requests, stored_cookie, limits, tmp_path):
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [
            _FakeResponse(json_data={"data": {"visionVideoDetail": {"photo": None}}})
        ]
        with pytest.raises(KuaishouError):
            _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))

    def test_missing_photo_url_raises_clear_error(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        payload = _detail_payload()
        payload["visionVideoDetail"]["photo"]["photoUrl"] = None
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [_FakeResponse(json_data={"data": payload})]
        with pytest.raises(KuaishouError):
            _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))

    def test_rate_limited_response_raises_clear_error(self, fake_requests, stored_cookie, limits, tmp_path):
        """风控响应（result=400002）不能被当成成功。"""
        fake_requests.get_responses = [_FakeResponse(url=VIDEO_URL)]
        fake_requests.post_responses = [
            _FakeResponse(json_data={"data": {"result": 400002, "url": "https://captcha..."}})
        ]
        with pytest.raises(KuaishouError):
            _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))

    def test_tags_are_joined(self, fake_requests, stored_cookie, limits, no_ffmpeg, tmp_path):
        fake_requests.get_responses = [
            _FakeResponse(url=VIDEO_URL),
            _FakeResponse(chunks=[b"x" * 512]),
        ]
        fake_requests.post_responses = [
            _FakeResponse(json_data={"data": _detail_payload(tags=["a", "b", "c"])})
        ]
        result = _make_downloader().download(SHARE_TEXT, output_dir=str(tmp_path))
        assert result.raw_info["tags"] == "a,b,c"
