"""BYOK 站点级配置：三种部署模式，支持运行时切换。

## 三种模式

| 模式 | 行为 | 典型部署者 |
|---|---|---|
| ``shared``   | 只用站点 key；用户 BYOK 入口完全隐藏 | 个人自托管（默认）|
| ``optional`` | 站点 key 是默认档位；用户可配自己的，配了就用 | 公司/社区内部 |
| ``required`` | 必须配自己的；未配置时提交直接拒绝 | 对外提供服务 |

**默认 ``shared``** 是刻意的：开源项目 clone 下来直接跑，不该因为升级就被要求
配置任何东西。这也是「零破坏承诺」的落点——``mode == "shared"`` 时，凭据解析
走的分支与改造前完全相同。

## 为什么用 JSON 文件而不是环境变量

项目里已有两种运行时配置机制（见 ``utils/env_compat`` 与 ``TranscriberConfigManager``）：

- 环境变量：部署期固定，改了要重启容器；
- JSON 文件：管理员在界面上改，**立即生效**。

「全站切换 BYOK 模式」是运营动作——部署者可能在试运行几天后决定从 ``optional``
切到 ``required``。为此重启容器是不可接受的，所以走 JSON 文件，与
``config/transcriber.json`` 同级、同模式。

## 读取缓存

``get()`` 在每次凭据解析时都会被调用（每个任务的每次 LLM 调用），不能每次都打
IO。这里做 1 秒的进程内缓存：既避免高频读盘，又保证「管理员点击后立即生效」。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---- 模式常量 ----
MODE_SHARED = "shared"
MODE_OPTIONAL = "optional"
MODE_REQUIRED = "required"
ALL_MODES = (MODE_SHARED, MODE_OPTIONAL, MODE_REQUIRED)

# 默认值。文件不存在 / 读失败 / 值非法时一律回退到这里，
# 保证任何异常都不会把部署切到一个「用户配不了 key 又用不了站点 key」的死状态。
DEFAULT_MODE = MODE_SHARED
DEFAULT_ALLOW_CUSTOM_BASE_URL = True

# 1 秒缓存。见模块 docstring 的「读取缓存」。
_CACHE_TTL_SECONDS = 1.0


class ByokConfigManager:
    """BYOK 站点配置（config/byok.json）。"""

    def __init__(self, filepath: str = "config/byok.json"):
        self.path = Path(filepath)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cached: Dict[str, Any] | None = None
        self._cached_at: float = 0.0

    # ---------------- 内部 ----------------

    def _read_raw(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # 文件损坏不该让整个服务起不来
            logger.warning(f"读取 {self.path} 失败，按默认配置处理: {exc}")
            return {}

    def _write_raw(self, data: Dict[str, Any]) -> None:
        # 目录可能在进程运行期间消失（容器里被清、测试里 chdir、运维误删），
        # 所以每次写都要确保父目录存在——只在 __init__ 里建一次不够。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)  # 原子替换，避免读到写了一半的文件

    @staticmethod
    def _normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
        """把任意输入收敛成合法配置。

        非法 mode 回退 shared 而不是抛错：配置错导致全站不可用，
        比「悄悄降级到默认」糟糕得多。
        """
        mode = str(raw.get("mode") or "").strip().lower()
        if mode not in ALL_MODES:
            if mode:
                logger.warning(f"非法的 byok mode={mode!r}，回退 {DEFAULT_MODE}")
            mode = DEFAULT_MODE

        allow_custom = raw.get("allow_custom_base_url", DEFAULT_ALLOW_CUSTOM_BASE_URL)
        if not isinstance(allow_custom, bool):
            allow_custom = DEFAULT_ALLOW_CUSTOM_BASE_URL

        return {
            "mode": mode,
            "allow_custom_base_url": allow_custom,
            "updated_at": raw.get("updated_at"),
        }

    # ---------------- 对外 ----------------

    def get(self) -> Dict[str, Any]:
        """读取当前配置（带 1 秒缓存）。"""
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and (now - self._cached_at) < _CACHE_TTL_SECONDS:
                return dict(self._cached)
            normalized = self._normalize(self._read_raw())
            self._cached = normalized
            self._cached_at = now
            return dict(normalized)

    def get_mode(self) -> str:
        return self.get()["mode"]

    def is_byok_enabled(self) -> bool:
        """用户级 BYOK 是否可用（optional 或 required）。"""
        return self.get_mode() != MODE_SHARED

    def requires_byok(self) -> bool:
        return self.get_mode() == MODE_REQUIRED

    def update(
        self,
        *,
        mode: str | None = None,
        allow_custom_base_url: bool | None = None,
    ) -> Dict[str, Any]:
        """更新配置并立即失效缓存（管理员切换后下一次解析就生效）。"""
        raw = self._read_raw()
        if mode is not None:
            normalized_mode = str(mode).strip().lower()
            if normalized_mode not in ALL_MODES:
                raise ValueError(
                    f"非法的模式: {mode!r}，可选值: {', '.join(ALL_MODES)}"
                )
            raw["mode"] = normalized_mode
        if allow_custom_base_url is not None:
            raw["allow_custom_base_url"] = bool(allow_custom_base_url)

        raw["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        with self._lock:
            self._write_raw(raw)
            self._cached = None  # 失效缓存，保证「立即生效」
            self._cached_at = 0.0

        logger.info(
            f"BYOK 配置已更新: mode={raw.get('mode')}, "
            f"allow_custom_base_url={raw.get('allow_custom_base_url')}"
        )
        return self._normalize(raw)

    def invalidate_cache(self) -> None:
        """测试用：强制下次 get() 重新读盘。"""
        with self._lock:
            self._cached = None
            self._cached_at = 0.0


# 模块级单例。注意：配置路径相对 cwd，与 TranscriberConfigManager 一致。
byok_config = ByokConfigManager()
