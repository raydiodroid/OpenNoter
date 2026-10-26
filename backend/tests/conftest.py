"""pytest 全局配置。

## 为什么需要「确定化 DNS」

有几个测试会走完整的 URL 校验链路：

    is_supported_video_url(url)
      → detect_platform / PATH_PATTERNS        （纯字符串判断，稳定）
      → assert_safe_url(url, suffixes=())
          → resolve_host(host)                 ← 真实 DNS！

最后一步是**真实的 socket.getaddrinfo**。这带来两个问题：

1. **测试结果依赖运行环境的 DNS**。CI 容器、公司内网、家用宽带的解析结果
   可能不同；如果某个域名解析到 IPv6 私有段（`fc00::/7`）或保留段，
   校验会**误判为不安全**，测试随机变红。
   本机就踩到过：`www.youtube.com` 被解析出 `2001::1`（保留段），
   导致 3 条 YouTube 相关测试恒红 —— 而代码本身完全正常。

2. **离线环境跑不了测试**。DNS 不通时 `resolve_host` 抛
   `UnsafeUrlError("域名解析失败")`，同样变红。

## 做法

提供一个 `stable_dns` fixture（autouse），把 `url_safety.resolve_host`
替换成「对已知平台域名返回公网 IP，其余回落到真实解析」。

- 已知平台域名 → 固定返回公网 IP，行为可预测；
- 其它域名 → 仍走真实解析（保留「内网地址拦截」这类测试的真实性）。

需要验证真实 DNS 行为的测试，可以显式覆盖这个 fixture（或临时 `monkeypatch`
回原函数）。
"""

from __future__ import annotations

import pytest

# 已知平台域名 → 一个「确定属于公网、且不会被内网黑名单命中」的 IP。
#
# 用 TEST-NET-3 文档段 (203.0.113.0/24) **不行** —— 它本身是保留段。
# 这里选一个普通公网地址：93.184.216.34 (example.com 的历史 A 记录)。
# 关键是它不落在 url_safety 的黑名单里（私网/回环/链路本地/CGNAT 等）。
_PUBLIC_IP = "93.184.216.34"

_KNOWN_PLATFORM_HOSTS = {
    "www.bilibili.com",
    "bilibili.com",
    "m.bilibili.com",
    "b23.tv",
    "bili2233.cn",
    "www.youtube.com",
    "youtube.com",
    "youtu.be",
    "www.youtube-nocookie.com",
    "www.douyin.com",
    "douyin.com",
    "v.douyin.com",
    "www.kuaishou.com",
    "kuaishou.com",
}


@pytest.fixture(autouse=True)
def stable_dns(monkeypatch):
    """让平台域名的 DNS 解析结果确定化（见模块 docstring）。

    autouse：所有测试默认启用，避免"某些文件记得加、某些忘了"导致
    flaky。需要真实解析的测试自行覆盖。

    ## 为什么打两层桩

    本仓库有一部分测试（`test_url_safety.py`、`test_video_url_support.py`
    等）用 `importlib.util.spec_from_file_location` **独立加载**被测模块，
    目的是隔离 `app` 包的 import 副作用。

    独立加载意味着：它们持有自己的 `url_safety` 模块对象，
    对「正常 import 进来的 `app.utils.url_safety`」做 monkeypatch **打不到**。

    所以这里同时打两层：

      1. `socket.getaddrinfo`（最底层）—— 对**任何**加载方式的模块都生效；
      2. `app.utils.url_safety.resolve_host`（仅供正常 import 的调用方）。

    优先用第 1 层，因为它是唯一能覆盖 importlib 隔离加载的方式。
    """
    import socket

    real_getaddrinfo = socket.getaddrinfo

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        h = (host or "").strip().lower().rstrip(".")
        if h in _KNOWN_PLATFORM_HOSTS:
            # 返回一个最小的、形状合法的 getaddrinfo 结果
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (_PUBLIC_IP, port or 0),
                ),
            ]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    # 第二层：正常 import 路径（有些代码直接引用 resolve_host）
    try:
        from app.utils import url_safety

        real_resolve = url_safety.resolve_host

        def _fake_resolve(host: str) -> list[str]:
            h = (host or "").strip().lower().rstrip(".")
            if h in _KNOWN_PLATFORM_HOSTS:
                return [_PUBLIC_IP]
            return real_resolve(host)

        monkeypatch.setattr(url_safety, "resolve_host", _fake_resolve)
    except Exception:  # noqa: BLE001 - 隔离加载场景下 app 包可能还没准备好
        pass

    yield


@pytest.fixture
def real_dns(monkeypatch):
    """显式要求「真实 DNS」的测试用它覆盖 autouse 的 stable_dns。

    用法：在测试签名里加 `real_dns` 参数（不 autouse 的 fixture 会覆盖同名
    autouse 行为 —— 这里通过重新 setattr 回真实实现达成）。
    """
    import socket

    from app.utils import url_safety

    def _real(host: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:  # 与生产实现保持一致
            raise url_safety.UnsafeUrlError(f"域名解析失败: {host}") from exc
        return [info[4][0] for info in infos]

    monkeypatch.setattr(url_safety, "resolve_host", _real)
    yield
