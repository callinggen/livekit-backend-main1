from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class EmailTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    category: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(default="", max_length=255)
    subject: str = Field(..., min_length=1, max_length=200)
    html_body: str = Field(..., min_length=1)
    preview_text: Optional[str] = Field(default=None, max_length=255)


class EmailTemplateCreate(EmailTemplateBase):
    pass


class EmailTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    category: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    subject: Optional[str] = Field(None, min_length=1, max_length=200)
    html_body: Optional[str] = Field(None, min_length=1)
    preview_text: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = Field(None)


class EmailTemplateOut(EmailTemplateBase):
    id: int
    user_id: Optional[int] = None
    is_system: bool = False
    status: str = "active"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
