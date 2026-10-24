"""API Key 管理改造的单元测试。

覆盖纯逻辑部分（不碰数据库）：哈希算法、状态机判定、UTC 归一化、
IP 白名单匹配、限流计数。带数据库的端到端行为靠线上验证。
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.services.api_key_runtime import FixedWindowRateLimiter
from app.services.auth import as_utc, effective_key_status, generate_api_key, hash_api_key, verify_api_key
from app.utils.ip_allow import ip_allowed
from app.db.models.api_keys import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_EXPIRED,
    STATUS_REVOKED,
)


# ---------- 哈希 ----------

def test_hash_api_key_is_deterministic_and_hex():
    """SHA-256 必须确定（才能按哈希直接索引查找），且是 64 位十六进制。"""
    h1 = hash_api_key("sk-bili-abc")
    h2 = hash_api_key("sk-bili-abc")
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)
    assert hash_api_key("sk-bili-abd") != h1


def test_verify_api_key_accepts_sha256():
    h = hash_api_key("sk-bili-abc")
    assert verify_api_key("sk-bili-abc", h, "sha256") is True
    assert verify_api_key("sk-bili-xyz", h, "sha256") is False


def test_verify_api_key_still_supports_legacy_bcrypt():
    """存量密钥是 bcrypt 哈希，迁移期内必须还能验。"""
    import bcrypt

    h = bcrypt.hashpw(b"sk-bili-abc", bcrypt.gensalt(rounds=4)).decode()
    assert verify_api_key("sk-bili-abc", h, "bcrypt") is True
    assert verify_api_key("sk-bili-nope", h, "bcrypt") is False


def test_generate_api_key_shape():
    """生成的密钥要带齐展示与定位所需的非秘密字段。"""
    g = generate_api_key()
    assert g["plain"].startswith("sk-bili-")
    assert len(g["plain"]) > 40
    assert g["key_id"].startswith("bk_")
    assert g["prefix"] == g["plain"][: len("sk-bili-") + 8]
    assert g["last_four"] == g["plain"][-4:]
    assert g["hash_algo"] == "sha256"
    assert g["key_hash"] == hash_api_key(g["plain"])
    # key_id 不能是 secret 的派生值 —— 派生值会随 secret 一起泄露
    assert g["key_id"] not in g["plain"]


def test_generate_api_key_is_unique():
    keys = {generate_api_key()["plain"] for _ in range(50)}
    assert len(keys) == 50


# ---------- 时间归一化 ----------

def test_as_utc_treats_naive_as_utc():
    naive = datetime(2026, 10, 20, 8, 0, 0)
    assert as_utc(naive) == datetime(2026, 10, 20, 8, 0, 0, tzinfo=timezone.utc)


def test_as_utc_passes_through_none():
    assert as_utc(None) is None


# ---------- 状态机 ----------

class _FakeKey:
    """最小化的 ApiKey 替身，只带状态判定需要的字段。"""

    def __init__(self, status=STATUS_ACTIVE, expires_at=None, revoked_reason=None):
        self.status = status
        self.expires_at = expires_at
        self.revoked_reason = revoked_reason
        self.revoked = status == STATUS_REVOKED


def test_status_active_when_not_expired():
    now = datetime.now(timezone.utc)
    assert effective_key_status(_FakeKey(), now) == STATUS_ACTIVE


def test_status_expired_is_computed_live():
    """过期是随时间推移才生效的，不能只信库里的 status。"""
    now = datetime.now(timezone.utc)
    key = _FakeKey(status=STATUS_ACTIVE, expires_at=now - timedelta(minutes=1))
    assert effective_key_status(key, now) == STATUS_EXPIRED


def test_status_not_expired_yet():
    now = datetime.now(timezone.utc)
    key = _FakeKey(status=STATUS_ACTIVE, expires_at=now + timedelta(days=1))
    assert effective_key_status(key, now) == STATUS_ACTIVE


def test_revoked_and_disabled_are_terminal_regardless_of_expiry():
    now = datetime.now(timezone.utc)
    expired = now - timedelta(days=1)
    assert effective_key_status(_FakeKey(STATUS_REVOKED, expired), now) == STATUS_REVOKED
    assert effective_key_status(_FakeKey(STATUS_DISABLED, expired), now) == STATUS_DISABLED


def test_naive_expiry_is_treated_as_utc():
    """SQLite 取出来的是 naive datetime，不能当本地时间。"""
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    key = _FakeKey(status=STATUS_ACTIVE, expires_at=datetime(2026, 10, 20, 13, 0, 0))
    assert effective_key_status(key, now) == STATUS_ACTIVE


# ---------- IP 白名单 ----------

@pytest.mark.parametrize(
    "ip,cidrs,expected",
    [
        ("1.2.3.4", ["1.2.3.4"], True),
        ("1.2.3.4", ["1.2.3.0/24"], True),
        ("1.2.3.4", ["10.0.0.0/8"], False),
        ("1.2.3.4", [], True),          # 空 = 不限制
        ("1.2.3.4", None, True),        # None = 不限制
        ("1.2.3.4", ["1.2.3.5", "1.2.3.4"], True),
        ("1.2.3.4", ["写错了的"], False),   # 写错的条目被跳过，不至于把 key 锁死
        ("1.2.3.4", ["1.2.3.0/24", "10.0.0.0/8"], True),
        ("::ffff:1.2.3.4", ["1.2.3.0/24"], True),  # IPv4-mapped IPv6
    ],
)
def test_ip_allowed(ip, cidrs, expected):
    assert ip_allowed(ip, cidrs) is expected


def test_ip_allowed_empty_ip_with_allowlist():
    assert ip_allowed("", ["1.2.3.4"]) is False


# ---------- 限流 ----------

def test_rate_limiter_blocks_over_limit():
    limiter = FixedWindowRateLimiter(window=60)
    results = [limiter.check(1, 3)[0] for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_rate_limiter_is_per_key():
    limiter = FixedWindowRateLimiter(window=60)
    for _ in range(3):
        limiter.check(1, 3)
    assert limiter.check(1, 3)[0] is False
    assert limiter.check(2, 3)[0] is True


def test_rate_limiter_reset():
    limiter = FixedWindowRateLimiter(window=60)
    for _ in range(3):
        limiter.check(1, 3)
    assert limiter.check(1, 3)[0] is False
    limiter.reset(1)
    assert limiter.check(1, 3)[0] is True


def test_rate_limiter_reports_remaining():
    limiter = FixedWindowRateLimiter(window=60)
    assert limiter.check(1, 10)[1] == 9
    assert limiter.check(1, 10)[1] == 8


def test_rate_limiter_window_rolls_over():
    limiter = FixedWindowRateLimiter(window=1)
    assert limiter.check(1, 1)[0] is True
    assert limiter.check(1, 1)[0] is False
    time.sleep(1.1)
    assert limiter.check(1, 1)[0] is True
