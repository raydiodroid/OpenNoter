import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

import requests

from app.services.cookie_policy import COOKIE_ALLOWLISTS

QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
QR_SOURCE = "main-fe-header"
QR_TTL_SECONDS = 180
QR_POLL_INTERVAL_SECONDS = 1.5
COOKIE_NAMES = COOKIE_ALLOWLISTS["bilibili"]
QR_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BilibiliQrLoginError(Exception):
    pass


@dataclass
class _QrSession:
    session_id: str
    user_id: int
    qrcode_key: str = field(repr=False)
    qr_url: str = field(repr=False)
    expires_at: float
    created_at: float
    http: requests.Session = field(repr=False)
    status: str = "waiting"
    last_poll_at: float = 0.0
    polling: bool = False
    failures: int = 0


class BilibiliQrLoginManager:
    """In-memory, user-bound Bilibili QR login sessions for a single backend process."""

    def __init__(self, cookie_writer: Callable[[str, str], None], session_factory=requests.Session):
        self._cookie_writer = cookie_writer
        self._session_factory = session_factory
        self._sessions: dict[str, _QrSession] = {}
        self._lock = threading.Lock()

    def start(self, user_id: int) -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            active = [s for s in self._sessions.values() if s.user_id == user_id and s.status in {"waiting", "scanned"}]
            if len(active) >= 2:
                raise BilibiliQrLoginError("已有二维码登录正在等待确认")

        http = self._session_factory()
        headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
        try:
            response = http.get(
                QR_GENERATE_URL,
                params={"source": QR_SOURCE, "go_url": "https://www.bilibili.com/"},
                headers=headers,
                timeout=(5, 10),
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or {}
            qr_url = data.get("url")
            qrcode_key = data.get("qrcode_key")
            if body.get("code") != 0 or not isinstance(qr_url, str) or not isinstance(qrcode_key, str):
                raise BilibiliQrLoginError("哔哩哔哩暂时无法生成登录二维码")
            if not QR_KEY_RE.fullmatch(qrcode_key) or not self._is_bilibili_url(qr_url):
                raise BilibiliQrLoginError("哔哩哔哩返回的二维码数据无效")
        except BilibiliQrLoginError:
            http.close()
            raise
        except Exception as exc:
            http.close()
            raise BilibiliQrLoginError("连接哔哩哔哩失败，请稍后重试") from exc

        now = time.monotonic()
        session_id = secrets.token_urlsafe(32)
        item = _QrSession(
            session_id=session_id,
            user_id=user_id,
            qrcode_key=qrcode_key,
            qr_url=qr_url,
            expires_at=now + QR_TTL_SECONDS,
            created_at=now,
            http=http,
        )
        with self._lock:
            self._prune_locked(now)
            self._sessions[session_id] = item
        return {"session_id": session_id, "qr_url": qr_url, "expires_in": QR_TTL_SECONDS}

    def poll(self, session_id: str, user_id: int) -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            item = self._sessions.get(session_id)
            if not item or item.user_id != user_id:
                raise BilibiliQrLoginError("二维码登录会话不存在或已过期")
            if item.expires_at <= now:
                item.status = "expired"
                item.http.close()
                return {"status": "expired"}
            if item.status not in {"waiting", "scanned"}:
                return {"status": item.status}
            if item.polling or now - item.last_poll_at < QR_POLL_INTERVAL_SECONDS:
                return {"status": item.status}
            item.polling = True
            item.last_poll_at = now

        try:
            response = item.http.get(
                QR_POLL_URL,
                params={"qrcode_key": item.qrcode_key, "source": QR_SOURCE},
                headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
                timeout=(5, 10),
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or {}
            if body.get("code") != 0:
                return self._mark_failed(item)
            poll_code = data.get("code")
            if poll_code == 86101:
                return self._finish_poll(item, "waiting")
            if poll_code == 86090:
                return self._finish_poll(item, "scanned")
            if poll_code == 86038:
                return self._finish_poll(item, "expired")
            if poll_code != 0:
                return self._mark_failed(item)

            cookie = self._read_login_cookie(item.http, data)
            if not cookie:
                return self._mark_failed(item)
            self._cookie_writer("bilibili", cookie)
            return self._finish_poll(item, "success")
        except Exception:
            return self._mark_transient_failure(item)

    def cancel(self, session_id: str, user_id: int) -> None:
        with self._lock:
            item = self._sessions.get(session_id)
            if not item or item.user_id != user_id:
                return
            self._sessions.pop(session_id, None)
        item.http.close()

    def _finish_poll(self, item: _QrSession, status: str) -> dict:
        with self._lock:
            current = self._sessions.get(item.session_id)
            if current is item:
                item.status = status
                item.polling = False
                if status in {"success", "expired", "failed"}:
                    item.http.close()
            return {"status": item.status}

    def _mark_failed(self, item: _QrSession) -> dict:
        return self._finish_poll(item, "failed")

    def _mark_transient_failure(self, item: _QrSession) -> dict:
        with self._lock:
            item.failures += 1
            item.polling = False
            if item.failures >= 3:
                item.status = "failed"
                item.http.close()
            return {"status": item.status}

    def _prune_locked(self, now: float) -> None:
        stale = []
        for key, item in self._sessions.items():
            if item.expires_at <= now and item.status in {"waiting", "scanned"}:
                item.status = "expired"
                item.polling = False
                item.http.close()
            if item.status not in {"waiting", "scanned"} and now - item.created_at > QR_TTL_SECONDS + 60:
                stale.append(key)
        for key in stale:
            item = self._sessions.pop(key)
            item.http.close()

    @staticmethod
    def _is_bilibili_url(value: str) -> bool:
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").lower()
            return (
                parsed.scheme == "https"
                and not parsed.username
                and not parsed.password
                and (host == "bilibili.com" or host.endswith(".bilibili.com"))
            )
        except ValueError:
            return False

    @classmethod
    def _read_login_cookie(cls, http: requests.Session, data: dict) -> Optional[str]:
        values: dict[str, str] = {}
        for cookie in http.cookies:
            host = (cookie.domain or "").lstrip(".").lower()
            if host == "bilibili.com" or host.endswith(".bilibili.com"):
                if cookie.name in COOKIE_NAMES:
                    values[cookie.name] = cookie.value

        login_url = data.get("url")
        if isinstance(login_url, str) and cls._is_cross_domain_url(login_url):
            query_values = parse_qs(urlsplit(login_url).query)
            for name in COOKIE_NAMES:
                if name not in values and query_values.get(name):
                    values[name] = query_values[name][0]
            if "SESSDATA" not in values and "ticket" in query_values:
                try:
                    response = http.get(
                        login_url,
                        headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
                        timeout=(5, 10),
                        allow_redirects=False,
                    )
                    response.close()
                except Exception:
                    pass
                for cookie in http.cookies:
                    host = (cookie.domain or "").lstrip(".").lower()
                    if (host == "bilibili.com" or host.endswith(".bilibili.com")) and cookie.name in COOKIE_NAMES:
                        values[cookie.name] = cookie.value

        sessdata = values.get("SESSDATA", "").strip()
        if not sessdata or any(c in sessdata for c in ";\r\n\0"):
            return None
        return "; ".join(f"{name}={values[name]}" for name in COOKIE_NAMES if values.get(name))

    @staticmethod
    def _is_cross_domain_url(value: str) -> bool:
        try:
            parsed = urlsplit(value)
            return (
                parsed.scheme == "https"
                and parsed.hostname in {"passport.biligame.com", "passport.bilibili.com"}
                and parsed.path in {"/crossDomain", "/x/passport-login/web/crossDomain"}
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            return False
