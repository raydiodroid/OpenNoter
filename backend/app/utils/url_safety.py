"""出站 URL 安全校验：防 SSRF。

image_proxy 这类「拿用户给的 URL 去服务端抓资源」的接口，如果不校验目标地址，
持 API Key 的人就能让服务器去访问内网：127.0.0.1 上的后端、10.0.x / 192.168.x
网段、100.64.x（Tailscale）、以及 169.254.169.254 云元数据端点。

这里做三层防护：
  1. scheme 只允许 http / https（挡 file://、gopher:// 等）
  2. 主机解析出的每个 IP 都要过一遍「私网/保留地址」黑名单（挡 DNS 指向内网）
  3. 域名白名单（挡任意外部站点被当作图床代理）

## 两层接口：语法层 vs 出站层（别混用）

本模块对外暴露两个校验入口，语义**不同**，选错会出真问题：

- `assert_url_shape_safe(url)`：**纯语法层**，不联网。只校验 scheme 与
  「host 是 IP 字面量时不能落在私网/保留段」。用于**分类/预判/入口过滤**，
  例如判断「用户贴的链接是不是我们支持的视频链接」。
- `assert_safe_url(url, suffixes=...)`：**出站层**，会做真实 `socket.getaddrinfo`
  并把每个解析结果过一遍黑名单。用于**真正要发请求之前**的网关。

为什么必须分开：判断「平台是否支持」是语法/语义问题；DNS 能否解析、此刻解析
到哪个地址属于运行环境的网络条件。两者混在一起时，公司 DNS 拦了 youtube、
内网无 DNS、DNS 被污染，都会表现为「不支持该平台」—— 用户看到的是错误文案，
真正的原因被藏起来。反之，把语法层当出站层用则会漏掉真实的 SSRF（DNS 指向内网）。
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse, urlunparse

ALLOWED_SCHEMES = ("http", "https")

# 额外手工拉黑的网段：Python 的 ipaddress 判定里没覆盖全，或属于本项目在用的私网
_EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),   # Tailscale / CGNAT
    ipaddress.ip_network("198.18.0.0/15"),   # 基准测试网段
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("169.254.0.0/16"),  # 含云元数据 169.254.169.254
    ipaddress.ip_network("fe80::/10"),       # IPv6 链路本地
)

# 各平台图片 CDN 的域名后缀白名单（不带前导点，统一按「host == suffix 或
# host 以 .suffix 结尾」匹配）
DEFAULT_ALLOWED_HOST_SUFFIXES = (
    "hdslb.com",      # B 站图片
    "biliimg.com",
    "bilivideo.com",
    "ytimg.com",      # YouTube 封面
    "douyinpic.com",  # 抖音
    "byteimg.com",
    "kuaishou.com",
    "kwaicdn.com",
)


class UnsafeUrlError(ValueError):
    """URL 不满足出站安全要求。"""


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_blocked_ip(ip) -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    return any(ip in network for network in _EXTRA_BLOCKED_NETWORKS if ip.version == network.version)


def resolve_host(host: str) -> list[str]:
    """解析主机名，返回全部 A/AAAA 记录。"""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"域名解析失败: {host}") from exc
    return [info[4][0] for info in infos]


def allowed_host_suffixes() -> tuple[str, ...]:
    """白名单来源：环境变量优先，未配置时用内置的各平台 CDN 列表。

    设为 `*` 表示不限制域名（仅保留 IP 层面的私网拦截）。
    """
    from app.utils.env_compat import getenv

    raw = (getenv("IMAGE_PROXY_ALLOWED_HOSTS", "") or "").strip()
    if not raw:
        return DEFAULT_ALLOWED_HOST_SUFFIXES
    if raw == "*":
        return ()
    return tuple(
        item.strip().lower().lstrip(".")
        for item in raw.split(",")
        if item.strip()
    )


def _host_allowed(host: str, suffixes) -> bool:
    if not suffixes:
        return True
    lowered = host.lower()
    for suffix in suffixes:
        suffix = suffix.lower().lstrip(".")
        if lowered == suffix or lowered.endswith("." + suffix):
            return True
    return False


def assert_url_shape_safe(url: str) -> str:
    """**语法层**校验：只看 URL 字符串本身，不做任何网络 I/O。

    能拦下的是「不依赖解析结果就能确定不安全」的情况：

      - 非 http/https 协议（`file://`、`javascript:`、`gopher://`）；
      - host 本身是 IP 字面量且落在私网/保留段（`127.0.0.1`、`10.x`、
        `192.168.x`、`169.254.169.254`、`100.64.x` …）。

    拦不下的是「域名指向内网」（那要看解析结果）—— 所以**不能**拿它当出站
    网关用，真正发请求前必须再走 :func:`assert_safe_url`。

    反过来，**也不要把 DNS 判断加回这个函数**：调用方（视频链接校验器）判的是
    「这条链接是不是受支持平台的视频」，DNS 通不通是运行环境的事，混进来会让
    「网络抖动」变成「平台不支持」（见模块 docstring）。

    :raises UnsafeUrlError: 协议非法，或 host 是内网/保留 IP 字面量。
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"只允许 http/https 协议，当前为 {scheme or '空'}")

    host = parsed.hostname
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL 不允许包含用户名或密码")
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")

    literal = _parse_ip(host)
    if literal is not None and _is_blocked_ip(literal):
        raise UnsafeUrlError(f"不允许访问内网地址: {host}")

    return url


def assert_safe_url(url: str, suffixes=None) -> str:
    """**出站层**校验：语法 + 真实 DNS 解析 + 域名白名单。通过则原样返回。

    这是「服务端马上要按这个 URL 发请求」时该用的函数（image_proxy、
    probe_endpoint、短链解析、字幕下载…）。域名解析失败也会抛
    :class:`UnsafeUrlError`：**在这里**，解析不出来就意味着请求发不出去，
    判不安全是合理且安全的（宁可拒绝，不要盲发）。

    只做分类/预判、不打算立刻发请求的调用方（如视频链接校验器）请用
    :func:`assert_url_shape_safe`。

    :param suffixes: 域名后缀白名单；``None`` 表示用默认图片 CDN 白名单，
        ``()`` 表示不限制域名（只做 scheme + IP 层校验）。
    """
    # 语法层先过（与出站层的区别在这里：这一步不联网）
    assert_url_shape_safe(url)

    parsed = urlparse(url)
    host = parsed.hostname or ""

    # 只有域名才需要解析；IP 字面量已在语法层按字面量判过
    if _parse_ip(host) is None:
        for ip_str in resolve_host(host):
            ip = _parse_ip(ip_str)
            if ip is not None and _is_blocked_ip(ip):
                raise UnsafeUrlError(f"域名 {host} 解析到内网地址 {ip_str}")

    if suffixes is None:
        suffixes = allowed_host_suffixes()
    if not _host_allowed(host, suffixes):
        raise UnsafeUrlError(f"来源域名不在白名单内: {host}")

    return url


# ---------------------------------------------------------------------------
# 逐跳校验的安全请求（F6 / F16 共用）
# ---------------------------------------------------------------------------
# 为什么需要这个而不是直接 `requests.get(url, allow_redirects=True)`：
#
#   `assert_safe_url()` 只校验**你给它的那一个 URL**。一旦让 HTTP 库自己跟随
#   302，后续每一跳都不会再校验。攻击者只要控制（或碰上一个开放的）短链服务，
#   就能让服务端跟着跳到 `http://169.254.169.254/...` 或内网地址。
#
#   `image_proxy` 早就按「`follow_redirects=False` + 每跳重新 assert_safe_url」
#   实现过。那套逻辑此前是**内联**的，别的出站点（probe、短链解析）各自写了
#   一遍 `requests.get(..., allow_redirects=True)`，于是同一类洞修一次漏两处。
#
# 这里把它抽成单一实现，所有「服务端主动发出站请求」的地方都走它。

DEFAULT_MAX_REDIRECTS = 3


class TooManyRedirects(UnsafeUrlError):
    """跳转次数超过上限。继承 UnsafeUrlError，调用方可一并按「不安全」处理。"""


def pin_url_to_ip(url: str, *, suffixes=()) -> tuple[str, str]:
    """一次解析、校验所有地址，再固定连接目标；TLS 仍验证原始域名。"""
    assert_url_shape_safe(url)
    parsed = urlparse(url)
    host = parsed.hostname
    allowed = allowed_host_suffixes() if suffixes is None else suffixes
    if not _host_allowed(host, allowed):
        raise UnsafeUrlError(f"来源域名不在白名单内: {host}")
    ips = [host] if _parse_ip(host) is not None else resolve_host(host)
    if not ips:
        raise UnsafeUrlError(f"域名解析失败: {host}")
    for value in ips:
        ip = _parse_ip(value)
        if ip is None or _is_blocked_ip(ip):
            raise UnsafeUrlError(f"域名 {host} 解析到不安全地址")
    address = f"[{ips[0]}]" if ":" in ips[0] else ips[0]
    if parsed.port:
        address += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=address)), parsed.netloc


def _request_pinned(method, url, *, hostname, proxy_url=None, **kwargs):
    """requests 兼容响应；HTTPS 的 SNI 与证书检查均使用原始主机名。"""
    import requests
    from requests.adapters import HTTPAdapter

    class HostnameAdapter(HTTPAdapter):
        def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
            super().init_poolmanager(connections, maxsize, block=block,
                                     server_hostname=hostname, assert_hostname=hostname,
                                     **pool_kwargs)

        def proxy_manager_for(self, proxy, **proxy_kwargs):
            return super().proxy_manager_for(proxy, server_hostname=hostname,
                                             assert_hostname=hostname, **proxy_kwargs)

    session = requests.Session()
    # 不隐式使用环境/netrc；仅通过管理员配置的代理连接已经固定的 IP。
    session.trust_env = False
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    session.mount("https://", HostnameAdapter())
    kwargs.pop("proxies", None)
    try:
        response = session.request(method, url, allow_redirects=False, **kwargs)
    except BaseException:
        session.close()
        raise
    close = response.close
    def close_response():
        try:
            close()
        finally:
            session.close()
    response.close = close_response
    return response


def safe_get(url: str, *, suffixes=None, max_redirects: int = DEFAULT_MAX_REDIRECTS,
             method: str = "GET", **kwargs):
    """每一跳只连接经过校验的 IP；跨来源跳转不携带认证头或 Cookie。"""
    from urllib.parse import urljoin
    from app.services.proxy_config_manager import ProxyConfigManager
    proxy_url = ProxyConfigManager().get_proxy_url()
    current = url
    kwargs = dict(kwargs)
    kwargs.pop("allow_redirects", None)
    headers = dict(kwargs.pop("headers", {}) or {})
    for _ in range(max_redirects + 1):
        pinned, host_header = pin_url_to_ip(current, suffixes=suffixes)
        request_headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        request_headers["Host"] = host_header
        response = _request_pinned(method.upper(), pinned, hostname=urlparse(current).hostname,
                                   proxy_url=proxy_url, headers=request_headers, **kwargs)
        # 业务层仍看到原始 URL，不能把固定的 IP 当作重定向或视频来源。
        response.url = current
        if not 300 <= response.status_code < 400:
            return response
        location = response.headers.get("Location")
        response.close()
        if not location:
            raise UnsafeUrlError("重定向缺少 Location 头")
        target = urljoin(current, location)
        def origin(value):
            p = urlparse(value)
            return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)
        if origin(current) != origin(target):
            headers = {k: v for k, v in headers.items()
                       if k.lower() not in {"authorization", "cookie", "proxy-authorization", "x-api-key"}}
            kwargs.pop("auth", None)
            kwargs.pop("cookies", None)
        current = target
    raise TooManyRedirects(f"跳转次数超过上限（{max_redirects}）")
