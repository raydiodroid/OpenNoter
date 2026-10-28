"""用户自带的平台 Cookie（BYOK 的 Cookie 部分）。

## 为什么 Cookie 也要 BYOK —— 它比 API Key 更需要

| 维度 | API Key（user_credentials） | 平台 Cookie（本表） |
|---|---|---|
| 本质 | **花钱的额度** | **登录身份**（B 站 SESSDATA 等） |
| 共用后果 | 用户花站点的钱 | 用户用管理员的账号看视频；观看历史/推荐混在一起 |
| 他人可冒用 | 否（只能花钱） | **是**（拿到即登录） |
| 缺失影响 | 任务失败 | 静默降级（无 Cookie 也能下 360P–1080P 普通画质） |

多用户共用一份 Cookie 时，站点侧看到的是「同一个人在异地看各种视频」——
既是隐私问题，也可能触发平台风控。所以用户的 Cookie 理应是用户自己的。

## 与 config/downloader.json 的关系

两者平行，不是替代：

- ``config/downloader.json``  = 站点共享的 Cookie（默认档位，管理员维护）
- ``user_cookies``（本表）    = 用户自己的 Cookie（可选档位，用户维护）

解析优先级：用户 Cookie → 站点 Cookie → 无 Cookie（降级）。

## 为什么用 credential_crypto 而不是 secret_box

Cookie 是**可冒用的身份**，比 API Key 更敏感。``secret_box`` 用全局单密钥、
无绑定，密文可以被复制到另一行并正常解密——对 Cookie 来说，那等于
「把 A 的登录态复制给 B」。``credential_crypto`` 用 AAD 绑定
``(user_id, capability)``，密文搬到别的用户行立刻解不开。

## capability 这一列复用什么值

复用 ``credential_crypto`` 的 AAD 语义。Cookie 的 capability 用
``cookie:<platform>``（如 ``cookie:bilibili``），与 ``llm``/``asr`` 不冲突，
且天然把「哪一个平台的 Cookie」绑进密文——A 的 B 站 Cookie 密文即使被搬到
A 自己的抖音行也解不开。
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)

from app.db.engine import Base

# 与 cookie_policy.COOKIE_PLATFORMS 保持一致；这里不 import 是为了避免
# db/models 层依赖 services 层（既有代码保持这个方向）。
SUPPORTED_COOKIE_PLATFORMS = ("bilibili", "douyin", "kuaishou", "youtube")


def cookie_capability(platform: str) -> str:
    """把平台名映射成 AAD 用的 capability 字符串。

    与 ``llm`` / ``asr`` 共用 credential_crypto 的 AAD 空间，靠前缀区分。
    """
    return f"cookie:{platform}"


class UserCookie(Base):
    """用户自带的单个平台 Cookie。

    一个用户每个平台最多一份。没有 = 按解析优先级回退。
    """

    __tablename__ = "user_cookies"

    id = Column(String, primary_key=True)  # uuid4 hex
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform = Column(String(32), nullable=False)

    # enc:v2: 密文（AAD 绑定 user_id + cookie:<platform>）
    cookie = Column(String, nullable=False)

    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_user_cookie_platform"),
    )
