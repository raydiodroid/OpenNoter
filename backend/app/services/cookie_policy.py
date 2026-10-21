"""下载器 Cookie 的策略层：格式校验、白名单过滤、按平台区分格式。

设计要点：

1. **cookies 是凭据，不是浏览器 profile 导出** —— 按平台白名单只保留下载器真正
   需要的项，避免把 `s_v_web_id`、埋点、广告归因这些一并存下来。
2. **两种格式并存**：
   - `header`：`name=value; name=value`，给 requests / httpx 直接用（B站、抖音、快手）
   - `netscape`：Netscape cookies.txt，给 yt-dlp 的 `cookiefile` 用（YouTube）
   这两种格式不能共用一个解析器。历史上 YouTube 的白名单是空元组，等于永久禁用
   Cookie —— 而 YouTube 恰恰是最需要它的平台（年龄限制 / 会员 / 403 风控）。
3. **抖音要保留 `UIFID_TEMP` / `UIFID`**：抖音 Argus 风控要 `uifid`，它来自这两个
   cookie。旧白名单把它们过滤掉了，导致浏览器一键同步过来的有效身份也存不下。
   `msToken` 仍然要丢弃——那是每次请求动态生成的，存下来反而会过期后污染请求。
"""

import re
from typing import Optional

COOKIE_MAX_LENGTH = 32768
NETSCAPE_MAX_LENGTH = 256 * 1024  # cookies.txt 可能很长（多域名多条目）
COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

COOKIE_FORMAT_HEADER = "header"
COOKIE_FORMAT_NETSCAPE = "netscape"

# 平台 -> 存储格式
COOKIE_FORMATS: dict[str, str] = {
    "bilibili": COOKIE_FORMAT_HEADER,
    "douyin": COOKIE_FORMAT_HEADER,
    "kuaishou": COOKIE_FORMAT_HEADER,
    "youtube": COOKIE_FORMAT_NETSCAPE,
}

# 只在 header 格式下生效。
# Keep these lists narrow: downloader cookies are credentials, not a browser profile export.
COOKIE_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "bilibili": (
        "SESSDATA",
        "bili_jct",
        "DedeUserID",
        "DedeUserID__ckMd5",
        "sid",
        "buvid3",
        "buvid4",
    ),
    "douyin": (
        "sessionid",
        "sessionid_ss",
        "sid_guard",
        "sid_tt",
        "uid_tt",
        "uid_tt_ss",
        "passport_csrf_token",
        "passport_csrf_token_default",
        "sid_ucp_v1",
        "ssid_ucp_v1",
        "ttwid",
        "odin_tt",
        # Argus 风控需要 uifid，来源就是这两个 cookie。缺了它们，
        # /aweme/v1/web/aweme/detail/ 会返回
        # "Blocked by ArgusSecurityPlugin Uifid Not Found"。
        "UIFID_TEMP",
        "UIFID",
        "s_v_web_id",
        "verifyFp",
    ),
    "kuaishou": (
        "did",
        "didv",
        "kpf",
        "kpn",
        "clientid",
        "userId",
        "kuaishou.server.web_st",
        "kuaishou.server.web_ph",
    ),
    # netscape 格式走行级校验，不用 name 白名单（cookies.txt 里本来就是
    # YouTube 全站 cookie，逐个列名字不现实）。
    "youtube": (),
}

COOKIE_PLATFORMS = frozenset(COOKIE_ALLOWLISTS)

# Netscape cookies.txt 每行 7 个 tab 分隔字段：
# domain \t includeSubdomains \t path \t secure \t expiry \t name \t value
_NETSCAPE_FIELDS = 7


def cookie_format(platform: str) -> str:
    if platform not in COOKIE_FORMATS:
        raise ValueError("不支持的 Cookie 平台")
    return COOKIE_FORMATS[platform]


# ---------------------------------------------------------------------------
# header 格式
# ---------------------------------------------------------------------------
def _filter_header_cookies(platform: str, cookie: str) -> str:
    allowed = set(COOKIE_ALLOWLISTS[platform])
    filtered: dict[str, str] = {}
    for part in cookie.split(";"):
        pair = part.strip()
        if not pair:
            continue
        name, separator, item_value = pair.partition("=")
        name = name.strip()
        if not separator or not COOKIE_NAME_RE.fullmatch(name):
            raise ValueError("Cookie 格式无效")
        if name in allowed:
            filtered[name] = item_value.strip()

    return "; ".join(
        f"{name}={filtered[name]}"
        for name in COOKIE_ALLOWLISTS[platform]
        if name in filtered
    )


# ---------------------------------------------------------------------------
# netscape 格式
# ---------------------------------------------------------------------------
# curl / 浏览器导出的 cookies.txt 用 `#HttpOnly_` 前缀标记 HttpOnly cookie。
# 这一行**不是注释，是数据行**：yt-dlp 的 YoutubeDLCookieJar.load() 也是先
# strip 掉这个前缀再 split('\t') 的。
#
# 这里踩过一次坑：早期实现把所有以 '#' 开头的行都当注释丢掉，结果 YouTube 的
# 登录态 cookie（SID / HSID / SSID / APISID / SAPISID / __Secure-1PSID /
# __Secure-3PSID 等）**全部是 HttpOnly**，被整批丢弃。更糟的是
# CookieConfigManager.get() 发现 filtered != stored 时会回写文件，于是用户
# 粘贴的正确 cookies.txt 在第一次读取后就从磁盘上被永久删除，表现为
# 「保存成功但仍是游客态」。
_HTTPONLY_PREFIX = "#HttpOnly_"


