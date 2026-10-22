"""IP 白名单：来源 IP 判断与 CIDR 匹配。

信任模型（与 nginx/*.conf 对齐）：
  本仓库自带 nginx 用 ``proxy_set_header X-Forwarded-For $remote_addr``
  **覆盖**（不是追加）客户端头，并同时设置 ``X-Real-IP $remote_addr``。
  因此经本 nginx 到达 backend 时，XFF 最左一项与 X-Real-IP 都是真实对端，
  客户端无法再伪造 XFF 绕过 API Key IP 白名单。

  若前面还有 SafeLine / NPMplus 等反代，请让它们直连 backend，或自行保证
  「写入 XFF/X-Real-IP 的那一跳」是唯一受信跳；切勿改回
  ``$proxy_add_x_forwarded_for``（会恢复伪造面）。
"""

import ipaddress
from typing import Iterable, Optional


def client_ip(request) -> str:
    """取真实来源 IP。取不到就返回空串。

    优先 X-Forwarded-For 最左一项（本仓库 nginx 已覆盖为 $remote_addr），
    其次 X-Real-IP，最后 TCP 对端。
    """
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
