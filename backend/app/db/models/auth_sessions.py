"""Revocable browser sessions. Raw JWTs and session identifiers are never stored."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from app.db.engine import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    auth_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
