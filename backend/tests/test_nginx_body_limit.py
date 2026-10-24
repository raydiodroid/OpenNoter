"""P2：nginx client_max_body_size 与 UPLOAD_MAX 对齐，勿再放行 10G。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_nginx_body_limit_aligned_with_upload():
    for name in ("nginx/default.conf", "nginx/standalone.conf"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "client_max_body_size 10G" not in text, name
        assert "client_max_body_size 512m" in text, name
