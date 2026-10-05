from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Float, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, SafeDateTime


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    transaction_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    type: Mapped[str] = mapped_column(
        String,
        index=True,
        nullable=False,
        # subscription_credit, topup_credit, calling_usage, whatsapp_usage, email_usage, refund, manual_adjustment, bonus_credit, expiration
    )

    service: Mapped[str] = mapped_column(
        String,
        index=True,
        default="system",
        # calling, whatsapp, email, system
    )

    credits: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        # e.g. -7.50 for usage, +2000.0 for subscription
    )

    reference_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        # call_id, job_id, email_id, razorpay_payment_id, etc.
    )

    description: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    balance_before: Mapped[float] = mapped_column(
        Float,
        default=0.0,
    )

    balance_after: Mapped[float] = mapped_column(
        Float,
        default=0.0,
    )

    rate_version: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        default="v1.0",
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        SafeDateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    user = relationship("User", back_populates="credit_transactions")


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    service: Mapped[str] = mapped_column(
        String,
        index=True,
        nullable=False,
        # calling, whatsapp, email
    )

    reference_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    message_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    email_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    credits_consumed: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        SafeDateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class AdminRateConfig(Base):
    __tablename__ = "admin_rate_configs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    key: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=False,
        # AI_CALLING_CREDITS_PER_MINUTE, WHATSAPP_MESSAGES_PER_CREDIT, EMAILS_PER_CREDIT
    )

    value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        String,
        default="",
    )

    updated_at: Mapped[datetime] = mapped_column(
        SafeDateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
