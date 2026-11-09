"""视频 URL 校验的回归测试。

核心是这条：历史实现用「URL 里包含 douyin / kuaishou 子串」判断平台，
于是任意内网地址只要带上这个 query 就能通过校验，接着下载器会真的去请求它
（SSRF）。这组测试把「必须放行」和「必须拒绝」两侧都钉住。

另外还钉住一条**语义分层**：`is_supported_video_url` 负责「这是不是受支持
平台的视频链接」，是纯语法判定；DNS 通不通、解析到哪，属于运行环境，不该
影响这个结论（用户报的「YouTube 链接提示不受支持」就是这两件事混在一起）。
出站 IP 层拦截仍然存在，只是留在真正发请求的 `assert_safe_url` 里。
"""

import importlib.util
import pathlib
import socket
import sys
import types
import unittest
from contextlib import contextmanager
from unittest import mock

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
url_safety = _load("app.utils.url_safety", "app/utils/url_safety.py")
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
    # 多一个尾斜杠也是真实存在的 YouTube 链接形态（部分客户端/复制来源会带）
    "https://www.youtube.com/watch/?v=dQw4w9WgXcQ",
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


@contextmanager
def patched_dns(ips=None, *, failing=False, calls=None):
    """同时打桩 DNS 的两个入口：`url_safety.resolve_host` 与 `socket.getaddrinfo`。

    ## 为什么两层都要打

    1. `resolve_host` 是 `assert_safe_url` 实际调用的那层；
    2. `socket.getaddrinfo` 是最底层 —— 旧实现（以及任何漏网的直接解析）都
       会落到它。只打一层的话，`is_supported_video_url` 曾经的「偷偷解析」
       可能被另一层的桩掩盖，测不出问题。

    ## 为什么要显式打桩（不能靠 conftest 的 stable_dns）

    `conftest.py` 的 `stable_dns` 只是把**已知平台域名**打桩成公网 IP，
    恰好会让「DNS 失败」「DNS 被污染」这两类场景**测不出来**。本组测试要的
    正是这两类场景，所以显式覆盖。

    :param ips: 所有域名都解析成这些 IP（模拟污染）。
    :param failing: 解析一律失败（模拟离线/内网无 DNS/被 DNS 拦截）。
    :param calls: 传入 list 时记录每次「被调用」的 host，用于断言零网络 I/O。
    """
    resolved_ips = list(ips or [])
    recorded = calls if calls is not None else []

    def _fake_resolve(host):
        recorded.append(host)
        if failing:
            raise url_safety.UnsafeUrlError(f"域名解析失败: {host}")
        return list(resolved_ips)

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        recorded.append(host)
        if failing:
            raise socket.gaierror(-2, "Name or service not known")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port or 0))
            for ip in resolved_ips
        ]

    with mock.patch.object(url_safety, "resolve_host", _fake_resolve), mock.patch.object(
        socket, "getaddrinfo", _fake_getaddrinfo
    ):
        yield recorded


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


