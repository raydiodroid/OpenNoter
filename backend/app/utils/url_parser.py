"""链接识别 / 视频 ID 提取 / 安全校验入口。

这里集中处理三件事，避免各调用方各写一份正则：

1. **平台识别**：按 hostname 精确匹配（不是子串匹配——子串匹配会让
   `http://169.254.169.254/?x=douyin` 这种 URL 通过校验）。
2. **视频 ID 提取**：B 站支持 BV/av，抖音支持 video/note/slides/modal_id 等各种形态。
3. **短链解析**：带 UA、带 timeout，见 `app/utils/bilibili_ids.py`。
"""

import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

from app.utils.bilibili_ids import (
    av2bv,
    is_short_link,
    normalize_bvid,
    resolve_short_url,
)
from app.utils.url_safety import UnsafeUrlError, assert_safe_url

logger = logging.getLogger(__name__)

# 各平台的主机名片段，用于从链接自动识别平台
PLATFORM_HOSTS = (
    ("bilibili", ("bilibili.com", "b23.tv", "bili2233.cn", "bilibili.tv", "acg.tv")),
    ("youtube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    (
        "douyin",
        (
            "douyin.com",
            "iesdouyin.com",
            "douyinpic.com",
        ),
    ),
    ("kuaishou", ("kuaishou.com", "chenzhongtech.com", "gifshow.com", "kwaicdn.com")),
)

# 抖音各类链接形态的视频 id 所在位置。
_DOUYIN_QUERY_ID_KEYS = ("modal_id", "aweme_id", "item_id", "video_id", "note_id", "id")
_DOUYIN_PATH_ID_PATTERNS = (
    r"/(?:share/)?(?:video|note|slides)/(\d+)",
    r"/video/(\d+)",
)


def detect_platform(url: str) -> Optional[str]:
    """从链接自动识别平台，识别不出来返回 None。

    只按 hostname 判断，绝不看 path/query —— 否则攻击者可以在任意 URL 里
    塞一个 "douyin" 子串绕过平台白名单。
    """
    host = _hostname(url)
    if not host:
        return None
    for platform, domains in PLATFORM_HOSTS:
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return platform
    return None


def _hostname(url: str) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# 视频 ID
# ---------------------------------------------------------------------------
def extract_video_id(url: str, platform: str) -> Optional[str]:
    """从视频链接中提取平台内的稳定视频 ID。

    - bilibili: `BV1xxxxxxxxx`（av 号会先转成 BV，保证缓存文件名稳定）
    - youtube: 11 位 video id
    - douyin: 19 位 aweme id（支持 video/note/slides/modal_id 等形态）
    """
    if platform == "bilibili":
        return _extract_bilibili_id(url)
    if platform == "youtube":
        return _extract_youtube_id(url)
    if platform == "douyin":
        return _extract_douyin_id(url)
    return None


def _extract_bilibili_id(url: str) -> Optional[str]:
    if is_short_link(url):
        resolved = resolve_short_url(url)
        if resolved:
            url = resolved

    bv_match = re.search(r"BV([0-9A-Za-z]{10})", url)
    if bv_match:
        return f"BV{bv_match.group(1)}"

    av_match = re.search(r"/av(\d+)", url) or re.search(r"[?&]aid=(\d+)", url)
    if av_match:
        try:
            return av2bv(int(av_match.group(1)))
        except (ValueError, TypeError):
            return None
    return None


def _extract_youtube_id(url: str) -> Optional[str]:
    # 匹配 v=xxxxx、youtu.be/xxxxx、shorts/xxxxx、embed/xxxxx、live/xxxxx
    match = re.search(
        r"(?:v=|youtu\.be/|shorts/|embed/|live/)([0-9A-Za-z_-]{11})",
        url,
    )
    return match.group(1) if match else None


def _extract_douyin_id(url: str) -> Optional[str]:
    if is_douyin_short_link(url):
        resolved = resolve_short_url(url)
        if resolved:
            url = resolved

    for pattern in _DOUYIN_PATH_ID_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    try:
        query = parse_qs(urlparse(url).query)
    except (ValueError, TypeError):
        return None
    for key in _DOUYIN_QUERY_ID_KEYS:
        for value in query.get(key) or []:
            if value and value.isdigit():
                return value
    return None


def is_douyin_short_link(url: str) -> bool:
    """v.douyin.com / jx.douyin.com 这类需要先跳转的短链。"""
    host = _hostname(url)
    return host in {"v.douyin.com", "jx.douyin.com"}


def detect_bilibili_page(url: str) -> Optional[int]:
    """取 B 站分 P 序号（从 1 开始），非分 P 返回 None。

    `extract_bilibili_p_number` 的历史别名，语义一致。
    """
    return extract_bilibili_p_number(url)


# ---------------------------------------------------------------------------
# URL 归一化
# ---------------------------------------------------------------------------
def normalize_video_url(url: str) -> str:
    """
    将任意包含 BV/av 号的 B 站链接规范化为标准视频链接。

    支持稍后再看（/list/watchlater/?bvid=BV...）、收藏夹播放页（/list/mlXXX?bvid=BV...）、
    带追踪参数的分享链接、av 号等。保留分 P 参数，丢弃其余查询参数。

    短链与无 ID 的链接原样返回（后者交由校验器拒绝）。
    """
    if is_short_link(url):
        return url

    bvid = _extract_bilibili_id(url)
    if not bvid:
        return url

    normalized = f"https://www.bilibili.com/video/{bvid}"
    p = extract_bilibili_p_number(url)
    if p:
        normalized += f"?p={p}"
    return normalized


def resolve_bilibili_short_url(short_url: str) -> Optional[str]:
    """解析 b23.tv / bili2233.cn 短链，返回真实链接；失败返回 None。"""
    return resolve_short_url(short_url)


def extract_bilibili_p_number(url: str) -> Optional[int]:
    """
    从 B 站分 P 视频 URL 中提取 p 参数（分 P 序号）。

    支持格式：
      - https://www.bilibili.com/video/BVxxx/?p=36
      - https://www.bilibili.com/video/BVxxx?p=5
      - https://b23.tv/xxxxx?p=10
      - https://www.bilibili.com/video/BVxxx/pN (尾缀形式)

    :param url: B 站视频链接
    :return: 分 P 序号（从 1 开始），非分 P 视频返回 None
    """
    if is_short_link(url):
        url = resolve_short_url(url) or url

    # 匹配 ?p=NNN 或 &p=NNN
    match = re.search(r"[?&]p=(\d+)", url)
    if match:
        p = int(match.group(1))
        if p >= 1:
            return p

    # 匹配 /pN 尾缀形式（较少见）
    match = re.search(r"/p(\d+)(?:/?$|\?|&)", url)
    if match:
        p_val = int(match.group(1))
        if p_val >= 1:
            return p_val

    return None


# ---------------------------------------------------------------------------
# 出站安全
# ---------------------------------------------------------------------------
def assert_fetchable(url: str) -> str:
    """校验一个「服务端会去请求」的 URL 不指向内网，通过则原样返回。

    视频链接、封面链接、字幕链接在发请求前都应该过一遍：这些 URL 有一部分
    来自平台返回的 JSON，也有一部分来自用户输入。
    """
    try:
        return assert_safe_url(url)
    except UnsafeUrlError:
        raise


def is_safe_fetch_url(url: str) -> bool:
    """布尔版本，供不方便抛异常的分支使用。"""
    try:
        assert_safe_url(url)
        return True
    except UnsafeUrlError:
        return False
