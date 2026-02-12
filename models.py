from sqlalchemy import Column, Integer, DateTime
from database import Base
from datetime import datetime, timezone, timedelta

VN_TZ = timezone(timedelta(hours=7))

class CryLog(Base):
    __tablename__ = "cry_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(VN_TZ))
