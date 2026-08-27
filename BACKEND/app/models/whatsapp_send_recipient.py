from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WhatsAppSendRecipient(Base):
    __tablename__ = "whatsapp_send_recipients"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    send_job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("whatsapp_send_jobs.id"),
        nullable=False,
        index=True,
    )

    contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("contacts.id"),
        nullable=True,
        index=True,
    )

    call_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("calls.id"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="there",
    )

    phone: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String,
        default="sent",  # "sent", "delivered", "failed", "skipped_duplicate", "insufficient_credits"
        index=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    details: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,  # Stores provider response / item breakdown
    )

    send_job = relationship(
        "WhatsAppSendJob",
        back_populates="recipients",
    )
