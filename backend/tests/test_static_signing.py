"""F9：截图 per-task 长期令牌的回归测试。

## 背景（为什么这些测试必须存在）

`/static/screenshots/<task_id>/...` 的读取凭据是 URL 上的 `?t=`。这条链路上
有三个「写错了也能跑、但上线后才炸」的陷阱，本文件逐条钉死：

1. **不能用 TTL 签名**：截图 URL 在生成笔记时就被写进 markdown，并持久化到
   `note_results/*.json` 与前端 IndexedDB **两处**。带过期时间的签名会在过期后
   永久破图，且无法通过刷新恢复（见 `_verify/FIX-P0-F9-CORRECTION.md`）。
   → `test_token_is_stable_across_calls` / `test_token_has_no_time_component`

2. **验签必须在 `get_optional_user` 之前**：`<img>` 是裸 GET，不带 Cookie /
   Authorization；若先走鉴权，无凭据会当场 401，验签代码永远执行不到。
   → `test_signed_url_works_without_credentials`

3. **失败必须 403 不 401**：前端 `lib/api.ts` 的 401 拦截器会跳登录页，
   返 401 会把「图片链接坏了」误判成「你被踢下线」。
   → `test_bad_token_returns_403_not_401`

另外钉住降级语义：无密钥部署（`AUTH_ENFORCE=0` + 没配 secret）要**不签名、
不 500**，但校验端必须 fail-closed。
"""

from __future__ import annotations

import re
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


def _reset_app_modules():
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)


@pytest.fixture()
def signing(monkeypatch):
    """干净的 app 命名空间 + 固定密钥。"""
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    _reset_app_modules()
    monkeypatch.setenv("OPENNOTER_SECRET_KEY", "unit-test-static-signing-key")
    monkeypatch.delenv("OPENNOTER_JWT_SECRET", raising=False)
    try:
        _install_app_namespace()
        from app.utils import static_signing

        yield static_signing
    finally:
        _reset_app_modules()
        sys.modules.update(saved)


@pytest.fixture()
def signing_without_key(monkeypatch):
    """模拟「没配任何密钥」的单人自托管部署。"""
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    _reset_app_modules()
    for key in (
        "OPENNOTER_SECRET_KEY",
        "OPENNOTER_JWT_SECRET",
        "BILINOTE_SECRET_KEY",
        "BILINOTE_JWT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    try:
        _install_app_namespace()
        from app.utils import static_signing

        yield static_signing
    finally:
        _reset_app_modules()
        sys.modules.update(saved)


# ---------------------------------------------------------------------------
# 1. 签发
# ---------------------------------------------------------------------------
def test_signs_screenshot_url(signing):
    url = signing.sign_static_url("/static/screenshots/task-abc/shot_000.jpg")
    assert "?t=" in url
    token = url.split("?t=", 1)[1]
    # 令牌格式契约：base64url（`-_`），去尾部 `=`，43 字符（32 字节 SHA256）
    assert len(token) == 43, f"令牌长度应为 43，实际 {len(token)}：{token}"
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token), token


def test_token_is_stable_across_calls(signing):
    """**核心回归**：同一 task 每次签出同一个令牌（= 不含时间成分）。

    如果这里变成 flaky/每次不同，说明有人给签名加了 `exp`/`nonce`／时间戳，
    那会让已经持久化的历史 markdown 全部破图。
    """
    a = signing.sign_payload("task-abc")
    b = signing.sign_payload("task-abc")
    assert a == b
    assert a is not None


def test_token_has_no_time_component(signing):
    """令牌里不能有任何可解析的时间字段（43 字符 base64url，无分隔符）。"""
    token = signing.sign_payload("task-abc")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    assert "." not in token and ":" not in token and "=" not in token


def test_sign_is_idempotent(signing):
    """对已签名的 URL 再签一次，结果逐字节相同（迁移脚本可反复跑）。"""
    once = signing.sign_static_url("/static/screenshots/T/a.jpg")
    twice = signing.sign_static_url(once)
    assert once == twice


def test_existing_query_uses_ampersand(signing):
    """已有 `?v=2` 时必须用 `&t=` 追加，不能拼成 `?v=2?t=`（会变成无效 query）。"""
    signed = signing.sign_static_url("/static/screenshots/T/a.jpg?v=2")
    assert "?v=2&t=" in signed, signed
    assert signed.count("?") == 1, f"只能有一个 ?：{signed}"
    # 原有参数不能被弄丢
    assert "v=2" in signed


