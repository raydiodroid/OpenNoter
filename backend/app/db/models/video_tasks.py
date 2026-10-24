from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import declarative_base

from app.db.engine import Base


class VideoTask(Base):
    __tablename__ = "video_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    task_id = Column(String, unique=True, nullable=False)
    # 任务归属。nullable=True 是为了兼容存量数据：迁移时统一回填到首个管理员。
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())

    # ---- 任务状态（migrate_task_status.py 补充，见 README/审计报告）----
    # 状态此前只存在 note_results/*.status.json 里，而删除任务会物理删掉它，
    # 导致「累计任务」「24h 失败」这类监控指标随用户删笔记而倒退。
    # 落一份到 DB，让监控有不随用户操作消失的来源。
    status = Column(String(32), nullable=True, index=True)
    error = Column(String(512), nullable=True)
    failed_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, nullable=True)
    # 软删除标记：用户删笔记时不再物理删除这一行，只打时间戳。
    # 否则「累计任务」「24h 失败」会随用户删笔记一起倒退/消失——
    # 监控指标不该被普通用户操作篡改（审计结论）。
    deleted_at = Column(DateTime, nullable=True, index=True)
