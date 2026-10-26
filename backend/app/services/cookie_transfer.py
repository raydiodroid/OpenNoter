import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from app.services.cookie_policy import COOKIE_MAX_LENGTH, COOKIE_NAME_RE, COOKIE_PLATFORMS


def normalize_origin(value: str) -> Optional[str]:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        host = parsed.hostname.lower()
        port = parsed.port
        default_port = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
        authority = f"[{host}]" if ":" in host else host
        if port and not default_port:
            authority = f"{authority}:{port}"
        return f"{parsed.scheme.lower()}://{authority}"
    except (ValueError, TypeError):
        return None


def validate_cookie_string(value: str) -> str:
    cookie = value.strip()
    if not cookie or len(cookie) > COOKIE_MAX_LENGTH or "\r" in cookie or "\n" in cookie or "\0" in cookie:
        raise ValueError("Cookie 为空或格式无效")
    pairs = [part.strip() for part in cookie.split(";") if part.strip()]
    if not pairs:
        raise ValueError("Cookie 为空或格式无效")
    for pair in pairs:
        name, separator, _ = pair.partition("=")
        if not separator or not COOKIE_NAME_RE.fullmatch(name.strip()):
            raise ValueError("Cookie 格式无效")
    return "; ".join(pairs)


@dataclass(frozen=True)
class CookieTransfer:
    platform: str
    user_id: int
    origin: str
    expires_at: float


class CookieTransferManager:
    """Short-lived, single-use capability for an extension to transfer browser cookies."""

    def __init__(self, ttl_seconds: int = 90):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, CookieTransfer] = {}
        self._lock = threading.Lock()

    def issue(self, platform: str, user_id: int, origin: str) -> str:
        if platform not in COOKIE_PLATFORMS:
            raise ValueError("不支持的 Cookie 平台")
        normalized_origin = normalize_origin(origin)
        if not normalized_origin:
            raise ValueError("来源地址无效")
        now = time.monotonic()
        with self._lock:
            self._items = {key: item for key, item in self._items.items() if item.expires_at > now}
            if sum(item.user_id == user_id for item in self._items.values()) >= 3:
                raise ValueError("Cookie 获取请求过多，请稍后重试")
            ticket = secrets.token_urlsafe(32)
            self._items[ticket] = CookieTransfer(
                platform=platform,
                user_id=user_id,
                origin=normalized_origin,
                expires_at=now + self.ttl_seconds,
            )
            return ticket

    def consume(
        self,
        ticket: str,
        origin: str,
        cookie: str,
        *,
        expected_user_id: Optional[int] = None,
    ) -> Optional[CookieTransfer]:
        """核销一张 ticket。

        五个校验全部通过才返回条目（且无论如何先把它从表里删掉，保证单次使用）：
          1. ticket 存在；
          2. 未过期；
          3. **origin 与签发时一致**（防止别的页面来抢）；
          4. Cookie 形态合法；
          5. 若调用方提供了身份，**必须与签发者一致**（F40）。

        关于第 5 条：`issue()` 一直记录了 `user_id`，但 `consume()` 从不比对。
        ticket 虽然只由管理员签发（90s TTL、单次、绑 origin），可一旦它从
        前端 JS / 日志 / 浏览器扩展消息里泄漏，**任何拿到 ticket 的人**都能用它
        把 Cookie 写进系统（写入是全局的，会影响所有用户）。

        所以：**如果 completion 请求带了有效身份，就必须与签发者匹配**；
        没带身份时保持原行为（该端点设计上就是免登录的，浏览器扩展拿不到
        Cookie 凭据）。这是"有更强证据就用更强证据"的渐进策略。
        """
        normalized_origin = normalize_origin(origin)
        if not normalized_origin:
            return None
        try:
            validate_cookie_string(cookie)
        except ValueError:
            return None
        now = time.monotonic()
        with self._lock:
            item = self._items.get(ticket)
            if not item:
                return None

            # 校验顺序很重要：**先校验、通过后才弹出**。
            #
            # 曾经把「弹出」放到最前面来保证单次使用，结果是：一次带错误 origin
            # 的探测（攻击者试一下）就会把 ticket 消耗掉，真正的合法请求随后
            # 拿到 410 —— 攻击者不需要知道 ticket 内容就能做**拒绝服务**。
            #
            # 正确语义：单次使用指「**成功**消费一次后作废」，不是「碰一次就烧掉」。
            # 失败的分支（过期/来源不符/身份不符）各自处理是否清除。
            if item.expires_at <= now:
                self._items.pop(ticket, None)   # 过期条目顺手清理
                return None
            if item.origin != normalized_origin:
                # 来源不符**不**消耗：可能是用户先打开了错的页面（或攻击者探测），
                # 真正的合法请求仍应能成功。留 TTL 兜底。
                return None
            # F40：带了身份就必须是签发者本人
            if expected_user_id is not None and item.user_id != expected_user_id:
                return None

            # 全部校验通过 → 成功消费，立即作废（这才是「单次使用」）
            self._items.pop(ticket, None)
            return item


cookie_transfer_manager = CookieTransferManager()