def test_unprotected_paths_untouched(signing):
    """不需要令牌的路径原样返回。

    受保护的两类是：`/static/screenshots/<task>/<file>`（截图）与
    `/static/cover/<file>`（本地视频封面）。其余一律不动 —— 尤其是
    `/uploads/`（走独立鉴权）与旧格式截图（无 task 子目录，无法绑定）。
    """
    for url in (
        "/uploads/a.mp4",
        "/static/screenshots/loose.jpg",  # 旧格式，无 task 子目录
        "/static/other/T/a.jpg",
        "",
    ):
        assert signing.sign_static_url(url) == url, url


def test_cover_paths_are_signed(signing):
    """本地视频封面也要签名。

    封面文件名是 `<上传名>_cover.jpg`，上传名是 uuid4，不可猜；但它同样
    需要交给 `<img>`，桌面端/扩展带不上凭据。若不签名，本地视频封面在
    强制认证下会 401 —— 这正是 X1 修好之后剩下的最后一个缺口。
    """
    signed = signing.sign_static_url("/static/cover/z.jpg")
    assert signed.startswith("/static/cover/z.jpg?t="), signed
    token = signed.split("?t=", 1)[1]
    assert signing.verify_cover_token("z.jpg", token)
    # 封面令牌不能跨文件用
    assert not signing.verify_cover_token("other.jpg", token)
    # 封面令牌也不能拿去读截图（两类令牌前缀不同）
    assert not signing.verify_token("z", token)


def test_flat_screenshot_is_signed(signing):
    """**扁平截图**（历史格式）也必须签名。

    ## 为什么这条特别重要

    生产环境实测：`static/screenshots/` 下 123 张截图**全是扁平格式**
    （`screenshot_000_<uuid>.jpg`），0 个子目录。早期版本就是这么存的。

    这类路径**反推不出 task_id**，所以最初的实现直接返回 None（不签名）
    ⇒ 升级后所有历史笔记的截图在强制认证下全 401 —— 那比"不修"更糟
    （用户打开旧笔记全是破图）。

    修法：与封面一致，按**文件名**绑定令牌。文件名含 uuid4，不可枚举。
    """
    url = "/static/screenshots/screenshot_000_0d4932ef-209f-48a8-bee4-3637a778a23c.jpg"
    signed = signing.sign_static_url(url)
    assert "?t=" in signed, f"扁平截图未被签名：{signed}"

    token = signed.split("?t=", 1)[1]
    name = url.rsplit("/", 1)[1]
    assert signing.verify_flat_screenshot_token(name, token)

    # 不能跨文件用
    assert not signing.verify_flat_screenshot_token("screenshot_001_other.jpg", token)


def test_flat_screenshot_token_isolated_from_task_token(signing):
    """扁平截图令牌与 per-task 令牌互不通用（前缀不同）。"""
    flat = signing.sign_static_url("/static/screenshots/screenshot_000_a.jpg")
    task = signing.sign_static_url("/static/screenshots/task-1/shot.jpg")
    flat_tok = flat.split("?t=", 1)[1]
    task_tok = task.split("?t=", 1)[1]

    assert not signing.verify_token("task-1", flat_tok), "扁平令牌不应能读 task 目录"
    assert not signing.verify_flat_screenshot_token("screenshot_000_a.jpg", task_tok)


def test_flat_screenshot_only_for_screenshot_prefix(signing):
    """只有 `screenshot_` 开头的扁平文件名才按名签名，避免把任意文件纳入。"""
    # 非截图命名的扁平文件不应被签名
    for url in (
        "/static/screenshots/random-file.jpg",
        "/static/screenshots/notes.txt",
    ):
        assert signing.sign_static_url(url) == url, url


def test_sign_never_raises_on_garbage(signing):
    """签名在笔记生成主流程里被调用 —— 任何输入都不许抛异常。"""
    for bad in (None, 123, "/static/screenshots/", "/static/screenshots//a.jpg", "?t="):
        signer = signing.sign_static_url
        result = signer(bad)
        assert result == bad or isinstance(result, str)


# ---------------------------------------------------------------------------
# 2. 校验
# ---------------------------------------------------------------------------
def test_verify_accepts_matching_token(signing):
    token = signing.sign_payload("task-abc")
    assert signing.verify_token("task-abc", token) is True


