"""Cookie 加密存储的回归测试。

平台 Cookie（尤其 B 站 `SESSDATA`）是长期登录凭证，落盘必须加密。
这些测试确保：
  1. 写盘后磁盘上**看不到明文**
  2. 读回内容与写入一致（Cookie 过滤策略仍生效）
  3. 历史明文能兼容读取，且可被显式迁移成密文
  4. 密钥变化/密文损坏时不会把密文当明文用
  5. 文件权限 0600、写入原子
"""

import json
import os
import stat
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
def cookie_mgr(tmp_path, monkeypatch):
    """隔离的 CookieConfigManager + 固定密钥。

    与其它测试隔离：snapshot/restore app.* 模块，避免互相污染。
    """
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    monkeypatch.setenv("OPENNOTER_SECRET_KEY", "unit-test-secret-key-0001")

    try:
        _install_app_namespace()
        from app.services.cookie_manager import CookieConfigManager

        path = tmp_path / "config" / "downloader.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        yield CookieConfigManager(str(path)), path
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# ---------------------------------------------------------------------------
# 加密落盘
# ---------------------------------------------------------------------------
def test_cookie_is_encrypted_on_disk(cookie_mgr):
    mgr, path = cookie_mgr
    mgr.set("bilibili", "SESSDATA=supersecret123; bili_jct=csrf456; DedeUserID=42")

    raw = path.read_text(encoding="utf-8")
    assert "enc:v1:" in raw, "应写成密文"
    assert "SESSDATA" not in raw, "磁盘上不应出现明文键名"
    assert "supersecret123" not in raw, "磁盘上不应出现明文值"
    assert "csrf456" not in raw


def test_cookie_roundtrip(cookie_mgr):
    mgr, _ = cookie_mgr
    original = "SESSDATA=abc; bili_jct=def; DedeUserID=4251178"
    mgr.set("bilibili", original)

    got = mgr.get("bilibili")
    assert got is not None
    assert "SESSDATA=abc" in got
    assert "DedeUserID=4251178" in got


def test_file_permissions_are_0600(cookie_mgr):
    mgr, path = cookie_mgr
    mgr.set("bilibili", "SESSDATA=x; bili_jct=y; DedeUserID=1")

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"权限应为 0600，实际 {oct(mode)}"


def test_no_temp_file_left_behind(cookie_mgr):
    mgr, path = cookie_mgr
    mgr.set("bilibili", "SESSDATA=x; bili_jct=y; DedeUserID=1")
    leftovers = list(path.parent.glob("*.tmp"))
    assert leftovers == [], f"不应残留临时文件: {leftovers}"


# ---------------------------------------------------------------------------
# 历史明文兼容与迁移
# ---------------------------------------------------------------------------
def test_legacy_plaintext_is_readable(cookie_mgr):
    mgr, path = cookie_mgr
    path.write_text(
        json.dumps({"bilibili": {"cookie": "SESSDATA=legacy; bili_jct=old; DedeUserID=9"}}),
        encoding="utf-8",
    )

    got = mgr.get("bilibili")
    assert got is not None
    assert "SESSDATA=legacy" in got


def test_migrate_plaintext_encrypts_existing(cookie_mgr):
    mgr, path = cookie_mgr
    path.write_text(
        json.dumps(
            {
                "bilibili": {"cookie": "SESSDATA=legacy; bili_jct=old; DedeUserID=9"},
                "douyin": {"cookie": "sessionid=abc; ttwid=xyz"},
            }
        ),
        encoding="utf-8",
    )

    migrated = mgr.migrate_plaintext()
    assert migrated == 2

    raw = path.read_text(encoding="utf-8")
    assert "SESSDATA" not in raw
    assert "sessionid" not in raw
    assert raw.count("enc:v1:") == 2

    # 迁移后仍可读
    assert "SESSDATA=legacy" in (mgr.get("bilibili") or "")
    assert "sessionid=abc" in (mgr.get("douyin") or "")


def test_migrate_is_idempotent(cookie_mgr):
    mgr, path = cookie_mgr
    path.write_text(
        json.dumps({"bilibili": {"cookie": "SESSDATA=x; bili_jct=y; DedeUserID=1"}}),
        encoding="utf-8",
    )
    assert mgr.migrate_plaintext() == 1
    assert mgr.migrate_plaintext() == 0, "已加密的数据不应重复迁移"


def test_migrate_on_empty_file(cookie_mgr):
    mgr, path = cookie_mgr
    path.write_text("{}", encoding="utf-8")
    assert mgr.migrate_plaintext() == 0


# ---------------------------------------------------------------------------
# 密钥变化 / 损坏
# ---------------------------------------------------------------------------
def test_wrong_key_does_not_return_ciphertext(tmp_path, monkeypatch):
    """换密钥后读不出旧密文时，必须当作「未配置」而不是把密文当 Cookie。"""
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    for name in saved:
        sys.modules.pop(name, None)

    try:
        path = tmp_path / "downloader.json"

        monkeypatch.setenv("OPENNOTER_SECRET_KEY", "first-secret-key")
        _install_app_namespace()
        from app.services.cookie_manager import CookieConfigManager

        CookieConfigManager(str(path)).set(
            "bilibili", "SESSDATA=abc; bili_jct=def; DedeUserID=1"
        )

        # 换密钥后重新 import（密钥在调用时读取，其实不用重载模块）
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        _install_app_namespace()
        monkeypatch.setenv("OPENNOTER_SECRET_KEY", "second-different-key")
        from app.services.cookie_manager import CookieConfigManager as C2

        got = C2(str(path)).get("bilibili")
        assert got is None, "密钥不匹配时应返回 None，绝不能返回密文"
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_corrupt_ciphertext_is_ignored(cookie_mgr):
    mgr, path = cookie_mgr
    path.write_text(
        json.dumps({"bilibili": {"cookie": "enc:v1:this-is-not-valid-base64"}}),
        encoding="utf-8",
    )
    assert mgr.get("bilibili") is None


# ---------------------------------------------------------------------------
# 生产路径解析策略仍生效
# ---------------------------------------------------------------------------
def test_cookie_filter_policy_still_applied(cookie_mgr):
    """加密不能绕过 Cookie 白名单过滤（比如 msToken 应被丢弃）。"""
    mgr, path = cookie_mgr
    # msToken 是每次请求动态生成的，策略层会过滤掉
    mgr.set("douyin", "sessionid=keepme; msToken=dropme")

    got = mgr.get("douyin") or ""
    assert "sessionid=keepme" in got
    assert "msToken" not in got
