from datetime import datetime
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    full_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    email: Mapped[str | None] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=True,
    )

    phone_number: Mapped[str | None] = mapped_column(
        String,
        unique=True,
        index=True,
        nullable=True,
    )

    hashed_password: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    is_first_login: Mapped[bool] = mapped_column(
        default=True,
    )

    is_admin: Mapped[bool] = mapped_column(
        default=False,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    credits: Mapped[int] = mapped_column(
        Integer,
        default=2000,
    )

    subscription_plan: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    company_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    industry: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    agent_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    agent_language: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    agent_voice: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    agent_script: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    agents = relationship("Agent", back_populates="user", cascade="all, delete-orphan")
