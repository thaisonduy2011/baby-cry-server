from sqlalchemy import Column, Integer, DateTime
from database import Base

class CrySession(Base):
    __tablename__ = "cry_sessions"

    id = Column(Integer, primary_key=True, index=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
