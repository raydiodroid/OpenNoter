import importlib.util
import pathlib
import sys
import time
import unittest
import types
import tempfile

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]

if "app" not in sys.modules:
    app_package = types.ModuleType("app")
    app_package.__path__ = [str(ROOT / "app")]
    sys.modules["app"] = app_package
if "app.services" not in sys.modules:
    services_package = types.ModuleType("app.services")
    services_package.__path__ = [str(ROOT / "app" / "services")]
    sys.modules["app.services"] = services_package

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cookie_policy = _load_module(
    "app.services.cookie_policy",
    ROOT / "app" / "services" / "cookie_policy.py",
)
cookie_manager = _load_module(
    "app.services.cookie_manager",
    ROOT / "app" / "services" / "cookie_manager.py",
)
cookie_transfer = _load_module(
    "app.services.cookie_transfer",
    ROOT / "app" / "services" / "cookie_transfer.py",
)
qr_login = _load_module(
    "bilibili_qr_login_under_test",
    ROOT / "app" / "services" / "bilibili_qr_login.py",
)


class TestDownloaderCookieAllowlist(unittest.TestCase):
    def test_filters_platform_cookies_and_deduplicates_names(self):
        self.assertEqual(
            cookie_policy.filter_downloader_cookies(
                "bilibili",
                "SESSDATA=secret; tracking=remove; bili_jct=csrf; SESSDATA=new",
            ),
            "SESSDATA=new; bili_jct=csrf",
        )
        self.assertEqual(
            cookie_policy.filter_downloader_cookies(
                "douyin",
                "sessionid=auth; msToken=dynamic; analytics=remove; ttwid=device",
            ),
            "sessionid=auth; ttwid=device",
        )
        self.assertEqual(
            cookie_policy.filter_downloader_cookies(
                "kuaishou",
                "did=web_id; kuaishou.server.web_st=auth; ad_id=remove",
            ),
            "did=web_id; kuaishou.server.web_st=auth",
        )
        # YouTube 走 Netscape cookies.txt，不认 header 形式——贴错格式要明确报错，
        # 而不是静默存成空串（以前白名单是空元组，等于永久禁用 Cookie）。
        with self.assertRaises(ValueError):
            cookie_policy.filter_downloader_cookies("youtube", "PREF=xyz")

    def test_youtube_accepts_netscape_cookies(self):
        netscape = (
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t1799999999\tSID\tsid-value\n"
            ".youtube.com\tTRUE\t/\tTRUE\t1799999999\tHSID\thsid-value\n"
        )
        filtered = cookie_policy.filter_downloader_cookies("youtube", netscape)
        self.assertIn("SID\tsid-value", filtered)
        self.assertIn("HSID\thsid-value", filtered)
        self.assertEqual(cookie_policy.cookie_entry_count("youtube", filtered), 2)

    def test_youtube_rejects_malformed_netscape(self):
        # tab 数量不对（常见于用户把浏览器扩展导出的 JSON 直接贴进来）
        with self.assertRaises(ValueError):
            cookie_policy.filter_downloader_cookies(
                "youtube", ".youtube.com\tTRUE\t/\tSID\tvalue\n"
            )

    def test_drops_cookie_when_no_names_are_on_allowlist(self):
        self.assertEqual(cookie_policy.filter_downloader_cookies("bilibili", "tracking=remove"), "")

    def test_legacy_cookie_config_is_filtered_on_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "downloader.json"
            path.write_text(
                '{"bilibili":{"cookie":"SESSDATA=auth; tracking=remove"}}',
                encoding="utf-8",
            )
            manager = cookie_manager.CookieConfigManager(str(path))
            self.assertEqual(manager.get("bilibili"), "SESSDATA=auth")
            self.assertNotIn("tracking", path.read_text(encoding="utf-8"))


