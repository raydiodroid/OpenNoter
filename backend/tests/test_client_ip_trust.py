"""nginx XFF 信任模型与 client_ip() 对齐的回归。

本仓库 nginx 用 $remote_addr 覆盖 X-Forwarded-For；经反代到达 backend 时
XFF 最左 = 真实对端，客户端无法伪造。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils.ip_allow import client_ip


class _Headers(dict):
    def get(self, key, default=""):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def _req(*, headers=None, peer="10.0.0.9"):
    return SimpleNamespace(
        headers=_Headers(headers or {}),
        client=SimpleNamespace(host=peer) if peer is not None else None,
    )


def test_prefers_xff_leftmost_when_present():
    """nginx 覆盖后的 XFF 最左项即真实对端。"""
    ip = client_ip(
        _req(headers={"X-Forwarded-For": "203.0.113.10", "X-Real-IP": "10.0.0.1"})
    )
    assert ip == "203.0.113.10"


def test_xff_multi_takes_leftmost_only():
    """若误配置为追加链，仍取最左；正确部署应只有一项。"""
    ip = client_ip(_req(headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1"}))
    assert ip == "198.51.100.7"


def test_falls_back_to_x_real_ip():
    ip = client_ip(_req(headers={"X-Real-IP": "192.0.2.55"}))
    assert ip == "192.0.2.55"


def test_falls_back_to_tcp_peer():
    ip = client_ip(_req(peer="172.16.1.2"))
    assert ip == "172.16.1.2"


def test_invalid_xff_falls_through():
    ip = client_ip(
        _req(headers={"X-Forwarded-For": "not-an-ip", "X-Real-IP": "192.0.2.9"})
    )
    assert ip == "192.0.2.9"


def test_empty_when_no_source():
    ip = client_ip(_req(peer=None))
    assert ip == ""
