"""Cookie 过期判定的测试。

核心事实：**只有 netscape 格式带过期时间**。header 格式（B站/抖音/快手）
从内容上无从得知何时过期——所以这个模块的设计重点不是「算得准」，
而是「不给出虚假的确定感」：

- netscape → 算真实过期时间，能明确说「已过期」
- header   → 只能说「保存了多少天」，超过阈值时提示「可能失效」，
             **不能**说「已过期」（Cookie 也可能因为改密码/风控提前失效）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.cookie_expiry import (
    HEADER_COOKIE_STALE_DAYS,
    describe_expiry,
    extract_netscape_expiry,
)


def _netscape(expiry_ts: int, name: str = "SID") -> str:
    return f".youtube.com\tTRUE\t/\tTRUE\t{expiry_ts}\t{name}\tvalue"


class TestNetscapeExpiry:
    def test_picks_earliest_non_session(self):
        now = datetime.now(timezone.utc)
        soon = int((now + timedelta(days=5)).timestamp())
        later = int((now + timedelta(days=500)).timestamp())
        text = "\n".join([_netscape(later, "A"), _netscape(soon, "B")])

        got = extract_netscape_expiry(text)
        assert got is not None
        # 允许 2 秒误差（int 截断）
        assert abs((got - (now + timedelta(days=5))).total_seconds()) < 3

    def test_ignores_session_cookies(self):
        """expiry=0 是会话 Cookie，不参与「最早过期」比较。"""
        future = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        text = "\n".join([_netscape(0, "SESSION"), _netscape(future, "SID")])

        got = extract_netscape_expiry(text)
        assert got is not None
        assert got > datetime.now(timezone.utc)

    def test_all_session_returns_none(self):
        assert extract_netscape_expiry(_netscape(0)) is None

    def test_no_valid_lines_returns_none(self):
        assert extract_netscape_expiry("garbage") is None
        assert extract_netscape_expiry("") is None

    def test_ignores_comments(self):
        future = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
        text = "# Netscape HTTP Cookie File\n# comment line\n" + _netscape(future)
        got = extract_netscape_expiry(text)
        assert got is not None

    def test_httponly_prefix_is_data_not_comment(self):
        """``#HttpOnly_`` 开头的是数据行，不能被当成注释跳过。"""
        future = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())
        line = "#HttpOnly_" + _netscape(future).lstrip()
        got = extract_netscape_expiry(line)
        assert got is not None


class TestDescribeExpiry:
    def test_netscape_expired(self):
        past = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
        d = describe_expiry(
            platform="youtube",
            cookie_text=_netscape(past),
            saved_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        assert d["expired"] is True
        assert d["expiry_kind"] == "known"
        assert d["expires_at"] is not None

    def test_netscape_not_expired(self):
        future = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        d = describe_expiry(
            platform="youtube",
            cookie_text=_netscape(future),
            saved_at=datetime.now(timezone.utc),
        )
        assert d["expired"] is False
        assert d["expiry_kind"] == "known"

    def test_header_format_reports_unknown(self):
        """header 格式（B站）没有过期时间 → 必须如实报 unknown。"""
        d = describe_expiry(
            platform="bilibili",
            cookie_text="SESSDATA=abc; bili_jct=def",
            saved_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        assert d["expiry_kind"] == "unknown"
        assert d["expires_at"] is None
        # 关键：不知道过期时间时，**不能**断言已过期
        assert d["expired"] is False
        assert d["age_days"] == pytest.approx(3.0, abs=0.1)

    def test_header_goes_stale_after_threshold(self):
        d = describe_expiry(
            platform="douyin",
            cookie_text="sessionid=x",
            saved_at=datetime.now(timezone.utc)
            - timedelta(days=HEADER_COOKIE_STALE_DAYS + 1),
        )
        assert d["stale"] is True
        # stale ≠ expired：措辞上必须区分
        assert d["expired"] is False

    def test_header_fresh_not_stale(self):
        d = describe_expiry(
            platform="kuaishou",
            cookie_text="did=x",
            saved_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        assert d["stale"] is False

    def test_no_saved_at_still_safe(self):
        """没有保存时间也不能崩（历史数据可能缺这一列）。"""
        d = describe_expiry(platform="bilibili", cookie_text="SESSDATA=x", saved_at=None)
        assert d["age_days"] is None
        assert d["stale"] is False
        assert d["expired"] is False

    def test_naive_datetime_treated_as_utc(self):
        """SQLite 取出来的 datetime 是 naive 的，必须当 UTC 处理。"""
        naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
        d = describe_expiry(
            platform="bilibili", cookie_text="SESSDATA=x", saved_at=naive
        )
        assert d["age_days"] == pytest.approx(2.0, abs=0.1)
