from pydantic import BaseModel, EmailStr
from typing import List, Optional


class EmailContactItem(BaseModel):
    name: str
    email: str  # Use str to be flexible; validated in service


class EmailCampaignCreate(BaseModel):
    name: str
    subject: str
    html_body: str
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    schedule_date: Optional[str] = None   # ISO datetime string or None for immediate
    schedule_time: Optional[str] = None
    contacts: List[EmailContactItem]
