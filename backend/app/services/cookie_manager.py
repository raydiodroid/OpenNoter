import json
import os
from pathlib import Path
from typing import Optional, Dict

from app.services.cookie_policy import COOKIE_PLATFORMS, filter_downloader_cookies


class CookieConfigManager:
    def __init__(self, filepath: str = "config/downloader.json"):
        self.path = Path(filepath)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def _read(self) -> Dict[str, Dict[str, str]]:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write(self, data: Dict[str, Dict[str, str]]):
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except Exception:
            os.close(fd)
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get(self, platform: str) -> Optional[str]:
        data = self._read()
        stored = data.get(platform, {}).get("cookie")
        if stored is None:
            return None
        try:
            filtered = filter_downloader_cookies(platform, stored)
        except ValueError:
            filtered = ""
        if filtered != stored:
            if filtered:
                data[platform] = {"cookie": filtered}
            else:
                data.pop(platform, None)
            self._write(data)
        return filtered or None

    def set(self, platform: str, cookie: str):
        filtered = filter_downloader_cookies(platform, cookie)
        data = self._read()
        if filtered:
            data[platform] = {"cookie": filtered}
        else:
            data.pop(platform, None)
        self._write(data)

    def delete(self, platform: str):
        data = self._read()
        if platform in data:
            del data[platform]
            self._write(data)

    def list_all(self) -> Dict[str, str]:
        return {platform: cookie for platform in COOKIE_PLATFORMS if (cookie := self.get(platform))}

    def exists(self, platform: str) -> bool:
        return self.get(platform) is not None