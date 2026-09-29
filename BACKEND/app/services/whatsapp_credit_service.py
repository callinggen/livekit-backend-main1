from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.user import User
from app.services.notification_service import notification_service
from app.services.universal_credit_service import UniversalCreditService


class WhatsAppCreditService:
    """
    Centralized WhatsApp credit calculation and management service powered by UniversalCreditService.
    Specification Rule:
    10 WhatsApp messages = 1 credit (0.1 credit per message)
    """

    CREDIT_PER_MESSAGE = 0.1  # Base default: 1 credit = 10 messages

    @classmethod
    def calculate_item_credits(cls, item_type: str) -> float:
        """Get credit cost for a single message/item (0.1 credit)."""
        return 0.1

    @classmethod
    def calculate_total_credits(cls, items: List[Dict[str, Any]], recipient_count: int) -> float:
        """
        Calculate total required credits for sending given items to recipient_count contacts.
        Formula: items_count * recipient_count * 0.1 credit
        """
        if recipient_count <= 0 or not items:
            return 0.0

        item_count = len(items)
        return round(item_count * recipient_count * 0.1, 4)

    @classmethod
    def check_has_sufficient_credits(cls, user: User, required_credits: float) -> bool:
        """Non-raising credit check helper."""
        if required_credits <= 0:
            return True
        total_balance = float(user.subscription_credit_balance or 0.0) + float(user.topup_credit_balance or 0.0)
        return total_balance >= required_credits

    @classmethod
    async def verify_and_reserve_credits(
        cls,
        db: AsyncSession,
        user: User,
        required_credits: float,
    ) -> bool:
        """
        Authoritatively check if the user has enough credits.
        Raises HTTPException if insufficient.
        """
        if required_credits <= 0:
            return True

        await db.refresh(user)
        total_balance = float(user.subscription_credit_balance or 0.0) + float(user.topup_credit_balance or 0.0)
        if total_balance < required_credits:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Insufficient credits for WhatsApp. Required: {round(required_credits, 2)}, Available: {round(total_balance, 2)}. "
                    "Please recharge your credits to proceed."
                ),
            )
        return True

    @classmethod
    async def deduct_credits(
        cls,
        db: AsyncSession,
        user: User,
        amount_or_messages: float,
        reference_id: str | None = None,
        description: str = "",
    ) -> float:
        """
        Deduct credits using UniversalCreditService.
        """
        if amount_or_messages <= 0:
            return float(user.credits or 0.0)

        # If amount_or_messages is passed as messages count or credit amount
        # We handle both: if message count >= 1 and passed as int/float message count
        # In UniversalCreditService, we pass count_or_duration=message_count
        tx = await UniversalCreditService.deduct_credits(
            db=db,
            user_id=user.id,
            service="whatsapp",
            count_or_duration=amount_or_messages if amount_or_messages >= 1 else amount_or_messages * 10,
            reference_id=reference_id,
            description=description or f"WhatsApp – {int(amount_or_messages if amount_or_messages >= 1 else amount_or_messages * 10)} message(s)"
        )

        try:
            await notification_service.check_and_trigger_credit_notifications(db, user)
        except Exception as e:
            print(f"[WhatsAppCreditService] Warning: Failed to check credit notifications: {e}")

        return round(float(user.credits or 0.0), 2)