class TestCookieTransfer(unittest.TestCase):
    def test_transfer_is_origin_bound_and_single_use(self):
        manager = cookie_transfer.CookieTransferManager()
        ticket = manager.issue("bilibili", 7, "https://opennoter.example")

        self.assertIsNone(manager.consume(ticket, "https://attacker.example", "SESSDATA=secret"))
        transfer = manager.consume(ticket, "https://opennoter.example", "SESSDATA=secret; bili_jct=csrf")
        self.assertEqual(transfer.platform, "bilibili")
        self.assertEqual(transfer.user_id, 7)
        self.assertIsNone(manager.consume(ticket, "https://opennoter.example", "SESSDATA=secret"))

    def test_rejects_malformed_cookie_and_unsupported_platform(self):
        manager = cookie_transfer.CookieTransferManager()
        with self.assertRaises(ValueError):
            manager.issue("local", 7, "https://opennoter.example")
        with self.assertRaises(ValueError):
            cookie_transfer.validate_cookie_string("SESSDATA=secret\r\nX-Injected: yes")

    def test_origin_normalization_rejects_non_web_origins(self):
        self.assertEqual(cookie_transfer.normalize_origin("HTTPS://OPENNOTER.EXAMPLE:443/"), "https://opennoter.example")
        self.assertIsNone(cookie_transfer.normalize_origin("javascript:alert(1)"))
        self.assertIsNone(cookie_transfer.normalize_origin("https://user@opennoter.example"))


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body

    def close(self):
        return None


class FakeSession:
    def __init__(self, poll_code=86101, include_cookie=True):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.poll_code = poll_code
        self.include_cookie = include_cookie
        self.closed = False

    def get(self, url, **kwargs):
        if url == qr_login.QR_GENERATE_URL:
            return FakeResponse({
                "code": 0,
                "data": {
                    "url": "https://account.bilibili.com/h5/account-h5/auth/scan?qrcode_key=1234567890abcdef",
                    "qrcode_key": "1234567890abcdef",
                },
            })
        if url == qr_login.QR_POLL_URL:
            if self.poll_code == 0 and self.include_cookie:
                self.cookies.set("SESSDATA", "session-value", domain=".bilibili.com", path="/")
                self.cookies.set("bili_jct", "csrf-value", domain=".bilibili.com", path="/")
                self.cookies.set("tracking", "not-forwarded", domain=".bilibili.com", path="/")
            return FakeResponse({"code": 0, "data": {"code": self.poll_code, "url": ""}})
        raise AssertionError(f"Unexpected URL: {url}")

    def close(self):
        self.closed = True


class TestBilibiliQrLogin(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.http = FakeSession()
        self.manager = qr_login.BilibiliQrLoginManager(
            lambda platform, cookie: self.saved.append((platform, cookie)),
            session_factory=lambda: self.http,
        )
        self.session_id = self.manager.start(11)["session_id"]

    def _allow_next_poll(self):
        self.manager._sessions[self.session_id].last_poll_at = time.monotonic() - 2

    def test_status_progression_and_cookie_save(self):
        self.assertEqual(self.manager.poll(self.session_id, 11)["status"], "waiting")
        self._allow_next_poll()
        self.http.poll_code = 86090
        self.assertEqual(self.manager.poll(self.session_id, 11)["status"], "scanned")
        self._allow_next_poll()
        self.http.poll_code = 0
        self.assertEqual(self.manager.poll(self.session_id, 11)["status"], "success")
        self.assertEqual(self.saved, [("bilibili", "SESSDATA=session-value; bili_jct=csrf-value")])

    def test_session_is_bound_to_creator(self):
        with self.assertRaises(qr_login.BilibiliQrLoginError):
            self.manager.poll(self.session_id, 12)

    def test_login_success_without_sessdata_does_not_save(self):
        self.http.poll_code = 0
        self.http.include_cookie = False
        self.assertEqual(self.manager.poll(self.session_id, 11)["status"], "failed")
        self.assertEqual(self.saved, [])

    def test_cross_domain_fallback_rejects_untrusted_hosts(self):
        self.assertTrue(qr_login.BilibiliQrLoginManager._is_cross_domain_url(
            "https://passport.biligame.com/x/passport-login/web/crossDomain?ticket=abc",
        ))
        self.assertFalse(qr_login.BilibiliQrLoginManager._is_cross_domain_url(
            "https://passport.biligame.com.attacker.example/x/passport-login/web/crossDomain?ticket=abc",
        ))


if __name__ == "__main__":
    unittest.main()