class TestPlatformSupportIsIndependentOfDns(unittest.TestCase):
    """「平台是否受支持」不能依赖 DNS（用户 bug 的直接回归）。

    断言用的链接覆盖 **两个平台**：这不是 YouTube 特例 —— 旧实现下 B 站
    链接在 DNS 失败时同样被判「不受支持」，用户只是更容易在 YouTube 上撞到。
    """

    SAMPLE = {
        "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "bilibili": "https://www.bilibili.com/video/BV17x411w7KC",
    }

    def test_supported_when_dns_fails(self):
        """DNS 解析失败（离线/内网无 DNS/公司 DNS 拦截）时仍判支持。"""
        with patched_dns(failing=True):
            for platform, url in self.SAMPLE.items():
                with self.subTest(platform=platform):
                    self.assertTrue(
                        validator.is_supported_video_url(url),
                        f"{platform} 不应因 DNS 失败而被判不受支持",
                    )

    def test_supported_when_dns_is_poisoned_to_private_address(self):
        """DNS 被污染/劫持到内网或保留地址时仍判支持。

        ## 这为什么不算放行 SSRF

        `is_supported_video_url` 只回答「这是不是我们支持的平台视频链接」，
        不发起任何请求。真正的出站 IP 层校验在 `assert_safe_url`（每跳校验，
        `image_proxy` / `probe_endpoint` / 短链解析 / 字幕下载都在用），
        见下面 `TestOutboundLayerKeepsIpGuard` —— 同一个链接在那里仍会被拦。

        如果反过来把 DNS 判定塞回这里，国内 DNS 污染（youtube 常被解析到
        0.0.0.0 / 保留段）就会让「平台支持」这个纯语义结论随网络环境漂移。
        """
        poisoned = ["0.0.0.0"]
        with patched_dns(poisoned):
            for platform, url in self.SAMPLE.items():
                with self.subTest(platform=platform):
                    self.assertTrue(
                        validator.is_supported_video_url(url),
                        f"{platform} 不应因 DNS 污染而被判不受支持",
                    )

    def test_validator_performs_no_network_io(self):
        """校验器必须是纯函数：一次 DNS 调用都不该发生。

        这条把设计意图钉死 —— 以后有人往校验器里加 `assert_safe_url`
        （或任何网络调用）会立刻红。
        """
        calls = []
        with patched_dns(["93.184.216.34"], calls=calls):
            for url in ACCEPTED:
                validator.is_supported_video_url(url)
        self.assertEqual(calls, [], f"is_supported_video_url 不应发起 DNS 解析，实际调用了：{calls}")


class TestOutboundLayerKeepsIpGuard(unittest.TestCase):
    """IP 层拦截没有被删掉，只是留在了正确的层（出站前）。

    防止这次修复被误做成「把关卡整个拆了」。
    """

    def test_assert_safe_url_still_rejects_domain_resolving_to_private_ip(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        with patched_dns(["10.0.0.5"]):
            # 出站层：域名解析到内网 → 必须拒绝（这才是 SSRF 的真实信号）
            with self.assertRaises(url_safety.UnsafeUrlError):
                url_safety.assert_safe_url(url, suffixes=())

    def test_assert_safe_url_still_rejects_when_dns_fails(self):
        """出站层解析失败仍然拒绝：请求根本发不出去，宁可拒绝不要盲发。"""
        with patched_dns(failing=True):
            with self.assertRaises(url_safety.UnsafeUrlError):
                url_safety.assert_safe_url("https://www.youtube.com/watch?v=x", suffixes=())

    def test_assert_safe_url_still_rejects_ip_literal_ssrf(self):
        """IP 字面量不依赖 DNS，纯语法层就该拒 —— 这一条在默认环境下也成立。"""
        for url in (
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8483/api/notes",
            "http://10.0.0.5/x",
            "http://192.168.1.1/admin",
            "http://100.64.0.1/tailscale",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                with self.assertRaises(url_safety.UnsafeUrlError):
                    url_safety.assert_safe_url(url, suffixes=())

    def test_shape_check_alone_does_not_do_dns(self):
        """语法层函数对域名不做解析（只拦 scheme 与 IP 字面量）。

        这是「校验器不联网」的实现基础；同时它**必须**拦住 IP 字面量 SSRF。
        """
        calls = []
        with patched_dns(failing=True, calls=calls):
            self.assertEqual(
                url_safety.assert_url_shape_safe("https://www.bilibili.com/video/BV17x411w7KC"),
                "https://www.bilibili.com/video/BV17x411w7KC",
            )
            with self.assertRaises(url_safety.UnsafeUrlError):
                url_safety.assert_url_shape_safe("http://169.254.169.254/latest/meta-data/")
        self.assertEqual(calls, [], "语法层不应触发 DNS 解析")


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
