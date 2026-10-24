"""B 站下载器回归测试。

覆盖这轮修复的 8 个问题里可离线验证的部分：

1. 音频码率：默认**保留原始 m4a**（不二次有损转码）；``quality`` 现在控制
   「可接受的最低音轨档位」（30280≥30232≥30216），mp3 回退模式下码率跟随
   ``audio_bitrate_kbps(quality)`` 且不能带 ``k``。
2. 输出文件名：单 P / 多 P（含 p=1）/ av 号三种输入都必须算对主干，
   与 yt-dlp ``id = f'{bvid}{"_p%d" % part_id}'`` 规则一致。
3. 临时 Cookie 文件：用到才写、0600、下载结束（含异常）后必须被删掉。
4. 视频格式选择器：avc1 优先（Safari 不认 AV1）。
5. skip_download=True：只取元信息、不产生媒体文件。
6. 体积/时长上限：max_filesize 透传、预检超限抛不可重试异常、
   max_filesize 静默跳过时必须抛 SizeLimitExceeded 而不是返回不存在的路径。
7. ``raw_info`` 必须可 JSON 序列化（note.py 会把 AudioDownloadResult 直接
   json.dumps 进音频缓存）。

所有测试都不访问真实网络：yt-dlp 的 YoutubeDL 被替换成 FakeYoutubeDL。
"""

import json
import os
import stat
from dataclasses import asdict

import pytest

from app.downloaders import bilibili_downloader as module
from app.downloaders.bilibili_downloader import BilibiliDownloader
from app.downloaders.errors import (
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
)

BILIBILI_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
MULTI_P_URL = "https://www.bilibili.com/video/BV1Ranw6DEYW?p=2"
AV_URL = "https://www.bilibili.com/video/av170001"

# BV17x411w7KC 是 av170001 的 BV 号（转换算法见 app/utils/bilibili_ids.py）
AV_AS_BV = "BV17x411w7KC"


def _base_meta(**overrides):
    meta = {
        "id": "BV1GJ411x7h7",
        "title": "【官方 MV】Never Gonna Give You Up",
        "duration": 212.4,
        "thumbnail": "https://i0.hdslb.com/example.jpg",
        "ext": "m4a",
        "tags": ["MV", "Rick Astley"],
        "uploader": "UP主",
        "description": "简介",
        "webpage_url": BILIBILI_URL,
        # B 站 DASH 音频只有 filesize_approx（filesize 为 NA）
        "filesize": None,
        "filesize_approx": 5_410_339,
        "formats": [{"format_id": "30280"}],
        # 模拟 yt-dlp 内部字段：含不可序列化对象，验证 raw_info 清洗
        "__postprocessors": [object()],
        "__write_download_archive": True,
    }
    meta.update(overrides)
    return meta


class FakeYoutubeDL:
    """替身：记录构造参数，按需写一个占位文件，绝不联网。"""

    instances = []
    meta = None
    behaviour = "ok"
    # cookiefile 路径 -> (内容, 权限)；必须在下载过程中抓，下载结束就被删了
    cookiefile_snapshots = {}

    def __init__(self, opts):
        self.opts = opts
        FakeYoutubeDL.instances.append(self)

    # -- 上下文管理 --
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    # -- yt-dlp 接口 --
    def extract_info(self, url, download=True):
        return dict(FakeYoutubeDL.meta)

    def process_ie_result(self, info, download=True):
        info = dict(info)
        opts = self.opts
        logger = opts.get("logger")

        # 临时 Cookie 文件在下载过程中才存在，必须在这里抓取内容/权限快照
        self.snapshot_cookiefile()

        if opts.get("skip_download"):
            return info

        if FakeYoutubeDL.behaviour == "max_filesize_skip":
            # 复刻 yt-dlp 的静默跳过：只打一行日志，不抛异常、不写文件。
            if logger is not None:
                logger.debug(
                    "[download] File is larger than max-filesize (999999999 bytes > 1 bytes). Aborting."
                )
            return info

        outtmpl = opts["outtmpl"]
        outdir = os.path.dirname(outtmpl)
        ext = info.get("ext") or "m4a"
        path = os.path.join(outdir, f"{info['id']}.{ext}")
        with open(path, "wb") as fh:
            fh.write(b"fake-media-bytes")
        info["filepath"] = path
        info["requested_downloads"] = [{"filepath": path, "ext": ext}]
        return info

    def snapshot_cookiefile(self):
        """在下载过程中读取临时 Cookie 文件内容/权限，供断言使用。"""
        path = self.opts.get("cookiefile")
        if not path or not os.path.exists(path):
            return None
        mode = stat.S_IMODE(os.stat(path).st_mode)
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        FakeYoutubeDL.cookiefile_snapshots[path] = (content, mode)
        return content


