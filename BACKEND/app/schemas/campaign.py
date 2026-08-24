from pydantic import BaseModel
from typing import List

from app.schemas.contact import ContactCreate


class CampaignCreate(BaseModel):
    campaign_name: str
    agent: str
    script: str
    schedule_date: str
    schedule_time: str
    
    selection_type: str = "all"
    start_row: int | None = None
    end_row: int | None = None
    whatsapp_automation: dict | None = None

    contacts: List[ContactCreate]