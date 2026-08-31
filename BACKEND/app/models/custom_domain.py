from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Boolean, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CustomEmailDomain(Base):
    __tablename__ = "custom_email_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Multi-tenant isolation: every domain belongs to a specific user/organization
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    resend_domain_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Status: not_started | pending | verified | failed
    status: Mapped[str] = mapped_column(String(50), default="pending")

    # JSON structure storing exact DNS records returned by Resend
    # Format: list of { "record": str, "type": str, "name": str, "value": str, "ttl": str, "status": str, "priority": int|None, "dns_verified": bool, "observed_value": str|None }
    dns_records: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    sending_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    region: Mapped[str] = mapped_column(String(50), default="us-east-1")

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = relationship("User", backref="custom_domains")
