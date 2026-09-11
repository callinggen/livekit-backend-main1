from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class SmtpMailboxCreate(BaseModel):
    provider: str = Field(default="gmail", description="gmail | outlook | zoho | custom")
    sender_name: str = Field(..., min_length=1, max_length=255)
    sender_email: EmailStr
    smtp_host: str = Field(..., min_length=1, max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_encryption: str = Field(default="tls", description="tls | ssl | none")
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, description="App Password or SMTP password")
    is_default: bool = False
    send_test_on_create: bool = True


class SmtpMailboxTest(BaseModel):
    provider: str = "gmail"
    sender_name: str
    sender_email: EmailStr
    smtp_host: str
    smtp_port: int = 587
    smtp_encryption: str = "tls"
    username: str
    password: str
    recipient_email: Optional[EmailStr] = None


class SmtpMailboxOut(BaseModel):
    id: int
    user_id: int
    provider: str
    sender_name: str
    sender_email: str
    smtp_host: str
    smtp_port: int
    smtp_encryption: str
    username: str
    is_verified: bool
    is_active: bool
    is_default: bool
    last_tested_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SmtpTestResult(BaseModel):
    success: bool
    message: str
    tested_at: datetime
