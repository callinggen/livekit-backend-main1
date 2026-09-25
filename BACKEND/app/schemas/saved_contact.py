from datetime import datetime
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, ConfigDict


class SavedContactBase(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    source: Optional[str] = "Manual"
    tag: Optional[str] = "General"
    metadata_fields: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class SavedContactCreate(SavedContactBase):
    pass


class SavedContactUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    source: Optional[str] = None
    tag: Optional[str] = None
    metadata_fields: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class SavedContactResponse(SavedContactBase):
    id: int
    user_id: int
    call_count: int = 0
    last_called_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SavedContactBatchCreate(BaseModel):
    contacts: List[SavedContactCreate]
    default_tag: Optional[str] = None
    source: Optional[str] = "Batch Upload"


class SavedContactBatchDelete(BaseModel):
    contact_ids: List[int]


class SavedContactStats(BaseModel):
    total_contacts: int
    valid_phones: int
    with_email: int
    total_tags: int
    recent_added_this_week: int


class SavedContactListResponse(BaseModel):
    items: List[SavedContactResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class SavedContactListSummary(BaseModel):
    tag: str
    total_contacts: int
    valid_phones: int
    with_email: int
    sources: List[str] = []
    source_url: Optional[str] = None
    is_google_sheet: bool = False
    last_synced_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SavedContactRenameList(BaseModel):
    old_tag: str
    new_tag: str


class SavedContactSyncRequest(BaseModel):
    google_sheet_url: Optional[str] = None


