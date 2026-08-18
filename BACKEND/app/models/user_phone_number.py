from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserPhoneNumber(Base):
    __tablename__ = "user_phone_numbers"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # 1. Region / Country
    region: Mapped[str] = mapped_column(
        String,
        default="India (+91)",
    )

    # 2. Phone Number
    phone_number: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    # 3. Number Type
    number_type: Mapped[str] = mapped_column(
        String,
        default="Mobile",
    )

    # 4. Provider
    provider_name: Mapped[str] = mapped_column(
        String,
        default="Vobiz",
    )

    # 5. Provider Account ID
    provider_account_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # 6. API Key / Auth Token
    api_key_auth_token: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # 7. SIP ID
    sip_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # 8. SIP Username
    sip_username: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # 9. SIP Password
    sip_password: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    # 10. Status
    status: Mapped[str] = mapped_column(
        String,
        default="Active",
    )

    # 11. Default Number
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
