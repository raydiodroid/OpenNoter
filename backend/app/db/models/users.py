from sqlalchemy import Boolean, Column, DateTime, Integer, String, func


from app.db.engine import Base


class User(Base):
    """OpenNoter 本地的用户记录。

    身份可以来自两条通道：
      - OIDC：oidc_sub + oidc_issuer 唯一标识外部身份（如 authentik）
      - 本地：email + hashed_password 支持离线/应急登录

    同一个邮箱通过任一通道首次登录时会建一条记录，后续两条通道都指向它。
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)

    # 本地密码登录（双通道的后备通道）。仅 OIDC 用户时为空。
    hashed_password = Column(String, nullable=True)

    # OIDC 身份绑定（首次通过 OIDC 登录时写入）
    oidc_sub = Column(String, nullable=True, unique=True, index=True)
    oidc_issuer = Column(String, nullable=True)

    # 权限：admin 可管理用户与全局配置，user 只能操作自己的任务与密钥
    is_superuser = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, server_default=func.now())
    last_login_at = Column(DateTime, nullable=True)

    # ---- 配额（多用户场景必填，否则一个用户能把队列占满）----
    # NULL 表示「跟随全局默认值」（见 app/services/quota.py）
    # 0 表示「不限制」
    max_concurrent_tasks = Column(Integer, nullable=True)   # 同时进行中的任务数
    daily_task_limit = Column(Integer, nullable=True)       # 每日提交任务数
    storage_quota_mb = Column(Integer, nullable=True)       # 笔记产物占用上限