def test_verify_rejects_cross_task_token(signing):
    """令牌绑定 task_id：换 task 必须失效，否则一个 token 能读全站截图。"""
    token = signing.sign_payload("task-abc")
    assert signing.verify_token("task-other", token) is False


@pytest.mark.parametrize("bad", ["", "AAAA", "x" * 43])
def test_verify_rejects_wrong_token(signing, bad):
    assert signing.verify_token("task-abc", bad) is False


def test_verify_rejects_tampered_token(signing):
    token = signing.sign_payload("task-abc")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert signing.verify_token("task-abc", tampered) is False


def test_has_token_distinguishes_present_from_absent(signing):
    """`?t=` 空值属于「带了令牌但无效」（→403），不能算「没带」（→401）。"""
    assert signing.has_token({}) is False
    assert signing.has_token({"t": ""}) is True
    assert signing.has_token({"t": "x"}) is True


# ---------------------------------------------------------------------------
# 3. 无密钥降级
# ---------------------------------------------------------------------------
def test_no_key_does_not_sign(signing_without_key):
    """无密钥部署：URL 不带 `t`，行为与改动前完全一致（不能 fail-closed）。"""
    url = "/static/screenshots/T/a.jpg"
    assert signing_without_key.sign_static_url(url) == url
    assert signing_without_key.sign_payload("T") is None


def test_no_key_verify_is_fail_closed(signing_without_key):
    """**校验端**不能降级：算不出期望值 ⇒ 无法证明合法 ⇒ 拒绝。

    若这里返回 True，「AUTH_ENFORCE=1 + 密钥暂时不可用」的窗口会变成
    「随便什么 token 都放行」，等于静默摘掉鉴权。
    """
    assert signing_without_key.verify_token("T", "anything") is False
    assert signing_without_key.verify_token("T", "") is False


# ---------------------------------------------------------------------------
# 4. 端到端：真实 serve_static（顺序 + 状态码）
# ---------------------------------------------------------------------------
@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """起一个真实 FastAPI TestClient，截图目录指向 tmp。"""
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    _reset_app_modules()

    static_root = tmp_path / "static"
    shots = static_root / "screenshots" / "task-e2e"
    shots.mkdir(parents=True)
    (shots / "shot.jpg").write_bytes(b"\xff\xd8fakejpeg")

    monkeypatch.setenv("OPENNOTER_SECRET_KEY", "unit-test-static-signing-key-e2e")
    monkeypatch.setenv("OPENNOTER_AUTH_ENFORCE", "1")
    monkeypatch.setenv("NOTE_OUTPUT_DIR", str(tmp_path / "note_results"))
    monkeypatch.chdir(tmp_path)

    import main as main_module

    # main.py 的 static_dir 是模块级常量，测试里无法改 cwd 生效（已 import 过），
    # 直接覆盖它指向 tmp 目录。
    monkeypatch.setattr(main_module, "static_dir", str(static_root), raising=False)

    from fastapi.testclient import TestClient

    try:
        yield TestClient(main_module.app, raise_server_exceptions=False), static_root
    finally:
        _reset_app_modules()
        sys.modules.update(saved)


def _signed_url(static_root, task_id="task-e2e", name="shot.jpg"):
    from app.utils.static_signing import sign_static_url

    return sign_static_url(f"/static/screenshots/{task_id}/{name}")


def test_signed_url_works_without_credentials(app_client):
    """**致命陷阱回归**：无 Cookie、无 Authorization，仅凭 `?t=` 必须能读到。

    这条如果挂了，说明验签被放到了 `get_optional_user` 之后 —— Tauri / 扩展的
    截图会全部 401，而 diff 看起来「已经加了令牌」。
    """
    client, static_root = app_client
    url = _signed_url(static_root)
    resp = client.get(url)
    assert resp.status_code == 200, f"带合法令牌应 200，实际 {resp.status_code}：{resp.text[:200]}"


def test_unsigned_request_without_credentials_is_401(app_client):
    """没带令牌又没凭据：仍然 401（不能因为加了令牌逻辑就把鉴权整个放开）。"""
    client, _ = app_client
    resp = client.get("/static/screenshots/task-e2e/shot.jpg")
    assert resp.status_code == 401


