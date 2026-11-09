"""BYOK 站点级配置：模型凭据与平台 Cookie **两个独立开关**，支持运行时切换。

## 为什么拆成两个

历史上这里只有**一个** ``mode``，同时管死模型凭据和个人 Cookie。但这两件事
的性质完全不同：

| | 模型凭据 | 平台 Cookie |
|---|---|---|
| 本质 | 花钱的**额度** | 登录的**身份** |
| 共用后果 | 花站点的钱 | **用管理员的账号**下载，观看历史混在一起 |
| 缺失影响 | 任务直接失败 | 静默降级（无 Cookie 也能下普通画质） |

所以「要不要让用户自带」这个决定天然是分开的：一个站点可能完全不想承担
API 成本（模型 ``required``），但乐于分享自己的 Cookie（Cookie ``shared``）；
反过来也成立。用一个开关绑死两者，管理员无论选哪个都会有半边是错的。

## 两个开关各自的模式

``model_mode`` —— 模型凭据（LLM / 转写）

| 模式 | 行为 | 典型部署者 |
|---|---|---|
| ``shared``   | 只用站点 key；用户 BYOK 入口完全隐藏 | 个人自托管（默认）|
| ``optional`` | 站点 key 是默认档位；用户可配自己的，配了就用 | 公司/社区内部 |
| ``required`` | 必须配自己的；未配置时提交直接拒绝 | 对外提供服务 |

``cookie_mode`` —— 平台 Cookie

| 模式 | 行为 |
|---|---|
| ``shared``   | 只用站点 Cookie；用户的 Cookie 入口隐藏 |
| ``optional`` | 站点 Cookie 是默认；用户可配自己的，配了就用 |

**Cookie 没有 ``required``**：它不花站点的钱，强制要求用户登自己的账号在语义上
不成立（没配 Cookie 也能正常下载，只是画质低些）。这个不对称是刻意的，见
``COOKIE_MODES``。

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

## 与其他配置的关系

文件不存在时，``mode`` 的初始值可以由环境变量 ``OPENNOTER_BYOK_MODE`` 指定——
Docker 部署时改 ``.env`` 比往容器里塞一个 JSON 文件方便。文件一旦存在，
**文件优先**（避免「界面改完被 env 覆盖」这种困惑）。

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

#: 模型凭据支持的三种模式
ALL_MODES = (MODE_SHARED, MODE_OPTIONAL, MODE_REQUIRED)

#: 平台 Cookie 只支持两种——**没有 required**。
#: 原因：Cookie 不消耗站点额度，没配也能正常下载（只是画质低），
#: 强制用户登自己的账号在语义上不成立。
COOKIE_MODES = (MODE_SHARED, MODE_OPTIONAL)

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

        非法值回退默认而不是抛错：配置错导致全站不可用，
        比「悄悄降级到默认」糟糕得多。

        ## 旧配置的迁移

        拆分前只有单个 ``mode``，同时管模型和 Cookie。老文件里通常还留着那个
        字段。迁移规则：**``mode`` 同时作为两者的初值**，然后由各自的新字段覆盖。

        这样升级后行为与升级前**完全一致**（管理员不用重配），而管理员一旦在
        新界面上分别设置过，就以 ``model_mode`` / ``cookie_mode`` 为准。
        """
        legacy = str(raw.get("mode") or "").strip().lower()

        # 文件里没写新字段时，允许用环境变量给初始值（Docker 部署更方便）。
        # 文件一旦写了就以文件为准——否则「界面切换」会被 env 悄悄覆盖。
        if not legacy:
            from app.utils.env_compat import getenv

            env_mode = (getenv("BYOK_MODE", "") or "").strip().lower()
            if env_mode in ALL_MODES:
                legacy = env_mode
            elif env_mode:
                logger.warning(f"环境变量 BYOK_MODE={env_mode!r} 非法，忽略")

        def pick(field: str, allowed: tuple[str, ...], default: str) -> str:
            value = str(raw.get(field) or "").strip().lower()
            if not value:
                value = legacy  # 回落到旧字段
            if value not in allowed:
                if value:
                    # 旧 mode=required 落到这里：Cookie 没有 required，
                    # 但升级前 required 下用户 Cookie 是**可用**的。若回退到
                    # shared，升级会悄悄关掉本来能用的功能，所以收敛到 optional
                    # （语义上最接近：仍然允许用户自带，只是不强制）。
                    if value == MODE_REQUIRED and MODE_OPTIONAL in allowed:
                        logger.info(
                            f"{field}: 旧配置 required 无对应值，收敛为 optional"
                            "（升级前用户 Cookie 可用，不能因拆分而关掉）"
                        )
                        return MODE_OPTIONAL
                    logger.warning(f"非法的 {field}={value!r}，回退 {default}")
                value = default
            return value

        model_mode = pick("model_mode", ALL_MODES, DEFAULT_MODE)
        cookie_mode = pick("cookie_mode", COOKIE_MODES, DEFAULT_MODE)

        allow_custom = raw.get("allow_custom_base_url", DEFAULT_ALLOW_CUSTOM_BASE_URL)
        if not isinstance(allow_custom, bool):
            allow_custom = DEFAULT_ALLOW_CUSTOM_BASE_URL

        return {
            "model_mode": model_mode,
            "cookie_mode": cookie_mode,
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

    # ---- 模型凭据（LLM / 转写）----

    def get_mode(self) -> str:
        """模型凭据模式。保留这个名字是因为它在解析链里被大量引用。"""
        return self.get()["model_mode"]

    get_model_mode = get_mode

    def is_byok_enabled(self) -> bool:
        """**模型**凭据的用户级 BYOK 是否可用（optional 或 required）。"""
        return self.get_mode() != MODE_SHARED

    def requires_byok(self) -> bool:
        return self.get_mode() == MODE_REQUIRED

    # ---- 平台 Cookie ----

    def get_cookie_mode(self) -> str:
        """平台 Cookie 模式（只会是 shared / optional）。"""
        return self.get()["cookie_mode"]

    def is_cookie_byok_enabled(self) -> bool:
        """**Cookie** 的用户级 BYOK 是否可用。

        与模型分开判断：管理员可能让用户自带模型凭据，但仍统一用站点 Cookie。
        历史上这两个共用 ``mode``，导致「关掉模型 BYOK」会连 Cookie 一起失效。
        """
        return self.get_cookie_mode() != MODE_SHARED

    def update(
        self,
        *,
        model_mode: str | None = None,
        cookie_mode: str | None = None,
        allow_custom_base_url: bool | None = None,
        mode: str | None = None,
    ) -> Dict[str, Any]:
        """更新配置并立即失效缓存（管理员切换后下一次解析就生效）。

        :param model_mode: 模型凭据模式
        :param cookie_mode: 平台 Cookie 模式（仅 shared / optional）
        :param allow_custom_base_url: 是否允许用户自定义模型接口地址
        :param mode: **已废弃**，仅为兼容旧调用方；等价于同时设置两者
            （Cookie 会被收敛到 shared/optional）
        """
        raw = self._read_raw()

        # 兼容旧参数：同时写两个新字段
        if mode is not None:
            normalized = str(mode).strip().lower()
            if normalized not in ALL_MODES:
                raise ValueError(
                    f"非法的模式: {mode!r}，可选值: {', '.join(ALL_MODES)}"
                )
            model_mode = model_mode if model_mode is not None else normalized
            cookie_mode = cookie_mode if cookie_mode is not None else normalized

        if model_mode is not None:
            normalized = str(model_mode).strip().lower()
            if normalized not in ALL_MODES:
                raise ValueError(
                    f"非法的模型凭据模式: {model_mode!r}，"
                    f"可选值: {', '.join(ALL_MODES)}"
                )
            raw["model_mode"] = normalized

        if cookie_mode is not None:
            normalized = str(cookie_mode).strip().lower()
            if normalized not in COOKIE_MODES:
                raise ValueError(
                    f"非法的 Cookie 模式: {cookie_mode!r}，"
                    f"可选值: {', '.join(COOKIE_MODES)}"
                    "（Cookie 不消耗站点额度，没有 required）"
                )
            raw["cookie_mode"] = normalized

        if allow_custom_base_url is not None:
            raw["allow_custom_base_url"] = bool(allow_custom_base_url)

        # 老字段不再写：它只作为迁移期的读回退，留着会让「哪个是真的」难判断。
        raw.pop("mode", None)

        raw["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        with self._lock:
            self._write_raw(raw)
            self._cached = None  # 失效缓存，保证「立即生效」
            self._cached_at = 0.0

        cfg = self._normalize(raw)
        logger.info(
            f"BYOK 配置已更新: model_mode={cfg['model_mode']}, "
            f"cookie_mode={cfg['cookie_mode']}, "
            f"allow_custom_base_url={cfg['allow_custom_base_url']}"
        )
        return cfg

    def invalidate_cache(self) -> None:
        """测试用：强制下次 get() 重新读盘。"""
        with self._lock:
            self._cached = None
            self._cached_at = 0.0


# 模块级单例。注意：配置路径相对 cwd，与 TranscriberConfigManager 一致。
byok_config = ByokConfigManager()
