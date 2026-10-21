"""环境变量读取：统一处理 OpenNoter 前缀与旧 BiliNote 前缀的兼容。

## 背景

项目从 BiliNote 更名为 OpenNoter，环境变量前缀也由 `BILINOTE_` 改为 `OPENNOTER_`。
但已经部署的实例，`.env` 里写的仍是旧前缀；直接改读新前缀会让它们**静默失效**——
例如 `BILINOTE_JWT_SECRET` 读不到就会重新生成随机密钥，导致所有登录态作废。

所以这里统一收口：**新前缀优先，旧前缀兜底**，并在检测到旧前缀时打一条警告，
提示用户迁移。这样改名对存量部署是平滑的。

## 用法

    from app.utils.env_compat import getenv
    secret = getenv("JWT_SECRET", "")

等价于先读 `OPENNOTER_JWT_SECRET`，没有再读 `BILINOTE_JWT_SECRET`。
"""

from __future__ import annotations

import os

NEW_PREFIX = "OPENNOTER_"
LEGACY_PREFIX = "BILINOTE_"

# 同一条警告只打一次，避免轮询接口刷屏
_warned: set[str] = set()


def getenv(name: str, default: str | None = None) -> str | None:
    """读环境变量，`OPENNOTER_<name>` 优先，`BILINOTE_<name>` 兜底。

    :param name: 不含前缀的名字，如 "JWT_SECRET"
    :param default: 两者都不存在时的返回值
    """
    new_key = f"{NEW_PREFIX}{name}"
    legacy_key = f"{LEGACY_PREFIX}{name}"

    value = os.getenv(new_key)
    if value is not None:
        return value

    legacy = os.getenv(legacy_key)
    if legacy is not None:
        if legacy_key not in _warned:
            _warned.add(legacy_key)
            try:
                from app.utils.logger import get_logger

                get_logger(__name__).warning(
                    f"检测到旧环境变量 {legacy_key}，请迁移为 {new_key}（当前仍兼容）"
                )
            except Exception:
                pass
        return legacy

    return default


def getint(name: str, default: int) -> int:
    """读整数型环境变量，兼容旧前缀。解析失败时返回 default。"""
    raw = getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def getbool(name: str, default: bool = False) -> bool:
    """读布尔型环境变量（"1"/"true"/"yes"/"on" 为真）。"""
    raw = getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