@pytest.fixture
def fake_ydl(monkeypatch):
    """把模块里的 yt_dlp.YoutubeDL 换成 FakeYoutubeDL，并在结束后复位。"""
    FakeYoutubeDL.instances = []
    FakeYoutubeDL.meta = _base_meta()
    FakeYoutubeDL.behaviour = "ok"
    FakeYoutubeDL.cookiefile_snapshots = {}
    monkeypatch.setattr(module.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    # 每个用例都从「没有 Cookie」开始，避免依赖开发机真实配置
    monkeypatch.setattr(BilibiliDownloader, "_read_cookie", lambda self: None)
    monkeypatch.delenv(module.AUDIO_CONTAINER_ENV, raising=False)
    return FakeYoutubeDL


def _last_opts():
    return FakeYoutubeDL.instances[-1].opts


# ---------------------------------------------------------------------------
# 1. 音频：保留原轨 / quality 生效 / 不转码
# ---------------------------------------------------------------------------
class TestAudioFormatSelection:
    def test_default_keeps_original_m4a_without_postprocessing(self, tmp_path, fake_ydl):
        result = BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))

        opts = _last_opts()
        # 不再写死 64k 转码：默认不加 FFmpegExtractAudio
        assert "postprocessors" not in opts
        assert result.file_path.endswith(".m4a")
        assert os.path.getsize(result.file_path) > 0

    @pytest.mark.parametrize(
        "quality,expected_tiers,forbidden",
        [
            ("fast", ["30280", "30232", "30216"], None),
            ("medium", ["30280", "30232"], "30216"),
            ("slow", ["30280"], "30232"),
        ],
    )
    def test_quality_controls_lowest_allowed_audio_tier(
        self, tmp_path, fake_ydl, quality, expected_tiers, forbidden
    ):
        BilibiliDownloader().download(
            BILIBILI_URL, output_dir=str(tmp_path), quality=quality
        )
        selector = _last_opts()["format"]
        for tier in expected_tiers:
            assert tier in selector
        if forbidden:
            assert forbidden not in selector
        # 无论哪档，最高档 204k 都必须排在最前
        assert selector.split("/")[0] == "30280"

    def test_mp3_opt_in_uses_quality_bitrate_without_k_suffix(self, tmp_path, fake_ydl, monkeypatch):
        """极少数只吃 mp3 的在线 ASR：走 FFmpegExtractAudio，码率跟随 quality。

        yt-dlp 的 Python API 不会像 CLI 那样 strip('k')，传 '128k' 会静默退化成
        编码器默认质量，所以这里必须是不带单位后缀的纯数字。
        """
        monkeypatch.setenv(module.AUDIO_CONTAINER_ENV, "mp3")
        BilibiliDownloader().download(
            BILIBILI_URL, output_dir=str(tmp_path), quality="medium"
        )
        postprocessors = _last_opts()["postprocessors"]
        assert len(postprocessors) == 1
        pp = postprocessors[0]
        assert pp["preferredcodec"] == "mp3"
        assert pp["preferredquality"] == "128"

    def test_unknown_audio_container_falls_back_to_m4a(self, tmp_path, fake_ydl, monkeypatch):
        monkeypatch.setenv(module.AUDIO_CONTAINER_ENV, "ogg")
        BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))
        assert "postprocessors" not in _last_opts()


