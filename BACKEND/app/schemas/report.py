from datetime import datetime
from typing import Optional, Any, Dict
from pydantic import BaseModel

class ReportBase(BaseModel):
    title: str
    start_date: str
    end_date: str
    content: str
    stats: Optional[Dict[str, Any]] = None

class ReportCreate(ReportBase):
    pass

class ReportOut(ReportBase):
    id: int
    generated_at: datetime

    class Config:
        orm_mode = True
        from_attributes = True
