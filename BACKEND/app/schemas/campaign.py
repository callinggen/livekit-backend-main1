from pydantic import BaseModel
from typing import List

from app.schemas.contact import ContactCreate


class CampaignCreate(BaseModel):
    campaign_name: str
    agent: str
    script: str
    schedule_date: str
    schedule_time: str
    
    outbound_phone_number: str | None = None
    selection_type: str = "all"

    start_row: int | None = None
    end_row: int | None = None
    
    upload_source: str | None = None
    sheet_name: str | None = None

    contacts: List[ContactCreate]