# ---------------------------------------------------------------------------
# 2. 输出文件名一致性（单 P / 多 P / av 号）
# ---------------------------------------------------------------------------
class TestOutputStem:
    @pytest.mark.parametrize(
        "url,expected",
        [
            (BILIBILI_URL, "BV1GJ411x7h7"),
            (MULTI_P_URL, "BV1Ranw6DEYW_p2"),
            (AV_URL, AV_AS_BV),
            # 多 P 的第 1 集也带 _p1：yt-dlp 只要选集（页数>1）就会加后缀
            (f"{BILIBILI_URL}?p=1", "BV1GJ411x7h7_p1"),
            # 带追踪参数不影响
            (
                f"{BILIBILI_URL}?spm_id_from=333.999.0.0&vd_source=abc",
                "BV1GJ411x7h7",
            ),
        ],
    )
    def test_resolve_output_stem(self, url, expected):
        assert BilibiliDownloader()._resolve_output_stem(url) == expected

    def test_existing_multi_p_video_is_reused(self, tmp_path, fake_ydl):
        """多 P 视频落盘名是 {id}_p2.mp4，存在性判断必须命中，不能再下一遍。"""
        target = tmp_path / "BV1Ranw6DEYW_p2.mp4"
        target.write_bytes(b"existing-video")

        path = BilibiliDownloader().download_video(MULTI_P_URL, output_dir=str(tmp_path))

        assert path == str(target)
        assert FakeYoutubeDL.instances == []  # 没有调用 yt-dlp

    def test_single_p_existing_video_is_reused(self, tmp_path, fake_ydl):
        target = tmp_path / "BV1GJ411x7h7.mp4"
        target.write_bytes(b"existing-video")

        path = BilibiliDownloader().download_video(BILIBILI_URL, output_dir=str(tmp_path))

        assert path == str(target)
        assert FakeYoutubeDL.instances == []

    def test_av_url_uses_bv_stem_for_existing_file(self, tmp_path, fake_ydl):
        same_video = tmp_path / f"{AV_AS_BV}.mp4"
        same_video.write_bytes(b"existing-video")

        path = BilibiliDownloader().download_video(AV_URL, output_dir=str(tmp_path))

        assert path == str(same_video)
        assert FakeYoutubeDL.instances == []

    def test_empty_existing_video_is_not_treated_as_valid(self, tmp_path, fake_ydl):
        """0 字节残文件不能当成「已下载」。"""
        (tmp_path / "BV1GJ411x7h7.mp4").write_bytes(b"")
        FakeYoutubeDL.meta = _base_meta(id="BV1GJ411x7h7", ext="mp4")

        BilibiliDownloader().download_video(BILIBILI_URL, output_dir=str(tmp_path))

        assert len(FakeYoutubeDL.instances) == 1  # 真的重新下载了

    def test_download_video_uses_id_reported_by_ytdlp(self, tmp_path, fake_ydl):
        """URL 不带 ?p= 但实际是多 P 时，以 yt-dlp 回报的 id 为准。"""
        FakeYoutubeDL.meta = _base_meta(id="BV1GJ411x7h7_p1", ext="mp4")

        path = BilibiliDownloader().download_video(BILIBILI_URL, output_dir=str(tmp_path))

        assert os.path.basename(path) == "BV1GJ411x7h7_p1.mp4"
        assert os.path.getsize(path) > 0


