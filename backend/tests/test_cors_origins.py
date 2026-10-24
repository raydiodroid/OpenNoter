"""P2：生产 CORS 固定扩展 ID；开发可用环境变量放宽。"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def cors_mod(monkeypatch):
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in list(saved):
        sys.modules.pop(name, None)
    for key in list(__import__("os").environ):
        if "CORS" in key:
            monkeypatch.delenv(key, raising=False)
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(BACKEND_DIR / "app")]
    pkg.__package__ = "app"
    sys.modules["app"] = pkg
    from app.utils import cors_origins

    yield cors_origins
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def _match(regex: str, origin: str) -> bool:
    return re.match(regex, origin) is not None


def test_default_blocks_arbitrary_extension(cors_mod):
    regex = cors_mod.build_cors_origin_regex(extension_ids="", allow_any_extension=False)
    assert not _match(regex, "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef")
    assert _match(regex, "http://localhost:3015")
    assert _match(regex, "tauri://localhost")


def test_whitelist_allows_listed_ids_only(cors_mod):
    regex = cors_mod.build_cors_origin_regex(
        extension_ids="abcdefghijklmnopqrstuvwxyzabcdef,otheridabcdefghijklmnopqrs",
        allow_any_extension=False,
    )
    assert _match(regex, "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef")
    assert _match(regex, "moz-extension://abcdefghijklmnopqrstuvwxyzabcdef")
    assert not _match(regex, "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")


def test_allow_any_extension_dev_mode(cors_mod):
    regex = cors_mod.build_cors_origin_regex(extension_ids="", allow_any_extension=True)
    assert _match(regex, "chrome-extension://abcdefghijklmnopabcdefghijklmnop")
    assert _match(regex, "moz-extension://uuid-here")


def test_parse_full_origin_in_env(cors_mod):
    regex = cors_mod.build_cors_origin_regex(
        extension_ids="chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef",
        allow_any_extension=False,
    )
    assert _match(regex, "chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef")
