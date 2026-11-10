from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from app.db.engine import Base


class TaskAttempt(Base):
    """Immutable submission ledger; deleting a note does not refund usage."""
    __tablename__ = "task_attempts"
    id = Column(String(36), primary_key=True)
    # Historical identity survives maintenance deletion of the task row.
    task_id = Column(String, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
