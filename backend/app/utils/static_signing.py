"""静态资源的 per-task 长期令牌签发/校验（F9）。

## 为什么需要

`/static/screenshots/<task_id>/...` 是**受 ACL 保护**的资源，但在两个客户端上
必然拿不到鉴权凭据：

- **Tauri 桌面端**：页面源是 `tauri://localhost`，请求 `/static/` 时既没有同源
  Cookie，也没法给 `<img>` 加自定义头。
- **浏览器扩展**：页面源是 `chrome-extension://`，`<img>` 同样加不了 `x-api-key`。

`<img src>` 是浏览器发起的裸 GET，**唯一能携带凭据的地方就是 URL 本身**。所以
后端的做法是：把「这个 URL 属于哪个 task」签成一个令牌，附在 query 上。

## 为什么是「长期令牌」而不是「TTL 签名」

这是本模块最关键的决策，**不要改成带过期时间的签名**。

截图 URL 不是每次请求动态生成的——它在**生成笔记时就被烧进 markdown 字符串**：

    services/note.py::_post_process_markdown
        img_url = f"{url_prefix}/{filename}"
        markdown = markdown.replace(marker, f"![]({img_url})", 1)

而这段 markdown 会被**持久化到两个地方**：

1. 后端 `note_results/<task_id>.json`（`save_note_to_file` 用 `asdict(note)` 落盘）
2. 前端 IndexedDB（`store/taskStore` 用 zustand `persist` + `idb-keyval` 存整个
   `tasks` 数组，含 markdown 版本历史）

于是：**带 TTL 的签名一旦过期，这篇历史笔记的截图就永久 403**，而 markdown 里
固化的签名永远不会自己更新——除非用户重新生成整篇笔记。典型的故障形态是
「开发时测试全过（都是刚生成的笔记），一周后用户打开旧笔记，截图全挂」。

所以令牌**不含任何时间成分**：`HMAC(secret, "task-static:" + task_id)`。
同一 task 永远签出同一个令牌。

安全边界随之明确：**拿到 URL 的人就能读那个 task 的截图**。这与「拿到 task_id
就能读」的暴露面基本相同（task_id 是 uuid4，不可枚举），而笔记正文本身已经
通过 `/tasks/{id}` 的 ACL 保护。对单人/内网自托管与常规多租户部署都可接受。

## 密钥来源

复用 `secret_box._derived_key()`（`OPENNOTER_SECRET_KEY` → 回退 `OPENNOTER_JWT_SECRET`），
再用不同的 HKDF `info` 派生出**专用于签名**的子密钥。这样：

- 运维不需要多管一把钥匙（与 Cookie/Provider key 同一来源，一处轮换全生效）；
- 签名密钥与加密密钥**不是同一份字节**（HKDF 分离用途）。

## 无密钥时降级

`AUTH_ENFORCE=0` 的单人自托管可以完全不配密钥。此时 `_derived_key()` 会抛
`RuntimeError`——本模块必须**吞掉并降级为不签名**，让 URL 保持原样（后端
`serve_static` 在未强制认证时本来就不会拦），绝不把整条链路变成 500。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# 令牌在 query 上的参数名。前端只做「query 原样透传」，不解这个值，
# 但仍建议保持简短（截图 URL 会写进 markdown，长度影响可读性）。
TOKEN_PARAM = "t"

# 签名原文前缀：与其它用途的 HMAC 隔离，避免跨用途重放。
# 不同资源类型用不同前缀 —— 截图令牌不能拿去读封面，反之亦然。
_SIGN_PREFIX = "task-static:"
_COVER_SIGN_PREFIX = "cover-static:"
# 扁平截图（历史格式，无 task 子目录）按文件名绑定
_FLAT_SCREENSHOT_SIGN_PREFIX = "screenshot-static:"

# HKDF info：与 secret_box 的加密用途区分开
_KEY_INFO = b"opennoter-static-token-signing-v1"
_HKDF_SALT = b"opennoter-salt-v1"

# 需要令牌的路径前缀。
#
# - `/static/screenshots/<task_id>/<file>` —— 截图，**按 task 绑定**令牌，
#   这样一张令牌只能读一个任务的截图（有 ACL 语义）。
# - `/static/cover/<file>` —— 本地视频封面。文件名是 `<上传名>_cover.jpg`，
#   而上传名是 uuid4（`sanitize_store_name` 生成），本身不可猜；且封面**不属于
#   某个 task 的私密产物**（同一份上传可能被多个 task 复用）。
#   所以这里用「按文件名」的令牌：只要文件名对得上就放行。
#
# - `/static/screenshots/<file>`（**扁平，无 task 子目录**）—— 历史截图格式。
#   早期版本把截图直接放在 `screenshots/` 下，文件名形如
#   `screenshot_000_<uuid>.jpg`。这类**无法反推 task_id**（文件名里没有），
#   但又必须能显示（否则升级后所有历史笔记的截图全 401）。
#   处理方式与封面一致：按文件名绑定令牌。文件名含 uuid4，不可枚举。
#
# 两者都用同一套 HMAC，只是 payload 前缀不同（见 `_SIGN_PREFIX_*`），
# 避免一类令牌被拿去读另一类资源。
_PROTECTED_PREFIX = "/static/screenshots/"
_COVER_PREFIX = "/static/cover/"


def _signing_key() -> Optional[bytes]:
    """派生出专用于签名的 32 字节密钥；无密钥配置时返回 None（降级）。"""
    try:
        from app.utils import secret_box

        base = secret_box._derived_key()  # noqa: SLF001 - 同一模块族的内部派生
    except RuntimeError:
        # 没有配置 SECRET_KEY / JWT_SECRET：本部署不启用签名（见模块 docstring）。
        return None
    except Exception as exc:  # noqa: BLE001 - 任何派生异常都不该让请求 500
        logger.warning(f"派生静态资源签名密钥失败，将不签发令牌：{exc}")
        return None

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_KEY_INFO,
    )
    return hkdf.derive(base)


def _b64url_nopad(raw: bytes) -> str:
    """base64url 去填充：URL 里不需要 `=`，也避免编码歧义。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _task_id_from_path(path: str) -> Optional[str]:
    """从 `/static/screenshots/<task_id>/<file>` 里取出 task_id。

    只认严格的三段以上结构；取不到返回 None（调用方按「不保护」处理）。
    """
    if not path.startswith(_PROTECTED_PREFIX):
        return None
    rest = path[len(_PROTECTED_PREFIX):]
    task_id, _, filename = rest.partition("/")
    if not task_id or not filename:
        return None
    # 防御：task_id 不该含路径分隔/穿越片段
    if "/" in task_id or ".." in task_id:
        return None
    return task_id


