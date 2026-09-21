from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, SafeDateTime


class SavedContact(Base):
    __tablename__ = "saved_contacts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    phone: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )

    email: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        index=True,
    )

    source: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        default="Manual",
    )

    tag: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        index=True,
        default="General",
    )

    metadata_fields: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    last_called_at: Mapped[datetime | None] = mapped_column(
        SafeDateTime,
        nullable=True,
    )

    call_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    notes: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        SafeDateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        SafeDateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", backref="saved_contacts")
