"""快手视频详情获取（GraphQL）。

本次修复的问题（都是线上会真实炸的）：

1. **所有请求都没有 timeout**：`requests.get/post` 一旦对端不响应就会挂住整个
   任务线程。统一加 `timeout=(5, 30)`（连接 5s / 读 30s）。
2. **模块级 `cfm = CookieConfigManager()` 单例**：import 时就构造，之后用户在
   UI 里改 Cookie 不生效，必须重启后端。改成每次调用现读。
3. **Cookie 为空会 AttributeError**：`if not cookies: logger.error(...)` 只打日志
   没有 return，紧接着 `cookies.strip()` 直接崩。现在抛明确的 `KuaishouError`。
4. **`print(video_details)` 会把整个 GraphQL 响应打到 stdout**（几十 KB，还可能
   带 Cookie 线索）。改成 logger，且只记关键字段。
5. **photo id 解析只用 `/short-video/(\\w+)`**：快手短链会按 UA 跳到
   `m.gifshow.com/fw/photo/<id>`（iPhone UA）或 `www.kuaishou.com/short-video/<id>`
   （桌面 UA），还有 `?photoId=` 形态。现在三种都认。

Cookie 来源（按优先级）：
- `CookieConfigManager().get("kuaishou")` 里用户配的 header Cookie；
- 没有时退回「访问首页拿匿名 Cookie」（快手首页会给 did/kpf/kpn/clientid 等）。

注意：`photo.duration` 的单位是**毫秒**（参考 KS-Downloader 的
`time_conversion(time_ms) = time_ms // 1000`，它用的就是同一套 visionVideoDetail
接口）。这里提供一个 `duration_seconds()` 做归一，避免把 11 秒的视频当成 185 分钟。
"""

import logging
import re

import requests

from app.downloaders.errors import DownloadError
from app.services.cookie_manager import CookieConfigManager
from app.utils.logger import get_logger

KUAISHOU_API_BASE = "https://www.kuaishou.com/graphql"
KUAISHOU_URL = "https://www.kuaishou.com/"

# 连接 / 读取超时。Read timeout 给 30s：视频详情接口偶尔要等得很久，
# 但没有上限会挂死任务线程。
REQUEST_TIMEOUT = (5, 30)

