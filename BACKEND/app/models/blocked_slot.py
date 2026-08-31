from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.database import Base

class BlockedSlot(Base):
    __tablename__ = "blocked_slots"

    id = Column(Integer, primary_key=True, index=True)
    blocked_date = Column(String, index=True) # YYYY-MM-DD format
    slot_time = Column(String, nullable=True) # HH:MM format or None for entire day
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
