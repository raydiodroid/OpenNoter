"""独立验证脚本：证明「平台是否受支持」与「DNS 可达性」已解耦。

## 为什么不用 pytest 跑

本仓库的完整测试依赖 sqlalchemy / fastapi 等后端依赖；本机（以及排障时的
最小环境）往往没有装。这个脚本只用标准库 + 按文件路径加载被测模块
（`importlib.util.spec_from_file_location`），因此 `python3` 直接可跑：

    cd backend && python3 tests/verify_url_dns_independence.py

它不会 import `app` 包（`app/__init__.py` 会拉起 FastAPI 路由 → sqlalchemy）。

## 它验证什么

并排跑 **git HEAD 的旧实现** 与 **工作区的新实现** 同一批场景：

  1. DNS 正常时，各平台视频链接都应放行；
  2. DNS 解析失败（离线 / 内网无 DNS / 公司 DNS 拦截）时**仍应判支持**；
  3. DNS 被污染到 0.0.0.0 / 内网段时**仍应判支持**；
  4. SSRF 集合（IP 直连、非法协议、伪装域名、非视频路径）**全部仍应拒绝**；
  5. 出站层 `assert_safe_url` 仍会拦「域名解析到内网」—— 证明 IP 层防护
     只是留在了正确的层，没有被删掉。

另外统计校验器内部的 DNS 调用次数：旧实现每条平台链接都解析一次，新实现为 0。

退出码 0 表示全部符合预期。
"""

from __future__ import annotations

import importlib.util
import pathlib
import socket
import subprocess
import sys
import tempfile
import types

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

# 顺序有依赖：url_parser 依赖 bilibili_ids / url_safety；validator 依赖 url_parser。
MODULES = (
    ("app.utils.bilibili_ids", "app/utils/bilibili_ids.py"),
    ("app.utils.url_safety", "app/utils/url_safety.py"),
    ("app.utils.url_parser", "app/utils/url_parser.py"),
    ("app.validators.video_url_validator", "app/validators/video_url_validator.py"),
)
# 从 git HEAD 还原旧实现时需要的全部文件（含 __init__.py 以构成合法的包命名空间）
HEAD_FILES = (
    "app/__init__.py",
    "app/validators/__init__.py",
    "app/utils/bilibili_ids.py",
    "app/utils/url_safety.py",
    "app/utils/url_parser.py",
    "app/validators/video_url_validator.py",
)

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
YT_TRAILING_SLASH = "https://www.youtube.com/watch/?v=dQw4w9WgXcQ"
YT_SHORT = "https://youtu.be/dQw4w9WgXcQ"
BILIBILI = "https://www.bilibili.com/video/BV17x411w7KC"

SSRF_MUST_REJECT = (
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.169.254/latest/meta-data/?x=douyin",
    "http://127.0.0.1:8483/api/notes",
    "http://10.0.0.5/x",
    "http://192.168.1.1/admin",
    "http://100.64.0.1/tailscale",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "https://evil.example.com/payload?x=douyin",
    "https://notbilibili.com.evil.com/video/BV17x411w7KC",
    "https://www.youtube.com/feed/subscriptions",
    "https://www.bilibili.com/anime/timeline",
)

SCENARIOS = (
    # (场景名, DNS 行为, [(标签, url, 期望)])
    (
        "1) DNS 正常",
        "public",
        (
            ("youtube watch", YT, True),
            ("youtube watch/（尾斜杠）", YT_TRAILING_SLASH, True),
            ("youtube short", YT_SHORT, True),
            ("bilibili", BILIBILI, True),
        ),
    ),
    (
        "2) DNS 解析失败（离线 / 内网无 DNS）",
        "fail",
        (
            ("youtube 仍判支持", YT, True),
            ("bilibili 仍判支持", BILIBILI, True),
        ),
    ),
    (
        "3) DNS 被污染到 0.0.0.0",
        "poison",
        (
            ("youtube 仍判支持", YT, True),
            ("bilibili 仍判支持", BILIBILI, True),
        ),
    ),
    (
        "4) SSRF 集合必须仍全部拒绝",
        "public",
        tuple((url, url, False) for url in SSRF_MUST_REJECT),
    ),
)


class DnsStub:
    """替换最底层的 `socket.getaddrinfo`，并统计调用次数。

    打最底层是有意的：旧实现经 `url_safety.resolve_host` 解析，新实现一次都不
    解析 —— 只有统计到底层调用的次数，才能证明「校验器不再做网络 I/O」。
    """

    def __init__(self) -> None:
        self._real = socket.getaddrinfo
        self.calls: list[str] = []

    def __enter__(self) -> "DnsStub":
        return self

    def __exit__(self, *exc_info) -> None:
        socket.getaddrinfo = self._real

    def configure(self, mode: str) -> None:
        if mode == "public":
            socket.getaddrinfo = self._answers("93.184.216.34")
        elif mode == "poison":
            socket.getaddrinfo = self._answers("0.0.0.0")
        elif mode == "fail":
            socket.getaddrinfo = self._fail
        else:  # pragma: no cover - 防御性分支
            raise ValueError(mode)

    def _answers(self, ip: str):
        def getaddrinfo(host, port, *args, **kwargs):
            self.calls.append(host)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port or 0))
            ]

        return getaddrinfo

    def _fail(self, host, port, *args, **kwargs):
        self.calls.append(host)
        raise socket.gaierror(-2, "Name or service not known")


