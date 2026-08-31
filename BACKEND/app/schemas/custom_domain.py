from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class DnsRecordItem(BaseModel):
    record: str = Field(..., description="Record label, e.g. SPF, DKIM, Return-Path")
    type: str = Field(..., description="DNS Record Type: TXT, CNAME, MX")
    name: str = Field(..., description="Host or Subdomain name")
    value: str = Field(..., description="Expected Record Value")
    ttl: Optional[str] = "Auto"
    priority: Optional[int] = None
    status: Optional[str] = "pending"
    dns_verified: Optional[bool] = False
    observed_value: Optional[str] = None


class CustomDomainCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=255, description="Domain name, e.g. company.com")
    region: Optional[str] = Field("us-east-1", description="Resend region, e.g. us-east-1, eu-west-1")


class CustomDomainOut(BaseModel):
    id: int
    domain: str
    resend_domain_id: Optional[str] = None
    status: str
    is_verified: bool
    sending_enabled: bool
    region: str
    dns_records: Optional[List[DnsRecordItem]] = []
    last_checked_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DnsVerificationResponse(BaseModel):
    domain_id: int
    domain: str
    status: str
    is_verified: bool
    sending_enabled: bool
    all_dns_matched: bool
    dns_records: List[DnsRecordItem]
    message: str


class VerifiedSenderOut(BaseModel):
    email: str
    display_name: str
    domain: str
    is_default: bool
    is_verified: bool
