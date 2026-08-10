from datetime import datetime
from sqlalchemy import DateTime, Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

class UserNotificationState(Base):
    __tablename__ = "user_notification_states"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # State tracking flags for credit alerts
    low_credit_sent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    critical_credit_sent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    credits_exhausted_sent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    last_low_credit_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    last_critical_credit_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    last_credits_exhausted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
