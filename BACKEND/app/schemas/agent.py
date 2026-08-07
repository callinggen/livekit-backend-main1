from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class AgentBase(BaseModel):
    name: str
    language: str
    voice: str
    script: str

class AgentCreate(AgentBase):
    pass

class AgentResponse(AgentBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
