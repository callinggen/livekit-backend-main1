from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.user import User
from app.services.notification_service import notification_service


class WhatsAppCreditService:
    """
    Centralized, authoritative WhatsApp credit calculation and management service.
    Rules:
    - Text message: 1 credit per recipient
    - Image message: 1 credit per recipient (EXPLICIT: Exactly 1 credit, not 2)
    - Document message: 1 credit per recipient
    """

    CREDIT_PER_TEXT = 1
    CREDIT_PER_IMAGE = 1      # Explicit requirement: exactly 1 credit
    CREDIT_PER_DOCUMENT = 1

    @classmethod
    def calculate_item_credits(cls, item_type: str) -> int:
        """Get credit cost for a single item type per recipient."""
        t = (item_type or "").strip().lower()
        if t == "text":
            return cls.CREDIT_PER_TEXT
        elif t == "image":
            return cls.CREDIT_PER_IMAGE
        elif t == "document":
            return cls.CREDIT_PER_DOCUMENT
        return 1

    @classmethod
    def calculate_total_credits(cls, items: List[Dict[str, Any]], recipient_count: int) -> int:
        """
        Calculate total required credits for sending given items to recipient_count contacts.
        """
        if recipient_count <= 0 or not items:
            return 0

        credits_per_recipient = sum(cls.calculate_item_credits(item.get("type", "text")) for item in items)
        return credits_per_recipient * recipient_count

    @classmethod
    async def verify_and_reserve_credits(
        cls,
        db: AsyncSession,
        user: User,
        required_credits: int,
    ) -> bool:
        """
        Authoritatively check if the user has enough credits.
        Raises HTTPException if insufficient.
        """
        await db.refresh(user)
        if user.credits < required_credits:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Insufficient WhatsApp credits. Required: {required_credits}, Available: {user.credits}. "
                    "Please recharge your credits to proceed."
                ),
            )
        return True

    @classmethod
    async def deduct_credits(
        cls,
        db: AsyncSession,
        user: User,
        amount: int,
    ) -> int:
        """
        Safely deduct credits and trigger notification if threshold breached.
        """
        if amount <= 0:
            return user.credits

        user.credits = max(0, user.credits - amount)
        await db.commit()
        await db.refresh(user)

        try:
            await notification_service.check_and_trigger_credit_notifications(db, user)
        except Exception as e:
            print(f"[WhatsAppCreditService] Warning: Failed to check credit notifications: {e}")

        return user.credits
