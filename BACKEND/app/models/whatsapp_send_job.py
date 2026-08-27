from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class WhatsAppSendJob(Base):
    __tablename__ = "whatsapp_send_jobs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    source_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="manual",  # "campaign_manual", "excel_csv", "campaign_automation", "manual"
        index=True,
    )

    source_name: Mapped[str] = mapped_column(
        String,
        nullable=False,  # e.g., "August Real Estate Campaign", "Leads.xlsx", "Campaign Automation"
    )

    campaign_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("campaigns.id"),
        nullable=True,
        index=True,
    )

    trigger_event: Mapped[str | None] = mapped_column(
        String,
        nullable=True,  # e.g., "AI Classification = Interested", "Missed Call", etc.
    )

    content_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="Text",  # "Text", "Image", "Document", "Mixed"
    )

    message_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    attachments: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,  # [{title, type, url, file_name, file_size, mime_type}]
    )

    total_contacts: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    sent_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    failed_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    credits_deducted: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    status: Mapped[str] = mapped_column(
        String,
        default="completed",  # "completed", "partial", "failed", "in_progress"
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    recipients = relationship(
        "WhatsAppSendRecipient",
        back_populates="send_job",
        cascade="all, delete-orphan",
    )
