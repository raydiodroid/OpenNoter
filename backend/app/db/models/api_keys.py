from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    func,
)


from app.db.engine import Base


# 密钥生命周期状态。取代原先只有一个裸 revoked 布尔 —— 过期、停用、吊销是三回事，
# 界面上必须能区分（Anthropic 的 Deactivate vs Delete、Cloudflare 的
# active/disabled/expired 都是这个思路）。
STATUS_ACTIVE = "active"      # 有效
STATUS_EXPIRED = "expired"    # 已过期（到期时间到了）
STATUS_DISABLED = "disabled"  # 已停用（临时、可恢复）
STATUS_REVOKED = "revoked"    # 已吊销（终态）

ALL_STATUSES = (STATUS_ACTIVE, STATUS_EXPIRED, STATUS_DISABLED, STATUS_REVOKED)

# 吊销原因。对应 GCP 的 disable_reason —— 出事时要能看出是人为轮换还是判定泄露。
REVOKE_MANUAL = "manual"      # 人工吊销
REVOKE_ROTATED = "rotated"    # 轮换导致旧 key 失效
REVOKE_LEAKED = "leaked"      # 判定为泄露
REVOKE_EXPIRED = "expired"    # 到期自动失效
REVOKE_POLICY = "policy"      # 策略触发（如 IP 白名单不再匹配）


class ApiKey(Base):
    """给 agent / 脚本调用的长期凭据。

    明文只在创建时返回一次。库里存的是：
      - key_hash：SHA-256 hex，**唯一索引**，鉴权时直接按哈希查（O(1)，不需要扫候选）
      - key_id / prefix / last_four：非秘密的展示与定位信息

    哈希算法从 bcrypt 迁到 SHA-256：API Key 本身 256 bit 熵，离线爆破本就不可行，
    bcrypt 的慢哈希是为低熵人类密码设计的，在这里只买 CPU 成本（约 300ms/次）
    和「带盐 → 无法按哈希索引查找」的额外代价。存量 bcrypt 密钥靠 hash_algo
    双轨 + 首次鉴权时透明重哈希平滑过渡。
    """

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # ---- 展示信息 ----
    name = Column(String, nullable=False)
    # 用途备注。回答「这把 key 存在哪、给谁用」（Stripe 的 Note / GitHub 的 description）
    description = Column(String, nullable=True)

    # ---- 身份与展示（均非秘密）----
    # 独立随机生成，不是 secret 的派生值 —— 派生值会随 secret 一起泄露
    key_id = Column(String, nullable=True, unique=True, index=True)
    # 形如 sk-bili-cPYYq5MC，仅用于界面上辨认
    prefix = Column(String, nullable=False)
    # 明文后 4 位，在密码管理器里肉眼核对用
    last_four = Column(String, nullable=True)

    # ---- 密钥材料（秘密）----
    key_hash = Column(String, nullable=False, unique=True, index=True)
    # sha256 | bcrypt。bcrypt 仅用于存量密钥的过渡期
    hash_algo = Column(String, nullable=False, default="sha256")

    # ---- 作用域与约束 ----
    # 形如 {"notes": "write", "chat": "read"}。空/None 表示等同用户全部权限
    scopes = Column(JSON, nullable=True)
    # 允许的来源 IP（CIDR 列表）。空表示不限制
    allowed_ips = Column(JSON, nullable=True)
    # 每分钟请求数上限。空表示不限制
    rate_limit_rpm = Column(Integer, nullable=True)

    # ---- 生命周期 ----
    status = Column(String, nullable=False, default=STATUS_ACTIVE)
    # 与 status 保持一致，保留是为了兼容既有代码里的 revoked 判断
    revoked = Column(Boolean, nullable=False, default=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)
    # 轮换宽限期截止。宽限期内旧 key 仍可用，但列表里要标记「即将吊销」
    grace_expires_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    # 预约生效时间
    not_before = Column(DateTime, nullable=True)
    # 轮换链：指向上一把 key
    rotated_from_id = Column(Integer, nullable=True)

    # ---- 活动 ----
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String, nullable=True)
    last_used_endpoint = Column(String, nullable=True)
    last_used_user_agent = Column(String, nullable=True)
    use_count = Column(Integer, nullable=False, default=0)
    last_rotated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())


class ApiKeyUsage(Base):
    """按 key 的调用明细。

    只记 API Key 的调用（网页登录态不记）。限流计数不入此表 —— 那是状态，
    不该无限增长；计数走进程内的固定窗口。
    """

    __tablename__ = "api_key_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True)
    method = Column(String, nullable=False)
    path = Column(String, nullable=False)
    status_code = Column(Integer, nullable=True)
    ip = Column(String, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), index=True)