# ---------------------------------------------------------------------------
# 3. 临时 Cookie 文件：用到才写、0600、用完必删
# ---------------------------------------------------------------------------
class TestTemporaryCookieFile:
    def test_no_cookie_means_no_cookiefile(self, tmp_path, fake_ydl):
        BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))
        assert "cookiefile" not in _last_opts()

    def test_cookiefile_is_written_0600_and_removed_after_download(
        self, tmp_path, fake_ydl, monkeypatch
    ):
        monkeypatch.setattr(
            BilibiliDownloader,
            "_read_cookie",
            lambda self: "SESSDATA=secret; bili_jct=csrf",
        )

        downloader = BilibiliDownloader()
        result = downloader.download(BILIBILI_URL, output_dir=str(tmp_path))

        cookiefile = _last_opts()["cookiefile"]
        content, mode = FakeYoutubeDL.cookiefile_snapshots[cookiefile]

        # 内容：Netscape 格式 + 登录项
        assert content is not None
        assert content.startswith("# Netscape HTTP Cookie File")
        assert "SESSDATA\tsecret" in content
        assert "\tbili_jct\tcsrf" in content
        # 权限 0600（文件里是登录凭据）
        assert mode == 0o600
        # 下载结束后文件与所在临时目录都必须消失
        assert not os.path.exists(cookiefile)
        assert not os.path.exists(os.path.dirname(cookiefile))
        assert result.file_path.endswith(".m4a")

    def test_cookiefile_is_removed_even_when_download_raises(self, tmp_path, fake_ydl, monkeypatch):
        monkeypatch.setattr(BilibiliDownloader, "_read_cookie", lambda self: "SESSDATA=secret")

        captured = {}
        original_process = FakeYoutubeDL.process_ie_result

        def boom(self, info, download=True):
            captured["cookiefile"] = self.opts["cookiefile"]
            raise RuntimeError("network exploded")

        monkeypatch.setattr(FakeYoutubeDL, "process_ie_result", boom)

        with pytest.raises(RetryableDownloadError):
            BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))

        monkeypatch.setattr(FakeYoutubeDL, "process_ie_result", original_process)
        assert not os.path.exists(captured["cookiefile"])

    def test_constructor_does_not_touch_disk(self, monkeypatch):
        """__init__ 里不允许落临时文件（历史 bug：每个实例泄漏一个明文 Cookie 文件）。"""
        created = []
        monkeypatch.setattr(
            module.tempfile, "mkdtemp", lambda **kwargs: created.append(kwargs) or "/tmp/x"
        )
        BilibiliDownloader()
        assert created == []

    def test_download_subtitles_also_cleans_up_cookiefile(self, tmp_path, fake_ydl, monkeypatch):
        monkeypatch.setattr(BilibiliDownloader, "_read_cookie", lambda self: "SESSDATA=secret")

        captured = {}

        def extract_info(self, url, download=True):
            captured["cookiefile"] = self.opts.get("cookiefile")
            return {
                "id": "BV1GJ411x7h7",
                "requested_subtitles": {
                    "zh-Hans": {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\n你好\n",
                    }
                },
            }

        monkeypatch.setattr(FakeYoutubeDL, "extract_info", extract_info)

        result = BilibiliDownloader().download_subtitles(BILIBILI_URL, output_dir=str(tmp_path))

        assert result is not None
        assert result.segments and result.segments[0].text == "你好"
        assert captured["cookiefile"]
        assert not os.path.exists(captured["cookiefile"])

    def test_subtitle_failure_from_player_api_falls_back(self, tmp_path, fake_ydl, monkeypatch):
        """player API 抛异常时回退到 yt-dlp；两者都没有字幕就返回 None。"""
        from app.downloaders import bilibili_subtitle

        class BoomFetcher:
            def fetch_subtitles(self, _url):
                raise RuntimeError("player api down")

        monkeypatch.setattr(bilibili_subtitle, "BilibiliSubtitleFetcher", BoomFetcher)
        FakeYoutubeDL.meta = {"id": "BV1GJ411x7h7", "requested_subtitles": {}}

        assert BilibiliDownloader().download_subtitles(BILIBILI_URL, output_dir=str(tmp_path)) is None

    def test_subtitle_file_found_when_ytdlp_omits_filepath(self, tmp_path, fake_ydl, monkeypatch):
        """yt-dlp 只回 requested_subtitles 不写 filepath 时，按命名规则也要能找到。

        多 P 场景下 URL 推算的 stem 与 yt-dlp 的 id 一致（_p2），字幕文件名是
        ``{stem}.{lang}.srt``。
        """
        from app.downloaders import bilibili_subtitle

        class BoomFetcher:
            def fetch_subtitles(self, _url):
                raise RuntimeError("player api down")

        monkeypatch.setattr(bilibili_subtitle, "BilibiliSubtitleFetcher", BoomFetcher)

        def extract_info(self, url, download=True):
            outdir = os.path.dirname(self.opts["outtmpl"])
            stem = os.path.basename(self.opts["outtmpl"]).replace(".%(ext)s", "")
            with open(
                os.path.join(outdir, f"{stem}.zh-Hans.srt"), "w", encoding="utf-8"
            ) as fh:
                fh.write(
                    "1\n00:00:01,000 --> 00:00:02,500\n第一句\n\n"
                    "2\n00:00:03,000 --> 00:00:04,000\n第二句\n"
                )
            return {
                "id": stem,
                "requested_subtitles": {"zh-Hans": {"ext": "srt", "filepath": None}},
            }

        monkeypatch.setattr(FakeYoutubeDL, "extract_info", extract_info)

        result = BilibiliDownloader().download_subtitles(MULTI_P_URL, output_dir=str(tmp_path))

        assert result is not None
        assert [(seg.start, seg.end, seg.text) for seg in result.segments] == [
            (1.0, 2.5, "第一句"),
            (3.0, 4.0, "第二句"),
        ]


# ---------------------------------------------------------------------------
# 4. 视频格式：avc1 优先
# ---------------------------------------------------------------------------
class TestVideoFormat:
    def test_video_format_prefers_avc1(self, tmp_path, fake_ydl):
        FakeYoutubeDL.meta = _base_meta(ext="mp4")
        BilibiliDownloader().download_video(BILIBILI_URL, output_dir=str(tmp_path))

        selector = _last_opts()["format"]
        assert selector.startswith("bv*[vcodec^=avc1][ext=mp4]")
        # fallback 链不能丢：老视频不一定有 avc1 的 mp4 流
        assert "bestvideo+bestaudio" in selector
        assert _last_opts()["merge_output_format"] == "mp4"

    def test_requests_carry_origin_and_referer(self, tmp_path, fake_ydl):
        """实测缺 Origin 时 playurl 更容易 412，两者都要带。"""
        BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))
        headers = _last_opts()["http_headers"]
        assert headers["Referer"] == "https://www.bilibili.com"
        assert headers["Origin"] == "https://www.bilibili.com"


# ---------------------------------------------------------------------------
# 5. skip_download
# ---------------------------------------------------------------------------
class TestSkipDownload:
    def test_skip_download_produces_no_media_file(self, tmp_path, fake_ydl):
        result = BilibiliDownloader().download(
            BILIBILI_URL, output_dir=str(tmp_path), skip_download=True
        )

        opts = _last_opts()
        assert opts.get("skip_download") is True
        assert opts.get("ignore_no_formats_error") is True
        # 元信息照常返回
        assert result.title
        assert result.duration == pytest.approx(212.4)
        assert result.video_id == "BV1GJ411x7h7"
        # 目录里不能有任何媒体文件（约定路径允许不存在）
        media = [name for name in os.listdir(tmp_path) if name.endswith((".m4a", ".mp3", ".mp4"))]
        assert media == []
        assert result.file_path.endswith(".m4a")

    def test_skip_download_does_not_pass_max_filesize(self, tmp_path, fake_ydl, monkeypatch):
        """只取元信息时不该套体积上限，否则「大视频但已有字幕」会无谓失败。"""
        monkeypatch.setenv("DOWNLOAD_MAX_SIZE_MB", "1")
        BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path), skip_download=True)
        assert "max_filesize" not in _last_opts()


# ---------------------------------------------------------------------------
# 6. 体积 / 时长上限
# ---------------------------------------------------------------------------
class TestLimits:
    def test_max_filesize_is_passed_through(self, tmp_path, fake_ydl, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_MAX_SIZE_MB", "200")
        BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))
        assert _last_opts()["max_filesize"] == 200 * 1024 * 1024

    def test_duration_limit_raises_before_download(self, tmp_path, fake_ydl, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_MAX_DURATION_SECONDS", "60")
        FakeYoutubeDL.meta = _base_meta(duration=3600)

        with pytest.raises(DurationLimitExceeded):
            BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))

        # 预检失败时不应该进入下载阶段
        assert not any(
            os.path.basename(name).endswith(".m4a") for name in os.listdir(tmp_path)
        )

    def test_size_limit_raises_before_download(self, tmp_path, fake_ydl, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_MAX_SIZE_MB", "1")
        FakeYoutubeDL.meta = _base_meta(filesize_approx=50 * 1024 * 1024)

        with pytest.raises(SizeLimitExceeded):
            BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))

    def test_silent_max_filesize_skip_is_reported_as_size_limit(self, tmp_path, fake_ydl):
        """max_filesize 超限时 yt-dlp 不抛异常也不写文件，必须自己判超限。"""
        FakeYoutubeDL.behaviour = "max_filesize_skip"

        with pytest.raises(SizeLimitExceeded):
            BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))

    def test_missing_output_without_size_evidence_is_retryable(self, tmp_path, fake_ydl, monkeypatch):
        """没有产出文件、也不像超限：算可重试失败，绝不返回不存在的路径。"""
        FakeYoutubeDL.meta = _base_meta(filesize_approx=None)
        monkeypatch.setattr(
            FakeYoutubeDL,
            "process_ie_result",
            lambda self, info, download=True: dict(info),
        )

        with pytest.raises(RetryableDownloadError):
            BilibiliDownloader().download(BILIBILI_URL, output_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# 7. raw_info 可序列化（note.py 会 json.dumps 进音频缓存）
# ---------------------------------------------------------------------------
class TestRawInfoSerializable:
    def test_raw_info_is_json_serializable_and_keeps_useful_fields(self, tmp_path, fake_ydl):
        downloader = BilibiliDownloader()
        result = downloader.download(BILIBILI_URL, output_dir=str(tmp_path))

        # 与 note.py 写缓存时的行为一致
        json.dumps(asdict(result), ensure_ascii=False)

        raw = result.raw_info
        assert raw["uploader"] == "UP主"
        assert raw["tags"] == ["MV", "Rick Astley"]
        assert raw["webpage_url"] == BILIBILI_URL
        # 私有字段 / 大块结构被裁掉
        assert not any(str(key).startswith("__") for key in raw)
        assert "formats" not in raw
        assert "requested_downloads" not in raw


# ---------------------------------------------------------------------------
# 8. dm 风控 patch 探测逻辑
# ---------------------------------------------------------------------------
class TestErrorClassification:
    """异常翻译：决定 note.py 要不要重试。

    403 必须可重试——B 站 CDN 直链带签名，签名过期时就是 403，重试一次
    通常会重新签发地址；误判成永久失败会让一次抖动变成任务失败。
    """

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("ERROR: unable to download video data: HTTP Error 403: Forbidden", RetryableDownloadError),
            ("ERROR: HTTP Error 412: Precondition Failed", RetryableDownloadError),
            ("read timed out", RetryableDownloadError),
            ("ERROR: [BiliBili] 1234: Video unavailable", module.DownloadError),
            ("ERROR: This video is private", module.DownloadError),
            ("ERROR: HTTP Error 404: Not Found", module.DownloadError),
        ],
    )
    def test_translate_ydl_error(self, message, expected):
        translated = BilibiliDownloader._translate_ydl_error(RuntimeError(message))
        assert type(translated) is expected

    def test_download_error_passes_through_unchanged(self):
        existing = SizeLimitExceeded("已有上限异常")
        assert BilibiliDownloader._translate_ydl_error(existing) is existing


