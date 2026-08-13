from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.database import Base

class ContactFormUser(Base):
    __tablename__ = "contact_form_users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, index=True)
    phone = Column(String)
    company = Column(String)
    industry = Column(String)
    appointment_time = Column(DateTime)
    status = Column(String, default="booked")
    created_at = Column(DateTime, default=datetime.utcnow)
