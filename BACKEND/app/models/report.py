from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, JSON, ForeignKey
from sqlalchemy.orm import validates
from app.database import Base, SafeDateTime

class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    title = Column(String(255), nullable=False)
    start_date = Column(String(50), nullable=False)
    end_date = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    stats = Column(JSON, nullable=True)
    generated_at = Column(SafeDateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)

    @validates("generated_at")
    def validate_generated_at(self, key, value):
        if value is not None and hasattr(value, "tzinfo") and value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value