def _cover_name_from_path(path: str) -> Optional[str]:
    """从 `/static/cover/<file>` 里取出文件名（作为令牌的绑定对象）。

    只认单层结构；`cover/sub/x.jpg` 这类不认（返回 None）。
    """
    if not path.startswith(_COVER_PREFIX):
        return None
    rest = path[len(_COVER_PREFIX):]
    if not rest or "/" in rest or ".." in rest or rest.startswith("."):
        return None
    return rest


def _flat_screenshot_name_from_path(path: str) -> Optional[str]:
    """从**扁平**截图路径 `/static/screenshots/<file>` 里取文件名。

    ## 为什么需要这条

    早期版本的截图直接放在 `screenshots/` 下（没有 `<task_id>/` 子目录），
    文件名是 `screenshot_000_<uuid>.jpg`。这类路径**无法反推 task_id**，
    但升级后如果不对它签名，历史笔记的截图会全部 401 —— 那是比"不修"更糟的
    回归（用户打开旧笔记全是破图）。

    绑定对象就是文件名本身：它含 uuid4，不可枚举；且它本来就已经通过
    markdown 出现在笔记里，暴露面与 task_id 相当。

    只认单层结构，且必须像截图文件（`screenshot_` 前缀）。
    """
    if not path.startswith(_PROTECTED_PREFIX):
        return None
    rest = path[len(_PROTECTED_PREFIX):]
    if not rest or "/" in rest or ".." in rest or rest.startswith("."):
        return None
    # 只接受历史截图命名，避免把任意文件纳入按名签名
    if not rest.lower().startswith("screenshot_"):
        return None
    return rest


def static_resource_key(path: str) -> Optional[tuple[str, str]]:
    """解析静态路径 → (资源类型, 签名绑定键)。

    返回：
      - ("task", task_id)   —— 截图，按 task 绑定
      - ("cover", filename) —— 封面，按文件名绑定
      - None                —— 不需要令牌的路径
    """
    task_id = _task_id_from_path(path)
    if task_id is not None:
        return ("task", task_id)
    flat = _flat_screenshot_name_from_path(path)
    if flat is not None:
        # 与封面同样按「文件名」绑定，但用独立的签名前缀
        # （`screenshot-static:`，见 _sign()），避免与封面令牌互换。
        return ("screenshot", flat)
    cover = _cover_name_from_path(path)
    if cover is not None:
        return ("cover", cover)
    return None


def _sign(resource: str, key_value: str) -> Optional[str]:
    """按资源类型签发令牌；无密钥时返回 None。"""
    key = _signing_key()
    if key is None:
        return None
    # 三类资源各用独立前缀，令牌不能跨类互换
    # （截图令牌读不了封面/扁平截图，反之亦然）。
    _PREFIXES = {
        "task": _SIGN_PREFIX,
        "cover": _COVER_SIGN_PREFIX,
        "screenshot": _FLAT_SCREENSHOT_SIGN_PREFIX,
    }
    prefix = _PREFIXES.get(resource, _SIGN_PREFIX)
    msg = f"{prefix}{key_value}".encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return _b64url_nopad(digest)


def sign_payload(task_id: str) -> Optional[str]:
    """为 task_id 生成长期令牌（截图用）；无密钥时返回 None。"""
    return _sign("task", task_id)


