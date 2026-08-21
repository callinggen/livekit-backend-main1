from datetime import datetime

from sqlalchemy import DateTime, Integer, String, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EmailContact(Base):
    __tablename__ = "email_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    email_campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_campaigns.id"), nullable=False
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)

    # Status: pending | sent | failed | bounced | unsubscribed
    status: Mapped[str] = mapped_column(String, default="pending")

    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    campaign = relationship("EmailCampaign", back_populates="contacts")
