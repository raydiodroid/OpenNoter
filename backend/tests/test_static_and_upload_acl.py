"""静态资源与上传文件的归属校验（R1 / R2）。

## 背景

R1：`/static` 原先是匿名 `StaticFiles` 挂载——任何人只要知道 URL 就能读
他人笔记里的视频截图。URL 会出现在 markdown（分享/导出/日志）里，
UUID 文件名不构成访问控制。

R2：`/uploads` 虽然加了登录校验，但登录只解决「你是谁」，不解决
「这个文件是不是你的」——任意登录用户知道 UUID 就能下载他人上传。

修复策略是「渐进式」：老数据没有归属信息，一律放行（否则全 404）；
新数据带归属，按 owner 校验。
"""

import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _install_app_namespace():
    if "app" in sys.modules:
        return
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(BACKEND_DIR / "app")]
    pkg.__package__ = "app"
    sys.modules["app"] = pkg


@pytest.fixture()
def utils(tmp_path, monkeypatch):
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)
    try:
        _install_app_namespace()
        from app.utils.path_safety import resolve_under_dir
        from app.utils.upload_paths import (
            can_read_upload,
            get_upload_owner,
            set_upload_owner,
        )
        from app.utils import upload_paths

        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
        yield {
            "resolve_under_dir": resolve_under_dir,
            "can_read_upload": can_read_upload,
            "get_upload_owner": get_upload_owner,
            "set_upload_owner": set_upload_owner,
            "upload_paths": upload_paths,
            "tmp": tmp_path,
        }
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


class U:
    def __init__(self, uid, admin=False):
        self.id = uid
        self.is_superuser = admin


# ---------------------------------------------------------------------------
# path_safety：静态路径沙箱
# ---------------------------------------------------------------------------
def test_normal_relative_path_ok(utils):
    root = utils["tmp"] / "static"
    root.mkdir()
    (root / "a.jpg").write_text("x")
    assert utils["resolve_under_dir"](root, "a.jpg").name == "a.jpg"


def test_nested_path_ok(utils):
    root = utils["tmp"] / "static"
    (root / "screenshots" / "task-1").mkdir(parents=True)
    (root / "screenshots" / "task-1" / "s.jpg").write_text("x")
    got = utils["resolve_under_dir"](root, "screenshots/task-1/s.jpg")
    assert got.name == "s.jpg"


@pytest.mark.parametrize(
    "evil",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "../../../root/.ssh/id_rsa",
        "screenshots/../../../etc/passwd",
        "..",
        ".",
        "",
    ],
)
def test_traversal_is_rejected(utils, evil):
    root = utils["tmp"] / "static"
    root.mkdir(exist_ok=True)
    with pytest.raises(ValueError):
        utils["resolve_under_dir"](root, evil)


def test_absolute_path_stays_inside_root(utils):
    """绝对路径被映射到 root 下，不逃出沙箱（pathlib 语义陷阱的回归测试）。"""
    root = utils["tmp"] / "static"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "passwd").write_text("fake")
    got = utils["resolve_under_dir"](root, "/etc/passwd")
    assert got == (root / "etc" / "passwd").resolve()


def test_symlink_escape_is_rejected(utils):
    """root 内指向外部的软链必须被拦（这是 resolve 而非 abspath 的价值）。"""
    root = utils["tmp"] / "static"
    root.mkdir()
    outside = utils["tmp"] / "secret.txt"
    outside.write_text("secret")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建软链")
    with pytest.raises(ValueError):
        utils["resolve_under_dir"](root, "link.txt")


