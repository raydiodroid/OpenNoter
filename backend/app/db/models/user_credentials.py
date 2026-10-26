"""用户自带上游凭据（BYOK）。

## 与 providers 表的关系

| 表 | 含义 | 谁维护 |
|---|---|---|
| ``providers`` | 站点共享的供应商目录（默认档位） | 管理员 |
| ``user_credentials`` | 某个用户的某套「端点 + 密钥」 | 该用户自己 |

**刻意不复用 ``providers`` 表**：那张表的主键是 ``String`` 且被 ``models.provider_id``
引用，给它加 ``user_id`` 会让「每加一个用户就多一行 provider」，污染目录语义。
业界参考（Dify 的 ``provider_credentials``、LibreChat 的 ``keys`` 集合、LiteLLM 的
``LiteLLM_CredentialsTable``）也全都是独立表。

## 为什么 base_url / api_key / model_name 三者必填

BYOK 的本质是**换一整套「端点 + 密钥 + 模型」**，它们天然是一个整体：

- 有 key 没 url → 不知道发往哪
- 有 url 没 key → 认证不过
- 有 url 有 key 没模型 → 无法指定调用哪个模型

只配其中一个没有意义，所以三者都在 DB 层 ``nullable=False``。

## capability 的取值

- ``default``：一份凭据覆盖所有能力（**推荐，绝大多数用户只需要这个**）
- ``llm``    ：只为 LLM 覆盖（ASR 仍用 default 或站点）
- ``asr``    ：只为 ASR 覆盖

解析优先级：精确匹配（``llm``/``asr``）→ ``default`` → 回退站点。
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

# ---- capability 常量 ----
CAP_DEFAULT = "default"
CAP_LLM = "llm"
CAP_ASR = "asr"
ALL_CAPABILITIES = (CAP_DEFAULT, CAP_LLM, CAP_ASR)


class UserCredential(Base):
    """用户自带的上游服务凭据。

    一个用户每种 capability 最多一份；``default`` 是「通吃」的那一份。
    """

    __tablename__ = "user_credentials"

    id = Column(String, primary_key=True)  # uuid4 hex，与 providers.id 同风格
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability = Column(String(16), nullable=False, default=CAP_DEFAULT)

    # ---- 端点 + 凭据 + 模型：三者必填，成对使用 ----
    base_url = Column(String(512), nullable=False)  # OpenAI 兼容端点
    api_key = Column(String, nullable=False)  # enc:v2: 密文（AAD 绑定 user+capability）
    model_name = Column(String(128), nullable=False)

    # 展示用别名，方便用户区分多套配置（如"我的 DeepSeek 生产号"）
    label = Column(String(128), nullable=True)

    enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # 一个用户每种能力位只留一份，避免「到底用哪份」的歧义
        UniqueConstraint("user_id", "capability", name="uq_user_credential_capability"),
    )
