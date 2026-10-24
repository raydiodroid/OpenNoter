"""CORS Origin 白名单构造。

生产默认：只放行 localhost / Tauri + 环境变量里显式列出的扩展 ID。
开发需要「任意 chrome-extension://」时，设 OPENNOTER_CORS_ALLOW_ANY_EXTENSION=true。
不要在生产打开「任意扩展」——恶意扩展也能同源调用带 Cookie 的 API。
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from app.utils.env_compat import getenv, getbool

# 本地 web + Tauri 各平台 webview origin（与历史行为一致）
_LOCAL_WEB_AND_TAURI = (
    r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"
    r"|^tauri://localhost$"
    r"|^https?://tauri\.localhost$"
)

# 开发版 unpacked 扩展 ID 不固定；仅在显式放宽时使用
_ANY_CHROME_EXTENSION = r"^chrome-extension://[a-z]+$"
_ANY_MOZ_EXTENSION = r"^moz-extension://.+$"


def _parse_extension_ids(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        # 允许用户写成 chrome-extension://id 或裸 id / uuid
        for prefix in ("chrome-extension://", "moz-extension://"):
            if item.lower().startswith(prefix):
                item = item[len(prefix) :]
                break
        item = item.strip().rstrip("/")
        if item:
            out.append(item)
    return out


def _extension_id_alternation(ids: Iterable[str]) -> str:
    escaped = [re.escape(i) for i in ids]
    if not escaped:
        return ""
    if len(escaped) == 1:
        return escaped[0]
    return "(?:" + "|".join(escaped) + ")"


def build_cors_origin_regex(
    *,
    extension_ids: Optional[str] = None,
    allow_any_extension: Optional[bool] = None,
) -> str:
    """拼 CORS allow_origin_regex。

    参数为 None 时从环境变量读取：
      OPENNOTER_CORS_EXTENSION_IDS     逗号分隔的扩展 ID（生产白名单）
      OPENNOTER_CORS_ALLOW_ANY_EXTENSION  true 时放行任意 chrome/moz 扩展源（仅开发）
    """
    if extension_ids is None:
        extension_ids = getenv("CORS_EXTENSION_IDS", "") or ""
    if allow_any_extension is None:
        allow_any_extension = getbool("CORS_ALLOW_ANY_EXTENSION", False)

    parts: list[str] = [_LOCAL_WEB_AND_TAURI]

    if allow_any_extension:
        parts.append(_ANY_CHROME_EXTENSION)
        parts.append(_ANY_MOZ_EXTENSION)
    else:
        ids = _parse_extension_ids(extension_ids)
        alt = _extension_id_alternation(ids)
        if alt:
            parts.append(rf"^chrome-extension://{alt}$")
            parts.append(rf"^moz-extension://{alt}$")

    return "|".join(f"(?:{p})" for p in parts)
