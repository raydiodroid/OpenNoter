"""共享下载基础设施的测试：URL 安全、ID 解析、av/BV 互转、限制策略。

覆盖的都是本次修复里「跨平台复用、且曾经出过真实问题」的部分：

1. **URL 校验的 SSRF 漏洞**：历史实现用子串匹配判断抖音/快手，
   `http://169.254.169.254/latest/meta-data/?x=douyin` 能通过校验，
   随后下载器会真的对这个地址发请求。
2. **av 号识别**：历史实现只认 BV，`av170001` 会一路走到 `None.mp4`。
3. **抖音链接形态**：历史实现只认 `/video/`，`note` / `slides` / `modal_id` 全部返回空串。
4. **下载限制**：历史实现完全没有大小/时长上限。
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
    """按文件路径加载模块，避免触发 app/__init__.py 的整包导入。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise ImportError(f"{relative} spec not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bilibili_ids = _load("app.utils.bilibili_ids", "app/utils/bilibili_ids.py")
url_safety = _load("app.utils.url_safety", "app/utils/url_safety.py")
# url_parser 依赖 bilibili_ids / url_safety，上面已经塞进 sys.modules 了
url_parser = _load("app.utils.url_parser", "app/utils/url_parser.py")
limits = _load("app.downloaders.limits", "app/downloaders/limits.py")


class TestAvBvConversion(unittest.TestCase):
    """av/BV 互转：算法常量来自 B 站前端的公开转换表。"""

    def test_known_pairs(self):
        # 这几对是社区文档里的标准样例，用来钉住算法常量没写反
        self.assertEqual(bilibili_ids.av2bv(170001), "BV17x411w7KC")
        self.assertEqual(bilibili_ids.av2bv(2), "BV1xx411c7mD")
        self.assertEqual(bilibili_ids.bv2av("BV17x411w7KC"), 170001)
        self.assertEqual(bilibili_ids.bv2av("BV1xx411c7mD"), 2)

    def test_roundtrip(self):
        for aid in (1, 2, 170001, 80433022, 116294762371214):
            with self.subTest(aid=aid):
                self.assertEqual(bilibili_ids.bv2av(bilibili_ids.av2bv(aid)), aid)

    def test_normalize_accepts_all_input_shapes(self):
        expected = "BV17x411w7KC"
        for raw in ("av170001", "AV170001", "170001", "BV17x411w7KC", "bv17x411w7KC"):
            with self.subTest(raw=raw):
                self.assertEqual(bilibili_ids.normalize_bvid(raw), expected)

    def test_normalize_keeps_bv_body_case(self):
        """BV 号主体大小写敏感（base58 变体），只有前缀 BV 可以大小写不敏感。

        历史实现里曾经 `.lower()` 过整串，会把两个不同的视频 ID 映射成同一个。
        """
        self.assertEqual(bilibili_ids.normalize_bvid("bv17x411w7KC"), "BV17x411w7KC")
        # 主体全小写是另一个（大概率不存在的）ID，不能等同于 BV17x411w7KC
        self.assertNotEqual(bilibili_ids.normalize_bvid("bv17x411w7kc"), "BV17x411w7KC")

    def test_normalize_rejects_garbage(self):
        for raw in ("", None, "not-an-id", "av", "BV123"):
            with self.subTest(raw=raw):
                self.assertIsNone(bilibili_ids.normalize_bvid(raw))


class TestVideoIdExtraction(unittest.TestCase):
    def test_bilibili_bv_and_av(self):
        cases = [
            ("https://www.bilibili.com/video/BV17x411w7KC", "BV17x411w7KC"),
            ("https://www.bilibili.com/video/av170001", "BV17x411w7KC"),
            ("https://www.bilibili.com/video/av170001?p=3", "BV17x411w7KC"),
            ("https://www.bilibili.com/list/watchlater/?bvid=BV17x411w7KC", "BV17x411w7KC"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(url_parser.extract_video_id(url, "bilibili"), expected)

    def test_bilibili_p_number(self):
        self.assertEqual(
            url_parser.extract_bilibili_p_number("https://www.bilibili.com/video/BV1Ranw6DEYW?p=2"),
            2,
        )
        self.assertIsNone(
            url_parser.extract_bilibili_p_number("https://www.bilibili.com/video/BV1Ranw6DEYW")
        )

    def test_douyin_all_link_shapes(self):
        expected = "7345492945006595379"
        cases = [
            f"https://www.douyin.com/video/{expected}",
            f"https://www.douyin.com/discover?modal_id={expected}",
            f"https://www.douyin.com/user/MS4wLjAB?modal_id={expected}",
            f"https://m.douyin.com/share/video/{expected}",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(url_parser.extract_video_id(url, "douyin"), expected)

    def test_douyin_note_and_slides(self):
        note_id = "7684636895083644273"
        self.assertEqual(
            url_parser.extract_video_id(f"https://www.douyin.com/note/{note_id}", "douyin"),
            note_id,
        )
        self.assertEqual(
            url_parser.extract_video_id(
                f"https://m.douyin.com/share/slides/{note_id}", "douyin"
            ),
            note_id,
        )

    def test_youtube_shapes(self):
        vid = "dQw4w9WgXcQ"
        for url in (
            f"https://www.youtube.com/watch?v={vid}",
            f"https://youtu.be/{vid}",
            f"https://www.youtube.com/shorts/{vid}",
            f"https://www.youtube.com/live/{vid}",
        ):
            with self.subTest(url=url):
                self.assertEqual(url_parser.extract_video_id(url, "youtube"), vid)


class TestPlatformDetection(unittest.TestCase):
    def test_hostname_based(self):
        self.assertEqual(url_parser.detect_platform("https://www.bilibili.com/video/BV17x411w7KC"), "bilibili")
        self.assertEqual(url_parser.detect_platform("https://b23.tv/abc"), "bilibili")
        self.assertEqual(url_parser.detect_platform("https://v.douyin.com/abc/"), "douyin")
        self.assertEqual(url_parser.detect_platform("https://youtu.be/dQw4w9WgXcQ"), "youtube")

    def test_substring_does_not_fool_detection(self):
        """这是 SSRF 修复的核心：平台判定只看 hostname。"""
        self.assertIsNone(url_parser.detect_platform("http://169.254.169.254/x?y=douyin"))
        self.assertIsNone(url_parser.detect_platform("https://evil.example.com/?q=kuaishou"))
        self.assertIsNone(url_parser.detect_platform("https://notbilibili.com.evil.com/video/BV1x"))


class TestDownloadLimits(unittest.TestCase):
    def test_defaults_match_documented_values(self):
        limits_now = limits.get_download_limits()
        self.assertEqual(limits_now.max_size_mb, 200)
        self.assertEqual(limits_now.max_duration_seconds, 300)
        self.assertEqual(limits_now.retry_count, 2)

    def test_zero_means_unlimited(self):
        zero = limits.DownloadLimits(max_size_mb=0, max_duration_seconds=0)
        self.assertIsNone(zero.max_size_bytes)
        self.assertFalse(zero.size_exceeds(10 ** 12))
        self.assertFalse(zero.duration_exceeds(10 ** 9))

    def test_size_and_duration_predicates(self):
        capped = limits.DownloadLimits(max_size_mb=10, max_duration_seconds=60)
        self.assertEqual(capped.max_size_bytes, 10 * 1024 * 1024)
        self.assertTrue(capped.size_exceeds(11 * 1024 * 1024))
        self.assertFalse(capped.size_exceeds(9 * 1024 * 1024))
        self.assertTrue(capped.duration_exceeds(61))
        self.assertFalse(capped.duration_exceeds(59))

    def test_env_override(self):
        import os

        original = {k: os.environ.get(k) for k in (
            "DOWNLOAD_MAX_SIZE_MB", "DOWNLOAD_MAX_DURATION_SECONDS", "DOWNLOAD_RETRY_COUNT"
        )}
        try:
            os.environ["DOWNLOAD_MAX_SIZE_MB"] = "512"
            os.environ["DOWNLOAD_MAX_DURATION_SECONDS"] = "1800"
            os.environ["DOWNLOAD_RETRY_COUNT"] = "5"
            overridden = limits.get_download_limits()
            self.assertEqual(overridden.max_size_mb, 512)
            self.assertEqual(overridden.max_duration_seconds, 1800)
            self.assertEqual(overridden.retry_count, 5)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_invalid_env_falls_back_to_default(self):
        import os

        original = os.environ.get("DOWNLOAD_MAX_SIZE_MB")
        try:
            os.environ["DOWNLOAD_MAX_SIZE_MB"] = "not-a-number"
            self.assertEqual(limits.get_download_limits().max_size_mb, 200)
        finally:
            if original is None:
                os.environ.pop("DOWNLOAD_MAX_SIZE_MB", None)
            else:
                os.environ["DOWNLOAD_MAX_SIZE_MB"] = original


class TestUrlSafety(unittest.TestCase):
    def test_blocks_private_and_metadata_addresses(self):
        for url in (
            "http://127.0.0.1/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/x",
            "http://192.168.1.1/x",
            "http://100.64.0.1/x",  # Tailscale / CGNAT
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                with self.assertRaises(url_safety.UnsafeUrlError):
                    url_safety.assert_safe_url(url, suffixes=())

    def test_allows_public_host_with_empty_suffixes(self):
        # suffixes=() 表示不做域名白名单，只做 IP 层校验
        self.assertEqual(
            url_safety.assert_safe_url("https://www.bilibili.com/video/BV17x411w7KC", suffixes=()),
            "https://www.bilibili.com/video/BV17x411w7KC",
        )


if __name__ == "__main__":
    unittest.main()
