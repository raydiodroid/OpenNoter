"""平台 Cookie 的持久化。

## 安全说明（重要）

平台 Cookie（尤其 B 站的 `SESSDATA`）是**长期登录凭证**，拿到即可冒用账号。
所以落盘前一律用 `app.utils.secret_box` 加密（AES-128-CBC + HMAC）：

- 文件里存的是 `enc:v1:<base64>`，不再是明文
- 密钥由 `OPENNOTER_SECRET_KEY`（未设时回退 `OPENNOTER_JWT_SECRET`）派生
- 读取历史明文时自动兼容，并在下次写入时转成密文（无需迁移脚本）

此外文件权限固定 0600，且写入用「临时文件 + 原子 rename」，
避免写到一半崩溃留下半截 JSON。
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict

from app.services.cookie_policy import COOKIE_PLATFORMS, filter_downloader_cookies
from app.utils import secret_box
import logging

logger = logging.getLogger(__name__)


class CookieConfigManager:
    def __init__(self, filepath: str = "config/downloader.json"):
        self.path = Path(filepath)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def _read(self) -> Dict[str, Dict[str, str]]:
        """读配置并解密。

        密文解不开（密钥换了/文件损坏）时**丢弃该平台**而不是把密文当明文用——
        否则下游会把 `enc:v1:...` 当成 Cookie 发给平台，产生难查的 401。
        """
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}

        out: Dict[str, Dict[str, str]] = {}
        for platform, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            stored = entry.get("cookie")
            if not isinstance(stored, str) or not stored:
                continue
            if secret_box.is_encrypted(stored):
                try:
                    plain = secret_box.decrypt(stored)
                except RuntimeError:
                    # 无密钥：无法解密，跳过而非崩
                    continue
                if plain:
                    out[platform] = {"cookie": plain}
                # None → 密钥不符/损坏，当作未配置
            else:
                out[platform] = {"cookie": stored}  # 历史明文
        return out

    def _write(self, data: Dict[str, Dict[str, str]]):
        """加密后原子写入（0600 权限）。

        先写 `<name>.tmp` 再 rename：避免进程在写一半时被杀，
        留下一个半截 JSON 让下次启动读不出任何 Cookie。
        """
        payload: Dict[str, Dict[str, str]] = {}
        try:
            for platform, entry in data.items():
                cookie = (entry or {}).get("cookie") if isinstance(entry, dict) else None
                if not cookie:
                    continue
                payload[platform] = {"cookie": secret_box.encrypt(cookie)}
        except RuntimeError:
            # 没配密钥时**不能**默默退化为明文 —— SESSDATA 是可直接登录账号的凭据，
            # 而「没配密钥」不等于「不在意安全」：可能只是有人漏配了 .env。
            #
            # 区分两种部署（与 static_signing 的降级原则一致）：
            #   * 开了鉴权（AUTH_ENFORCE=1）→ 这是**配置错误**，必须 fail-closed。
            #     否则用户以为 Cookie 加密了，实际是明文躺在磁盘/备份/容器层里。
            #   * 没开鉴权（AUTH_ENFORCE=0，单人内网自托管）→ 允许明文，
            #     但大声警告，并在 README 写清。
            from app.services.auth import auth_enforced

            if auth_enforced():
                raise RuntimeError(
                    "无法加密 Cookie：未配置 OPENNOTER_SECRET_KEY / OPENNOTER_JWT_SECRET。\n"
                    "你已开启鉴权（AUTH_ENFORCE=1），Cookie（含 B 站 SESSDATA）必须加密存储。\n"
                    "请生成密钥并写入 .env：\n"
                    '  python -c "import secrets;print(secrets.token_urlsafe(48))"'
                )

            logger.warning(
                "未配置 OPENNOTER_SECRET_KEY / OPENNOTER_JWT_SECRET，"
                "且鉴权未开启（AUTH_ENFORCE=0）—— Cookie 将以**明文**存储。"
                "若要加密，请配置密钥。"
            )
            payload = {}
            for platform, entry in data.items():
                cookie = (entry or {}).get("cookie") if isinstance(entry, dict) else None
                if cookie:
                    payload[platform] = {"cookie": cookie}

        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except Exception:
            os.close(fd)
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _write_plaintext(self, data: Dict[str, Dict[str, str]]):
        """不加密直接原子写入（仅供 `get()` 的回写降级路径使用）。

        与 `_write()` 的区别只有「不做加密」：权限、原子写、过滤逻辑完全一致。
        **不要**用它处理新写入的 Cookie —— 那必须走 `_write()`（fail-closed）。
        """
        payload: Dict[str, Dict[str, str]] = {}
        for platform, entry in data.items():
            cookie = (entry or {}).get("cookie") if isinstance(entry, dict) else None
            if cookie:
                payload[platform] = {"cookie": cookie}

        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except Exception:
            os.close(fd)
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

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
            # 回写发生在「读到一个需要规范的旧值」时（例如把 tracking 参数过滤掉）。
            # 它有两个目的：① 下次省一次过滤；② **把不该留的字段从磁盘上清掉**。
            # 第 ② 点是安全相关的，所以不能因为加密不可用就整段跳过。
            #
            # 但如果这种情况又恰逢「开启鉴权 + 没配密钥」，`_write()` 会按设计
            # fail-closed 抛错。这里的取舍是：**回写允许降级为明文**（只是把
            # 已经存在于磁盘上的旧值重写一遍，不引入新的明文风险），
            # 而不是让「下载带 Cookie 的视频」整个不可用、报错点还离真实原因很远。
            try:
                self._write(data)
            except RuntimeError as exc:
                logger.warning(
                    "配置了鉴权但缺少密钥，Cookie 回写降级为明文（请尽快配置密钥）：%s", exc,
                )
                self._write_plaintext(data)
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

    def migrate_plaintext(self) -> int:
        """把存量明文 Cookie 转成密文，返回迁移的平台数。

        `_read()` 为了兼容会原样返回历史明文，但**不会**主动写回——
        那样每次读都产生一次磁盘写。这里提供一个显式入口，
        在启动时调用一次即可完成静默升级。
        """
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return 0
        if not isinstance(raw, dict):
            return 0

        plaintext_platforms = [
            p
            for p, entry in raw.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("cookie"), str)
            and entry["cookie"]
            and not secret_box.is_encrypted(entry["cookie"])
        ]
        if not plaintext_platforms:
            return 0

        # 复用 _write 走加密 + 原子写
        self._write(self._read())
        return len(plaintext_platforms)

    def list_all(self) -> Dict[str, str]:
        return {platform: cookie for platform in COOKIE_PLATFORMS if (cookie := self.get(platform))}

    def exists(self, platform: str) -> bool:
        return self.get(platform) is not None