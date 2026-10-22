"""P2：probe_endpoint 复用 assert_safe_url，缩管理员内网探测面。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

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
def probe_mod(monkeypatch):
    saved = {
        name: mod for name, mod in sys.modules.items() if name == "app" or name.startswith("app.")
    }
    for name in list(saved):
        sys.modules.pop(name, None)
    monkeypatch.delenv("OPENNOTER_PROBE_ALLOW_PRIVATE", raising=False)
    monkeypatch.delenv("BILINOTE_PROBE_ALLOW_PRIVATE", raising=False)
    try:
        _install_app_namespace()
        import importlib.util

        target = BACKEND_DIR / "app" / "routers" / "provider.py"
        spec = importlib.util.spec_from_file_location("app.routers.provider", target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["app.routers.provider"] = mod
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _call(mod, base_url: str):
    req = mod.EndpointProbeRequest(base_url=base_url, api_key="")
    admin = MagicMock()
    return mod.probe_endpoint(req, admin)


def test_probe_blocks_metadata_ip(probe_mod):
    resp = _call(probe_mod, "http://169.254.169.254/latest")
    body = resp.body if hasattr(resp, "body") else None
    # R.success 返回 JSONResponse；取 data
    import json

    payload = json.loads(resp.body.decode() if hasattr(resp, "body") else resp)
    data = payload.get("data") or payload
    assert data["ok"] is False
    assert "不安全" in data["message"] or "内网" in data["message"]


def test_probe_blocks_loopback(probe_mod):
    import json

    resp = _call(probe_mod, "http://127.0.0.1:11434")
    payload = json.loads(resp.body.decode())
    data = payload["data"]
    assert data["ok"] is False
    assert "不安全" in data["message"] or "内网" in data["message"]


def test_probe_allow_private_skips_ip_check(probe_mod, monkeypatch):
    import json

    monkeypatch.setenv("OPENNOTER_PROBE_ALLOW_PRIVATE", "true")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": [{"id": "llama"}]}
    with patch("requests.get", return_value=fake) as get:
        resp = _call(probe_mod, "http://127.0.0.1:11434")
    payload = json.loads(resp.body.decode())
    assert payload["data"]["ok"] is True
    assert payload["data"]["count"] == 1
    get.assert_called_once()
    assert get.call_args[0][0] == "http://127.0.0.1:11434/models"


def test_probe_allows_public_https(probe_mod):
    import json

    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": [{"id": "gpt"}, {"id": "o1"}]}
    with patch("requests.get", return_value=fake):
        resp = _call(probe_mod, "https://api.openai.com/v1")
    payload = json.loads(resp.body.decode())
    assert payload["data"]["ok"] is True
    assert payload["data"]["count"] == 2


def test_probe_blocks_file_scheme(probe_mod):
    import json

    resp = _call(probe_mod, "file:///etc/passwd")
    payload = json.loads(resp.body.decode())
    assert payload["data"]["ok"] is False