# ---------------------------------------------------------------------------
# upload 归属
# ---------------------------------------------------------------------------
def test_upload_without_owner_is_readable_when_logged_in(utils):
    """历史文件没有侧车：强制认证下**至少要登录**。

    F17：旧行为是「无归属 → 无条件放行」，等于任何登录用户只要知道文件名
    （uuid4 hex，但会经日志/分享链接泄露）就能读别人的历史上传。
    现在收紧为「强制认证时要求已登录」—— 不再对匿名开放，同时不锁死老文件。
    """
    f = utils["tmp"] / "old.mp4"
    f.write_text("data")
    assert utils["get_upload_owner"](f) is None
    # 登录用户可以读（不锁死老部署）
    assert utils["can_read_upload"](f, U(1), auth_enforced=True) is True
    # 匿名用户不能读（F17 修复点）
    assert utils["can_read_upload"](f, None, auth_enforced=True) is False


def test_upload_without_owner_readable_when_auth_disabled(utils):
    """未开启鉴权的单人部署：无归属文件仍放行（与改动前一致）。"""
    f = utils["tmp"] / "old2.mp4"
    f.write_text("data")
    assert utils["can_read_upload"](f, None, auth_enforced=False) is True


def test_owner_can_read_own_upload(utils):
    f = utils["tmp"] / "mine.mp4"
    f.write_text("data")
    utils["set_upload_owner"](f, 7)
    assert utils["can_read_upload"](f, U(7), auth_enforced=True) is True


def test_other_user_cannot_read_upload(utils):
    """R2 核心：登录用户不能读他人的上传文件。"""
    f = utils["tmp"] / "others.mp4"
    f.write_text("data")
    utils["set_upload_owner"](f, 7)
    assert utils["can_read_upload"](f, U(8), auth_enforced=True) is False


def test_admin_can_read_any_upload(utils):
    f = utils["tmp"] / "x.mp4"
    f.write_text("data")
    utils["set_upload_owner"](f, 7)
    assert utils["can_read_upload"](f, U(99, admin=True), auth_enforced=True) is True


def test_anonymous_cannot_read_owned_upload_when_enforced(utils):
    f = utils["tmp"] / "x.mp4"
    f.write_text("data")
    utils["set_upload_owner"](f, 7)
    assert utils["can_read_upload"](f, None, auth_enforced=True) is False


def test_auth_disabled_relaxes_ownership(utils):
    """单机自托管（AUTH_ENFORCE=0）不因归属而拒绝。"""
    f = utils["tmp"] / "x.mp4"
    f.write_text("data")
    utils["set_upload_owner"](f, 7)
    assert utils["can_read_upload"](f, U(8), auth_enforced=False) is True


def test_owner_sidecar_content(utils):
    f = utils["tmp"] / "x.mp4"
    f.write_text("data")
    utils["set_upload_owner"](f, 42)
    sidecar = f.with_name(f.name + ".owner")
    assert sidecar.exists()
    assert sidecar.read_text().strip() == "42"


def test_set_owner_none_is_noop(utils):
    """匿名上传不写侧车（保持历史行为：无归属=放行）。"""
    f = utils["tmp"] / "anon.mp4"
    f.write_text("data")
    utils["set_upload_owner"](f, None)
    assert utils["get_upload_owner"](f) is None
    assert not f.with_name(f.name + ".owner").exists()


# ---------------------------------------------------------------------------
# 端点级冒烟：确保路由函数能被真实调用（防 NameError 这类问题）
# ---------------------------------------------------------------------------
def test_serve_static_has_required_imports():
    """`/static` 路由体和 `/uploads` 路由体用到的名字必须都已导入。

    回归背景：把 `/static` 改成鉴权路由时用了 `Path`，但 main.py 漏了
    `from pathlib import Path`。单元测试只覆盖工具函数，没发现这个问题，
    线上登录用户访问图片直接 500（NameError）。这里做静态名检查——
    比完整 import main.py 轻量（后者依赖 create_app 等一堆初始化）。
    """
    import ast

    src = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 收集模块级已导入的名字
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)

    # 收集这两个路由函数体内实际引用的名字
    targets = {"serve_static", "serve_upload"}
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    used.add(sub.id)

    # Path 是本次回归的关键点
    if "Path" in used:
        assert "Path" in imported, (
            "main.py 的路由体用了 Path 但没导入 —— 这会导致线上 500"
        )