def test_bad_token_returns_403_not_401(app_client):
    """令牌错误必须 403：401 会触发前端跳登录页，把用户踢下线。"""
    client, _ = app_client
    resp = client.get("/static/screenshots/task-e2e/shot.jpg?t=AAAA")
    assert resp.status_code == 403, f"应 403，实际 {resp.status_code}"


def test_empty_token_returns_403_not_401(app_client):
    """`?t=` 空值同样是「带了令牌但无效」→ 403。"""
    client, _ = app_client
    resp = client.get("/static/screenshots/task-e2e/shot.jpg?t=")
    assert resp.status_code == 403, f"应 403，实际 {resp.status_code}"


def test_cross_task_token_returns_403(app_client):
    """令牌不能跨任务用。

    注意：现在 `serve_static` **先 resolve 再反推 task_id**（防 `%2e%2e`
    绕过），所以当目标任务的目录/文件不存在时，会在 resolve 阶段直接 404，
    到不了验签那一层。两种结果都算「拒绝」，断言放宽为 403/404。
    """
    client, static_root = app_client
    url = _signed_url(static_root).replace("task-e2e", "task-other")
    resp = client.get(url)
    assert resp.status_code in (403, 404), resp.status_code
    assert resp.status_code != 200


def test_cross_task_token_403_when_file_exists(app_client):
    """目标任务文件存在时，用别家的令牌必须是 403（不是 404、更不是 200）。"""
    client, static_root = app_client
    other = static_root / "screenshots" / "task-other"
    other.mkdir(parents=True, exist_ok=True)
    (other / "shot.jpg").write_bytes(b"other-secret")

    url = _signed_url(static_root).replace("task-e2e", "task-other")
    resp = client.get(url)
    assert resp.status_code == 403, resp.status_code
    # 且绝不能泄漏对方内容
    assert b"other-secret" not in resp.content


def test_encoded_traversal_cannot_escape_task_scope(app_client):
    """`%2e%2e` 编码穿越不能绕过令牌作用域（P0 回归锁）。

    历史 bug：授权用 URL 字面量 `parts[1]`、读取用 resolve 后的真实路径，
    两者不一致 ⇒ 拿 A 的令牌可以读 B 的截图、甚至 static 根下的任意文件。
    修法是**先 resolve 再从真实路径反推 task_id**（授权对象 == 读取对象）。
    """
    client, static_root = app_client
    token = _signed_url(static_root).split("?t=", 1)[1]

    other = static_root / "screenshots" / "task-victim"
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.jpg").write_bytes(b"VICTIM-SECRET")
    (static_root / "top-secret.txt").write_text("TOP-SECRET")

    cases = [
        f"/static/screenshots/task-e2e/%2e%2e/task-victim/secret.jpg?t={token}",
        f"/static/screenshots/task-e2e/%2e%2e/%2e%2e/top-secret.txt?t={token}",
    ]
    for url in cases:
        resp = client.get(url)
        assert resp.status_code != 200, f"{url} 竟然 200"
        assert b"VICTIM-SECRET" not in resp.content
        assert b"TOP-SECRET" not in resp.content


def test_token_does_not_unlock_non_screenshot_paths(app_client):
    """令牌只对 `screenshots/<task>/<file>` 生效，不能拿来读 cover/。"""
    client, static_root = app_client
    cover = static_root / "cover"
    cover.mkdir(parents=True, exist_ok=True)
    (cover / "c.jpg").write_bytes(b"x")
    from app.utils.static_signing import sign_payload

    token = sign_payload("task-e2e")
    resp = client.get(f"/static/cover/c.jpg?t={token}")
    assert resp.status_code == 403


def test_path_traversal_still_blocked_with_valid_token(app_client):
    """令牌不能绕过路径沙箱。"""
    client, static_root = app_client
    token = _signed_url(static_root).split("?t=", 1)[1]
    resp = client.get(f"/static/screenshots/task-e2e/../../../etc/passwd?t={token}")
    assert resp.status_code in (404, 403)
    assert resp.status_code != 200


def test_same_url_still_works_after_repeat_requests(app_client):
    """令牌不含时间 ⇒ 反复请求都应 200（这就是「不会 TTL 爆炸」的可执行断言）。"""
    client, static_root = app_client
    url = _signed_url(static_root)
    for _ in range(3):
        assert client.get(url).status_code == 200
