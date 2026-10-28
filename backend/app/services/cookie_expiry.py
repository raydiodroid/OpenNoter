"""Cookie 过期时间的提取与判定。

## 一个绕不开的事实：只有 netscape 格式带过期时间

本项目支持两种 Cookie 格式（见 ``cookie_policy.COOKIE_FORMATS``）：

| 格式 | 平台 | 是否含过期时间 |
|---|---|---|
| ``header``（`name=value; name2=value2`） | B 站 / 抖音 / 快手 | **否** |
| ``netscape``（7 个 tab 分隔字段） | YouTube | **是**（第 5 个字段是 unix 时间戳） |

所以「Cookie 什么时候过期」这个问题，对 header 格式**从内容上无法回答**——
浏览器导出的请求头 Cookie 本来就不带 expiry。

## 两种格式分别怎么判定

**netscape**：解析每个 cookie 行的 expiry 字段，取**最早**的非会话过期时间
（`0` 表示会话 Cookie，浏览器关闭即失效，不参与比较）。这是真实的过期时间。

**header**：没有内容依据，只能退而记录**保存时间**，并按「经验有效期」提示：
B 站 SESSDATA、抖音 sessionid 这类登录态通常在 30~90 天量级，但**平台可以随时
让任意 Cookie 失效**（改密码、异地登录风控、主动登出）。

因此对 header 格式，UI 的措辞必须是「已保存 N 天」而不是「还有 N 天过期」——
后者会给出虚假的确定感。

## 为什么不靠「有效期推算」当成硬判断

Cookie 失效的真实原因里，**时间只占一部分**：改密码、平台风控、主动登出都会
让未到期的 Cookie 立即失效。所以本模块只负责「把能确定的信息如实算出来」，
真正的「还能不能用」应当由主动探测回答（见 ``probe_cookie``，暂未实现）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.services.cookie_policy import (
    COOKIE_FORMAT_NETSCAPE,
    cookie_format,
)

# header 格式的「经验提示阈值」（天）。
#
# 这是**提示**不是判定：超过这个天数不代表一定失效，只是值得提醒用户
# 重新确认。取值偏保守（30 天），因为登录态失效的代价是任务失败，
# 而误报的代价只是多看一眼设置页。
HEADER_COOKIE_STALE_DAYS = 30


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def extract_netscape_expiry(cookie_text: str) -> Optional[datetime]:
    """从 netscape cookies.txt 里取**最早**的非会话过期时间。

    返回 ``None`` 表示：没有可解析的行 / 全是会话 Cookie（expiry=0）。

    会话 Cookie 被刻意忽略：它们「关闭浏览器即失效」，与「到期时间」不是
    一回事，混进来会让「最早过期」恒为过去某个值，失去意义。
    """
    if not cookie_text:
        return None

    earliest: Optional[int] = None
    for raw_line in cookie_text.split("\n"):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        # 与 cookie_policy 解析保持一致：`#HttpOnly_` 前缀是数据行，不是注释
        body = line
        if body.startswith("#HttpOnly_"):
            body = body[len("#HttpOnly_"):]
        elif body.lstrip().startswith("#"):
            continue  # 真注释

        fields = body.split("\t")
        if len(fields) != 7:
            continue

        try:
            expiry = int(fields[4].strip())
        except (TypeError, ValueError):
            continue

        if expiry <= 0:
            continue  # 会话 Cookie，不参与「最早过期」比较
        if earliest is None or expiry < earliest:
            earliest = expiry

    if earliest is None:
        return None
    try:
        return datetime.fromtimestamp(earliest, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def describe_expiry(
    *,
    platform: str,
    cookie_text: str,
    saved_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> dict:
    """算出该 Cookie 的过期信息，供接口与 UI 使用。

    返回字段（都在 ``GET /me/cookies`` 的条目里）：

    - ``expires_at``：已知的真实过期时间（仅 netscape 可能有），ISO 字符串
    - ``expired``：是否**已确认**过期（只有 expires_at 存在时才可能是 True）
    - ``age_days``：保存至今的天数（header 格式的主要参考）
    - ``stale``：header 格式超过 ``HEADER_COOKIE_STALE_DAYS`` 时给 True，
      提示「可能已失效，建议重新确认」——措辞上不能叫「已过期」
    - ``expiry_kind``：``"known"``（有真实过期时间）/ ``"unknown"``（只能用天数参考）
    """
    now = now or datetime.now(timezone.utc)
    fmt = cookie_format(platform)

    result = {
        "expires_at": None,
        "expired": False,
        "age_days": None,
        "stale": False,
        "expiry_kind": "unknown" if fmt != COOKIE_FORMAT_NETSCAPE else "unknown",
    }

    age_days: Optional[float] = None
    saved_utc = _as_utc(saved_at)
    if saved_utc is not None:
        age_days = max(0.0, (now - saved_utc).total_seconds() / 86400.0)
        result["age_days"] = round(age_days, 1)

    if fmt == COOKIE_FORMAT_NETSCAPE:
        expires = extract_netscape_expiry(cookie_text)
        if expires is not None:
            result["expiry_kind"] = "known"
            result["expires_at"] = expires.isoformat()
            result["expired"] = expires <= now
            return result

    # header 格式，或 netscape 但全是会话 Cookie → 没有确定的过期时间
    result["expiry_kind"] = "unknown"
    if age_days is not None and age_days >= HEADER_COOKIE_STALE_DAYS:
        result["stale"] = True
    return result
