from datetime import datetime

from sqlalchemy import DateTime, Integer, String, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    campaign_name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    agent: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    script: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    schedule_date: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    schedule_time: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String,
        default="pending",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    
    voicemail_detection: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    upload_source: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    sheet_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    contacts = relationship(
    "Contact",
    back_populates="campaign",
    cascade="all, delete-orphan",
    )
    jobs = relationship(
    "Job",
    back_populates="campaign",
    cascade="all, delete-orphan",
   )