from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    phone: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String,
        default="pending",
    )

    response: Mapped[str] = mapped_column(
        String,
        default="",
    )

    transcript: Mapped[str] = mapped_column(
        String,
        default="",
    )

    duration: Mapped[str] = mapped_column(
        String,
        default="",
    )

    customer_name: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    appointment_date: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    appointment_time: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    campaign = relationship("Campaign", back_populates="contacts")