headers = {
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Origin": "https://www.kuaishou.com",
    "Pragma": "no-cache",
    "Referer": "https://www.kuaishou.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "accept": "*/*",
    "content-type": "application/json",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

logger = get_logger(__name__)

# 快手短链 / 分享链接里 photo id 可能出现在这些位置：
#   /short-video/3xdqx42tk6mcxvi
#   /fw/photo/3xdqx42tk6mcxvi        （m.gifshow.com 移动端落地页）
#   /photo/3xdqx42tk6mcxvi
#   ?photoId=3xdqx42tk6mcxvi
_PHOTO_ID_PATTERNS = (
    re.compile(r"/short-video/([A-Za-z0-9_\-]+)"),
    re.compile(r"/fw/photo/([A-Za-z0-9_\-]+)"),
    re.compile(r"/photo/([A-Za-z0-9_\-]+)"),
    re.compile(r"[?&]photoId=([A-Za-z0-9_\-]+)"),
)

_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


class KuaishouError(DownloadError):
    """快手解析失败（Cookie 缺失 / 风控 / 详情为空 / ID 解析失败）。

    继承 `DownloadError`（retryable = False）：这类失败重试没有意义，
    不应该让 note.py 的任务级重试白跑三次。
    """


def normalize_duration_seconds(raw) -> float:
    """把 visionVideoDetail 的 `photo.duration` 归一成秒。

    实测参考实现 KS-Downloader（JoeanAmier）对同一接口的 `photo.duration`
    做的是 `duration // 1000`，即**毫秒**。为兼容「万一返回的是秒」的旧行为，
    用一个保守规则：>= 1000 视为毫秒，< 1000 视为秒。

    为什么不无条件除以 1000：11 秒的视频是 11000（毫秒），但 500 秒的视频
    如果是秒单位就是 500 —— 500 秒内不可能是毫秒（0.5 秒的视频不存在），
    所以阈值判断不会把正常的秒值误伤；只有「>= 1000 秒」的长视频在单位
    真是秒时会被当成 1 秒多，而这种长视频默认本来就会被时长上限拦掉。
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return value / 1000.0 if value >= 1000 else value


def extract_photo_id(url: str):
    """从（已跳转的）快手链接里提取 photo id，失败返回 None。"""
    for pattern in _PHOTO_ID_PATTERNS:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return None


class KuaiShou:
    def __init__(self, user_id=None):
        self.header = headers.copy()
        self.cookie = None
        # BYOK：任务归属用户，用于解析「用户自己的快手 Cookie」。
        # None = 匿名/站点模式，行为与改造前一致。
        self.user_id = user_id

    @staticmethod
    def _extract_kuaishou_link(text):
        """从分享文案里挑出第一个 http(s) 链接。

        历史实现直接 `url[0]`，文案里没有链接时抛 IndexError。
        """
        found = _URL_RE.findall(text or "")
        return found[0] if found else None

    def get_photo_id(self, url):
        """跟随短链跳转，从落地页 URL 里解析 photo id。"""
        response = requests.get(
            url, allow_redirects=True, headers=self.header, timeout=REQUEST_TIMEOUT
        )
        photo_id = extract_photo_id(response.url)
        if photo_id is None:
            # 落地页 URL 里没有 ID 时，再从 URL 本身找一次（可能是标准链接）
            photo_id = extract_photo_id(url)
        return photo_id

    def get_temp_cookies(self):
        """取 Cookie：优先用户配置的，其次访问首页拿匿名 Cookie。

        每次都现读 `CookieConfigManager()`，不再用模块级单例 ——
        否则后台改 Cookie 后必须重启进程才生效。
        """
        stored = None
        try:
            from app.services.credential_resolver import resolve_cookie

            stored = resolve_cookie(self.user_id, "kuaishou")
        except Exception as exc:  # 配置文件损坏也不该让下载直接崩
            logger.warning(f"读取快手 Cookie 配置失败，将使用匿名 Cookie：{exc}")

        if stored:
            return stored

        response = requests.get(
            url=KUAISHOU_URL,
            headers=self.header,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
        )
        cookie_string = "; ".join(
            f"{key}={value}" for key, value in response.cookies.get_dict().items()
        )
        logger.debug(f"快手匿名 Cookie 获取完成（{len(response.cookies.get_dict())} 项）")
        return cookie_string

    def get_video_details(self, url, photo_id):
        """调用 visionVideoDetail 拿作品详情。失败返回 None。"""
        json_data = {
            "operationName": "visionVideoDetail",
            "variables": {"photoId": photo_id, "page": "detail"},
            "query": (
                "query visionVideoDetail($photoId: String, $type: String, $page: String, $webPageArea: String) {\n"
                "  visionVideoDetail(photoId: $photoId, type: $type, page: $page, webPageArea: $webPageArea) {\n"
                "    status\n    type\n    author {\n      id\n      name\n      following\n      headerUrl\n"
                "      __typename\n    }\n    photo {\n      id\n      duration\n      caption\n      likeCount\n"
                "      realLikeCount\n      coverUrl\n      photoUrl\n      liked\n      timestamp\n      expTag\n"
                "      llsid\n      viewCount\n      videoRatio\n      stereoType\n      croppedPhotoUrl\n"
                "      __typename\n    }\n    tags {\n      type\n      name\n      __typename\n    }\n"
                "    commentLimit {\n      canAddComment\n      __typename\n    }\n    llsid\n"
                "    danmakuSwitch\n    __typename\n  }\n}\n"
            ),
        }
        response = requests.post(
            url=KUAISHOU_API_BASE,
            headers=self.header,
            json=json_data,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            logger.warning(f"快手详情接口 HTTP {response.status_code}")
            return None

        try:
            payload = response.json()
        except ValueError:
            logger.warning("快手详情接口返回的不是 JSON")
            return None

        # 风控/验证码：{"data":{"result":400002,"url":"...captcha..."}}
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and data.get("result") not in (None, 1):
            logger.warning(
                "快手详情接口被风控拦截（result=%s），请更新 Cookie 或更换出口 IP",
                data.get("result"),
            )
            return None
        return payload

    def run(self, url):
        """解析分享链接，返回 GraphQL 的 `data` 段。

        :raises KuaishouError: 链接 / ID / Cookie / 详情任一环节失败
        """
        real_url = self._extract_kuaishou_link(url)
        if not real_url:
            raise KuaishouError(f"快手链接解析失败，文案里没有可用的 URL：{url[:120]}")

        cookies = self.get_temp_cookies()
        if not cookies:
            # 历史 bug：这里只 logger.error 没 return，下一行 cookies.strip() 直接
            # AttributeError，用户看到的是 'NoneType' object has no attribute 'strip'。
            raise KuaishouError(
                "快手 Cookie 获取失败，且匿名访问首页也没拿到 Cookie。"
                "请在设置页配置快手 Cookie 后重试（或更换出口 IP）"
            )
        self.header["Cookie"] = cookies.strip()

        photo_id = self.get_photo_id(real_url)
        if not photo_id:
            raise KuaishouError(f"快手视频 ID 解析失败：{real_url[:160]}")

        video_details = self.get_video_details(real_url, photo_id)
        if not video_details:
            raise KuaishouError(
                "快手视频详情解析失败（可能被风控拦下，请配置有效 Cookie 后重试）"
            )

        data = video_details.get("data")
        if not isinstance(data, dict):
            raise KuaishouError("快手视频详情返回结构异常")

        # 只记关键字段，不要把整个响应（几十 KB）打进日志。
        detail = data.get("visionVideoDetail") or {}
        photo = detail.get("photo") or {}
        logger.info(
            "快手视频解析成功: id=%s, 时长=%s, 标题长度=%s",
            photo.get("id"),
            normalize_duration_seconds(photo.get("duration")),
            len(photo.get("caption") or ""),
        )
        return data


if __name__ == "__main__":
    ks = KuaiShou()
    ks.run(
        "https://v.kuaishou.com/2vBqX74 王宝强携手刘昊然、岳云鹏上演精彩名场面 全程高能 看一遍笑一遍"
    )
