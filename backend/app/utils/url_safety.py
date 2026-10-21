"""出站 URL 安全校验：防 SSRF。

image_proxy 这类「拿用户给的 URL 去服务端抓资源」的接口，如果不校验目标地址，
持 API Key 的人就能让服务器去访问内网：127.0.0.1 上的后端、10.0.x / 192.168.x
网段、100.64.x（Tailscale）、以及 169.254.169.254 云元数据端点。

这里做三层防护：
  1. scheme 只允许 http / https（挡 file://、gopher:// 等）
  2. 主机解析出的每个 IP 都要过一遍「私网/保留地址」黑名单（挡 DNS 指向内网）
  3. 域名白名单（挡任意外部站点被当作图床代理）
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

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


def assert_safe_url(url: str, suffixes=None) -> str:
    """校验 URL 可以安全地由服务端发起请求，通过则原样返回。"""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"只允许 http/https 协议，当前为 {scheme or '空'}")

    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL 缺少主机名")

    literal = _parse_ip(host)
    if literal is not None:
        if _is_blocked_ip(literal):
            raise UnsafeUrlError(f"不允许访问内网地址: {host}")
    else:
        for ip_str in resolve_host(host):
            ip = _parse_ip(ip_str)
            if ip is not None and _is_blocked_ip(ip):
                raise UnsafeUrlError(f"域名 {host} 解析到内网地址 {ip_str}")

    if suffixes is None:
        suffixes = allowed_host_suffixes()
    if not _host_allowed(host, suffixes):
        raise UnsafeUrlError(f"来源域名不在白名单内: {host}")

    return url
