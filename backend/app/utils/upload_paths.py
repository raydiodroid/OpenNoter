"""上传目录沙箱：文件名净化、扩展名/大小限制、路径必须落在 uploads 根内。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

# 允许的媒体扩展（本地上传走转写链路，非媒体无意义且扩大攻击面）
ALLOWED_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".webm",
        ".mkv",
        ".mov",
        ".avi",
        ".m4v",
        ".mp3",
        ".wav",
        ".m4a",
        ".flac",
        ".aac",
        ".ogg",
        ".opus",
    }
)


def uploads_dir() -> Path:
    """uploads 根目录（相对路径按进程 CWD 解析）。"""
    return Path(os.getenv("UPLOAD_DIR", "uploads"))


def uploads_root() -> Path:
    return uploads_dir().resolve()


def max_upload_bytes() -> int:
    mb = int(os.getenv("UPLOAD_MAX_SIZE_MB", "500") or "500")
    if mb <= 0:
        return 0  # 0 = 不限制（仅测试/特殊部署）
    return mb * 1024 * 1024


def sanitize_store_name(original_filename: str | None) -> str:
    """丢掉路径成分，校验扩展名，返回 UUID 存储名（保留安全扩展名）。"""
    raw = Path(original_filename or "upload.bin").name
    # 再剥一层，防奇怪的 ``..`` 文件名
    raw = Path(raw).name
    ext = Path(raw).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不允许的文件类型: {ext or '(无扩展名)'}")
    return f"{uuid.uuid4().hex}{ext}"


def resolve_under_uploads(url_or_path: str) -> Path:
    """把 ``/uploads/xxx`` 或本地路径解析成绝对路径，且必须仍在 uploads 根下。

    用于 LocalDownloader 与下载接口，堵住 ``/uploads/../etc/passwd`` 一类穿越。
    """
    if not url_or_path or not str(url_or_path).strip():
        raise ValueError("空路径")

    s = str(url_or_path).strip()
    root = uploads_root()

    if s.startswith("/uploads/") or s.startswith("uploads/"):
        # 只取 uploads/ 之后的相对段；禁止绝对化穿越
        rel = s.split("uploads/", 1)[1]
        # 拒绝空段与纯 ``.``
        if not rel or rel in (".",):
            raise ValueError("非法 uploads 路径")
        candidate = (root / rel).resolve()
    else:
        candidate = Path(s).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("路径超出 uploads 目录") from exc

    return candidate
