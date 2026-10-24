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

    def consume(self, ticket: str, origin: str, cookie: str) -> Optional[CookieTransfer]:
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
            if not item or item.expires_at <= now or item.origin != normalized_origin:
                self._items.pop(ticket, None) if item and item.expires_at <= now else None
                return None
            self._items.pop(ticket, None)
            return item


cookie_transfer_manager = CookieTransferManager()
