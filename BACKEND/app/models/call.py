from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id"),
        nullable=False,
    )

    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=False,
    )

    phone: Mapped[str] = mapped_column(
        String,
        nullable=False,
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
        default=datetime.utcnow,
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

    recording_url: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )