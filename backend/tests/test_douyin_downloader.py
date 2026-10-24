"""抖音下载链路测试：字段归一化、软拦截重试、候选回退、上限、skip_download。

全部离线：网络层用假的 `http` 实现（`FakeHttp`）注入，`DouyinAPI` /
`DouyinDownloader` 都支持从构造参数注入，不需要 monkeypatch 全局 requests。
ffmpeg 也用假的 `subprocess.run` 替身，测试机器上没装 ffmpeg 也能跑。

覆盖的每一条都对应一个实测确认过的历史 bug（见 `douyin_api.py` 模块注释）：

1. 200 + 0 字节是**软拦截**，必须判为可重试，而不是「视频不存在」
2. `video.duration` 是毫秒，`music.duration` 才是秒
3. `item_title` 是空串，真实标题在 `desc`
4. `cover_original_scale` 在图文/分享页里不存在，要逐级兜底且不能 KeyError
5. 视频地址 `play_addr` 优先于 `download_addr`（后者带水印）
6. 候选地址逐个回退
7. 分享页 `/playwm/` -> `/play/`
8. `bit_rate[]` 按分辨率而非码率排序；`format == "dash"` 的档位被跳过
9. `skip_download=True` 不写任何文件
10. 体积/时长上限
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 走真实的 app 包导入（pytest.ini 里 pythonpath=.），这样拿到的就是生产代码本身，
# 不需要像历史上那样 stub 一堆模块。只是注意别在模块级实例化下载器（会读 Cookie 文件）。

from app.downloaders import douyin_api as api_module  # noqa: E402
from app.downloaders import douyin_downloader as downloader_module  # noqa: E402
from app.downloaders.douyin_api import (  # noqa: E402
    DouyinAPI,
    TtwidCache,
    build_media,
    collect_audio_candidates,
    collect_video_candidates,
    duration_seconds,
    parse_share_payload,
    pick_cover,
    pick_title,
    prefer_watermark_free,
    sorted_mp4_gears,
)
from app.downloaders.douyin_downloader import DouyinDownloader  # noqa: E402
from app.downloaders.errors import (  # noqa: E402
    DownloadError,
    DurationLimitExceeded,
    RetryableDownloadError,
    SizeLimitExceeded,
)

AWEME_ID = "7345492945006595379"
NOTE_ID = "7684636895083644273"

# 抖音 CDN 真实响应开头的魔数（实测 `\x00\x00\x00 ftypisom`）。下载层会校验
# 前 64 字节，防止把风控页面当媒体文件落盘，所以测试里的假响应也要带上它。
MEDIA_MAGIC = b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


def media_bytes(size: int, fill: bytes = b"x") -> bytes:
    """造一段「看起来像 mp4」的假媒体字节。"""
    body = fill * max(size - len(MEDIA_MAGIC), 1)
    return (MEDIA_MAGIC + body)[:size]


# ---------------------------------------------------------------------------
# 假网络
# ---------------------------------------------------------------------------
class FakeResponse:
    """最小化的 requests.Response 替身。"""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        chunks: Optional[List[bytes]] = None,
    ):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.cookies = cookies or {}
        self._chunks = chunks if chunks is not None else ([content] if content else [])
        self.closed = False
        self.url = ""

    def iter_content(self, chunk_size: int = 1024):
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True


class FakeHttp:
    """按顺序返回预置响应的假网络实现。

    支持三种用法：
      - `queue(...)` 依次消费（模拟「第一次空响应、第二次正常」这类重试场景）
      - `default=...` 兜底
      - 每次请求都记录到 `self.calls`，供断言请求头/参数/超时
    """

    def __init__(self, default: Optional[FakeResponse] = None):
        self.default = default
        self.calls: List[Dict[str, Any]] = []
        self._queue: List[FakeResponse] = []

    def queue(self, *responses: FakeResponse) -> "FakeHttp":
        self._queue.extend(responses)
        return self

    def _next(self) -> FakeResponse:
        if self._queue:
            return self._queue.pop(0)
        if self.default is not None:
            return self.default
        raise AssertionError("FakeHttp 没有可用响应")

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next()

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._next()


def detail_payload(item: Dict[str, Any]) -> bytes:
    return json.dumps({"aweme_detail": item, "status_code": 0}, ensure_ascii=False).encode()


def video_item(**overrides) -> Dict[str, Any]:
    """一个「实测形状」的普通视频 item。"""
    item: Dict[str, Any] = {
        "aweme_id": AWEME_ID,
        "desc": "大战触龙神……#召唤神龙万宁版",
        "item_title": "",
        "duration": 11123,  # 毫秒，故意和 video.duration 一样
        "author": {"nickname": "复古百晓生"},
        "statistics": {"play_count": 0, "digg_count": 4631, "comment_count": 1241},
        "text_extra": [{"hashtag_name": "召唤神龙万宁版"}],
        "video_tag": [{"tag_name": "二次元"}],
        "music": {
            "title": "@复古百晓生创作的原声",
            "duration": 11,  # 秒
            "play_url": {"uri": "https://sf6-cdn-tos.douyinstatic.com/obj/ies-music/bgm.mp3"},
        },
        "video": {
            "duration": 11123,  # 毫秒
            "width": 1280,
            "height": 720,
            "play_addr": {
                "uri": "v0d00fg10000cno6s2rc77u72sm6cfv0",
                "url_list": [
                    "https://v95-web-sz.douyinvod.com/play_addr_1.mp4",
                    "https://v26-weba.douyinvod.com/play_addr_2.mp4",
                ],
                "data_size": 2395049,
            },
            "play_addr_h264": {
                "url_list": ["https://v95-web-sz.douyinvod.com/play_addr_1.mp4"]
            },
            "play_addr_265": {"url_list": ["https://v95-web-sz.douyinvod.com/play_265.mp4"]},
            "download_addr": {
                "url_list": ["https://v95-web-sz.douyinvod.com/download_logo.mp4"]
            },
            "cover_original_scale": {"url_list": ["https://p3.douyinpic.com/cover_orig.jpeg"]},
            "origin_cover": {"url_list": ["https://p3.douyinpic.com/origin_cover.jpeg"]},
            "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpeg"]},
            "bit_rate": [
                # 实测：540 档位的码率高于 720 档位，按码率排序会选错
                {
                    "gear_name": "normal_540_0",
                    "format": "mp4",
                    "bit_rate": "1377578",
                    "play_addr": {
                        "width": 1024,
                        "height": 576,
                        "url_list": ["https://cdn/br_540.mp4"],
                    },
                },
                {
                    "gear_name": "normal_720_0",
                    "format": "mp4",
                    "bit_rate": "1327020",
                    "play_addr": {
                        "width": 1280,
                        "height": 720,
                        "url_list": ["https://cdn/br_720.mp4"],
                    },
                },
                # dash 档位是纯视频轨（无音轨），必须跳过
                {
                    "gear_name": "dash_1080",
                    "format": "dash",
                    "bit_rate": "9999999",
                    "play_addr": {
                        "width": 1920,
                        "height": 1080,
                        "url_list": ["https://cdn/dash_1080.mp4"],
                    },
                },
            ],
        },
    }
    item.update(overrides)
    return item


def image_item(**overrides) -> Dict[str, Any]:
    """图文作品（实测：detail 接口可能返回 images_base，字段形状也不一样）。"""
    item: Dict[str, Any] = {
        "aweme_id": NOTE_ID,
        "desc": "糯糯你怎么这样…#糯糯",
        "item_title": None,
        "aweme_type": 68,
        "duration": 0,
        "author": {"nickname": "海盗握力"},
        "statistics": {"digg_count": 17573, "comment_count": 693},
        "music": {
            "title": "爱我还是他",
            "duration": 64,
            "play_url": {
                "uri": "https://sf11-cdn-tos.douyinstatic.com/obj/tos-cn-ve-2774/audio.m4a",
                "url_list": [
                    "https://sf11-cdn-tos.douyinstatic.com/obj/tos-cn-ve-2774/audio.m4a"
                ],
            },
        },
        "images": [
            {
                "uri": "tos-cn-i-0813c000-ce/o45AjfndAIfDT6cq2s4KTgA3eIQ8IALKAFAleG",
                "url_list": ["https://p26-sign.douyinpic.com/image_1.webp"],
            }
        ],
        "video": {
            "duration": 0,
            "width": 1760,
            "height": 2368,
            "play_addr": {
                "uri": "https://sf11-cdn-tos.douyinstatic.com/obj/tos-cn-ve-2774/audio.m4a",
                "url_list": [],
            },
            "cover": {"url_list": ["https://p3-pc-sign.douyinpic.com/cover.jpeg"]},
            "bit_rate": [],
        },
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# 字段归一化（纯函数）
# ---------------------------------------------------------------------------
class TestDurationSeconds(unittest.TestCase):
    def test_milliseconds_converted_to_seconds(self):
        """video.duration 是毫秒：实测 395440 -> 395.44 秒，不是 395440 秒。"""
        item = video_item()
        item["video"]["duration"] = 395440
        self.assertAlmostEqual(duration_seconds(item), 395.44, places=3)

    def test_short_video_is_not_absurd(self):
        """11.1 秒的视频不能被记成 185 分 23 秒。"""
        self.assertAlmostEqual(duration_seconds(video_item()), 11.123, places=3)

    def test_music_duration_is_seconds(self):
        """music.duration 是秒，不能除以 1000。"""
        item = video_item()
        item["video"]["duration"] = 0
        item["duration"] = 0
        self.assertEqual(duration_seconds(item), 11.0)

    def test_top_level_duration_is_milliseconds_too(self):
        """部分接口把 duration 放到 item 顶层，单位同样是毫秒。"""
        item = video_item()
        item["video"]["duration"] = 0
        item["duration"] = 72000
        self.assertEqual(duration_seconds(item), 72.0)

    def test_missing_duration_is_zero(self):
        self.assertEqual(duration_seconds({}), 0.0)
        self.assertEqual(duration_seconds({"video": {}}), 0.0)


class TestTitle(unittest.TestCase):
    def test_desc_wins_over_item_title(self):
        """实测 item_title 是空串，真实标题在 desc。"""
        self.assertEqual(pick_title(video_item()), "大战触龙神……#召唤神龙万宁版")

    def test_desc_wins_even_when_item_title_present(self):
        item = video_item(desc="真实标题", item_title="旧标题")
        self.assertEqual(pick_title(item), "真实标题")

    def test_item_title_falls_back_when_desc_empty(self):
        item = video_item(desc="", item_title="兜底标题")
        self.assertEqual(pick_title(item), "兜底标题")

    def test_none_desc_does_not_crash(self):
        item = video_item(desc=None, item_title=None)
        self.assertEqual(pick_title(item, AWEME_ID), f"抖音作品 {AWEME_ID}")

    def test_whitespace_collapsed(self):
        item = video_item(desc="  多行\n标题   带空格  ", item_title="")
        self.assertEqual(pick_title(item), "多行 标题 带空格")


class TestCover(unittest.TestCase):
    def test_original_scale_preferred(self):
        self.assertEqual(pick_cover(video_item()), "https://p3.douyinpic.com/cover_orig.jpeg")

    def test_falls_back_to_origin_cover(self):
        item = video_item()
        item["video"].pop("cover_original_scale")
        self.assertEqual(pick_cover(item), "https://p3.douyinpic.com/origin_cover.jpeg")

    def test_falls_back_to_cover(self):
        item = video_item()
        item["video"].pop("cover_original_scale")
        item["video"].pop("origin_cover")
        self.assertEqual(pick_cover(item), "https://p3.douyinpic.com/cover.jpeg")

    def test_image_post_has_no_original_scale_and_must_not_crash(self):
        """实测图文作品没有 cover_original_scale；旧代码在这里 KeyError。

        这里只断言「不抛异常且能拿到某一级」，具体落到哪一级由下一个用例钉住。
        """
        item = image_item()
        self.assertNotIn("cover_original_scale", item["video"])
        self.assertIsNotNone(pick_cover(item))

    def test_image_post_uses_video_cover_when_present(self):
        item = image_item()
        self.assertEqual(pick_cover(item), "https://p3-pc-sign.douyinpic.com/cover.jpeg")

    def test_falls_back_to_first_image(self):
        item = image_item()
        item["video"].pop("cover")
        self.assertEqual(pick_cover(item), "https://p26-sign.douyinpic.com/image_1.webp")

    def test_both_missing_returns_none(self):
        item = image_item()
        item["video"].pop("cover")
        item.pop("images")
        self.assertIsNone(pick_cover(item))

    def test_garbage_shapes_do_not_raise(self):
        for bad in ({}, {"video": None}, {"video": []}, {"video": {"cover": "oops"}}):
            with self.subTest(bad=bad):
                self.assertIsNone(pick_cover(bad))


class TestVideoCandidates(unittest.TestCase):
    def test_play_addr_before_download_addr(self):
        """download_addr 是 720x720 正方裁剪 + 重编码 + 带水印版，只能兜底。"""
        candidates = collect_video_candidates(video_item()["video"])
        play_index = candidates.index("https://v95-web-sz.douyinvod.com/play_addr_1.mp4")
        download_index = candidates.index("https://v95-web-sz.douyinvod.com/download_logo.mp4")
        self.assertLess(play_index, download_index)

    def test_all_play_addr_urls_collected(self):
        """不能只取 url_list[0]：候选要全收集，否则 CDN 单点故障就整个失败。"""
        candidates = collect_video_candidates(video_item()["video"])
        self.assertIn("https://v95-web-sz.douyinvod.com/play_addr_1.mp4", candidates)
        self.assertIn("https://v26-weba.douyinvod.com/play_addr_2.mp4", candidates)

    def test_duplicate_urls_deduped(self):
        """play_addr 与 play_addr_h264 实测逐字节相同，不能重复下载。"""
        candidates = collect_video_candidates(video_item()["video"])
        self.assertEqual(len(candidates), len(set(candidates)))

    def test_play_addr_265_included(self):
        candidates = collect_video_candidates(video_item()["video"])
        self.assertIn("https://v95-web-sz.douyinvod.com/play_265.mp4", candidates)

    def test_empty_and_garbage_safe(self):
        for bad in (None, {}, {"play_addr": None}, {"play_addr": {"url_list": None}}):
            with self.subTest(bad=bad):
                self.assertEqual(collect_video_candidates(bad), [])


class TestBitRateSelection(unittest.TestCase):
    def test_dash_gears_skipped(self):
        """format == "dash" 是纯视频轨（无音轨），选中就是静音视频。"""
        gears = sorted_mp4_gears(video_item()["video"])
        self.assertEqual([gear["gear_name"] for gear in gears], ["normal_720_0", "normal_540_0"])

    def test_sorted_by_resolution_not_bitrate(self):
        """实测 normal_540_0 码率高于 normal_720_0，但分辨率更低。"""
        gears = sorted_mp4_gears(video_item()["video"])
        first = gears[0]
        self.assertEqual(
            (first["play_addr"]["width"], first["play_addr"]["height"]), (1280, 720)
        )

    def test_area_used_as_primary_key(self):
        """面积大的排前面，即使码率更低（竖屏 720x1280 vs 横屏 1024x576）。"""
        video = {
            "bit_rate": [
                {
                    "format": "mp4",
                    "bit_rate": "900000",
                    "play_addr": {"width": 1024, "height": 576, "url_list": ["landscape"]},
                },
                {
                    "format": "mp4",
                    "bit_rate": "700000",
                    "play_addr": {"width": 720, "height": 1280, "url_list": ["portrait"]},
                },
            ]
        }
        gears = sorted_mp4_gears(video)
        self.assertEqual(gears[0]["play_addr"]["url_list"], ["portrait"])

    def test_resolution_on_gear_itself_supported(self):
        """部分接口把 width/height 放在档位自己身上（不嵌套在 play_addr），要兼容。"""
        video = {
            "bit_rate": [
                {"format": "mp4", "bit_rate": "1", "width": 640, "height": 360, "play_addr": {"url_list": ["low"]}},
                {"format": "mp4", "bit_rate": "1", "width": 1920, "height": 1080, "play_addr": {"url_list": ["high"]}},
            ]
        }
        self.assertEqual(sorted_mp4_gears(video)[0]["play_addr"]["url_list"], ["high"])

    def test_mp4_gears_appended_after_play_addr(self):
        candidates = collect_video_candidates(video_item()["video"])
        self.assertIn("https://cdn/br_720.mp4", candidates)
        self.assertNotIn("https://cdn/dash_1080.mp4", candidates)
        self.assertLess(
            candidates.index("https://v95-web-sz.douyinvod.com/play_addr_1.mp4"),
            candidates.index("https://cdn/br_720.mp4"),
        )


class TestAudioCandidates(unittest.TestCase):
    def test_video_uses_video_stream_not_music(self):
        """普通视频的音轨在视频流里；music.play_url 是 BGM，绝不能当音轨。"""
        candidates = collect_audio_candidates(video_item())
        self.assertTrue(candidates)
        self.assertFalse(any("ies-music" in url for url in candidates))

    def test_image_post_uses_its_own_audio(self):
        """图文作品的 video.play_addr.uri 就是它自己的音频文件（M4A）。"""
        candidates = collect_audio_candidates(image_item())
        self.assertTrue(candidates)
        self.assertTrue(candidates[0].startswith("https://sf11-cdn-tos.douyinstatic.com/"))

    def test_image_post_no_duplicate_urls(self):
        candidates = collect_audio_candidates(image_item())
        self.assertEqual(len(candidates), len(set(candidates)))


class TestWatermarkFree(unittest.TestCase):
    def test_playwm_replaced_with_play(self):
        wm = (
            "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0d00fg&ratio=720p&line=0"
        )
        expected = (
            "https://aweme.snssdk.com/aweme/v1/play/?video_id=v0d00fg&ratio=720p&line=0"
        )
        self.assertEqual(prefer_watermark_free(wm), expected)

    def test_non_watermark_url_untouched(self):
        url = "https://v95-web-sz.douyinvod.com/abc/video.mp4"
        self.assertEqual(prefer_watermark_free(url), url)

    def test_share_page_candidates_are_watermark_free(self):
        share_item = {
            "aweme_id": AWEME_ID,
            "desc": "分享页",
            "video": {
                "duration": 5000,
                "play_addr": {
                    "url_list": [
                        "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=abc&ratio=720p"
                    ]
                },
            },
        }
        media = build_media(share_item, AWEME_ID)
        self.assertTrue(media.video_candidates)
        for url in media.video_candidates:
            self.assertNotIn("/playwm/", url)


class TestBuildMedia(unittest.TestCase):
    def test_video_item_fields(self):
        media = build_media(video_item(), AWEME_ID)
        self.assertEqual(media.aweme_id, AWEME_ID)
        self.assertEqual(media.title, "大战触龙神……#召唤神龙万宁版")
        self.assertAlmostEqual(media.duration, 11.123, places=3)
        self.assertFalse(media.is_image_post)
        self.assertEqual(media.container_ext, "mp4")
        self.assertEqual(media.webpage_url, f"https://www.douyin.com/video/{AWEME_ID}")
        self.assertIn("召唤神龙万宁版", media.tags)
        self.assertEqual(media.author, "复古百晓生")
        self.assertEqual(media.estimated_size, 2395049)

    def test_image_post_fields(self):
        media = build_media(image_item(), NOTE_ID)
        self.assertTrue(media.is_image_post)
        self.assertEqual(media.container_ext, "m4a")
        self.assertEqual(media.webpage_url, f"https://www.douyin.com/note/{NOTE_ID}")
        self.assertEqual(media.video_candidates, ())
        self.assertTrue(media.audio_candidates)
        self.assertEqual(media.duration, 64.0)

    def test_image_post_detected_by_images_not_aweme_type(self):
        """aweme_type 实测会变（同一图文作品一次 68 一次 0），不能当判据。"""
        item = image_item(aweme_type=0)
        self.assertTrue(build_media(item, NOTE_ID).is_image_post)

    def test_missing_everything_does_not_crash(self):
        media = build_media({}, AWEME_ID)
        self.assertEqual(media.aweme_id, AWEME_ID)
        self.assertIn(AWEME_ID, media.title)
        self.assertEqual(media.duration, 0.0)
        self.assertIsNone(media.cover_url)
        self.assertEqual(media.video_candidates, ())
        self.assertEqual(media.audio_candidates, ())


# ---------------------------------------------------------------------------
# 分享页解析
# ---------------------------------------------------------------------------
def share_html(item: Optional[Dict[str, Any]], filter_reason: str = "") -> bytes:
    loader_data: Dict[str, Any] = {"video_layout": None}
    info: Dict[str, Any] = {"extra": {}, "is_oversea": False, "status_code": 0}
    if item is not None:
        info["item_list"] = [item]
        info["filter_list"] = []
    else:
        info["item_list"] = []
        info["filter_list"] = [{"aweme_id": AWEME_ID, "filter_reason": filter_reason}]
    loader_data["video_(id)/page"] = {"renderInSSR": 1, "videoInfoRes": info}
    payload = json.dumps({"loaderData": loader_data}, ensure_ascii=False)
    # JSON 里故意塞一个 "}" 和 "</script>" 片段，确认解析不是靠正则截断
    payload = payload.replace('"is_oversea": false', '"is_oversea": false, "note": "} </script>"')
    html = f"<html><script>window._ROUTER_DATA = {payload}</script></html>"
    return html.encode("utf-8")


class TestSharePageParsing(unittest.TestCase):
    def test_parse_item(self):
        item, reason = parse_share_payload(share_html(video_item()).decode())
        self.assertIsNotNone(item)
        self.assertEqual(item["aweme_id"], AWEME_ID)
        self.assertIsNone(reason)

    def test_parse_filter_reason(self):
        item, reason = parse_share_payload(
            share_html(None, "SYSTEM_ITEM_NOT_EXIST").decode()
        )
        self.assertIsNone(item)
        self.assertEqual(reason, "SYSTEM_ITEM_NOT_EXIST")

    def test_missing_router_data(self):
        self.assertEqual(parse_share_payload("<html>no data</html>"), (None, None))

    def test_malformed_json(self):
        html = "<script>window._ROUTER_DATA = {broken</script>"
        self.assertEqual(parse_share_payload(html), (None, None))

    def test_loader_data_key_is_not_hardcoded(self):
        """loaderData 的 key 按作品类型变化（video_/note_），不能写死。"""
        html = json.dumps(
            {
                "loaderData": {
                    "note_(id)/page": {"videoInfoRes": {"item_list": [image_item()]}}
                }
            }
        )
        item, _ = parse_share_payload(f"window._ROUTER_DATA = {html}")
        self.assertEqual(item["aweme_id"], NOTE_ID)


# ---------------------------------------------------------------------------
# DouyinAPI：软拦截、403、图文回退
# ---------------------------------------------------------------------------
def make_api(http: FakeHttp, **kwargs) -> DouyinAPI:
    return DouyinAPI(
        cookie=None,
        http=http,
        ttwid_cache=TtwidCache(),
        **kwargs,
    )


TTWID = "1%7C" + "a" * 43 + "%7C1700000000%7C" + "b" * 64


def bootstrap_response(ttwid: str = TTWID) -> FakeResponse:
    return FakeResponse(status_code=200, content=b"<html>live</html>", cookies={"ttwid": ttwid})


class TestDetailRequest(unittest.TestCase):
    def test_headers_carry_argus_referer_ua_and_cookie(self):
        http = FakeHttp(default=bootstrap_response())
        http.queue(bootstrap_response(), FakeResponse(content=detail_payload(video_item())))
        api = make_api(http)

        api.fetch_detail(AWEME_ID)

        detail_call = [c for c in http.calls if c["url"] == api_module.DETAIL_URL][0]
        headers = detail_call["headers"]
        self.assertEqual(headers["x-tt-argus"], "1")
        self.assertEqual(headers["Referer"], "https://www.douyin.com/")
        self.assertIn("Chrome", headers["User-Agent"])
        self.assertEqual(headers["Cookie"], f"ttwid={TTWID}")
        self.assertEqual(detail_call["timeout"], (5, 30))
        self.assertEqual(detail_call["params"]["aweme_id"], AWEME_ID)

    def test_ttwid_is_cached_across_calls(self):
        """高频引导 live.douyin.com 本身会触发软拦截，必须缓存复用。"""
        http = FakeHttp(default=bootstrap_response())
        http.queue(bootstrap_response(), FakeResponse(content=detail_payload(video_item())))
        http.queue(FakeResponse(content=detail_payload(video_item())))
        api = make_api(http)

        api.fetch_detail(AWEME_ID)
        api.fetch_detail(AWEME_ID)

        live_calls = [c for c in http.calls if c["url"] == api_module.LIVE_URL]
        self.assertEqual(len(live_calls), 1)

    def test_empty_body_is_retryable_not_missing(self):
        """200 + 0 字节 = 软拦截，不是「视频不存在」。"""
        http = FakeHttp()
        http.queue(
            bootstrap_response(),
            FakeResponse(status_code=200, content=b""),
            bootstrap_response(),  # 第二轮刷新 ttwid
            FakeResponse(status_code=200, content=b""),
        )
        api = make_api(http)

        with self.assertRaises(RetryableDownloadError) as ctx:
            api.fetch_detail(AWEME_ID)
        self.assertIn("空响应", str(ctx.exception))
        self.assertTrue(RetryableDownloadError.retryable)

    def test_soft_block_refreshes_ttwid_and_retries(self):
        """第一轮空响应 -> 刷新 ttwid -> 第二轮成功（astrbot 的两轮策略）。"""
        fresh_ttwid = "1%7C" + "c" * 43 + "%7C1700000001%7C" + "d" * 64
        http = FakeHttp()
        http.queue(
            bootstrap_response(),
            FakeResponse(content=b""),
            bootstrap_response(fresh_ttwid),
            FakeResponse(content=detail_payload(video_item())),
        )
        api = make_api(http)

        item = api.fetch_detail(AWEME_ID)
        self.assertEqual(item["aweme_id"], AWEME_ID)

        live_calls = [c for c in http.calls if c["url"] == api_module.LIVE_URL]
        self.assertEqual(len(live_calls), 2, "第二轮必须先刷新 ttwid")
        detail_calls = [c for c in http.calls if c["url"] == api_module.DETAIL_URL]
        self.assertEqual(len(detail_calls), 2)
        self.assertEqual(detail_calls[1]["headers"]["Cookie"], f"ttwid={fresh_ttwid}")

    def test_403_is_retryable(self):
        http = FakeHttp(default=bootstrap_response())
        http.queue(
            bootstrap_response(),
            FakeResponse(status_code=403, content=b"Blocked by ArgusSecurityPlugin Uifid Not Found"),
            FakeResponse(status_code=403, content=b"Blocked by ArgusSecurityPlugin Uifid Not Found"),
        )
        with self.assertRaises(RetryableDownloadError):
            make_api(http).fetch_detail(AWEME_ID)

    def test_403_then_success(self):
        http = FakeHttp()
        http.queue(
            bootstrap_response(),
            FakeResponse(status_code=403, content=b"blocked"),
            bootstrap_response(),
            FakeResponse(content=detail_payload(video_item())),
        )
        self.assertEqual(make_api(http).fetch_detail(AWEME_ID)["aweme_id"], AWEME_ID)

    def test_timeout_error_is_retryable(self):
        class BoomHttp(FakeHttp):
            def get(self, url, **kwargs):
                self.calls.append({"url": url, **kwargs})
                import requests

                raise requests.Timeout("read timeout")

        api = make_api(BoomHttp())
        with self.assertRaises(RetryableDownloadError):
            api.fetch_detail(AWEME_ID)

    def test_missing_detail_without_reason_is_retryable(self):
        http = FakeHttp(default=bootstrap_response())
        http.queue(
            bootstrap_response(),
            FakeResponse(content=json.dumps({"aweme_detail": None, "status_code": 8}).encode()),
            FakeResponse(content=json.dumps({"aweme_detail": None, "status_code": 8}).encode()),
        )
        with self.assertRaises(RetryableDownloadError):
            make_api(http).fetch_detail(AWEME_ID)

    def test_deleted_video_is_not_retryable(self):
        """item_list 为空 + filter_reason 明确 = 作品不存在，重试没用。"""
        payload = json.dumps(
            {
                "aweme_detail": None,
                "filter_detail": {"aweme_id": AWEME_ID, "filter_reason": "core_dep"},
            }
        ).encode()
        http = FakeHttp(default=bootstrap_response())
        http.queue(bootstrap_response(), FakeResponse(content=payload))
        with self.assertRaises(DownloadError) as ctx:
            make_api(http).fetch_detail(AWEME_ID)
        self.assertNotIsInstance(ctx.exception, RetryableDownloadError)

    def test_empty_ttwid_omits_cookie_header(self):
        """ttwid 为空时必须整体省略 Cookie，传空串一样被软拦截。

        引导页和注册接口都不给 ttwid 时，两个 Cookie 值必须都能校验过
        （不能出现 `ttwid=` 或 `ttwid=None` 这种更容易被拦的形态）。
        """
        empty = FakeResponse(status_code=200, content=b"<html>no cookie</html>", cookies={})
        http = FakeHttp(default=empty)
        http.queue(
            empty,  # live.douyin.com 引导
            empty,  # ttwid 注册接口备选
            FakeResponse(content=detail_payload(video_item())),
        )
        api = make_api(http)
        api.fetch_detail(AWEME_ID)
        detail_call = [c for c in http.calls if c["url"] == api_module.DETAIL_URL][0]
        self.assertNotIn("Cookie", detail_call["headers"])

    def test_registers_ttwid_when_live_bootstrap_fails(self):
        """live.douyin.com 不下发 ttwid 时走 ttwid.bytedance.com 注册接口。"""
        empty = FakeResponse(status_code=200, content=b"<html>no cookie</html>", cookies={})
        http = FakeHttp()
        http.queue(
            empty,
            FakeResponse(status_code=200, content=b"", cookies={"ttwid": TTWID}),
            FakeResponse(content=detail_payload(video_item())),
        )
        api = make_api(http)
        api.fetch_detail(AWEME_ID)
        register_calls = [c for c in http.calls if c.get("method") == "POST"]
        self.assertEqual(len(register_calls), 1)
        self.assertEqual(register_calls[0]["url"], api_module.TTWID_REGISTER_URL)
        detail_call = [c for c in http.calls if c["url"] == api_module.DETAIL_URL][0]
        self.assertEqual(detail_call["headers"]["Cookie"], f"ttwid={TTWID}")


class TestUserCookiePassthrough(unittest.TestCase):
    """用户配置的 Cookie 除 ttwid 外要原样带上（登录态作品才解析得了）。"""

    def test_user_ttwid_is_reused_without_bootstrap(self):
        """用户自带 ttwid 时直接复用，省一次 live.douyin.com 引导。"""
        http = FakeHttp()
        http.queue(FakeResponse(content=detail_payload(video_item())))
        api = DouyinAPI(
            cookie="sessionid=abc123; ttwid=user-ttwid-value",
            http=http,
            ttwid_cache=TtwidCache(),
        )
        api.fetch_detail(AWEME_ID)
        live_calls = [c for c in http.calls if c["url"] == api_module.LIVE_URL]
        self.assertEqual(live_calls, [])
        detail_call = [c for c in http.calls if c["url"] == api_module.DETAIL_URL][0]
        self.assertEqual(
            detail_call["headers"]["Cookie"], "ttwid=user-ttwid-value; sessionid=abc123"
        )

    def test_refresh_replaces_user_ttwid(self):
        """刷新 ttwid 时必须换掉用户配置里的旧值，否则「刷新」等于没刷。"""
        fresh = "1%7C" + "e" * 43 + "%7C1700000002%7C" + "f" * 64
        http = FakeHttp()
        http.queue(
            FakeResponse(content=b""),           # 第一轮软拦截
            bootstrap_response(fresh),           # 刷新引导
            FakeResponse(content=detail_payload(video_item())),
        )
        api = DouyinAPI(
            cookie="sessionid=abc123; ttwid=user-ttwid-value",
            http=http,
            ttwid_cache=TtwidCache(),
        )
        api.fetch_detail(AWEME_ID)
        detail_calls = [c for c in http.calls if c["url"] == api_module.DETAIL_URL]
        self.assertEqual(len(detail_calls), 2)
        refreshed_cookie = detail_calls[1]["headers"]["Cookie"]
        self.assertIn(f"ttwid={fresh}", refreshed_cookie)
        self.assertNotIn("user-ttwid-value", refreshed_cookie)
        self.assertIn("sessionid=abc123", refreshed_cookie)

    def test_extra_cookies_forwarded_without_ttwid_in_user_cookie(self):
        http = FakeHttp()
        http.queue(
            bootstrap_response(),
            FakeResponse(content=detail_payload(video_item())),
        )
        api = DouyinAPI(
            cookie="sessionid=abc123; uid_tt=999",
            http=http,
            ttwid_cache=TtwidCache(),
        )
        api.fetch_detail(AWEME_ID)
        detail_call = [c for c in http.calls if c["url"] == api_module.DETAIL_URL][0]
        cookie_header = detail_call["headers"]["Cookie"]
        self.assertTrue(cookie_header.startswith(f"ttwid={TTWID};"))
        self.assertIn("sessionid=abc123", cookie_header)
        self.assertIn("uid_tt=999", cookie_header)

    def test_no_user_cookie_sends_only_ttwid(self):
        http = FakeHttp()
        http.queue(bootstrap_response(), FakeResponse(content=detail_payload(video_item())))
        api = make_api(http)
        api.fetch_detail(AWEME_ID)
        detail_call = [c for c in http.calls if c["url"] == api_module.DETAIL_URL][0]
        self.assertEqual(detail_call["headers"]["Cookie"], f"ttwid={TTWID}")

    def test_strip_cookie_helper(self):
        self.assertEqual(
            api_module._strip_cookie("a=1; ttwid=x; b=2", ("ttwid",)), "a=1; b=2"
        )
        self.assertEqual(api_module._strip_cookie(None, ("ttwid",)), "")
        self.assertEqual(api_module._strip_cookie("", ("ttwid",)), "")
        self.assertEqual(api_module._strip_cookie("garbage", ("ttwid",)), "")


class TestImagePostFallback(unittest.TestCase):
    def test_images_base_falls_back_to_share_page(self):
        """实测：/aweme/detail/ 对图文作品返回 aweme_detail=null + images_base。"""
        filter_payload = json.dumps(
            {
                "aweme_detail": None,
                "filter_detail": {"aweme_id": NOTE_ID, "filter_reason": "images_base"},
            }
        ).encode()
        http = FakeHttp(default=bootstrap_response())
        http.queue(
            bootstrap_response(),
            FakeResponse(content=filter_payload),
            FakeResponse(content=share_html(image_item())),
        )
        item = make_api(http).fetch_item(NOTE_ID)
        self.assertEqual(item["aweme_id"], NOTE_ID)
        share_calls = [c for c in http.calls if "iesdouyin.com/share/" in c["url"]]
        self.assertEqual(len(share_calls), 1)
        # 分享页必须用 iPhone UA（桌面 UA 会被 302）
        self.assertIn("iPhone", share_calls[0]["headers"]["User-Agent"])
        self.assertIn("ttwid=", share_calls[0]["headers"]["Cookie"])

    def test_share_page_requires_ttwid_cookie(self):
        http = FakeHttp(default=bootstrap_response())
        http.queue(
            bootstrap_response(),
            FakeResponse(content=share_html(None, "SYSTEM_ITEM_NOT_EXIST")),
        )
        with self.assertRaises(DownloadError):
            make_api(http).fetch_share_item(AWEME_ID)

    def test_share_page_empty_body_is_retryable(self):
        http = FakeHttp(default=bootstrap_response())
        http.queue(
            bootstrap_response(),
            FakeResponse(status_code=200, content=b""),
            FakeResponse(status_code=200, content=b""),
        )
        with self.assertRaises(RetryableDownloadError):
            make_api(http).fetch_share_item(AWEME_ID)

    def test_video_detail_does_not_hit_share_page(self):
        http = FakeHttp(default=bootstrap_response())
        http.queue(bootstrap_response(), FakeResponse(content=detail_payload(video_item())))
        make_api(http).fetch_item(AWEME_ID)
        self.assertFalse([c for c in http.calls if "iesdouyin.com/share/" in c["url"]])

    def test_fetch_media_rejects_toothless_item(self):
        """没有任何媒体流的作品要给出明确错误，而不是返回空候选让下游炸。"""
        toothless = {"aweme_id": AWEME_ID, "desc": "空作品", "video": {}, "music": {}}
        http = FakeHttp(default=bootstrap_response())
        http.queue(bootstrap_response(), FakeResponse(content=detail_payload(toothless)))
        with self.assertRaises(DownloadError):
            make_api(http).fetch_media(AWEME_ID)


# ---------------------------------------------------------------------------
# 下载层
# ---------------------------------------------------------------------------
def fake_ffmpeg(record: Optional[List[List[str]]] = None):
    """替换 subprocess.run：写一个非空假音频文件，模拟 ffmpeg 成功。"""

    def runner(command, **kwargs):
        if record is not None:
            record.append(list(command))
        target = command[-1]
        with open(target, "wb") as handle:
            handle.write(b"ID3fake-mp3")
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    return runner


class DownloaderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.output_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _downloader(self, http: FakeHttp) -> DouyinDownloader:
        api = make_api(http)
        return DouyinDownloader(cookie=None, api=api, http=http)

    def _api_with_item(self, item: Dict[str, Any]) -> FakeHttp:
        http = FakeHttp(default=bootstrap_response())
        http.queue(bootstrap_response(), FakeResponse(content=detail_payload(item)))
        return http


class TestSkipDownload(DownloaderTestCase):
    def test_skip_download_writes_no_files(self):
        http = self._api_with_item(video_item())
        downloader = self._downloader(http)
        result = downloader.download(
            f"https://www.douyin.com/video/{AWEME_ID}",
            output_dir=self.output_dir,
            skip_download=True,
        )
        self.assertEqual(os.listdir(self.output_dir), [])
        self.assertEqual(result.title, "大战触龙神……#召唤神龙万宁版")
        self.assertEqual(result.duration, 11.123)
        # 没有发起任何 CDN 下载请求
        cdn_calls = [c for c in http.calls if "douyinvod.com" in c["url"]]
        self.assertEqual(cdn_calls, [])

    def test_skip_download_still_returns_metadata_for_image_post(self):
        http = self._api_with_item(image_item())
        downloader = self._downloader(http)
        result = downloader.download(
            f"https://www.douyin.com/note/{NOTE_ID}",
            output_dir=self.output_dir,
            skip_download=True,
        )
        self.assertEqual(os.listdir(self.output_dir), [])
        self.assertEqual(result.duration, 64.0)
        self.assertTrue(result.raw_info["is_image_post"])


class TestCandidateFallback(DownloaderTestCase):
    def test_falls_back_to_next_candidate(self):
        http = self._api_with_item(video_item())
        good = media_bytes(2048)
        http.queue(
            FakeResponse(status_code=404, content=b""),  # play_addr_1 挂了
            FakeResponse(content=b"", chunks=[good]),  # play_addr_2 正常（下面把 good 换成媒体字节）
        )
        downloader = self._downloader(http)

        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            result = downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )

        self.assertTrue(os.path.exists(result.file_path))
        video_path = os.path.join(self.output_dir, f"{AWEME_ID}.mp4")
        self.assertEqual(pathlib.Path(video_path).read_bytes(), good)

    def test_all_candidates_failing_raises_retryable(self):
        http = self._api_with_item(video_item())
        http.queue(*[FakeResponse(status_code=500, content=b"") for _ in range(10)])
        downloader = self._downloader(http)

        with self.assertRaises(RetryableDownloadError):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        self.assertEqual([f for f in os.listdir(self.output_dir) if f.endswith(".part")], [])

    def test_empty_cdn_body_is_retryable_then_falls_back(self):
        """CDN 也会用 200 + 0 字节表达风控，要当可重试处理并换候选。"""
        http = self._api_with_item(video_item())
        http.queue(
            FakeResponse(content=b""),
            FakeResponse(content=media_bytes(4096, b"y")),
        )
        downloader = self._downloader(http)
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        video_path = os.path.join(self.output_dir, f"{AWEME_ID}.mp4")
        self.assertEqual(pathlib.Path(video_path).read_bytes(), media_bytes(4096, b"y"))

    def test_partial_file_cleaned_up_on_failure(self):
        """中途失败不能留下「看起来已完成」的半截文件。"""
        item = video_item()
        # 收窄到单个候选，确认「所有候选都失败」时不会留下半截文件
        item["video"]["play_addr"]["url_list"] = ["https://v95-web-sz.douyinvod.com/only.mp4"]
        item["video"]["play_addr_h264"]["url_list"] = []
        item["video"]["play_addr_265"]["url_list"] = []
        item["video"]["bit_rate"] = []
        item["video"]["download_addr"]["url_list"] = []
        http = FakeHttp()
        http.queue(
            bootstrap_response(),
            FakeResponse(content=detail_payload(item)),
            FakeResponse(status_code=500, content=b""),
        )
        downloader = self._downloader(http)
        video_path = os.path.join(self.output_dir, f"{AWEME_ID}.mp4")
        with open(video_path + ".part", "wb") as handle:
            handle.write(b"junk")

        with self.assertRaises(RetryableDownloadError):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        self.assertFalse(os.path.exists(video_path + ".part"))


class TestDownloadStreaming(DownloaderTestCase):
    def test_streams_to_disk_and_extracts_audio(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(1024, b"a"), media_bytes(1024, b"b")]))
        downloader = self._downloader(http)

        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            result = downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}",
                output_dir=self.output_dir,
                quality="slow",
            )

        self.assertEqual(result.file_path, os.path.join(self.output_dir, f"{AWEME_ID}.mp3"))
        self.assertEqual(result.platform, "douyin")
        self.assertEqual(result.video_id, AWEME_ID)

    def test_ffmpeg_command_drops_video_and_forces_mono(self):
        """音频从视频流抽：必须 -vn 丢视频轨、-ac 1 单声道、码率跟随 quality。"""
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(1024, b"a")]))
        downloader = self._downloader(http)
        recorded: List[List[str]] = []

        with mock.patch.object(
            downloader_module.subprocess, "run", fake_ffmpeg(recorded)
        ):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}",
                output_dir=self.output_dir,
                quality="slow",
            )

        command = recorded[0]
        self.assertIn("-vn", command)
        self.assertIn("libmp3lame", command)
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-b:a") + 1], "192k")
        self.assertIn(os.path.join(self.output_dir, f"{AWEME_ID}.mp4"), command)

    def test_quality_fast_uses_64k(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(1024, b"a")]))
        downloader = self._downloader(http)
        recorded: List[List[str]] = []
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg(recorded)):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}",
                output_dir=self.output_dir,
                quality="fast",
            )
        self.assertEqual(recorded[0][recorded[0].index("-b:a") + 1], "64k")

    def test_does_not_download_music_play_url(self):
        """BGM 地址（ies-music）绝不能出现在下载请求里。"""
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(1024, b"a")]))
        downloader = self._downloader(http)
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        cdn_urls = [c["url"] for c in http.calls if c["method"] == "GET"]
        self.assertFalse([url for url in cdn_urls if "ies-music" in url])

    def test_need_video_reuses_existing_file(self):
        """need_video=True 时复用 download_video 已下好的文件，不重复下载。"""
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(2048, b"v")]))
        downloader = self._downloader(http)
        video_path = os.path.join(self.output_dir, f"{AWEME_ID}.mp4")
        pathlib.Path(video_path).write_bytes(media_bytes(2048, b"v"))

        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}",
                output_dir=self.output_dir,
                need_video=True,
            )
        cdn_calls = [c for c in http.calls if "douyinvod.com" in c["url"]]
        self.assertEqual(cdn_calls, [])


class TestNonMediaResponse(DownloaderTestCase):
    """CDN 用 200 + 一小段 HTML/JSON 表达风控时，不能落盘成「视频文件」。

    这条很关键：一旦把 HTML 存成 `{id}.mp4`，下一轮进来会命中存在性检查
    （文件存在且非空），然后 ffmpeg 对着 HTML 报一个莫名其妙的「moov atom not
    found」，用户看到的是「视频损坏」而不是「被风控了」。
    """

    def test_html_response_is_retryable_and_not_saved(self):
        http = self._api_with_item(video_item())
        html = b"<html><body>rate limited</body></html>"
        http.queue(
            FakeResponse(status_code=200, content=html),
            FakeResponse(chunks=[media_bytes(2048)]),
        )
        downloader = self._downloader(http)
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        video_path = os.path.join(self.output_dir, f"{AWEME_ID}.mp4")
        self.assertEqual(pathlib.Path(video_path).read_bytes(), media_bytes(2048))

    def test_all_non_media_responses_raise_retryable(self):
        item = video_item()
        item["video"]["play_addr"]["url_list"] = ["https://v95-web-sz.douyinvod.com/only.mp4"]
        item["video"]["play_addr_h264"]["url_list"] = []
        item["video"]["play_addr_265"]["url_list"] = []
        item["video"]["bit_rate"] = []
        item["video"]["download_addr"]["url_list"] = []

        http = FakeHttp()
        http.queue(
            bootstrap_response(),
            FakeResponse(content=detail_payload(item)),
            FakeResponse(status_code=200, content=b"<html>blocked</html>"),
        )
        downloader = self._downloader(http)

        with self.assertRaises(RetryableDownloadError):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        self.assertFalse(os.path.exists(os.path.join(self.output_dir, f"{AWEME_ID}.mp4")))

    def test_looks_like_media_helper(self):
        self.assertTrue(downloader_module._looks_like_media(media_bytes(64)))
        self.assertFalse(downloader_module._looks_like_media(b"<html>no</html>"))
        self.assertFalse(downloader_module._looks_like_media(b""))
        self.assertFalse(downloader_module._looks_like_media(b"abc"))


class TestSkipDownloadIgnoresLimits(DownloaderTestCase):
    """skip_download 不落任何媒体，体积/时长上限对它没有意义。"""

    def test_oversized_video_still_returns_metadata(self):
        item = video_item()
        item["video"]["duration"] = 999_000  # 999 秒 > 默认 300 秒
        item["video"]["play_addr"]["data_size"] = 900 * 1024 * 1024
        http = self._api_with_item(item)
        downloader = self._downloader(http)

        result = downloader.download(
            f"https://www.douyin.com/video/{AWEME_ID}",
            output_dir=self.output_dir,
            skip_download=True,
        )
        self.assertAlmostEqual(result.duration, 999.0, places=3)
        self.assertEqual(os.listdir(self.output_dir), [])

    def test_full_download_still_rejects_oversized(self):
        item = video_item()
        item["video"]["duration"] = 999_000
        http = self._api_with_item(item)
        downloader = self._downloader(http)
        with self.assertRaises(DurationLimitExceeded):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )


class TestDownloadVideo(DownloaderTestCase):
    def test_download_video_writes_file(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(4096, b"v")]))
        downloader = self._downloader(http)
        path = downloader.download_video(
            f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
        )
        self.assertEqual(path, os.path.join(self.output_dir, f"{AWEME_ID}.mp4"))
        self.assertEqual(pathlib.Path(path).read_bytes(), media_bytes(4096, b"v"))

    def test_download_video_skips_when_exists(self):
        http = self._api_with_item(video_item())
        downloader = self._downloader(http)
        video_path = os.path.join(self.output_dir, f"{AWEME_ID}.mp4")
        pathlib.Path(video_path).write_bytes(b"already")
        path = downloader.download_video(
            f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
        )
        self.assertEqual(path, video_path)
        self.assertEqual([c for c in http.calls if "douyinvod.com" in c["url"]], [])

    def test_zero_byte_existing_file_is_redownloaded(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(1024, b"v")]))
        downloader = self._downloader(http)
        video_path = os.path.join(self.output_dir, f"{AWEME_ID}.mp4")
        pathlib.Path(video_path).write_bytes(b"")
        downloader.download_video(
            f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
        )
        self.assertEqual(pathlib.Path(video_path).read_bytes(), media_bytes(1024, b"v"))

    def test_image_post_has_no_video(self):
        http = self._api_with_item(image_item())
        downloader = self._downloader(http)
        with self.assertRaises(DownloadError):
            downloader.download_video(
                f"https://www.douyin.com/note/{NOTE_ID}", output_dir=self.output_dir
            )

    def test_download_headers_carry_referer(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(64, b"v")]))
        downloader = self._downloader(http)
        downloader.download_video(
            f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
        )
        cdn_call = [c for c in http.calls if "douyinvod.com" in c["url"]][0]
        self.assertEqual(cdn_call["headers"]["Referer"], "https://www.douyin.com/")
        self.assertEqual(cdn_call["timeout"], (5, 30))
        self.assertTrue(cdn_call["stream"])


class TestImagePostAudio(DownloaderTestCase):
    def test_image_post_downloads_its_audio_not_bgm(self):
        http = self._api_with_item(image_item())
        http.queue(FakeResponse(chunks=[media_bytes(2048, b"audio")]))
        downloader = self._downloader(http)
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            result = downloader.download(
                f"https://www.douyin.com/note/{NOTE_ID}", output_dir=self.output_dir
            )
        self.assertEqual(result.file_path, os.path.join(self.output_dir, f"{NOTE_ID}.mp3"))
        self.assertTrue(result.raw_info["is_image_post"])
        cdn_urls = [c["url"] for c in http.calls if c["method"] == "GET"]
        self.assertTrue([url for url in cdn_urls if url.endswith(".m4a")])


# ---------------------------------------------------------------------------
# 体积 / 时长上限
# ---------------------------------------------------------------------------
class TestLimits(DownloaderTestCase):
    def test_duration_limit_rejects_before_download(self):
        long_item = video_item()
        long_item["video"]["duration"] = 400_000  # 400 秒 > 默认 300 秒
        http = self._api_with_item(long_item)
        downloader = self._downloader(http)

        with self.assertRaises(DurationLimitExceeded):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        self.assertEqual([c for c in http.calls if "douyinvod.com" in c["url"]], [])
        self.assertEqual(os.listdir(self.output_dir), [])

    def test_declared_size_over_limit_rejected_before_download(self):
        big_item = video_item()
        big_item["video"]["play_addr"]["data_size"] = 500 * 1024 * 1024
        http = self._api_with_item(big_item)
        downloader = self._downloader(http)

        with self.assertRaises(SizeLimitExceeded):
            downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        self.assertEqual([c for c in http.calls if "douyinvod.com" in c["url"]], [])
        self.assertEqual(os.listdir(self.output_dir), [])

    def test_size_limit_enforced_while_streaming(self):
        """Content-Length 可能缺失，所以要边写边核对。"""
        item = video_item()
        item["video"]["play_addr"]["data_size"] = None  # 让预判失效
        item["video"]["play_addr_h264"].pop("url_list", None)
        item["video"]["play_addr_265"].pop("url_list", None)
        item["video"]["bit_rate"] = []
        item["video"]["download_addr"].pop("url_list", None)

        http = self._api_with_item(item)
        # 每块 1MB，连续给 60 块 = 60MB；默认上限 200MB 不会触发，
        # 所以用环境变量把它压到 5MB。
        # 每块都是带魔数的 1MB，让「边写边核对」这条路径能跑到
        http.queue(FakeResponse(chunks=[media_bytes(1024 * 1024, b"z")] * 60))
        downloader = self._downloader(http)

        old = os.environ.get("DOWNLOAD_MAX_SIZE_MB")
        os.environ["DOWNLOAD_MAX_SIZE_MB"] = "5"
        try:
            with self.assertRaises(SizeLimitExceeded):
                downloader.download(
                    f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
                )
        finally:
            if old is None:
                os.environ.pop("DOWNLOAD_MAX_SIZE_MB", None)
            else:
                os.environ["DOWNLOAD_MAX_SIZE_MB"] = old
        self.assertFalse(
            os.path.exists(os.path.join(self.output_dir, f"{AWEME_ID}.mp4"))
        )

    def test_zero_limit_means_unlimited(self):
        item = video_item()
        item["video"]["duration"] = 999_999_000  # 约 11 天
        item["video"]["play_addr"]["data_size"] = 900 * 1024 * 1024
        http = self._api_with_item(item)
        http.queue(FakeResponse(chunks=[media_bytes(128, b"v")]))
        downloader = self._downloader(http)

        old_size = os.environ.get("DOWNLOAD_MAX_SIZE_MB")
        old_duration = os.environ.get("DOWNLOAD_MAX_DURATION_SECONDS")
        os.environ["DOWNLOAD_MAX_SIZE_MB"] = "0"
        os.environ["DOWNLOAD_MAX_DURATION_SECONDS"] = "0"
        try:
            with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
                downloader.download(
                    f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
                )
        finally:
            for key, value in (
                ("DOWNLOAD_MAX_SIZE_MB", old_size),
                ("DOWNLOAD_MAX_DURATION_SECONDS", old_duration),
            ):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# ---------------------------------------------------------------------------
# URL / 结果
# ---------------------------------------------------------------------------
class TestUrlAndResult(DownloaderTestCase):
    def test_modal_id_link_resolved(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(64, b"v")]))
        downloader = self._downloader(http)
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            downloader.download(
                f"https://www.douyin.com/discover?modal_id={AWEME_ID}",
                output_dir=self.output_dir,
            )
        self.assertTrue(os.path.exists(os.path.join(self.output_dir, f"{AWEME_ID}.mp4")))

    def test_unparseable_url_raises(self):
        http = FakeHttp()
        downloader = self._downloader(http)
        with self.assertRaises(DownloadError):
            downloader.download("https://www.douyin.com/user/abc", output_dir=self.output_dir)

    def test_result_contains_metadata_for_prompt_builder(self):
        """raw_info 里必须有 tags/description/uploader，下游 GPTSource 要读。"""
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(64, b"v")]))
        downloader = self._downloader(http)
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            result = downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        self.assertEqual(result.raw_info["uploader"], "复古百晓生")
        self.assertEqual(result.raw_info["description"], "大战触龙神……#召唤神龙万宁版")
        self.assertIsInstance(result.raw_info["tags"], list)
        self.assertEqual(result.raw_info["like_count"], 4631)
        self.assertEqual(result.cover_url, "https://p3.douyinpic.com/cover_orig.jpeg")

    def test_video_path_is_none_for_audio_only(self):
        """download() 只返回音频时 video_path 必须是 None。

        历史实现里这里会指向一个不存在的文件，下游截图逻辑会拿它去 ffmpeg。
        """
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(64, b"v")]))
        downloader = self._downloader(http)
        with mock.patch.object(downloader_module.subprocess, "run", fake_ffmpeg()):
            result = downloader.download(
                f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
            )
        self.assertIsNone(result.video_path)


class TestFFmpegFailure(DownloaderTestCase):
    def test_ffmpeg_failure_raises_download_error(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(64, b"v")]))
        downloader = self._downloader(http)

        def failing(command, **kwargs):
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"Invalid data found")

        with mock.patch.object(downloader_module.subprocess, "run", failing):
            with self.assertRaises(DownloadError) as ctx:
                downloader.download(
                    f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
                )
        self.assertIn("ffmpeg", str(ctx.exception))

    def test_missing_ffmpeg_binary_raises_download_error(self):
        http = self._api_with_item(video_item())
        http.queue(FakeResponse(chunks=[media_bytes(64, b"v")]))
        downloader = self._downloader(http)

        def no_binary(command, **kwargs):
            raise FileNotFoundError("ffmpeg not found")

        with mock.patch.object(downloader_module.subprocess, "run", no_binary):
            with self.assertRaises(DownloadError):
                downloader.download(
                    f"https://www.douyin.com/video/{AWEME_ID}", output_dir=self.output_dir
                )


class TestNoPrintNoCookieLeak(unittest.TestCase):
    def test_module_has_no_print_calls(self):
        source = (ROOT / "app" / "downloaders" / "douyin_downloader.py").read_text("utf-8")
        self.assertNotIn("print(", source)

    def test_api_module_has_no_print_calls(self):
        source = (ROOT / "app" / "downloaders" / "douyin_api.py").read_text("utf-8")
        self.assertNotIn("print(", source)

    def _code_only(self, name: str) -> str:
        """去掉注释和 docstring 后的源码，用于「不该出现的实现」这类检查。"""
        import ast
        import io
        import tokenize

        source = (ROOT / "app" / "downloaders" / name).read_text("utf-8")
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    if isinstance(body[0].value.value, str):
                        docstrings.add(id(body[0].value))
        pieces = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
        return " ".join(pieces)

    def test_api_module_does_not_use_abogus_or_websign(self):
        """x-tt-argus 方案已经取代 WebSign/a_bogus；残留的话是回归。"""
        code = self._code_only("douyin_api.py")
        self.assertNotIn("ABogus", code)
        self.assertNotIn("x-secsdk-web-signature", code)
        self.assertNotIn("msToken", code)

    def test_iteminfo_api_not_used(self):
        """iteminfo 已废弃（返回 encrypt_data_miss），不能再调。"""
        for name in ("douyin_api.py", "douyin_downloader.py"):
            code = self._code_only(name)
            self.assertNotIn("iteminfo", code)


if __name__ == "__main__":
    unittest.main()
