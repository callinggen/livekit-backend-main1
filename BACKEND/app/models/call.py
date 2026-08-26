from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id"),
        nullable=True,
    )

    campaign_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("campaigns.id"),
        nullable=True,
    )

    detection_metadata: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=True,
    )

    phone: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    direction: Mapped[str] = mapped_column(
        String,
        default="outbound",
    )

    caller_number: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    called_number: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    phone_line_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user_phone_numbers.id"),
        nullable=True,
    )

    tenant_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    agent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("agents.id"),
        nullable=True,
    )
    room_name: Mapped[str | None] = mapped_column(
    String,
    nullable=True,
    )

    livekit_participant_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String,
        default="queued",
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    duration: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    transcript: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    summary: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    human_response: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    category: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    recording_url: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    credits_deducted: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )