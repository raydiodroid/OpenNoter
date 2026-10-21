"""IP 白名单：来源 IP 判断与 CIDR 匹配。

反代链是 client → SafeLine → NPMplus → nginx → backend，每一跳都会往
X-Forwarded-For 后面追加自己的地址，所以**最左一项**是 SafeLine 记录的真实
客户端 IP（SafeLine 作为前置 WAF 会重写而不是追加这个头）。最左一项之后的
都是我们自己链路上的地址。
"""

import ipaddress
from typing import Iterable, Optional


def client_ip(request) -> str:
    """取真实来源 IP。取不到就返回空串。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            pass

    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        try:
            return str(ipaddress.ip_address(real_ip))
        except ValueError:
            pass

    return request.client.host if request.client else ""


def ip_allowed(ip: str, cidrs: Optional[Iterable[str]]) -> bool:
    """来源 IP 是否在允许列表内。

    cidrs 为空/None 表示不限制。列表里可以写单个 IP，也可以写 CIDR。
    写错的条目会被跳过（不让一条手误把整把 key 锁死）。
    """
    if not cidrs:
        return True
    if not ip:
        return False

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False

    # IPv4-mapped IPv6（::ffff:1.2.3.4）归一成 IPv4 再比
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped

    for entry in cidrs:
        entry = (entry or "").strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False
