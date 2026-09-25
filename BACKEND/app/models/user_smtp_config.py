from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Boolean, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserSmtpConfig(Base):
    __tablename__ = "user_smtp_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Multi-tenant isolation: every SMTP mailbox belongs to a specific user
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    # Provider preset: gmail | outlook | zoho | custom
    provider: Mapped[str] = mapped_column(String(50), default="gmail", nullable=False)

    # Display / Sender information
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Server connection settings
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_encryption: Mapped[str] = mapped_column(String(20), default="tls", nullable=False)  # tls | ssl | none

    # Authentication credentials
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)

    # Operational status
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = relationship("User", backref="smtp_configs")
