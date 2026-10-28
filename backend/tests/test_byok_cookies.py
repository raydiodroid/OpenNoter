"""BYOK Cookie 部分的测试：解析优先级、加密绑定、接口与降级行为。

重点覆盖三件最容易出错的事：

1. **加密绑定到 (user_id, platform)** —— A 的 B 站 Cookie 密文不能解在别人的行上，
   也不能解在自己的抖音行上（Cookie 是可冒用的身份，这比 API Key 更要紧）；
2. **「配了但坏了」不回退站点 Cookie** —— 否则等于用管理员身份下载用户要的视频；
3. **shared 模式与改造前等价** —— 直接读站点 Cookie。
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cookie.db'}")
    monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "unit-test-cookie-secret-0001")
    monkeypatch.setenv("OPENNOTER_JWT_SECRET", "unit-test-cookie-jwt-0001")
    monkeypatch.chdir(tmp_path)

    from sqlalchemy import text

    from app.db.engine import get_engine
    from app.db.init_db import init_db

    init_db()
    # users 配额列由 migrate_task_status.py 补，create_all 不管（既有约定）
    with get_engine().begin() as conn:
        existing = {r[1] for r in conn.execute(text("PRAGMA table_info(users)"))}
        for col in ("max_concurrent_tasks", "daily_task_limit", "storage_quota_mb"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} INTEGER"))

    from app.db.engine import SessionLocal
    from app.db.models.users import User
    from app.db.models.user_cookies import UserCookie

    db = SessionLocal()
    try:
        for uid, email in ((1, "alice@example.com"), (2, "bob@example.com")):
            if db.query(User).filter(User.id == uid).first() is None:
                db.add(User(id=uid, email=email, is_superuser=False, is_active=True))
        # 隔离：每次 fixture 清空 Cookie 与站点配置，避免用例互相污染
        db.query(UserCookie).delete()
        db.commit()
    finally:
        db.close()

    # 清站点 Cookie 配置
    from app.services.cookie_manager import CookieConfigManager

    mgr = CookieConfigManager()
    for p in ("bilibili", "douyin", "kuaishou", "youtube"):
        try:
            mgr.set(p, "")
        except Exception:  # noqa: BLE001
            pass

    from app.services.byok_config import byok_config

    byok_config.update(mode="optional")
    yield tmp_path


BILI_COOKIE = "SESSDATA=abc123; bili_jct=xyz; DedeUserID=42"


def _save_user_cookie(user_id: int, platform: str, value: str = BILI_COOKIE):
    from app.db.engine import SessionLocal
    from app.db.models.user_cookies import UserCookie, cookie_capability
    from app.utils import credential_crypto as cc

    db = SessionLocal()
    try:
        row = (
            db.query(UserCookie)
            .filter(UserCookie.user_id == user_id, UserCookie.platform == platform)
            .first()
        )
        sealed = cc.seal(value, user_id=user_id, capability=cookie_capability(platform))
        if row is None:
            db.add(
                UserCookie(id=uuid.uuid4().hex, user_id=user_id, platform=platform, cookie=sealed)
            )
        else:
            row.cookie = sealed
        db.commit()
    finally:
        db.close()


class TestCookieCryptoBinding:
    def test_roundtrip(self, env):
        from app.db.models.user_cookies import cookie_capability
        from app.utils import credential_crypto as cc

        cap = cookie_capability("bilibili")
        ct = cc.seal("SESSDATA=x", user_id=1, capability=cap)
        assert cc.reveal(ct, user_id=1, capability=cap) == "SESSDATA=x"

    def test_bound_to_user(self, env):
        """A 的 Cookie 密文搬到 B 的行 → 解不开（防冒用账号）。"""
        from app.db.models.user_cookies import cookie_capability
        from app.utils import credential_crypto as cc

        cap = cookie_capability("bilibili")
        ct = cc.seal("SESSDATA=x", user_id=1, capability=cap)
        assert cc.reveal(ct, user_id=2, capability=cap) is None

    def test_bound_to_platform(self, env):
        """同一用户的 B 站 Cookie 密文搬到抖音行 → 解不开。"""
        from app.db.models.user_cookies import cookie_capability
        from app.utils import credential_crypto as cc

        ct = cc.seal("SESSDATA=x", user_id=1, capability=cookie_capability("bilibili"))
        assert cc.reveal(ct, user_id=1, capability=cookie_capability("douyin")) is None


class TestCookieResolve:
    def test_shared_mode_uses_site_cookie(self, env):
        """shared 模式下即便用户配了 Cookie 也读站点配置——与改造前等价。"""
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve_cookie
        from app.services.cookie_manager import CookieConfigManager

        _save_user_cookie(1, "bilibili", "SESSDATA=USER")
        CookieConfigManager().set("bilibili", "SESSDATA=SITE")
        byok_config.update(mode="shared")

        got = resolve_cookie(1, "bilibili")
        assert got and "SITE" in got

    def test_optional_prefers_user_cookie(self, env):
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve_cookie
        from app.services.cookie_manager import CookieConfigManager

        _save_user_cookie(1, "bilibili", "SESSDATA=USER")
        CookieConfigManager().set("bilibili", "SESSDATA=SITE")
        byok_config.update(mode="optional")

        got = resolve_cookie(1, "bilibili")
        assert got and "USER" in got, f"应优先用用户 Cookie，实际: {got}"

    def test_falls_back_to_site_when_user_has_none(self, env):
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve_cookie
        from app.services.cookie_manager import CookieConfigManager

        CookieConfigManager().set("bilibili", "SESSDATA=SITE")
        byok_config.update(mode="optional")

        got = resolve_cookie(2, "bilibili")
        assert got and "SITE" in got

    def test_no_cookie_returns_none(self, env):
        """完全没有 Cookie → None（调用方降级到无 Cookie 下载，不报错）。"""
        from app.services.credential_resolver import resolve_cookie

        assert resolve_cookie(2, "bilibili") is None

    def test_broken_user_cookie_does_not_fall_back_to_site(self, env, monkeypatch):
        """最要紧的一条：配了但解不开 → 降级到「无 Cookie」，绝不冒用站点身份。

        若这里回退到站点 Cookie，等于用管理员的账号去下载用户要的视频，
        正是 BYOK 要消除的串号问题。
        """
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve_cookie
        from app.services.cookie_manager import CookieConfigManager

        _save_user_cookie(1, "bilibili", "SESSDATA=USER")
        CookieConfigManager().set("bilibili", "SESSDATA=SITE")
        byok_config.update(mode="optional")

        # 换掉加密密钥 → 既有密文解不开
        monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "totally-new-secret-zzzzz")

        got = resolve_cookie(1, "bilibili")
        assert got is None, f"不应回退站点 Cookie，实际拿到: {got}"

    def test_anonymous_uses_site(self, env):
        from app.services.credential_resolver import resolve_cookie
        from app.services.cookie_manager import CookieConfigManager

        CookieConfigManager().set("bilibili", "SESSDATA=SITE")
        got = resolve_cookie(None, "bilibili")
        assert got and "SITE" in got

    def test_required_mode_does_not_force_cookie(self, env):
        """required 模式不该因为「没配 Cookie」而报错——Cookie 是可选增强。"""
        from app.services.byok_config import byok_config
        from app.services.credential_resolver import resolve_cookie

        byok_config.update(mode="required")
        # 不抛异常，返回 None
        assert resolve_cookie(1, "bilibili") is None


class TestCookieStatusApi:
    def test_status_lists_all_platforms(self, env):
        from app.services.credential_resolver import build_cookie_status

        status = build_cookie_status(1)
        assert status["mode"] == "optional"
        assert status["byok_enabled"] is True
        platforms = {p["platform"] for p in status["platforms"]}
        assert platforms == {"bilibili", "douyin", "kuaishou", "youtube"}

    def test_status_never_exposes_cookie_value(self, env):
        """状态接口只给条目数/布尔，绝不能回传 Cookie 内容。"""
        import json

        from app.services.credential_resolver import build_cookie_status

        _save_user_cookie(1, "bilibili")
        status = build_cookie_status(1)
        raw = json.dumps(status, ensure_ascii=False)
        assert "abc123" not in raw
        assert "SESSDATA" not in raw

        bili = next(p for p in status["platforms"] if p["platform"] == "bilibili")
        assert bili["source"] == "own"
        assert bili["configured"] is True
        assert bili["entry_count"] > 0

    def test_status_marks_broken(self, env, monkeypatch):
        from app.services.credential_resolver import build_cookie_status

        _save_user_cookie(1, "bilibili")
        monkeypatch.setenv("OPENNOTER_CREDENTIAL_SECRET", "another-new-secret-qqqq")

        status = build_cookie_status(1)
        bili = next(p for p in status["platforms"] if p["platform"] == "bilibili")
        assert bili["broken"] is True
        assert bili["configured"] is False


class TestCookieValidation:
    def test_platform_allowlist(self):
        from app.db.models.user_cookies import SUPPORTED_COOKIE_PLATFORMS
        from app.services.cookie_policy import COOKIE_PLATFORMS

        # 两个白名单必须一致，否则会出现「接口允许保存但下载器读不到」
        assert set(SUPPORTED_COOKIE_PLATFORMS) == set(COOKIE_PLATFORMS)

    def test_header_cookie_filters_unknown_fields(self):
        """Cookie 会被拼进请求头，必须在保存前按 allowlist 过滤。"""
        from app.services.cookie_policy import filter_downloader_cookies

        got = filter_downloader_cookies(
            "bilibili", "SESSDATA=keep; EVIL_TRACKING=drop; bili_jct=keep2"
        )
        assert "SESSDATA=keep" in got
        assert "bili_jct=keep2" in got
        assert "EVIL_TRACKING" not in got

    def test_invalid_format_rejected(self):
        """YouTube 要求 netscape cookies.txt；粘贴 header 格式应被**拒绝**（抛错）。

        `filter_downloader_cookies` 对「格式完全不符」是抛 ValueError，
        而不是静默返回空——接口层据此给用户明确的格式提示。
        """
        from app.services.cookie_policy import filter_downloader_cookies

        with pytest.raises(ValueError):
            filter_downloader_cookies("youtube", "SESSDATA=x")


class TestCookieStatusExpiry:
    """状态接口要如实透出过期信息，且区分「已过期」与「可能失效」。"""

    def test_status_includes_expiry_fields(self, env):
        from app.services.credential_resolver import build_cookie_status

        _save_user_cookie(1, "bilibili")
        status = build_cookie_status(1)
        bili = next(p for p in status["platforms"] if p["platform"] == "bilibili")

        # 字段必须存在（前端类型依赖它们）
        for key in ("expires_at", "expired", "age_days", "stale", "expiry_kind"):
            assert key in bili, f"缺少字段 {key}"

        # B站是 header 格式：无法得知过期时间，不能断言已过期
        assert bili["expiry_kind"] == "unknown"
        assert bili["expired"] is False

    def test_youtube_netscape_reports_known_expiry(self, env):
        """YouTube 是 netscape 格式，能算出真实过期时间。"""
        from datetime import datetime, timedelta, timezone

        from app.services.credential_resolver import build_cookie_status

        past = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
        nc = f".youtube.com\tTRUE\t/\tTRUE\t{past}\tSID\told"
        _save_user_cookie(1, "youtube", nc)

        status = build_cookie_status(1)
        yt = next(p for p in status["platforms"] if p["platform"] == "youtube")
        assert yt["expiry_kind"] == "known"
        assert yt["expired"] is True
        assert yt["expires_at"] is not None

    def test_unconfigured_platform_has_neutral_expiry(self, env):
        """未配置的平台不该报告「已过期」或「stale」。"""
        from app.services.credential_resolver import build_cookie_status

        status = build_cookie_status(1)
        for p in status["platforms"]:
            assert p["expired"] is False
            assert p["stale"] is False
