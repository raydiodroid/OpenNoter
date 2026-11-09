"""F2：本地上传在规范 API（`POST /api/notes`）上的入口契约。

## 背景

`CreateNoteRequest` 原先**无条件**调 `is_supported_video_url()`，而它第一关是
`parsed.scheme not in ("http", "https") → False`。上传接口返回的是相对路径
`/uploads/<uuid>.ext`（scheme 为空），于是本地上传在 `POST /api/notes` 上
必然 400 —— 这正是"本地上传完全不��用"的根因。

修法：`model_validator(mode="after")`（要读 `platform`，而它声明在 `url` 之后，
`field_validator` 的 `info.data` 拿不到）按来源分流。

## 这里锁住的四件事

1. `/uploads/xxx` 被接受，且 `platform` 留空时**自动补 local**
   （否则路由层 `detect_platform("/uploads/x")` 返回 None，会走"无法识别平台"分支）；
2. 穿越 / 绝对路径 / 非 URL 字符串**仍然被拒**（不能为了修本地文件把校验放开）；
3. 在线链接行为不变；
4. SSRF 黑名单没有被这次改动削弱。
"""

import pytest

from app.exceptions.note import NoteError
from app.routers.note import CreateNoteRequest
from app.utils.upload_paths import is_upload_ref, safe_upload_rel


# ---------------------------------------------------------------------------
# 入口校验：接受
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {"url": "/uploads/abc.mp4", "platform": "local"},
        {"url": "/uploads/abc.mp4"},  # platform 留空必须自动补 local
        {"url": "uploads/abc.mp4", "platform": "local"},
        {"url": "/uploads/sub/abc.mp4", "platform": "local"},
        # 不带 platform，但路径本身就是 uploads 引用
        {"url": "uploads/abc.mp4"},
        {"url": "/uploads/sub/abc.mp4"},
    ],
)
def test_local_upload_accepted(payload):
    """上传引用必须被接受，且 platform 归一成 local。"""
    req = CreateNoteRequest(**payload)
    assert req.platform == "local"


def test_platform_left_empty_is_filled_with_local():
    """关键回归：不补 local 的话，路由层会走到"无法识别视频平台"。"""
    from app.utils.url_parser import detect_platform

    req = CreateNoteRequest(url="/uploads/abc.mp4")
    assert req.platform == "local"
    # 证明「必须补」：detect_platform 自己认不出 uploads 路径
    assert detect_platform("/uploads/abc.mp4") is None


def test_existing_platform_is_not_overwritten():
    """显式给了别的 platform 时不能被 local 顶上（保持调用方意图）。"""
    req = CreateNoteRequest(
        url="https://www.bilibili.com/video/BV1LLnj6fE1z", platform="bilibili"
    )
    assert req.platform == "bilibili"


# ---------------------------------------------------------------------------
# 入口校验：拒绝
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {"url": "/uploads/../../etc/passwd", "platform": "local"},
        {"url": "/etc/passwd", "platform": "local"},
        {"url": "not-a-url", "platform": "local"},
        # local 平台但给的是空字符串
        {"url": "", "platform": "local"},
        # 只写到目录，没有文件名
        {"url": "/uploads/", "platform": "local"},
    ],
)
def test_local_upload_rejected(payload):
    """路径穿越 / 绝对路径 / 非 URL 必须在入口被拒。"""
    with pytest.raises(NoteError):
        CreateNoteRequest(**payload)


def test_ssrf_blacklist_is_not_weakened():
    """SSRF 黑名单不能被这次改动放过（云元数据地址）。"""
    with pytest.raises(NoteError):
        CreateNoteRequest(url="http://169.254.169.254/x?y=douyin")


def test_plain_http_url_still_goes_through_video_whitelist():
    """非 uploads 的 http(s) 仍必须过视频域名白名单。"""
    with pytest.raises(NoteError):
        CreateNoteRequest(url="https://example.com/not-a-video")


# ---------------------------------------------------------------------------
# 两个纯函数本身
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        ("/uploads/abc.mp4", True),
        ("uploads/abc.mp4", True),
        ("  /uploads/abc.mp4  ", True),
        ("/uploads/", True),  # 是引用，但形态非法 → 由 safe_upload_rel 判
        ("/etc/passwd", False),
        ("not-a-url", False),
        ("", False),
        (None, False),
    ],
)
def test_is_upload_ref(value, expected):
    assert is_upload_ref(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/uploads/abc.mp4", True),
        ("uploads/abc.mp4", True),
        ("/uploads/sub/abc.mp4", True),
        ("/uploads/../../etc/passwd", False),
        ("/uploads/a/../b.mp4", False),
        ("/uploads/", False),
        ("uploads/", False),
        ("/etc/passwd", False),
        ("not-a-url", False),
        ("", False),
    ],
)
def test_safe_upload_rel(value, expected):
    assert safe_upload_rel(value) is expected


