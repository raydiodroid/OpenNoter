"""抖音 Web 端（免登录）详情获取 + 字段归一化。

历史实现（2026-10 之前）走的是 a_bogus + msToken + WebSign 的签名链路，而
2026-10-21 的多轮 A/B 实测表明：`/aweme/v1/web/aweme/detail/` 现在**不需要任何
密码学签名**，网关只校验下面四件事：

1. `x-tt-argus: 1` —— 只校验这个头「存在」，不校验值。缺了直接
   403 `Blocked by ArgusSecurityPlugin Uifid Not Found`。
2. `Referer: https://www.douyin.com/` —— 缺了会 200 但 0 字节（软拦截）。
3. 正常桌面 Chrome UA。
4. `Cookie: ttwid=<...>` —— 缺了同样 200 但 0 字节；ttwid 为空时必须**整体省略**
   Cookie 头，传空串或伪造值一样是 0 字节。

`ttwid` 由 `GET https://live.douyin.com/` 的 Set-Cookie 下发（127 字符，
形如 `1|<43chars>|<unix秒>|<64hex>`）；`www.douyin.com/` 拿不到（只有
`__ac_nonce`），别试。

几个必须记住的实测结论（不要凭经验推翻，详见仓库里的调研记录）：

- **200 + 0 字节 = 软拦截**，不是「视频不存在」。必须当成可重试信号
  （`RetryableDownloadError`），并在刷新 ttwid 后重试一次。高频探测会触发它，
  冷却 30–60s 自行恢复。
- `video.duration` 单位是**毫秒**，而 `music.duration` 是**秒**。
- `item_title` 实测是空串，真实标题在 `desc`。
- `video.cover_original_scale` 在部分作品（图文/分享页）里不存在，要逐级兜底。
- 视频地址**不要用 `download_addr`**：它与 `download_suffix_logo_addr` 逐字节相同，
  是 720x720 正方裁剪 + 重编码带水印版。`play_addr` 无水印。
- `bit_rate[]` 里 `format == "dash"` 的档位是纯视频轨（无音轨），只有
  `format == "mp4"` 的档位带 aac；且**码率排序 ≠ 分辨率排序**，要按
  `width * height` 排序、码率只做次级比较。
- 图文作品：`aweme_detail` 可能为 null 且 `filter_detail.filter_reason ==
  "images_base"`，此时回退移动端分享页 `https://www.iesdouyin.com/share/{video|note}/{id}`
  （必须 iPhone UA，否则 302；必须带 ttwid Cookie，否则 `videoInfoRes` 键都不出现）。
- `iesdouyin.com/web/api/v2/aweme/iteminfo/` 已废弃（返回 `encrypt_data_miss`），不要再用。

本模块只负责「拿数据 + 归一化」，不落盘、不做体积/时长判断（那些在
`douyin_downloader.py` 里做），这样字段逻辑可以脱离网络单测。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from app.downloaders.errors import DownloadError, RetryableDownloadError

logger = logging.getLogger(__name__)

# 桌面 Chrome UA：详情接口与 CDN 下载都认它。
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
# 移动端分享页必须用 iPhone UA：桌面 UA 会被 302 到 www.douyin.com。
IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

WEB_REFERER = "https://www.douyin.com/"
SHARE_REFERER = "https://www.iesdouyin.com/"

DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
SHARE_URL_TEMPLATE = "https://www.iesdouyin.com/share/{kind}/{aweme_id}"
LIVE_URL = "https://live.douyin.com/"
TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"

# (连接, 读) 超时。历史上抖音链路完全没有 timeout，一次卡住会挂满整个任务队列。
REQUEST_TIMEOUT: Tuple[int, int] = (5, 30)
# ttwid 引导只探测一个 HTML，读超时可以短一点。
BOOTSTRAP_TIMEOUT: Tuple[int, int] = (5, 15)

# ttwid 在进程内复用。实测高频请求才会触发软拦截，所以没必要每次去 live.douyin.com
# 重新领；30 分钟足够保守（ttwid 本身带了签发时间戳，服务端并不严格校验时长）。
TTWID_TTL_SECONDS = 30 * 60

# 详情接口的基础参数。签名（a_bogus）已经不需要了，这些参数只影响返回内容
# （尤其是图文作品：只传 aweme_id 会拿到 filter_reason=images_base，补上 aid /
# device_platform / channel 就能直接拿到 aweme_detail）。
BASE_PARAMS: Dict[str, str] = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "190500",
    "version_name": "19.5.0",
    "cookie_enabled": "true",
    "screen_width": "1920",
    "screen_height": "1080",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "130.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "130.0.0.0",
    "os_name": "Windows",
    "os_version": "10",
    "cpu_core_num": "12",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "0",
}

# 图文作品的过滤原因：这不是错误，而是「web 详情接口不给图文数据」的信号。
IMAGE_POST_FILTER_REASON = "images_base"

# ttwid 注册接口的固定请求体（live.douyin.com 引导失败时的备选路径）。
_TTWID_REGISTER_PAYLOAD: Dict[str, Any] = {
    "region": "cn",
    "aid": 1768,
    "needFid": False,
    "service": "www.ixigua.com",
    "migrate_info": {"ticket": "", "source": "node"},
    "cbUrlProtocol": "https",
    "union": True,
}

_TTWID_COOKIE_RE = re.compile(r"(?:^|[;\s])ttwid=(?P<value>[^;\s]+)")

# `_cookie_header` 的哨兵值：区分「没传」和「确定没有 ttwid」。
_UNSET: Any = object()


class ImagePostFallback(Exception):
    """详情接口对图文作品返回 `images_base`：调用方应改走移动端分享页。

    这是内部信号而非用户可见错误，所以不继承 `DownloadError`。
    """


# ---------------------------------------------------------------------------
# ttwid（匿名访客身份）
# ---------------------------------------------------------------------------
class TtwidCache:
    """进程内 ttwid 缓存（带 TTL + 失效）。

    单独做成类而不是模块级变量，是为了让测试可以注入一份全新的缓存，
    不互相污染。
    """

    def __init__(self, ttl_seconds: int = TTWID_TTL_SECONDS):
        self._ttl_seconds = ttl_seconds
        self._value: Optional[str] = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self) -> Optional[str]:
        with self._lock:
            if self._value and time.monotonic() < self._expires_at:
                return self._value
            return None

    def set(self, value: str) -> None:
        if not value:
            return
        with self._lock:
            self._value = value
            self._expires_at = time.monotonic() + self._ttl_seconds

    def invalidate(self) -> None:
        with self._lock:
            self._value = None
            self._expires_at = 0.0


# 所有 DouyinAPI 实例共享一份：一次任务里刷新出来的 ttwid，下个任务可以直接用，
# 避免每个任务都去打一次 live.douyin.com（高频引导本身也会触发软拦截）。
default_ttwid_cache = TtwidCache()


def extract_ttwid(response: Any) -> Optional[str]:
    """从响应里取 ttwid：优先 cookie jar，其次 Set-Cookie 头。

    做成独立函数是为了兼容注入的假响应对象（测试里不需要真的 requests）。
    """
    cookies = getattr(response, "cookies", None)
    if cookies is not None:
        try:
            value = cookies.get("ttwid")
        except (AttributeError, TypeError):
            value = None
        if value:
            return str(value)

    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    if not isinstance(raw, str):
        # requests 的 headers 是 CaseInsensitiveDict，Set-Cookie 只会保留一条；
        # 真出现多值（比如假响应给了 list）时拼起来再匹配。
        try:
            raw = ", ".join(str(item) for item in raw)
        except TypeError:
            raw = ""
    match = _TTWID_COOKIE_RE.search(raw)
    return match.group("value") if match else None


def extract_ttwid_from_cookie(cookie: Optional[str]) -> Optional[str]:
    """从用户配置的 `name=value; ...` Cookie 头里取 ttwid（可复用就不必重新引导）。"""
    if not cookie:
        return None
    match = _TTWID_COOKIE_RE.search(str(cookie))
    return match.group("value") if match else None


def _strip_cookie(cookie: Optional[str], drop_names: Iterable[str]) -> str:
    """从 `name=value; ...` 里剔掉指定项，其余原样保留。

    用于「ttwid 必须用引导出来的值，其他用户配置项照常带上」。
    """
    if not cookie:
        return ""
    dropped = {str(name).lower() for name in drop_names}
    pieces: List[str] = []
    for part in str(cookie).split(";"):
        pair = part.strip()
        if not pair:
            continue
        name, separator, _value = pair.partition("=")
        if not separator or name.strip().lower() in dropped:
            continue
        pieces.append(pair)
    return "; ".join(pieces)


# ---------------------------------------------------------------------------
# 纯函数：字段归一化
# ---------------------------------------------------------------------------
def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _url_list(container: Any) -> List[str]:
    """从 `{url_list: [...]}` 结构里取 URL；结构缺失/畸形一律返回空列表。"""
    if not isinstance(container, dict):
        return []
    urls = container.get("url_list")
    if not isinstance(urls, (list, tuple)):
        return []
    return [str(url) for url in urls if isinstance(url, str) and url]


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def prefer_watermark_free(url: str) -> str:
    """把分享页的 `/playwm/`（带水印）换成 `/play/`（无水印）。

    分享页只下发 playwm 地址。实测替换后 CDN 直接返回 h264 + aac 的无水印版本，
    不需要额外鉴权。
    """
    if not url:
        return url
    if "/playwm/" in url:
        return url.replace("/playwm/", "/play/")
    if url.rstrip("/").endswith("/playwm"):
        return url.rstrip("/") + "/"
    return url


def _gear_resolution(gear: Dict[str, Any]) -> Tuple[int, int]:
    """取某个码率档位的分辨率。

    实测 `bit_rate[]` 条目自己的 `width/height` 常常是 null，真实分辨率在
    `bit_rate[i].play_addr.width/height` 上，所以两处都要看。
    """
    play_addr = gear.get("play_addr")
    for source in (play_addr if isinstance(play_addr, dict) else {}, gear):
        width = _as_int(source.get("width"))
        height = _as_int(source.get("height"))
        if width and height:
            return width, height
    return 0, 0


def sorted_mp4_gears(video_data: Any) -> List[Dict[str, Any]]:
    """按「分辨率降序、码率次级」排列的 mp4 码率档位。

    两个实测坑：

    1. `format == "dash"` 的档位是纯视频轨（ffprobe 只有 h264,video），盲目取最高
       码率会得到静音视频 —— 这里直接过滤掉，只留带 aac 的 `format == "mp4"`。
    2. 码率排序 ≠ 分辨率排序：实测 `normal_540_0`(1377578) > `normal_720_0`(1327020)，
       按码率取第一反而拿到 1024x576。所以主键是 `width * height`。
    """
    if not isinstance(video_data, dict):
        return []
    gears = [
        gear
        for gear in (video_data.get("bit_rate") or [])
        if isinstance(gear, dict)
    ]
    playable = [
        gear
        for gear in gears
        if str(gear.get("format") or "").strip().lower() == "mp4"
    ]

    def sort_key(gear: Dict[str, Any]) -> Tuple[int, int]:
        width, height = _gear_resolution(gear)
        return width * height, _as_int(gear.get("bit_rate")) or 0

    return sorted(playable, key=sort_key, reverse=True)


def collect_video_candidates(video_data: Any) -> List[str]:
    """收集视频 URL 候选，按「无水印优先、清晰度优先」排序。

    顺序（任务实测确认）：

    1. `play_addr`（无水印，与 `play_addr_h264` 逐字节相同）
    2. `play_addr_h264`
    3. `play_addr_265`（HEVC + AAC，体积约小 29%）
    4. `bit_rate[]` 里 `format == "mp4"` 的档位（按分辨率降序，跳过 dash）
    5. `download_addr`（最后兜底：720x720 正方裁剪 + 重编码 + 带水印）

    注意 `has_watermark` 是**帖子级**标志，对无水印的 play_addr 也是 true，
    不能拿它做逐 URL 判据。
    """
    if not isinstance(video_data, dict):
        return []

    candidates: List[str] = []
    for key in ("play_addr", "play_addr_h264", "play_addr_265"):
        candidates.extend(_url_list(video_data.get(key)))
    for gear in sorted_mp4_gears(video_data):
        candidates.extend(_url_list(gear.get("play_addr")))
    candidates.extend(_url_list(video_data.get("download_addr")))

    return _dedupe(prefer_watermark_free(url) for url in candidates)


def collect_audio_candidates(item: Dict[str, Any]) -> List[str]:
    """收集音轨来源候选。

    普通视频：音轨就在视频流里，直接复用视频候选（下载后用 ffmpeg `-vn` 抽取），
    **绝不使用** `music.play_url` —— 那是 BGM/原声（`is_use_music=True` 的作品
    会拿到别人的配乐，转写完全错）。

    图文作品：没有视频轨，`video.play_addr` 指向的就是该作品的音频文件本身
    （实测与 `music.play_url.uri` 逐字节相同，容器是 M4A），这时才允许用
    `music.play_url` 兜底。
    """
    item = item if isinstance(item, dict) else {}
    video_data = item.get("video") if isinstance(item.get("video"), dict) else {}
    music = item.get("music") if isinstance(item.get("music"), dict) else {}

    if not is_image_post(item):
        return collect_video_candidates(video_data)

    candidates: List[str] = []
    # video.play_addr.uri 在图文作品里就是裸文件地址（分享页会包一层 playwm，
    # 这里把裸地址排在最前面，它才是真正能下到音频的那个）。
    play_addr = video_data.get("play_addr")
    if isinstance(play_addr, dict):
        uri = play_addr.get("uri")
        if isinstance(uri, str) and uri.startswith("http"):
            candidates.append(uri)
    play_url = music.get("play_url")
    if isinstance(play_url, dict):
        uri = play_url.get("uri")
        if isinstance(uri, str) and uri.startswith("http"):
            candidates.append(uri)
        candidates.extend(_url_list(play_url))
    candidates.extend(_url_list(play_addr))
    return _dedupe(prefer_watermark_free(url) for url in candidates)


def is_image_post(item: Dict[str, Any]) -> bool:
    """是否图文作品。

    只看 `images` 是否非空：`aweme_type` 在不同接口/不同参数下实测会变
    （同一个图文作品一次是 68、一次是 0），不可靠。
    """
    if not isinstance(item, dict):
        return False
    return bool(item.get("images"))


def duration_seconds(item: Dict[str, Any]) -> float:
    """作品时长（秒）。

    `video.duration` 是**毫秒**（实测 395440 -> music.duration=395 秒），
    `music.duration` 才是秒。图文作品的 `video.duration` 是 0，此时用配乐时长
    （音频文件本身就是配乐全长）。
    """
    if not isinstance(item, dict):
        return 0.0
    video_data = item.get("video") if isinstance(item.get("video"), dict) else {}
    duration_ms = _as_int(video_data.get("duration"))
    if not duration_ms:
        duration_ms = _as_int(item.get("duration"))
    if duration_ms and duration_ms > 0:
        return round(duration_ms / 1000.0, 3)

    music = item.get("music") if isinstance(item.get("music"), dict) else {}
    music_seconds = _as_int(music.get("duration"))
    if music_seconds and music_seconds > 0:
        return float(music_seconds)
    return 0.0


def pick_title(item: Dict[str, Any], aweme_id: Optional[str] = None) -> str:
    """标题：`desc` 优先、`item_title` 兜底。

    实测 `item_title` 是空字符串，真实标题只在 `desc` 里；反过来也有部分接口
    只给 item_title，所以两者都认，desc 优先。
    """
    if isinstance(item, dict):
        for key in ("desc", "item_title"):
            value = item.get(key)
            if isinstance(value, str):
                title = re.sub(r"\s+", " ", value).strip()
                if title:
                    return title
    fallback_id = aweme_id or (item.get("aweme_id") if isinstance(item, dict) else None)
    return f"抖音作品 {fallback_id}" if fallback_id else "抖音作品"


def pick_cover(item: Dict[str, Any]) -> Optional[str]:
    """封面：`cover_original_scale` -> `origin_cover` -> `cover` -> 作品首图。

    实测 `cover_original_scale` 在图文作品和分享页里都不存在，历史实现直接下标
    会 KeyError。任何一级缺失都必须能继续往下掉，全部缺失返回 None。
    """
    if not isinstance(item, dict):
        return None
    video_data = item.get("video") if isinstance(item.get("video"), dict) else {}
    for key in ("cover_original_scale", "origin_cover", "cover"):
        urls = _url_list(video_data.get(key))
        if urls:
            return urls[0]

    for image in item.get("images") or []:
        urls = _url_list(image)
        if urls:
            return urls[0]
    return None


def collect_tags(item: Dict[str, Any]) -> List[str]:
    """话题标签：`text_extra[].hashtag_name` + `video_tag[].tag_name`。"""
    if not isinstance(item, dict):
        return []
    tags: List[str] = []
    for extra in item.get("text_extra") or []:
        if isinstance(extra, dict):
            name = extra.get("hashtag_name")
            if isinstance(name, str) and name.strip():
                tags.append(name.strip())
    for tag in item.get("video_tag") or []:
        if isinstance(tag, dict):
            name = tag.get("tag_name")
            if isinstance(name, str) and name.strip():
                tags.append(name.strip())
    for challenge in item.get("cha_list") or []:
        if isinstance(challenge, dict):
            name = challenge.get("cha_name")
            if isinstance(name, str) and name.strip():
                tags.append(name.strip())
    return _dedupe(tags)


def estimate_size_bytes(item: Dict[str, Any]) -> Optional[int]:
    """从详情里估算媒体体积，用于「下载前就拒绝超大文件」。

    实测 play_addr 里带 `data_size`（字节），比 HEAD 请求/CDN content-length
    更早可用。
    """
    if not isinstance(item, dict):
        return None
    video_data = item.get("video") if isinstance(item.get("video"), dict) else {}

    def _size(container: Any) -> Optional[int]:
        if not isinstance(container, dict):
            return None
        value = _as_int(container.get("data_size"))
        return value if value and value > 0 else None

    if is_image_post(item):
        music = item.get("music") if isinstance(item.get("music"), dict) else {}
        return _size(music.get("play_url")) or _size(video_data.get("play_addr"))

    for key in ("play_addr", "play_addr_h264", "play_addr_265"):
        size = _size(video_data.get(key))
        if size:
            return size
    return None


@dataclass(frozen=True)
class DouyinMedia:
    """归一化后的抖音作品媒体信息（下载层只依赖这个结构，不再碰原始 JSON）。"""

    aweme_id: str
    title: str
    desc: str
    duration: float
    cover_url: Optional[str]
    video_candidates: Tuple[str, ...]
    audio_candidates: Tuple[str, ...]
    tags: Tuple[str, ...]
    author: str
    is_image_post: bool
    estimated_size: Optional[int] = None
    statistics: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def container_ext(self) -> str:
        """落盘容器扩展名：图文作品下载到的是 M4A，普通视频是 MP4。"""
        return "m4a" if self.is_image_post else "mp4"

    @property
    def webpage_url(self) -> str:
        kind = "note" if self.is_image_post else "video"
        return f"https://www.douyin.com/{kind}/{self.aweme_id}"


def build_media(item: Dict[str, Any], aweme_id: Optional[str] = None) -> DouyinMedia:
    """把详情接口/分享页的原始 item 归一化成 `DouyinMedia`（纯函数，可单测）。"""
    item = item if isinstance(item, dict) else {}
    resolved_id = str(item.get("aweme_id") or aweme_id or "")
    video_data = item.get("video") if isinstance(item.get("video"), dict) else {}
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    desc = item.get("desc") if isinstance(item.get("desc"), str) else ""

    image_post = is_image_post(item)
    # 普通视频的音轨就在视频流里；图文作品的 audio_candidates 才是它的音频文件。
    video_candidates: Sequence[str] = () if image_post else collect_video_candidates(video_data)
    audio_candidates = collect_audio_candidates(item)

    return DouyinMedia(
        aweme_id=resolved_id,
        title=pick_title(item, resolved_id),
        desc=desc,
        duration=duration_seconds(item),
        cover_url=pick_cover(item),
        video_candidates=tuple(_dedupe(video_candidates)),
        audio_candidates=tuple(_dedupe(audio_candidates)),
        tags=tuple(collect_tags(item)),
        author=str(author.get("nickname") or ""),
        is_image_post=image_post,
        estimated_size=estimate_size_bytes(item),
        statistics=statistics,
        raw=item,
    )


# ---------------------------------------------------------------------------
# 分享页解析
# ---------------------------------------------------------------------------
def extract_router_data(html: str) -> Optional[Dict[str, Any]]:
    """从分享页 HTML 里取 `window._ROUTER_DATA` 的 JSON。

    用 `JSONDecoder.raw_decode` 而不是正则匹配到 `</script>`：JSON 里可能包含
    `}` 或转义字符，正则很容易截断。
    """
    if not html:
        return None
    marker_index = html.find("window._ROUTER_DATA")
    if marker_index < 0:
        return None
    start = html.find("{", marker_index)
    if start < 0:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[start:])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_share_payload(html: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """解析分享页，返回 (item, filter_reason)。

    `loaderData` 下的 key 不固定（`video_(id)/page` / `note_(id)/page`），所以按
    「哪个值里含 videoInfoRes」来找。
    """
    payload = extract_router_data(html)
    if not payload:
        return None, None
    loader_data = payload.get("loaderData")
    if not isinstance(loader_data, dict):
        return None, None

    for value in loader_data.values():
        if not isinstance(value, dict):
            continue
        info = value.get("videoInfoRes")
        if not isinstance(info, dict):
            continue
        items = info.get("item_list")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0], None
        filters = info.get("filter_list")
        if isinstance(filters, list) and filters and isinstance(filters[0], dict):
            return None, str(filters[0].get("filter_reason") or "")
        return None, None
    return None, None


def share_url(aweme_id: str, kind: str = "video") -> str:
    return SHARE_URL_TEMPLATE.format(kind=kind, aweme_id=aweme_id)


# ---------------------------------------------------------------------------
# API 客户端
# ---------------------------------------------------------------------------
class DouyinAPI:
    """抖音免登录详情客户端。

    只做两件事：维护 ttwid 身份 + 拿 `aweme_detail`。字段语义见模块级纯函数。

    :param cookie: 用户配置的抖音 Cookie（`name=value; ...`），可以为 None。
        里面如果有 ttwid 会优先复用，省一次 live.douyin.com 引导。
    :param http: 注入的网络实现（默认 `requests` 模块）。测试里传假实现，
        这样不发真实请求。
    :param ttwid_cache: 注入的 ttwid 缓存（默认进程级共享缓存）。
    """

    def __init__(
        self,
        cookie: Optional[str] = None,
        *,
        http: Any = None,
        ttwid_cache: Optional[TtwidCache] = None,
        timeout: Tuple[int, int] = REQUEST_TIMEOUT,
    ):
        self._http = http if http is not None else requests
        self._ttwid_cache = ttwid_cache if ttwid_cache is not None else default_ttwid_cache
        self._seed_ttwid = extract_ttwid_from_cookie(cookie)
        # 用户配置的 Cookie 里除了 ttwid 还有 sessionid / uid_tt 这些登录态。
        # 免登录只需要 ttwid，但带上登录态能让「私密/仅粉丝可见」的作品也能解析，
        # 所以其余项原样保留（实测加进来不影响匿名请求）。
        self._extra_cookies = _strip_cookie(cookie, ("ttwid",))
        self._timeout = timeout

    # -- 对外 ------------------------------------------------------------
    @property
    def http(self) -> Any:
        """当前使用的网络实现（下载层复用它去拉 CDN，测试里也用它注入假响应）。"""
        return self._http

    @property
    def timeout(self) -> Tuple[int, int]:
        return self._timeout

    def fetch_media(self, aweme_id: str) -> DouyinMedia:
        """拿作品详情并归一化；图文作品自动回退分享页。"""
        item = self.fetch_item(aweme_id)
        media = build_media(item, aweme_id)
        if not media.audio_candidates and not media.video_candidates:
            raise DownloadError("抖音作品没有可下载的媒体流（可能已下架、私密或仅限登录观看）")
        return media

    def fetch_item(self, aweme_id: str) -> Dict[str, Any]:
        """优先 web 详情接口；图文作品（images_base）回退移动端分享页。"""
        try:
            return self.fetch_detail(aweme_id)
        except ImagePostFallback as exc:
            logger.info("抖音图文作品回退分享页：%s", exc)
        return self.fetch_share_item(aweme_id)

    def fetch_detail(self, aweme_id: str) -> Dict[str, Any]:
        """获取 `aweme_detail`，软拦截/403 时刷新 ttwid 重试一次。

        :raises RetryableDownloadError: 两轮都失败（软拦截）
        :raises ImagePostFallback: 图文作品，需要走分享页
        :raises DownloadError: 作品不存在/私密等不可重试错误
        """
        if not aweme_id:
            raise DownloadError("抖音作品 ID 为空")

        last_error: Optional[BaseException] = None
        # 首轮 + 刷新 ttwid 后重试一次（对应实测的「两轮策略」）。
        for attempt in range(2):
            refresh = attempt > 0
            if refresh:
                logger.info("刷新抖音 ttwid 后重试详情请求（aweme_id=%s）", aweme_id)
            try:
                return self._fetch_detail_once(aweme_id, refresh_ttwid=refresh)
            except RetryableDownloadError as exc:
                last_error = exc
                logger.warning(
                    "抖音详情请求失败（第 %d/2 轮，aweme_id=%s）：%s", attempt + 1, aweme_id, exc
                )
        raise last_error  # type: ignore[misc]

    def fetch_share_item(self, aweme_id: str) -> Dict[str, Any]:
        """从移动端分享页取 item（图文作品的主路径 / 详情接口的兜底）。"""
        if not aweme_id:
            raise DownloadError("抖音作品 ID 为空")

        headers = {
            "User-Agent": IPHONE_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": SHARE_REFERER,
        }
        cookie = self._cookie_header()
        if cookie:
            headers["Cookie"] = cookie

        last_error: Optional[BaseException] = None
        # 分享页的路径 kind 与作品类型不严格对应（实测图文用 video 也能拿到），
        # 两个都试一遍成本很低。
        for kind in ("video", "note"):
            url = share_url(aweme_id, kind)
            try:
                response = self._http.get(
                    url, headers=headers, timeout=self._timeout, allow_redirects=True
                )
            except requests.RequestException as exc:
                last_error = RetryableDownloadError(
                    f"抖音分享页请求失败：{exc.__class__.__name__}"
                )
                continue

            status = getattr(response, "status_code", 0)
            body = getattr(response, "content", b"") or b""
            if status != 200:
                last_error = RetryableDownloadError(f"抖音分享页返回 HTTP {status}")
                continue
            if not body:
                last_error = RetryableDownloadError("抖音分享页返回空响应（软拦截）")
                continue

            html = body.decode("utf-8", errors="ignore")
            item, filter_reason = parse_share_payload(html)
            if item:
                return item
            if filter_reason:
                raise DownloadError(
                    f"抖音作品不可用（{filter_reason}）：可能已删除、私密或受地区限制"
                )
            last_error = RetryableDownloadError("抖音分享页未包含作品数据（可能被风控拦截）")

        raise last_error or RetryableDownloadError("抖音分享页解析失败")

    # -- ttwid -----------------------------------------------------------
    def _ttwid(self, *, force_refresh: bool = False) -> Optional[str]:
        """取当前 ttwid（缓存 -> 用户配置 -> live.douyin.com 引导 -> 注册接口）。"""
        if force_refresh:
            self._ttwid_cache.invalidate()

        cached = self._ttwid_cache.get()
        if cached:
            return cached

        # 用户自己配了 Cookie 里的 ttwid 就直接复用；只在强制刷新时才重新引导，
        # 因为「刷新」的语义是换一个匿名身份，拿旧值等于没刷新。
        if self._seed_ttwid and not force_refresh:
            self._ttwid_cache.set(self._seed_ttwid)
            return self._seed_ttwid

        ttwid = self._bootstrap_ttwid() or self._register_ttwid()
        if ttwid:
            self._ttwid_cache.set(ttwid)
            logger.info("抖音 ttwid 引导成功（长度 %d）", len(ttwid))
        else:
            logger.warning("抖音 ttwid 引导失败，将以无 Cookie 方式请求（大概率被软拦截）")
        return ttwid

    def _bootstrap_ttwid(self) -> Optional[str]:
        """主路径：GET live.douyin.com，从 Set-Cookie 里取 ttwid。"""
        headers = {
            "User-Agent": DESKTOP_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        try:
            response = self._http.get(
                LIVE_URL, headers=headers, timeout=BOOTSTRAP_TIMEOUT, allow_redirects=True
            )
        except requests.RequestException as exc:
            logger.warning("抖音 ttwid 引导请求失败：%s", exc.__class__.__name__)
            return None

        ttwid = extract_ttwid(response)
        if ttwid:
            return ttwid

        logger.info("live.douyin.com 未下发 ttwid，尝试 ttwid 注册接口")
        return None

    def _register_ttwid(self) -> Optional[str]:
        """备选：POST ttwid.bytedance.com 的 union/register 接口。"""
        headers = {
            "User-Agent": DESKTOP_USER_AGENT,
            "Content-Type": "application/json",
        }
        try:
            response = self._http.post(
                TTWID_REGISTER_URL,
                json=_TTWID_REGISTER_PAYLOAD,
                headers=headers,
                timeout=BOOTSTRAP_TIMEOUT,
                allow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001 - 备选路径，失败就放弃
            logger.warning("抖音 ttwid 注册接口请求失败：%s", exc.__class__.__name__)
            return None
        return extract_ttwid(response)

    # -- 内部 ------------------------------------------------------------
    def _detail_headers(self, *, refresh_ttwid: bool = False) -> Dict[str, str]:
        headers = {
            "User-Agent": DESKTOP_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": WEB_REFERER,
            # 网关只校验这个头存在，不校验值。f2 / JoeanAmier 也都硬编码 "1"。
            # 千万不要传伪造的 a_bogus —— 实测比不传更糟（会退化成 200/空）。
            "x-tt-argus": "1",
        }
        ttwid = self._ttwid(force_refresh=refresh_ttwid)
        cookie = self._cookie_header(ttwid)
        # ttwid 为空时必须整体省略 Cookie，传空串同样被软拦截。
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _cookie_header(self, ttwid: Any = _UNSET) -> str:
        """拼出请求用的 Cookie：ttwid 打头 + 用户配置里的其余项。

        :param ttwid: 调用方已经算好的 ttwid。传 `_UNSET`（默认）表示「自己算」；
            传 `None` 表示「确定没有 ttwid」，此时**不能**再去引导一次
            （`_detail_headers` 已经算过了，重复引导等于多打一次 live.douyin.com，
            而高频引导本身就是软拦截的诱因）。

        用户 Cookie 里除了 ttwid 还有 sessionid / uid_tt 这类登录态，原样带上
        才能解析「私密/仅粉丝可见」的作品（实测带上不影响匿名请求）。
        """
        if ttwid is _UNSET:
            resolved_ttwid = self._ttwid()
        else:
            resolved_ttwid = ttwid
        if resolved_ttwid:
            if self._extra_cookies:
                return f"ttwid={resolved_ttwid}; {self._extra_cookies}"
            return f"ttwid={resolved_ttwid}"
        return self._extra_cookies

    def _fetch_detail_once(self, aweme_id: str, *, refresh_ttwid: bool = False) -> Dict[str, Any]:
        params = dict(BASE_PARAMS)
        params["aweme_id"] = aweme_id

        try:
            response = self._http.get(
                DETAIL_URL,
                params=params,
                headers=self._detail_headers(refresh_ttwid=refresh_ttwid),
                timeout=self._timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise RetryableDownloadError(
                f"抖音详情请求网络异常：{exc.__class__.__name__}"
            ) from exc

        status = getattr(response, "status_code", 0)
        body = getattr(response, "content", b"") or b""
        if status == 403:
            # 无 x-tt-argus 或 ttwid 失效时的典型返回：
            # "Blocked by ArgusSecurityPlugin Uifid Not Found"
            raise RetryableDownloadError("抖音详情接口 403（Argus 风控）")
        if status == 429 or status >= 500:
            raise RetryableDownloadError(f"抖音详情接口 HTTP {status}")
        if status != 200:
            raise DownloadError(f"抖音详情接口返回 HTTP {status}")
        if not body:
            # 200 + 0 字节 = 软拦截，绝不能当成「视频不存在」。
            raise RetryableDownloadError("抖音详情接口返回空响应（200 + 0 字节，软拦截）")

        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise RetryableDownloadError("抖音详情接口返回非 JSON 内容（软拦截）") from exc
        if not isinstance(payload, dict):
            raise RetryableDownloadError("抖音详情接口返回结构异常")

        detail = payload.get("aweme_detail")
        if isinstance(detail, dict) and detail:
            return detail

        filter_detail = payload.get("filter_detail")
        reason = ""
        if isinstance(filter_detail, dict):
            reason = str(filter_detail.get("filter_reason") or "")
        if reason == IMAGE_POST_FILTER_REASON:
            raise ImagePostFallback("图文作品（images_base）需要走移动端分享页")
        if reason:
            raise DownloadError(
                f"抖音作品不可用（{reason}）：可能已删除、私密或受地区限制"
            )
        raise RetryableDownloadError(
            f"抖音详情接口未返回作品数据（status_code={payload.get('status_code')}）"
        )
