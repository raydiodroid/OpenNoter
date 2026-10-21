"""API 契约相关的测试。

覆盖这轮改造里「形状」层面的改动：
  - 统一响应体 {code, msg, data} 与真实 HTTP 状态码
  - 鉴权通道：Authorization: Bearer 也能带 API Key
  - 出站 URL 安全校验（防 SSRF）
  - 平台自动识别
"""

import pytest

from app.utils.response import ResponseWrapper as R, http_status_for_code
from app.utils.url_parser import detect_platform
from app.utils.url_safety import UnsafeUrlError, assert_safe_url


# ---------- 错误码 → HTTP 状态码 ----------

@pytest.mark.parametrize(
    "code,expected",
    [
        (0, 200),
        (401, 401),
        (404, 404),
        (500, 500),
        (1001, 400),      # 业务错误码段 → 400
        (200104, 400),    # 供应商不存在
        (300102, 400),    # 模型不存在
        (500000, 500),    # 系统异常
    ],
)
def test_http_status_for_code(code, expected):
    assert http_status_for_code(code) == expected


def test_r_error_uses_real_status_code():
    resp = R.error(msg="供应商不存在/未保存", code=200104)
    assert resp.status_code == 400


def test_r_error_system_exception_is_500():
    resp = R.error(code=500000, msg="系统异常")
    assert resp.status_code == 500


def test_r_error_not_found_is_404():
    resp = R.error(msg="任务不存在", code=404, status_code=404)
    assert resp.status_code == 404


def test_r_success_is_200_with_zero_code():
    resp = R.success({"task_id": "x"})
    assert resp.status_code == 200


# ---------- 业务异常的错误码必须是 int ----------

@pytest.mark.parametrize(
    "module,cls,enum_member",
    [
        ("app.exceptions.provider", "ProviderError", "ProviderErrorEnum.NOT_FOUND"),
        ("app.exceptions.note", "NoteError", "NoteErrorEnum.PLATFORM_NOT_SUPPORTED"),
    ],
)
def test_business_error_code_is_int(module, cls, enum_member):
    """回归：枚举成员直接进 JSON 响应体会抛 TypeError。

    之前 ProviderError 原样存枚举成员，异常处理器塞进 JSON 响应体时炸掉，
    结果所有 ProviderError 都退化成 500「系统异常」，调用方看不到真正原因。
    """
    import importlib
    import json

    exc_mod = importlib.import_module(module)
    enums = importlib.import_module("app.enmus.exception")
    enum_name, member_name = enum_member.split(".")
    member = getattr(getattr(enums, enum_name), member_name)

    exc = getattr(exc_mod, cls)(member.message, member)
    assert isinstance(exc.code, int), f"{cls}.code 应为 int，实际是 {type(exc.code)}"
    assert exc.code == member.code
    # 关键：能进 JSON 响应体
    json.dumps({"code": exc.code, "msg": exc.message})


def test_biz_exception_code_is_int():
    import json

    from app.exceptions.biz_exception import BizException

    exc = BizException(code=2002, message="参数错误")
    assert exc.code == 2002
    json.dumps({"code": exc.code})


# ---------- 免鉴权的使用指引 ----------

def test_api_guide_is_public_and_describes_the_flow():
    """agent 拿不到 key 时，至少要能自助拿到「怎么调」的说明。"""
    from app.api_guide import build_guide, render_markdown
    from app import create_app

    app = create_app(lifespan=None)
    guide = build_guide(app, request=None)

    # 鉴权方式说清楚了
    assert guide["authentication"]["key_prefix"] == "sk-bili-"
    headers = " ".join(
        h for scheme in guide["authentication"]["schemes"] for h in scheme["headers"]
    )
    assert "x-api-key" in headers
    assert "Authorization: Bearer" in headers
    assert guide["authentication"]["how_to_get_a_key"]

    # 主流程说清楚了
    steps = guide["quickstart"]["steps"]
    assert steps[0]["method"] == "POST" and steps[0]["path"] == "/api/notes"
    assert steps[1]["method"] == "GET" and steps[1]["path"] == "/api/tasks/{task_id}"
    assert "url" in steps[0]["body"]

    # 端点清单是从路由表生成的，不该是空的
    paths = {ep["path"] for ep in guide["endpoints"]}
    assert "/api/notes" in paths
    assert "/api/tasks/{task_id}" in paths
    # 旧 RPC 风格路径不该出现在指引里
    assert "/api/generate_note" not in paths

    # Markdown 版本能渲染
    md = render_markdown(guide)
    assert "# OpenNoter API 使用指引" in md
    assert "| POST | `/api/notes` |" in md


