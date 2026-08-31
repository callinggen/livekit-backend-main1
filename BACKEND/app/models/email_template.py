from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailMarketingTemplate(Base):
    __tablename__ = "email_marketing_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # user_id is None for global/system curated templates available to everyone
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # Business, Sales, Events, Newsletter, Seasonal
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    preview_text: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
