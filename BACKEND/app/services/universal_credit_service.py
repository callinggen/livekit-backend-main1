import uuid
from datetime import datetime, timezone
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.user import User
from app.models.credit_models import CreditTransaction, UsageEvent, AdminRateConfig


class UniversalCreditService:

    @staticmethod
    async def get_rates(db: AsyncSession) -> dict:
        """
        Fetch active credit conversion rates from admin config or defaults.
        """
        default_rates = {
            "AI_CALLING_CREDITS_PER_MINUTE": 15.0,
            "WHATSAPP_MESSAGES_PER_CREDIT": 10.0,
            "EMAILS_PER_CREDIT": 5.0,
        }
        stmt = select(AdminRateConfig)
        res = await db.execute(stmt)
        configs = res.scalars().all()
        rates = dict(default_rates)
        for c in configs:
            rates[c.key] = float(c.value)
        return rates

    @staticmethod
    async def get_user_wallet(db: AsyncSession, user_id: int) -> dict:
        """
        Get universal credit wallet balances, rates, and low-credit status.
        """
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        sub_bal = float(user.subscription_credit_balance or 0.0)
        top_bal = float(user.topup_credit_balance or 0.0)
        total_bal = sub_bal + top_bal

        # Sync user.credits column
        if user.credits != total_bal:
            user.credits = total_bal
            await db.commit()

        rates = await UniversalCreditService.get_rates(db)

        # Calculate max equivalent usage
        calling_rate = rates.get("AI_CALLING_CREDITS_PER_MINUTE", 15.0)
        whatsapp_rate = rates.get("WHATSAPP_MESSAGES_PER_CREDIT", 10.0)
        email_rate = rates.get("EMAILS_PER_CREDIT", 5.0)

        max_calling_mins = round(total_bal / (calling_rate if calling_rate > 0 else 15.0), 1)
        max_whatsapp_msgs = int(total_bal * whatsapp_rate)
        max_emails = int(total_bal * email_rate)

        # Low balance alerts check (based on typical tier baseline or absolute threshold)
        warning_level = "normal"
        if total_bal <= 0:
            warning_level = "depleted" # 0 credits -> stop billable actions
        elif total_bal <= 50:
            warning_level = "critical" # 5% warning
        elif total_bal <= 100:
            warning_level = "urgent"   # 10% warning
        elif total_bal <= 250:
            warning_level = "warning"  # 25% warning

        return {
            "total_balance": round(total_bal, 2),
            "subscription_credit_balance": round(sub_bal, 2),
            "topup_credit_balance": round(top_bal, 2),
            "warning_level": warning_level,
            "rates": {
                "ai_calling_credits_per_minute": calling_rate,
                "ai_calling_pulse_seconds": 4,
                "whatsapp_messages_per_credit": whatsapp_rate,
                "emails_per_credit": email_rate,
            },
            "max_equivalent": {
                "calling_minutes": max_calling_mins,
                "whatsapp_messages": max_whatsapp_msgs,
                "emails": max_emails,
            }
        }

    @staticmethod
    async def check_preflight(db: AsyncSession, user_id: int, service: str, count_or_duration: float = 1.0) -> tuple[bool, float, str]:
        """
        Pre-flight check before starting an action.
        For calling: count_or_duration = seconds or minimum 60s (1 min = 15 credits).
        For whatsapp: count_or_duration = message count.
        For email: count_or_duration = email count.
        """
        rates = await UniversalCreditService.get_rates(db)
        user = await db.get(User, user_id)
        if not user:
            return False, 0.0, "User not found"

        total_bal = float(user.subscription_credit_balance or 0.0) + float(user.topup_credit_balance or 0.0)

        if service == "calling":
            calling_rate = rates.get("AI_CALLING_CREDITS_PER_MINUTE", 15.0)
            # Minimum billable threshold: 1 min call (15 credits) or requested duration
            mins = max(1.0, count_or_duration / 60.0) if count_or_duration > 0 else 1.0
            required_credits = round(mins * calling_rate, 4)
        elif service == "whatsapp":
            whatsapp_rate = rates.get("WHATSAPP_MESSAGES_PER_CREDIT", 10.0)
            required_credits = round(count_or_duration / (whatsapp_rate if whatsapp_rate > 0 else 10.0), 4)
        elif service == "email":
            email_rate = rates.get("EMAILS_PER_CREDIT", 5.0)
            required_credits = round(count_or_duration / (email_rate if email_rate > 0 else 5.0), 4)
        else:
            required_credits = float(count_or_duration)

        if total_bal < required_credits:
            return False, required_credits, f"Insufficient balance. Required: {required_credits} credits, Available: {round(total_bal, 2)} credits."

        return True, required_credits, "OK"

    @staticmethod
    async def deduct_credits(
        db: AsyncSession,
        user_id: int,
        service: str,
        count_or_duration: float,
        reference_id: str | None = None,
        description: str = "",
        metadata_json: dict | None = None,
    ) -> CreditTransaction:
        """
        Deduct credits according to priority: subscription_credit_balance -> topup_credit_balance.
        Calculates exact per-second / per-message / per-email usage.
        """
        rates = await UniversalCreditService.get_rates(db)
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # 1. Compute exact required credits
        raw_duration = None
        raw_messages = None
        raw_emails = None
        tx_type = f"{service}_usage"

        if service == "calling":
            # count_or_duration is duration in seconds
            raw_duration = int(count_or_duration)
            import math
            # 4-second pulse billing: 1 credit on pickup (1-4s), +1 credit for every 4s thereafter (math.ceil(duration / 4))
            # 60 seconds = 15 credits
            credits_to_deduct = float(math.ceil(max(1, raw_duration) / 4.0)) if raw_duration > 0 else 0.0
            if not description:
                mins = round(raw_duration / 60.0, 1)
                description = f"AI Calling – {raw_duration}s ({int(credits_to_deduct)} credits @ 1 cr/4s)"
        elif service == "whatsapp":
            raw_messages = int(count_or_duration)
            whatsapp_rate = rates.get("WHATSAPP_MESSAGES_PER_CREDIT", 10.0)
            credits_to_deduct = round(count_or_duration / (whatsapp_rate if whatsapp_rate > 0 else 10.0), 4)
            if not description:
                description = f"WhatsApp – {raw_messages} message(s)"
        elif service == "email":
            raw_emails = int(count_or_duration)
            email_rate = rates.get("EMAILS_PER_CREDIT", 5.0)
            credits_to_deduct = round(count_or_duration / (email_rate if email_rate > 0 else 5.0), 4)
            if not description:
                description = f"Email – {raw_emails} email(s)"
        else:
            credits_to_deduct = float(count_or_duration)
            if not description:
                description = f"Usage – {service}"

        sub_bal = float(user.subscription_credit_balance or 0.0)
        top_bal = float(user.topup_credit_balance or 0.0)
        total_before = sub_bal + top_bal

        if total_before < credits_to_deduct:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Insufficient balance. Required: {credits_to_deduct} credits, Available: {round(total_before, 2)} credits."
            )

        # 2. Priority deduction: Subscription credits first, then Top-up credits
        remaining_to_deduct = credits_to_deduct
        if sub_bal >= remaining_to_deduct:
            sub_bal -= remaining_to_deduct
            remaining_to_deduct = 0.0
        else:
            remaining_to_deduct -= sub_bal
            sub_bal = 0.0
            top_bal -= remaining_to_deduct
            remaining_to_deduct = 0.0

        user.subscription_credit_balance = round(sub_bal, 4)
        user.topup_credit_balance = round(top_bal, 4)
        total_after = user.subscription_credit_balance + user.topup_credit_balance
        user.credits = total_after

        # 3. Create CreditTransaction
        tx_id = f"tx_{uuid.uuid4().hex[:12]}"
        tx = CreditTransaction(
            transaction_id=tx_id,
            user_id=user_id,
            type=tx_type,
            service=service,
            credits=-abs(credits_to_deduct),
            reference_id=reference_id,
            description=description,
            balance_before=round(total_before, 2),
            balance_after=round(total_after, 2),
            rate_version="v1.0",
            metadata_json=metadata_json or {},
            created_at=datetime.now(timezone.utc),
        )
        db.add(tx)

        # 4. Create UsageEvent
        event = UsageEvent(
            user_id=user_id,
            service=service,
            reference_id=reference_id,
            duration_seconds=raw_duration,
            message_count=raw_messages,
            email_count=raw_emails,
            credits_consumed=credits_to_deduct,
            created_at=datetime.now(timezone.utc),
        )
        db.add(event)

        await db.commit()
        await db.refresh(tx)
        return tx

    @staticmethod
    async def add_credits(
        db: AsyncSession,
        user_id: int,
        amount: float,
        credit_type: str = "topup_credit",
        reference_id: str | None = None,
        description: str = "",
        metadata_json: dict | None = None,
    ) -> CreditTransaction:
        """
        Add credits (topup, subscription renewal, refund, bonus, manual adjustment).
        """
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        sub_bal = float(user.subscription_credit_balance or 0.0)
        top_bal = float(user.topup_credit_balance or 0.0)
        total_before = sub_bal + top_bal

        if credit_type == "subscription_credit":
            sub_bal += amount
            user.subscription_credit_balance = round(sub_bal, 4)
            if not description:
                description = f"+{int(amount)} Subscription Credit"
        else:
            top_bal += amount
            user.topup_credit_balance = round(top_bal, 4)
            if not description:
                description = f"+{int(amount)} Credit Top-up"

        total_after = user.subscription_credit_balance + user.topup_credit_balance
        user.credits = total_after

        tx_id = f"tx_{uuid.uuid4().hex[:12]}"
        tx = CreditTransaction(
            transaction_id=tx_id,
            user_id=user_id,
            type=credit_type,
            service="system",
            credits=amount,
            reference_id=reference_id,
            description=description,
            balance_before=round(total_before, 2),
            balance_after=round(total_after, 2),
            rate_version="v1.0",
            metadata_json=metadata_json or {},
            created_at=datetime.now(timezone.utc),
        )
        db.add(tx)
        await db.commit()
        await db.refresh(tx)
        return tx

    @staticmethod
    async def get_usage_summary(db: AsyncSession, user_id: int) -> dict:
        """
        Aggregate usage across AI Calling, WhatsApp, and Email.
        """
        # 1. Calling
        call_stmt = select(
            func.coalesce(func.sum(UsageEvent.duration_seconds), 0),
            func.coalesce(func.sum(UsageEvent.credits_consumed), 0.0),
        ).where(UsageEvent.user_id == user_id, UsageEvent.service == "calling")
        call_res = (await db.execute(call_stmt)).one()
        total_call_secs = call_res[0]
        total_call_credits = call_res[1]

        # 2. WhatsApp
        wa_stmt = select(
            func.coalesce(func.sum(UsageEvent.message_count), 0),
            func.coalesce(func.sum(UsageEvent.credits_consumed), 0.0),
        ).where(UsageEvent.user_id == user_id, UsageEvent.service == "whatsapp")
        wa_res = (await db.execute(wa_stmt)).one()
        total_wa_msgs = wa_res[0]
        total_wa_credits = wa_res[1]

        # 3. Email
        em_stmt = select(
            func.coalesce(func.sum(UsageEvent.email_count), 0),
            func.coalesce(func.sum(UsageEvent.credits_consumed), 0.0),
        ).where(UsageEvent.user_id == user_id, UsageEvent.service == "email")
        em_res = (await db.execute(em_stmt)).one()
        total_emails = em_res[0]
        total_email_credits = em_res[1]

        total_consumed = total_call_credits + total_wa_credits + total_email_credits

        return {
            "calling": {
                "duration_seconds": int(total_call_secs),
                "duration_minutes": round(total_call_secs / 60.0, 1),
                "credits_consumed": round(float(total_call_credits), 2),
            },
            "whatsapp": {
                "messages_sent": int(total_wa_msgs),
                "credits_consumed": round(float(total_wa_credits), 2),
            },
            "email": {
                "emails_sent": int(total_emails),
                "credits_consumed": round(float(total_email_credits), 2),
            },
            "total_credits_consumed": round(float(total_consumed), 2),
        }

    @staticmethod
    async def get_transactions(
        db: AsyncSession,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CreditTransaction]:
        """
        Get credit transaction history for a user.
        """
        stmt = (
            select(CreditTransaction)
            .where(CreditTransaction.user_id == user_id)
            .order_by(desc(CreditTransaction.created_at))
            .limit(limit)
            .offset(offset)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def update_admin_rate(db: AsyncSession, key: str, value: float, description: str = "") -> AdminRateConfig:
        """
        Admin updates a rate config.
        """
        stmt = select(AdminRateConfig).where(AdminRateConfig.key == key)
        res = await db.execute(stmt)
        config = res.scalar_one_or_none()
        if not config:
            config = AdminRateConfig(key=key, value=value, description=description)
            db.add(config)
        else:
            config.value = value
            if description:
                config.description = description
            config.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(config)
        return config
