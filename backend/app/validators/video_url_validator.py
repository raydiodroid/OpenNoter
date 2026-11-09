"""视频链接校验：只允许受支持的平台域名。

历史漏洞：这里曾用「子串包含」判断抖音/快手：

    if pattern in url:      # pattern 就是 "douyin" / "kuaishou"
        return True

于是 `http://169.254.169.254/latest/meta-data/?x=douyin` 能通过校验，
随后 `DouyinDownloader.extract_video_id()` 会真的对这个地址发 `requests.head`，
构成 SSRF（可探测/访问云元数据端点）。

现在改成：先按 hostname 判断平台归属，再按平台判路径形态；最后再过一遍
**纯语法层**的内网地址黑名单（`assert_url_shape_safe`，不联网），双保险。

## 为什么这里只做语法校验，不做 DNS 解析

这个函数回答的是「这条链接是不是我们支持的平台视频链接」，属于**语法/语义
判定**；DNS 能不能解析、此刻解析到哪个地址，是**运行环境**的网络条件。

历史上这里调的是会真实解析 DNS 的 `assert_safe_url`，于是两类与「平台是否
支持」完全无关的情况都会让用户看到「选择的平台不受支持」：

  1. DNS 解析失败（内网无 DNS / 公司 DNS 拦截 / 离线）；
  2. DNS 被污染或内网劫持，平台域名解析到 0.0.0.0 / 127.0.0.1 / 保留段。

（`backend/tests/conftest.py` 的 `stable_dns` fixture 早就为这个坑给测试打了
补丁，但生产代码没有同样的保护 —— 测试是绿的，用户却是错的。）
修复：换成 `assert_url_shape_safe`，判定不再依赖网络。

DNS 层的拦截并没有被削弱，只是**挪到了它该在的位置**：真正要发请求的出站
链路（`image_proxy` 逐跳校验、`probe_endpoint`、短链解析、字幕下载）仍然调用
完整的 `assert_safe_url`。SSRF 的正确防线在「发请求之前」，而不是「判断平台时」。
"""

import re
from typing import Optional
from urllib.parse import urlparse

from pydantic import AnyUrl, BaseModel, field_validator, model_validator

from app.utils.url_parser import detect_platform, normalize_video_url
from app.utils.url_safety import UnsafeUrlError, assert_url_shape_safe

# 各平台链接必须匹配的路径形态。hostname 由 detect_platform 负责，
# 这里只关心「同一个域名下这条路径是不是视频」。
PATH_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "bilibili": (
        re.compile(r"^/(?:video|bangumi/play)/(?:BV[0-9A-Za-z]{10}|av\d+)", re.IGNORECASE),
        # 稍后再看 / 收藏夹 / 排行榜跳转：bvid 在 query 里
        re.compile(r"^/(?:list|medialist)/", re.IGNORECASE),
        re.compile(r"^/festival/", re.IGNORECASE),
    ),
    "youtube": (
        # `watch?v=` 是主形态；`/watch/?v=`（多一个尾斜杠）也是真实存在的
        # YouTube 链接形态（部分客户端 / 复制来源会带），不能因此判不支持。
        re.compile(r"^/watch/?$"),
        re.compile(r"^/(?:shorts|embed|live|v|e)/[0-9A-Za-z_-]{11}"),
        # youtu.be/<id>：id 直接就是路径根
        re.compile(r"^/[0-9A-Za-z_-]{11}/?$"),
    ),
    "douyin": (
        re.compile(r"^/(?:video|note|slides)/\d+"),
        re.compile(r"^/share/(?:video|note|slides)/\d+"),
        re.compile(r"^/m/(?:video|note|slides)/\d+"),
        # 用户页 / 发现页点开视频后 URL 形如 /user/xxx?modal_id=123
        re.compile(r"^/(?:user|discover|search|hashtag)/"),
        re.compile(r"^/[A-Za-z0-9_-]{6,}/?$"),  # 短码（v.douyin.com 跳转后的形态）
    ),
    "kuaishou": (
        re.compile(r"^/(?:short-video|f|fw|photo|profile)/"),
        re.compile(r"^/[A-Za-z0-9_-]{5,}/?$"),
    ),
}

# 短链域：路径形态不固定，只看域名就放行（跳转后的真实链接由各下载器再校验）。
SHORT_LINK_HOSTS = frozenset({"b23.tv", "bili2233.cn", "v.douyin.com", "jx.douyin.com"})


def platform_of(url: str) -> Optional[str]:
    """链接所属平台；不受支持返回 None。"""
    return detect_platform(url)


def is_supported_video_url(url: str) -> bool:
    """判断一个 URL 是不是我们支持的视频链接。

    顺序：协议 → hostname 归属平台 → 短链直接放行 → 路径形态 → 内网地址黑名单
    （**纯语法层，不联网**，理由见模块 docstring）。
    """
    parsed_url = str(url or "").strip()
    if not parsed_url:
        return False

    try:
        parsed = urlparse(parsed_url)
    except (ValueError, TypeError):
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False

    if host in SHORT_LINK_HOSTS:
        return True

    platform = detect_platform(parsed_url)
    if not platform:
        return False

    patterns = PATH_PATTERNS.get(platform, ())
    path = parsed.path or "/"
    if not any(pattern.search(path) for pattern in patterns):
        return False

    # 域名合法 + 路径合法之后，再挡一次「host 是内网 IP 字面量」的输入，
    # 比如 `http://127.0.0.1/x`、`http://169.254.169.254/...`。
    #
    # 这里**故意**用语法层函数而不是 assert_safe_url：后者会做真实 DNS 解析，
    # 解析失败/被污染时会把「平台支持」误判成「平台不支持」（用户 bug）。
    # 域名形式的 SSRF（域名解析到内网）由真正的出站链路在发请求前拦截，
    # 见模块 docstring 与 image_proxy 的逐跳校验。
    try:
        assert_url_shape_safe(parsed_url)
    except UnsafeUrlError:
        return False
    return True


class VideoRequest(BaseModel):
    url: AnyUrl
    platform: str

    @model_validator(mode="before")
    @classmethod
    def normalize_url(cls, data):
        if isinstance(data, dict) and data.get("url"):
            platform = data.get("platform")
            if not platform:
                data["platform"] = detect_platform(str(data["url"])) or platform
            if data.get("platform") == "bilibili":
                data["url"] = normalize_video_url(str(data["url"]))
        return data

    @field_validator("url")
    def validate_video_url(cls, v):
        if not is_supported_video_url(str(v)):
            raise ValueError("暂不支持该视频平台或链接格式无效")
        return v