def test_api_guide_lists_only_canonical_endpoints():
    from app.api_guide import build_guide
    from app import create_app

    guide = build_guide(create_app(lifespan=None), request=None)
    paths = {ep["path"] for ep in guide["endpoints"]}
    for legacy in ("/api/model_list", "/api/get_all_providers", "/api/task_status/{task_id}"):
        assert legacy not in paths


def test_quickstart_urls_are_not_double_prefixed(monkeypatch):
    """回归：path 已含 /api 前缀，url 字段是拼好的完整地址，不能再拼一次。

    之前指引里同时给 api_root（带 /api）和 path（也带 /api），调用方一拼就变成
    /api/api/notes，直接 404。
    """
    from app.api_guide import build_guide
    from app import create_app

    # 线上配了 OPENNOTER_PUBLIC_BASE_URL（优先级高于请求头），这里先摘掉
    monkeypatch.delenv("OPENNOTER_PUBLIC_BASE_URL", raising=False)

    class FakeURL:
        scheme = "https"
        netloc = "opennoter.example.com"

    class FakeRequest:
        headers = {"host": "opennoter.example.com", "x-forwarded-proto": "https"}
        url = FakeURL()

    guide = build_guide(create_app(lifespan=None), request=FakeRequest())
    assert guide["base_url"] == "https://opennoter.example.com"

    for step in guide["quickstart"]["steps"]:
        assert step["url"] == f"https://opennoter.example.com{step['path']}"
        assert step["url"].count("/api/") == 1
        assert step["url"].startswith("https://")


def test_public_base_url_env_overrides_request_headers(monkeypatch):
    """反代链上 X-Forwarded-Proto 不可信，显式配置优先。"""
    from app.api_guide import build_guide
    from app import create_app

    monkeypatch.setenv("OPENNOTER_PUBLIC_BASE_URL", "https://public.example.com/")

    class FakeRequest:
        headers = {"host": "internal:8483", "x-forwarded-proto": "http"}
        url = None

    guide = build_guide(create_app(lifespan=None), request=FakeRequest())
    assert guide["base_url"] == "https://public.example.com"
    assert guide["quickstart"]["steps"][0]["url"].startswith("https://public.example.com/api/")


# ---------- 401 要带上鉴权提示 ----------

def test_unauthorized_carries_auth_hint():
    """只回一句「未登录」的话，调用方无从知道该怎么传凭据。"""
    from app.services.auth import _unauthorized

    exc = _unauthorized("未登录")
    assert exc.status_code == 401
    msg, data = exc.detail["msg"], exc.detail["data"]
    assert msg == "未登录"
    hint = data["hint"]
    assert hint["how_to_authenticate"]
    assert hint["how_to_get_a_key"]
    assert "/api/" in hint["usage_guide"]


def test_unauthorized_reason_is_overridable():
    from app.services.auth import _unauthorized

    exc = _unauthorized("API Key 已过期", reason="expired")
    assert exc.detail["data"]["hint"]["reason"] == "expired"


# ---------- 鉴权通道 ----------

def test_bearer_token_with_api_key_prefix_is_treated_as_api_key():
    """Authorization: Bearer sk-bili-... 必须被识别成 API Key，而不是拿去解 JWT。"""
    from app.services.auth import API_KEY_PREFIX, _extract_credentials

    class FakeRequest:
        def __init__(self, headers, cookies=None):
            self.headers = headers
            self.cookies = cookies or {}

    key = f"{API_KEY_PREFIX}abcdefghijklmnop"
    req = FakeRequest({"authorization": f"Bearer {key}"})
    assert _extract_credentials(req) == ("api_key", key)


