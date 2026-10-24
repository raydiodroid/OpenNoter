"""B 站短链解析与 aid/BV 互转的公共工具。

参考了 astrbot_plugin_link_resolver 在 `_resolve_short_url` 上的两个细节：

1. **带 UA**：b23.tv 对 `python-requests/2.34.2` 这类默认 UA 会直接回 412
   （实测），换成移动端/桌面端 UA 才稳定拿到 302。所以这里显式塞 UA。
2. **先 HEAD 失败再 GET**：部分短链只响应 GET，HEAD 会 405。

另外补上 `av` 号 ↔ `BV` 号互转：OpenNoter 之前只认 BV，`av170001` 会一路走到
`extract_video_id` 返回 None，然后拼出 `None.mp4` 这种缓存文件名。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 移动端 UA：实测 b23.tv 对默认 python-requests UA 返回 412，对移动端 UA 返回 302。
SHORT_LINK_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

SHORT_LINK_HOSTS = ("b23.tv", "bili2233.cn")
SHORT_LINK_TIMEOUT = (5, 10)  # (connect, read)


def is_short_link(url: str) -> bool:
    host = (_hostname(url) or "").lower()
    return any(host == h or host.endswith("." + h) for h in SHORT_LINK_HOSTS)


def _hostname(url: str) -> str:
    try:
        return requests.utils.urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001 - 输入可能任意
        return ""


def resolve_short_url(short_url: str, *, timeout=SHORT_LINK_TIMEOUT) -> Optional[str]:
    """把 b23.tv / bili2233.cn 短链解析成真实 URL。

    失败返回 None（调用方继续用原链接兜底），不抛异常——短链解析失败不该让
    整个任务挂掉。
    """
    headers = {"User-Agent": SHORT_LINK_USER_AGENT, "Referer": "https://www.bilibili.com/"}
    try:
        response = requests.head(
            short_url,
            allow_redirects=True,
            timeout=timeout,
            headers=headers,
        )
        # HEAD 被拒（405/412）或没拿到跳转时改用 GET。
        if response.status_code >= 400:
            response = requests.get(
                short_url,
                allow_redirects=True,
                timeout=timeout,
                headers=headers,
                stream=True,
            )
            response.close()
        return response.url
    except requests.RequestException as exc:
        logger.warning("解析 B 站短链失败: %s (%s)", short_url, exc)
        return None


# ---------------------------------------------------------------------------
# av 号 <-> BV 号
# ---------------------------------------------------------------------------
# 算法来自 B 站前端公开的 av/BV 转换表（xOR 掩码 + base58 变体）。
# 各开源项目（bilibili-API-collect、bilibili-api-python）用的是同一份常量。
_BV_XOR_CODE = 23442827791579
_BV_MASK_CODE = 2251799813685247
_BV_MAX_AID = 1 << 51
_BV_CHARS = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"


def av2bv(aid: int) -> str:
    """`aid`（数字）转 `BV` 号。非法输入抛 ValueError。"""
    if not isinstance(aid, int) or aid < 0 or aid >= _BV_MAX_AID:
        raise ValueError(f"invalid aid: {aid!r}")
    bytes_ = list("BV1000000000")
    bv_idx = len(bytes_) - 1
    tmp = (_BV_MAX_AID | aid) ^ _BV_XOR_CODE
    while tmp > 0:
        bytes_[bv_idx] = _BV_CHARS[tmp % 58]
        tmp //= 58
        bv_idx -= 1
    bytes_[3], bytes_[9] = bytes_[9], bytes_[3]
    bytes_[4], bytes_[7] = bytes_[7], bytes_[4]
    return "".join(bytes_)


def bv2av(bvid: str) -> int:
    """`BV` 号转 `aid`（数字）。非法输入抛 ValueError。"""
    if not isinstance(bvid, str) or len(bvid) != 12 or not bvid.startswith("BV"):
        raise ValueError(f"invalid bvid: {bvid!r}")
    chars = list(bvid)
    chars[3], chars[9] = chars[9], chars[3]
    chars[4], chars[7] = chars[7], chars[4]
    tmp = 0
    for ch in chars[3:]:
        idx = _BV_CHARS.find(ch)
        if idx < 0:
            raise ValueError(f"invalid bvid char: {ch!r}")
        tmp = tmp * 58 + idx
    return (tmp & _BV_MASK_CODE) ^ _BV_XOR_CODE


def normalize_bvid(value: str) -> Optional[str]:
    """把 `BV...` / `av...` / 纯数字 aid 统一成 BV 号；不认识返回 None。"""
    if not value:
        return None
    text = value.strip()
    bv_match = re.fullmatch(r"[Bb][Vv]([0-9A-Za-z]{10})", text)
    if bv_match:
        return f"BV{bv_match.group(1)}"
    av_match = re.fullmatch(r"[Aa][Vv](\d+)", text)
    aid: Optional[int] = int(av_match.group(1)) if av_match else None
    if aid is None and re.fullmatch(r"\d+", text):
        aid = int(text)
    if aid is None:
        return None
    try:
        return av2bv(aid)
    except ValueError:
        return None
