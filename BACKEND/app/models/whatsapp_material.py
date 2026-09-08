from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WhatsAppMaterial(Base):
    __tablename__ = "whatsapp_materials"

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

    title: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    type: Mapped[str] = mapped_column(
        String,
        nullable=False,  # "text", "image", "document"
        index=True,
    )

    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,  # Text template content with placeholders like {{name}}
    )

    file_path: Mapped[str | None] = mapped_column(
        String,
        nullable=True,  # Local filesystem path
    )

    file_url: Mapped[str | None] = mapped_column(
        String,
        nullable=True,  # Public or served URL for Evolution API / frontend
    )

    mime_type: Mapped[str | None] = mapped_column(
        String,
        nullable=True,  # e.g., "application/pdf", "image/png"
    )

    file_size: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,  # File size in bytes
    )

    tags: Mapped[str | None] = mapped_column(
        String,
        nullable=True,  # Comma-separated or single category tag
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
