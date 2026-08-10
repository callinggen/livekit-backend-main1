from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User
from app.models.notification_state import UserNotificationState
from app.services.email_service import email_service

class NotificationService:
    def get_plan_thresholds(self, plan_name: str | None, credits: int) -> tuple[int, int]:
        """
        Returns (low_threshold, critical_threshold) based on user plan:
        - Demo (50 credits): low=15, critical=5
        - Starter (500 credits): low=100, critical=20
        - Standard (2000 credits): low=200, critical=20
        - Pro / Custom (5000+ credits): low=500, critical=50
        """
        plan = (plan_name or "").strip().lower()

        if "demo" in plan:
            return (15, 5)
        elif "starter" in plan:
            return (100, 20)
        elif "standard" in plan:
            return (200, 20)
        elif "pro" in plan or "enterprise" in plan:
            return (500, 50)
        
        # Fallback based on credits scale if plan name is dynamic
        if credits <= 100:
            return (15, 5)
        elif credits <= 1000:
            return (100, 20)
        elif credits <= 3000:
            return (200, 20)
        else:
            return (500, 50)

    async def get_or_create_state(self, db: AsyncSession, user_id: int) -> UserNotificationState:
        stmt = select(UserNotificationState).where(UserNotificationState.user_id == user_id)
        res = await db.execute(stmt)
        state = res.scalars().first()
        if not state:
            state = UserNotificationState(user_id=user_id)
            db.add(state)
            await db.commit()
            await db.refresh(state)
        return state

    async def check_and_trigger_credit_notifications(self, db: AsyncSession, user: User):
        if not user or not user.email:
            return

        state = await self.get_or_create_state(db, user.id)
        credits = user.credits or 0
        plan_name = user.subscription_plan or "Standard"
        low_thresh, crit_thresh = self.get_plan_thresholds(plan_name, credits)

        # ── Cycle Reset Rules ──────────────────────────────────────────────
        # If user balance is recharged back above thresholds, reset sent flags for new cycle
        if credits >= low_thresh:
            if state.low_credit_sent or state.critical_credit_sent or state.credits_exhausted_sent:
                state.low_credit_sent = False
                state.critical_credit_sent = False
                state.credits_exhausted_sent = False
                await db.commit()
        elif credits > crit_thresh:
            if state.critical_credit_sent or state.credits_exhausted_sent:
                state.critical_credit_sent = False
                state.credits_exhausted_sent = False
                await db.commit()

        # ── Threshold Trigger Rules ─────────────────────────────────────────
        now = datetime.utcnow()

        if credits <= 0:
            if not state.credits_exhausted_sent:
                try:
                    email_service.send_credits_exhausted_email(
                        to_email=user.email,
                        full_name=user.full_name or "Client",
                        company_name=user.company_name or "Your Account",
                        plan_name=plan_name,
                    )
                    state.credits_exhausted_sent = True
                    state.last_credits_exhausted_at = now
                    await db.commit()
                except Exception as e:
                    print(f"[NotificationService] Error sending exhausted email: {e}")

        elif credits <= crit_thresh:
            if not state.critical_credit_sent:
                try:
                    email_service.send_critical_credit_email(
                        to_email=user.email,
                        full_name=user.full_name or "Client",
                        company_name=user.company_name or "Your Account",
                        remaining_credits=credits,
                        plan_name=plan_name,
                    )
                    state.critical_credit_sent = True
                    state.last_critical_credit_at = now
                    await db.commit()
                except Exception as e:
                    print(f"[NotificationService] Error sending critical credit email: {e}")

        elif credits < low_thresh:
            if not state.low_credit_sent:
                try:
                    email_service.send_low_credit_email(
                        to_email=user.email,
                        full_name=user.full_name or "Client",
                        company_name=user.company_name or "Your Account",
                        remaining_credits=credits,
                        plan_name=plan_name,
                    )
                    state.low_credit_sent = True
                    state.last_low_credit_at = now
                    await db.commit()
                except Exception as e:
                    print(f"[NotificationService] Error sending low credit email: {e}")

    # ── Security Event Notifications ────────────────────────────────────────
    def notify_password_changed(self, user: User):
        if not user or not user.email:
            return
        timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        try:
            email_service.send_password_changed_email(
                to_email=user.email,
                full_name=user.full_name or "User",
                timestamp_str=timestamp_str,
            )
        except Exception as e:
            print(f"[NotificationService] Error sending password changed email: {e}")

    def notify_first_time_password_changed(self, user: User):
        if not user or not user.email:
            return
        try:
            email_service.send_first_time_password_changed_email(
                to_email=user.email,
                full_name=user.full_name or "User",
            )
        except Exception as e:
            print(f"[NotificationService] Error sending first time password changed email: {e}")

    def notify_password_reset_requested(self, email: str, reset_code: str):
        try:
            email_service.send_password_reset_email(
                to_email=email,
                reset_code=reset_code,
            )
        except Exception as e:
            print(f"[NotificationService] Error sending password reset code email: {e}")
            raise e

    def notify_password_reset_completed(self, user: User):
        if not user or not user.email:
            return
        try:
            email_service.send_password_reset_success_email(
                to_email=user.email,
                full_name=user.full_name or "User",
            )
        except Exception as e:
            print(f"[NotificationService] Error sending password reset success email: {e}")

    # ── Account Lifecycle Notifications ─────────────────────────────────────
    def notify_account_created(self, user: User, temp_password: str):
        if not user or not user.email:
            return
        try:
            email_service.send_welcome_email(
                to_email=user.email,
                temp_password=temp_password,
                full_name=user.full_name or "User",
                company_name=user.company_name,
            )
        except Exception as e:
            print(f"[NotificationService] Error sending welcome email: {e}")

    def notify_account_activated(self, user: User):
        if not user or not user.email:
            return
        try:
            email_service.send_account_activated_email(
                to_email=user.email,
                full_name=user.full_name or "User",
            )
        except Exception as e:
            print(f"[NotificationService] Error sending account activated email: {e}")

    def notify_account_deactivated(self, user: User):
        if not user or not user.email:
            return
        try:
            email_service.send_account_deactivated_email(
                to_email=user.email,
                full_name=user.full_name or "User",
            )
        except Exception as e:
            print(f"[NotificationService] Error sending account deactivated email: {e}")

# Singleton instance
notification_service = NotificationService()
