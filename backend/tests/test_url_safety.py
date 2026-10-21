"""视频 URL 校验的回归测试。

核心是这条：历史实现用「URL 里包含 douyin / kuaishou 子串」判断平台，
于是任意内网地址只要带上这个 query 就能通过校验，接着下载器会真的去请求它
（SSRF）。这组测试把「必须放行」和「必须拒绝」两侧都钉住。
"""

import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

if "app" not in sys.modules:
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = [str(ROOT / "app")]
    sys.modules["app"] = app_pkg


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise ImportError(f"{relative} spec not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("app.utils.bilibili_ids", "app/utils/bilibili_ids.py")
_load("app.utils.url_safety", "app/utils/url_safety.py")
_load("app.utils.url_parser", "app/utils/url_parser.py")
validator = _load("app.validators.video_url_validator", "app/validators/video_url_validator.py")

ACCEPTED = [
    # B 站
    "https://www.bilibili.com/video/BV17x411w7KC",
    "https://www.bilibili.com/video/av170001",
    "https://www.bilibili.com/video/av170001?p=3",
    "https://www.bilibili.com/list/watchlater/?bvid=BV17x411w7KC",
    "https://b23.tv/abc123",
    # YouTube
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    # 抖音
    "https://www.douyin.com/video/7345492945006595379",
    "https://www.douyin.com/note/7684636895083644273",
    "https://www.douyin.com/discover?modal_id=7345492945006595379",
    "https://v.douyin.com/iRNBho6u/",
    "https://www.iesdouyin.com/share/video/7345492945006595379",
    # 快手
    "https://v.kuaishou.com/2vBqX74",
]

REJECTED = [
    # SSRF 相关：这些都是历史实现会放行的
    "http://169.254.169.254/latest/meta-data/?x=douyin",
    "http://127.0.0.1:8483/api/notes?platform=douyin",
    "http://10.0.0.5/x?y=kuaishou",
    "http://192.168.1.1/admin?x=douyin",
    "http://100.64.0.1/tailscale?x=kuaishou",
    # 任意域名 + 子串
    "https://evil.example.com/payload?x=douyin",
    "https://example.com/download?target=kuaishou_video",
    # 伪装域名
    "https://notbilibili.com.evil.com/video/BV17x411w7KC",
    # 协议
    "file:///etc/passwd?x=douyin",
    "javascript:alert(1)",
    # 平台域名但路径不是视频
    "https://www.bilibili.com/anime/timeline",
    "https://www.youtube.com/feed/subscriptions",
]


class TestAcceptedUrls(unittest.TestCase):
    def test_supported_urls_pass(self):
        for url in ACCEPTED:
            with self.subTest(url=url):
                self.assertTrue(
                    validator.is_supported_video_url(url),
                    f"应当放行：{url}",
                )


class TestRejectedUrls(unittest.TestCase):
    def test_ssrf_and_garbage_rejected(self):
        for url in REJECTED:
            with self.subTest(url=url):
                self.assertFalse(
                    validator.is_supported_video_url(url),
                    f"应当拒绝：{url}",
                )

    def test_empty_input_rejected(self):
        for url in ("", "   ", None):
            with self.subTest(url=url):
                self.assertFalse(validator.is_supported_video_url(url))


class TestVideoRequestModel(unittest.TestCase):
    def test_platform_auto_filled_when_missing(self):
        request = validator.VideoRequest(
            url="https://www.bilibili.com/video/BV17x411w7KC",
            platform="",
        )
        self.assertEqual(request.platform, "bilibili")

    def test_bilibili_url_normalized(self):
        request = validator.VideoRequest(
            url="https://www.bilibili.com/video/av170001?p=3&spm_id_from=333.881",
            platform="bilibili",
        )
        self.assertEqual(str(request.url), "https://www.bilibili.com/video/BV17x411w7KC?p=3")

    def test_rejects_ssrf_url_in_model(self):
        with self.assertRaises(ValueError):
            validator.VideoRequest(
                url="http://169.254.169.254/latest/meta-data/?x=douyin",
                platform="douyin",
            )


if __name__ == "__main__":
    unittest.main()