class TestDmPatchDetection:
    def test_returns_empty_when_upstream_already_provides_everything(self):
        from app.downloaders import bilibili_dm_patch

        class UpToDateIE:
            @property
            def _dm_params(self):
                return {"dm_img_list": "[]"}

            def _download_playinfo(self, bvid, cid, headers=None, query=None):
                return {"web_location": 1550101, **self._dm_params, **(query or {})}

        assert bilibili_dm_patch.missing_risk_control_keys(UpToDateIE) == []

    def test_reports_missing_keys_for_old_upstream(self):
        from app.downloaders import bilibili_dm_patch

        class OldIE:
            def _download_playinfo(self, bvid, cid, headers=None, query=None):
                return {"bvid": bvid, "cid": cid}

        missing = bilibili_dm_patch.missing_risk_control_keys(OldIE)
        assert set(bilibili_dm_patch.DM_IMG_KEYS).issubset(missing)
        assert "web_location" in missing

    def test_apply_is_idempotent_and_preserves_caller_query(self):
        from yt_dlp import YoutubeDL
        from yt_dlp.extractor.bilibili import BilibiliBaseIE

        from app.downloaders import bilibili_dm_patch

        assert bilibili_dm_patch.apply_bilibili_dm_img_patch() is True
        first = BilibiliBaseIE._download_playinfo
        assert bilibili_dm_patch.apply_bilibili_dm_img_patch() is True
        assert BilibiliBaseIE._download_playinfo is first

        captured = {}

        def fake_sign_wbi(params, video_id):
            captured.update(params)
            return params

        ie = BilibiliBaseIE(YoutubeDL({"quiet": True}))
        ie._sign_wbi = fake_sign_wbi
        ie._download_json = lambda *a, **k: {"code": 0, "data": {"ok": True}}
        ie._download_playinfo("BV1X9L16oEgB", 4242, headers={}, query={"qn": 64})

        assert set(bilibili_dm_patch.DM_IMG_KEYS).issubset(captured)
        assert captured["qn"] == 64

    def test_apply_skips_patching_when_upstream_complete(self, monkeypatch):
        """上游字段齐全时不该 monkey-patch，避免 yt-dlp 升级后 wrapper 变白炸源。"""
        from yt_dlp.extractor.bilibili import BilibiliBaseIE

        from app.downloaders import bilibili_dm_patch

        before = BilibiliBaseIE._download_playinfo
        monkeypatch.setattr(bilibili_dm_patch, "missing_risk_control_keys", lambda *a, **k: [])
        assert bilibili_dm_patch.apply_bilibili_dm_img_patch() is True
        assert BilibiliBaseIE._download_playinfo is before

    def test_installed_ytdlp_satisfies_minimum_version(self):
        """requirements.txt 锁 yt-dlp>=2026.7.4；低于它 playurl 会 412。"""
        from app.downloaders import bilibili_dm_patch

        assert bilibili_dm_patch.warn_if_ytdlp_too_old() is False
