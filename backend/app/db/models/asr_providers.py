from sqlalchemy import Boolean, Column, String
from app.db.engine import Base


class AsrProvider(Base):
    __tablename__ = 'asr_providers'

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    engine = Column(String, nullable=False)
    logo = Column(String, nullable=False)
    base_url = Column(String, nullable=False, default='')
    api_key = Column(String, nullable=False, default='')
    model_name = Column(String, nullable=False, default='')
    enabled = Column(Boolean, nullable=False, default=True)
    builtin = Column(Boolean, nullable=False, default=False)