def test_bearer_token_without_api_key_prefix_stays_jwt():
    from app.services.auth import _extract_credentials

    class FakeRequest:
        def __init__(self, headers, cookies=None):
            self.headers = headers
            self.cookies = cookies or {}

    req = FakeRequest({"authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb"})
    assert _extract_credentials(req) == ("bearer", "eyJhbGciOiJIUzI1NiJ9.aaa.bbb")


def test_x_api_key_header_takes_priority():
    from app.services.auth import _extract_credentials

    class FakeRequest:
        def __init__(self, headers, cookies=None):
            self.headers = headers
            self.cookies = cookies or {}

    req = FakeRequest({"x-api-key": "sk-bili-xxx", "authorization": "Bearer sk-bili-yyy"})
    assert _extract_credentials(req) == ("api_key", "sk-bili-xxx")


# ---------- 出站 URL 安全 ----------

@pytest.fixture
def fake_dns(monkeypatch):
    """把域名解析打桩，测试不依赖容器/机器的外网 DNS。"""
    mapping = {
        "i1.hdslb.com": ["203.119.128.1"],
        "cdn.example.com": ["203.119.128.2"],
        "evil.example.com": ["203.119.128.3"],
        "internal.example.com": ["10.0.0.5"],
    }

    def _resolve(host):
        if host not in mapping:
            raise UnsafeUrlError(f"域名解析失败: {host}")
        return mapping[host]

    monkeypatch.setattr("app.utils.url_safety.resolve_host", _resolve)
    return mapping


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8483/api/sys_check",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.8.12/",
        "http://192.168.2.49/",
        "http://100.64.0.2/",       # Tailscale
        "file:///etc/passwd",
        "gopher://127.0.0.1/",
    ],
)
def test_unsafe_urls_are_rejected(url):
    """IP 直连的场景不依赖 DNS，直接按地址判定。"""
    with pytest.raises(UnsafeUrlError):
        assert_safe_url(url)


def test_public_host_is_allowed(fake_dns):
    assert assert_safe_url("https://i1.hdslb.com/bfs/archive/x.jpg")


def test_host_resolving_to_private_ip_is_rejected(fake_dns):
    """域名指向内网（DNS  rebinding）也要拦。"""
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("https://internal.example.com/x.jpg")


def test_host_not_in_allowlist_is_rejected(fake_dns):
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("https://evil.example.com/x.jpg")


def test_allowlist_can_be_overridden(monkeypatch, fake_dns):
    monkeypatch.setenv("OPENNOTER_IMAGE_PROXY_ALLOWED_HOSTS", "example.com")
    assert assert_safe_url("https://cdn.example.com/x.jpg")
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("https://i1.hdslb.com/bfs/archive/x.jpg")


def test_allowlist_wildcard_allows_any_public_host(monkeypatch, fake_dns):
    monkeypatch.setenv("OPENNOTER_IMAGE_PROXY_ALLOWED_HOSTS", "*")
    assert assert_safe_url("https://evil.example.com/x.jpg")
    # 即便放开域名，内网地址照样拦
    with pytest.raises(UnsafeUrlError):
        assert_safe_url("http://127.0.0.1/")


# ---------- 平台自动识别 ----------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.bilibili.com/video/BV1LLnj6fE1z", "bilibili"),
        ("https://b23.tv/abc123", "bilibili"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("https://www.douyin.com/video/1234567890", "douyin"),
        ("https://example.com/video/1", None),
    ],
)
def test_detect_platform(url, expected):
    assert detect_platform(url) == expected


# ---------- 简化入口的请求模型 ----------

def test_create_note_request_accepts_video_url_alias():
    """前端老字段名 video_url 也要能进 /api/notes。"""
    from app.routers.note import CreateNoteRequest

    req = CreateNoteRequest(video_url="https://www.bilibili.com/video/BV1LLnj6fE1z")
    assert req.url == "https://www.bilibili.com/video/BV1LLnj6fE1z"


def test_create_note_request_model_and_provider_are_optional():
    from app.routers.note import CreateNoteRequest

    req = CreateNoteRequest(url="https://www.bilibili.com/video/BV1LLnj6fE1z")
    assert req.provider_id is None
    assert req.model_name is None
    assert req.quality.value == "fast"