def load_version(root: pathlib.Path):
    """按文件路径加载一套模块。会先清掉 sys.modules 里的 app.* 避免串味。"""
    for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)

    package = types.ModuleType("app")
    package.__path__ = [str(root / "app")]
    sys.modules["app"] = package

    loaded = {}
    for name, relative in MODULES:
        spec = importlib.util.spec_from_file_location(name, root / relative)
        if spec is None or spec.loader is None:  # pragma: no cover
            raise ImportError(f"{relative} 无法加载")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["app.validators.video_url_validator"], loaded["app.utils.url_safety"]


def materialize_head(destination: pathlib.Path) -> bool:
    """把 git HEAD 版本的旧实现还原到 destination/app 下。

    返回 False 表示拿不到 HEAD（例如不在 git 仓库里）—— 那种情况下脚本只跑
    工作区版本，并把「修复前」一列标成 n/a。
    """
    for relative in HEAD_FILES:
        result = subprocess.run(
            ["git", "show", f"HEAD:backend/{relative}"],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        if result.returncode != 0:
            return False
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.stdout)
    return True


def run_scenarios(validator, stub: DnsStub) -> tuple[dict, dict]:
    """跑全部场景，返回 ({标签: (实际值, 期望值)}, {场景名: DNS 调用次数})。"""
    results: dict[str, tuple[bool, bool]] = {}
    dns_calls: dict[str, int] = {}
    for scenario_name, dns_mode, cases in SCENARIOS:
        stub.configure(dns_mode)
        before = len(stub.calls)
        for label, url, expected in cases:
            results[f"{scenario_name} · {label}"] = (
                validator.is_supported_video_url(url),
                expected,
            )
        dns_calls[scenario_name] = len(stub.calls) - before
    return results, dns_calls


def check_outbound_layer(url_safety_module, stub: DnsStub) -> dict[str, bool]:
    """出站层仍必须拦「域名解析到内网」与「IP 字面量直连内网」。"""
    checks: dict[str, bool] = {}

    stub.configure("poison")
    # poison 用的是 0.0.0.0；再单独造一个解析到 10.0.0.5 的场景
    socket.getaddrinfo = stub._answers("10.0.0.5")  # noqa: SLF001 - 测试脚本内部使用
    for label, url in (
        ("域名解析到 10.0.0.5", YT),
        ("IP 字面量 169.254.169.254", "http://169.254.169.254/latest/meta-data/"),
    ):
        try:
            url_safety_module.assert_safe_url(url, suffixes=())
        except url_safety_module.UnsafeUrlError:
            checks[label] = True
        else:
            checks[label] = False
    return checks


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        head_root = pathlib.Path(tmp)
        has_head = materialize_head(head_root)

        before_results = before_dns_calls = None
        if has_head:
            old_validator, _old_safety = load_version(head_root)
            with DnsStub() as stub:
                before_results, before_dns_calls = run_scenarios(old_validator, stub)

        new_validator, new_safety = load_version(BACKEND_ROOT)
        with DnsStub() as stub:
            after_results, after_dns_calls = run_scenarios(new_validator, stub)
            outbound = check_outbound_layer(new_safety, stub)

    width = max(len(label) for label in after_results)
    print("=" * (width + 22))
    print(f"{'场景':<{width}}{'修复前':<10}{'修复后'}")
    print("-" * (width + 22))
    fails_after = 0
    for label, (got_after, expected) in after_results.items():
        if before_results is None:
            before_cell = "n/a"
        else:
            got_before = before_results[label][0]
            before_cell = "PASS" if got_before == expected else "FAIL"
        ok_after = got_after == expected
        fails_after += 0 if ok_after else 1
        print(f"{label:<{width}}{before_cell:<10}{'PASS' if ok_after else 'FAIL'}")

    print("-" * (width + 22))
    print("校验器内部 DNS 调用次数：")
    for scenario_name in after_dns_calls:
        before_cell = "n/a" if before_dns_calls is None else f"{before_dns_calls[scenario_name]:>3} 次"
        print(
            f"  {scenario_name:<{width - 2}}{before_cell:<12}"
            f"{after_dns_calls[scenario_name]:>3} 次"
        )

    print("\n出站层 assert_safe_url 仍然拦截：")
    for label, blocked in outbound.items():
        print(f"  {'PASS' if blocked else 'FAIL'}  {label}")

    all_ok = fails_after == 0 and all(outbound.values())
    print()
    if before_results is not None:
        fails_before = sum(
            1 for label, (got, expected) in before_results.items() if got != expected
        )
        print(f"修复前失败 {fails_before} 条 → 修复后失败 {fails_after} 条")
    print("RESULT:", "ALL PASS" if all_ok else "FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