def test_safe_upload_rel_agrees_with_resolve_under_uploads(tmp_path, monkeypatch):
    """形态校验与真实解析必须同口径（否则会出现"校验过、下载时炸"）。"""
    from app.utils.upload_paths import resolve_under_uploads

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    assert safe_upload_rel("/uploads/abc.mp4") is True
    assert resolve_under_uploads("/uploads/abc.mp4") == (tmp_path / "abc.mp4").resolve()

    assert safe_upload_rel("/uploads/../x.mp4") is False
    with pytest.raises(ValueError):
        resolve_under_uploads("/uploads/../x.mp4")


# ===========================================================================
# 审计补充（2026-11-09）：VideoRequest 不再放行非 http(s)
# ===========================================================================


class TestVideoRequestRejectsNonHttp:
    """`VideoRequest`（`/api/generate_note`）必须拒绝非 http(s) 输入。

    ## 背景（审计 P0）

    旧实现是：

        if parsed.scheme in ("http", "https"):
            if not is_supported_video_url(url): raise ...

    也就是说**只校验「看起来像链接」的输入**，其余原样 `return v`。于是：

        "x http://169.254.169.254/"   → scheme 空 → 放行
        "file:///etc/passwd"          → scheme=file → 放行

    这些值会流到下载器。快手那条链尤其危险：`_URL_RE` 会把
    `"x http://169.254.169.254/"` 里的 URL 抠出来还原成
    `http://169.254.169.254/`，再 `requests.get(allow_redirects=True)`
    零校验发出去 —— 盲 SSRF / 内网端口探测。

    「不是 http(s)」不是「不用校验」，而是**本身就不合法**。
    """

    @pytest.mark.parametrize(
        "bad",
        [
            "x http://169.254.169.254/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "ftp://example.com/a.mp4",
            "not-a-url",
            "/uploads/../../etc/passwd",
        ],
    )
    def test_non_http_scheme_rejected(self, bad):
        from app.routers.note import VideoRequest

        with pytest.raises(NoteError):
            VideoRequest(video_url=bad, platform="kuaishou")

    def test_valid_platform_url_still_accepted(self):
        """收紧不能把正常链接搞坏。"""
        from app.routers.note import VideoRequest

        req = VideoRequest(
            video_url="https://www.bilibili.com/video/BV1GJ411x7h7",
            platform="bilibili",
        )
        assert req.video_url.startswith("https://www.bilibili.com/")


class TestUploadOwnershipOnConsume:
    """上传文件的**消费路径**也必须校验归属。

    ## 背景（审计 P1）

    `main.py:363` 的静态读取路径用 `can_read_upload` 校验了归属，但
    「把这个文件拿去转写」这条消费路径没校验 —— 知道他人 upload 文件名
    就能让服务端处理别人的视频。

    上传文件名是 uuid4 hex（不可枚举），但 UUID 会经日志、代理、分享链接
    泄露；仓库自己在 `upload_paths.py:105-110` 论证过「UUID 不是访问控制」。
    """

    def test_helper_exists_and_uses_can_read_upload(self):
        import inspect

        from app.routers import note as note_module

        assert hasattr(note_module, "_assert_upload_owned")
        src = inspect.getsource(note_module._assert_upload_owned)
        assert "can_read_upload" in src

    def test_both_create_routes_call_the_helper(self):
        import inspect

        from app.routers import note as note_module

        for fn_name in ("generate_note", "create_note"):
            src = inspect.getsource(getattr(note_module, fn_name))
            assert "_assert_upload_owned(" in src, f"{fn_name} 未校验上传归属"

    def test_foreign_upload_rejected(self, tmp_path, monkeypatch):
        """他人名下的上传必须被拒。"""
        from app.utils import upload_paths

        target = tmp_path / "abc.mp4"
        target.write_bytes(b"x")
        upload_paths.set_upload_owner(target, 1)

        assert upload_paths.can_read_upload(
            target, _User(2), auth_enforced=True
        ) is False
        assert upload_paths.can_read_upload(
            target, _User(1), auth_enforced=True
        ) is True


class _User:
    """最小 User 替身：只需要 id / is_superuser。"""

    def __init__(self, uid, superuser=False):
        self.id = uid
        self.is_superuser = superuser
