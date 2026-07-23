from typing import Optional, Dict
from pydantic import BaseModel


class ContactCreate(BaseModel):
    name: str
    phone: str
    metadata_fields: Optional[Dict[str, str]] = None