def sign_cover_payload(filename: str) -> Optional[str]:
    """为封面文件名生成长期令牌；无密钥时返回 None。"""
    return _sign("cover", filename)


def verify_resource_token(resource: str, key_value: str, token: str) -> bool:
    """按资源类型校验令牌（verify_token / verify_cover_token 的共用实现）。"""
    expected = _sign(resource, key_value)
    if expected is None:
        return False
    try:
        expected_b = expected.encode("utf-8")
        supplied_b = str(token or "").encode("utf-8", errors="surrogateescape")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(expected_b, supplied_b)


def verify_cover_token(filename: str, token: str) -> bool:
    """校验封面令牌。"""
    return verify_resource_token("cover", filename, token)


def verify_flat_screenshot_token(filename: str, token: str) -> bool:
    """校验扁平截图（历史格式）令牌。"""
    return verify_resource_token("screenshot", filename, token)


def verify_token(task_id: str, token: str) -> bool:
    """校验令牌是否匹配该 task_id。

    ## 无密钥时必须 fail-closed（返回 False）

    这里的语义要分清两件事：

      - **签发端**可以降级：没配密钥时 `sign_payload()` 返回 None，
        `sign_static_url()` 原样返回 URL（不带 `t`）。这是对的 ——
        `AUTH_ENFORCE=0` 的单人部署本来就不该被要求配密钥。

      - **校验端不能降级**。如果这里在"算不出期望值"时返回 True，那么任何
        一个同时满足「`AUTH_ENFORCE=1`」+「密钥暂时不可用」的窗口
        （例如 KMS 抖动、只配了部分密钥变量、HMAC 派生抛异常）都会变成
        **「随便什么 token 都放行」** —— 鉴权被静默摘掉。

    所以：算不出期望令牌 ⇒ 无法证明请求合法 ⇒ 拒绝。

    ## 必须用 bytes 比较（不是 str）

    `hmac.compare_digest` 对 **str** 有一个限制：**两边都必须只含 ASCII**，
    否则抛 `TypeError: comparing strings with non-ASCII characters is not supported`。

    而 `token` 直接来自 query 参数，攻击者可以构造 `?t=中文`：

        GET /static/screenshots/<task>/x.jpg?t=%E4%B8%AD%E6%96%87

    若用 str 比较，这里会在比较阶段抛 TypeError → 未捕获 → **500 + 堆栈**。
    虽然不至于绕过鉴权，但会：(a) 让任何人能刷错误日志、触发异常路径，
    (b) 监控里表现为「服务端 500」掩盖真实原因。

    改成先编码成 bytes 再比较：`compare_digest` 对 bytes 没有 ASCII 限制，
    且仍是定长比较（保留防时序侧信道的作用）。
    """
    return verify_resource_token("task", task_id, token)


def sign_static_url(url: str) -> str:
    """给受保护的静态资源 URL 附加上 `?t=<令牌>`。

    覆盖两类：
      - `/static/screenshots/<task_id>/...` —— 按 task 绑定
      - `/static/cover/<file>` —— 按文件名绑定（本地视频封面）

    - 非受保护路径 / 无法解析 / 无密钥（降级）→ **原样返回**；
    - 任何异常都不得外抛：这个函数在笔记生成主流程里被调用，
      签名失败只该导致「图片需要登录才能看」，不该让整篇笔记生成失败。

    保留 URL 上已有的其它 query 参数（例如后续可能加的缓存破坏参数），
    只覆盖/追加 `t`。
    """
    try:
        if not url:
            return url

        parts = urlsplit(url)
        # 统一解析：截图按 task 绑定、封面按文件名绑定（见 static_resource_key）
        spec = static_resource_key(parts.path)
        if spec is None:
            return url
        resource, key_value = spec

        token = _sign(resource, key_value)
        if token is None:
            # 无密钥：不签名，原样返回（保持改动前行为）
            return url

        # 保留原有 query，替换/追加 t
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k != TOKEN_PARAM]
        query.append((TOKEN_PARAM, token))
        return urlunsplit((
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        ))
    except Exception as exc:  # noqa: BLE001 - 签名失败不能影响笔记生成
        logger.warning(f"静态资源签名失败，退回未签名 URL（{url}）：{exc}")
        return url


def has_token(query_params) -> bool:
    """请求是否带了令牌（用于区分「没带令牌」和「令牌错了」）。

    这个区分决定返回 401 还是 403：
      - 没带令牌 → 走正常登录鉴权，未登录就是 401（"你该登录了"）
      - 带了但不对 → 403（"链接失效"，别把用户踢去登录页）

    **判据是「参数存在」而不是「值非空」**：`?t=`（空值）属于"带了令牌但无效"，
    应该走 403 分支。若用 `bool(get(...))` 判真假，空值会被当成"没带" → 落到
    401，触发前端跳登录页 —— 用户只是打开一张图就被踢下线。
    """
    try:
        return TOKEN_PARAM in query_params
    except Exception:  # noqa: BLE001 - query_params 形态异常时保守当作"没带"
        return False