def _split_netscape_line(raw_line: str) -> tuple[str, list[str]]:
    """把一行 cookies.txt 拆成 (是否 HttpOnly, 7 个字段)。

    `#HttpOnly_` 前缀会被剥掉再拆；其余以 '#' 开头的行是真正的注释。
    """
    if raw_line.startswith(_HTTPONLY_PREFIX):
        return True, raw_line[len(_HTTPONLY_PREFIX):].split("\t")
    return False, raw_line.split("\t")


def normalize_netscape_cookies(value: str) -> str:
    """校验并规范化 Netscape cookies.txt 文本。

    - 保留 `#HttpOnly_` 数据行（剥掉前缀校验，输出时加回去）
    - 丢掉真正的注释行，自己补一行标准头
    - 拒绝含 NUL / CR 的内容
    - 字段数不对直接报错，不做「猜格式」的容错——猜错会让 yt-dlp 静默读不到
    """
    if len(value) > NETSCAPE_MAX_LENGTH:
        raise ValueError("Cookie 内容过长")
    if "\0" in value or "\r" in value:
        raise ValueError("Cookie 格式无效")

    lines: list[str] = ["# Netscape HTTP Cookie File"]
    kept = 0
    seen: set[tuple[str, str, str]] = set()
    for raw_line in value.split("\n"):
        if not raw_line.strip():
            continue

        http_only, fields = _split_netscape_line(raw_line)
        if not http_only and raw_line.lstrip().startswith("#"):
            # 真正的注释行（`# Netscape HTTP Cookie File`、`# comment` 等）
            continue

        if len(fields) != _NETSCAPE_FIELDS:
            raise ValueError(
                "YouTube Cookie 需要 Netscape cookies.txt 格式（7 个 tab 分隔字段），"
                "请勿粘贴 name=value; 形式的请求头 Cookie"
            )
        domain, _include_sub, path, _secure, _expiry, name, _val = fields
        if not domain.strip() or not name.strip():
            raise ValueError("Cookie 格式无效")
        key = (domain.strip().lower(), path, name.strip())
        if key in seen:
            # 同名同域只保留第一条，避免重复项让 yt-dlp 解析出错
            continue
        seen.add(key)
        lines.append(_HTTPONLY_PREFIX + "\t".join(fields) if http_only else raw_line)
        kept += 1

    if not kept:
        return ""
    return "\n".join(lines) + "\n"


def is_netscape_cookies(value: str) -> bool:
    """粗略判断一段文本像不像 Netscape cookies.txt。

    用于给用户更准确的错误提示（「你贴的是 header Cookie」vs「格式坏了」）。
    """
    for raw_line in (value or "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        http_only, fields = _split_netscape_line(line)
        if not http_only and line.startswith("#"):
            continue
        return len(fields) == _NETSCAPE_FIELDS
    return False


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def filter_downloader_cookies(platform: str, value: Optional[str]) -> str:
    """按平台格式校验并过滤 Cookie，返回可以直接落盘/使用的字符串。

    - header 平台：返回 `name=value; ...`
    - netscape 平台：返回规范化后的 cookies.txt 文本（无有效行时返回 ""）
    """
    if platform not in COOKIE_ALLOWLISTS:
        raise ValueError("不支持的 Cookie 平台")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Cookie 格式无效")
    if not value.strip():
        return ""

    cookie = value.strip()
    fmt = COOKIE_FORMATS[platform]

    if fmt == COOKIE_FORMAT_NETSCAPE:
        return normalize_netscape_cookies(cookie)

    if len(cookie) > COOKIE_MAX_LENGTH or "\r" in cookie or "\n" in cookie or "\0" in cookie:
        raise ValueError("Cookie 为空或格式无效")

    return _filter_header_cookies(platform, cookie)


def _count_netscape_entries(value: str) -> int:
    """统计 cookies.txt 里的真实数据行数量（`#HttpOnly_` 行也算）。"""
    count = 0
    for raw_line in value.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        http_only, fields = _split_netscape_line(line)
        if not http_only and line.startswith("#"):
            continue
        if len(fields) == _NETSCAPE_FIELDS:
            count += 1
    return count


def cookie_entry_count(platform: str, value: str) -> int:
    """统计 Cookie 条目数，用于接口回显与日志。"""
    if not value:
        return 0
    if COOKIE_FORMATS.get(platform) == COOKIE_FORMAT_NETSCAPE:
        return _count_netscape_entries(value)
    return len([part for part in value.split(";") if part.strip()])


def cookie_hint_for_empty(platform: str) -> str:
    """过滤后为空时给用户的提示（区分「格式不对」和「没有需要的项」）。"""
    if COOKIE_FORMATS.get(platform) == COOKIE_FORMAT_NETSCAPE:
        return "Cookie 中没有有效的 Netscape cookies.txt 条目"
    if not COOKIE_ALLOWLISTS[platform]:
        return "该平台的下载器当前不使用 Cookie，无需同步"
    return "Cookie 中没有该平台下载器需要的登录项"
