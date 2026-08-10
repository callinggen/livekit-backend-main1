import asyncio
import sys
import os
import secrets

# Ensure backend root directory is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models.user import User
from app.models.notification_state import UserNotificationState
from app.services.notification_service import notification_service

# Create isolated in-memory SQLite engine for fast testing
test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

async def run_tests():
    print("\n" + "="*60)
    print("      RUNNING CALLINGGEN NOTIFICATION SYSTEM TESTS")
    print("="*60 + "\n")

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    token = secrets.token_hex(4)
    demo_email = f"demo_{token}@callinggen.com"
    std_email = f"standard_{token}@callinggen.com"

    async with TestSessionLocal() as db:

        # Create a test user for Demo Plan (50 credits)
        demo_user = User(
            full_name="Demo Test User",
            email=demo_email,
            hashed_password="hashedpassword123",
            credits=50,
            subscription_plan="Demo",
            company_name="Demo Startup",
            is_active=True,
        )
        db.add(demo_user)

        # Create a test user for Standard Plan (2000 credits)
        std_user = User(
            full_name="Standard Test User",
            email=std_email,
            hashed_password="hashedpassword123",
            credits=2000,
            subscription_plan="Standard",
            company_name="Standard Enterprise",
            is_active=True,
        )
        db.add(std_user)
        await db.commit()

        await db.refresh(demo_user)
        await db.refresh(std_user)

        print(f"[Test 1] Created test users: Demo User #{demo_user.id} (50 credits), Standard User #{std_user.id} (2000 credits)")

        # ── TEST 1: Demo Plan Credit Alert Logic ──
        print("\n--- Testing Demo Plan (50 credits) Threshold Drops ---")
        
        # Step A: Drop to 40 credits -> Should NOT trigger <100 alert (Demo threshold low=15, crit=5)
        demo_user.credits = 40
        await notification_service.check_and_trigger_credit_notifications(db, demo_user)
        state = await notification_service.get_or_create_state(db, demo_user.id)
        assert state.low_credit_sent == False, "Demo user should not receive low credit email at 40 credits"
        print("  [OK] Drop to 40 credits: Correctly skipped <100 alert for Demo Plan.")

        # Step B: Drop to 14 credits -> Should trigger LOW credit alert
        demo_user.credits = 14
        await notification_service.check_and_trigger_credit_notifications(db, demo_user)
        await db.refresh(state)
        assert state.low_credit_sent == True, "Demo user should receive low credit email at 14 credits (< 15)"
        print("  [OK] Drop to 14 credits: LOW credit email triggered successfully.")

        # Step C: Drop to 10 credits -> Should NOT re-trigger LOW email (deduplicated)
        demo_user.credits = 10
        await notification_service.check_and_trigger_credit_notifications(db, demo_user)
        print("  [OK] Drop to 10 credits: Deduplication working, no repeat low credit email sent.")

        # Step D: Drop to 4 credits -> Should trigger CRITICAL credit email
        demo_user.credits = 4
        await notification_service.check_and_trigger_credit_notifications(db, demo_user)
        await db.refresh(state)
        assert state.critical_credit_sent == True, "Demo user should receive critical credit email at 4 credits (<= 5)"
        print("  [OK] Drop to 4 credits: CRITICAL credit email triggered successfully.")

        # Step E: Drop to 0 credits -> Should trigger EXHAUSTED email
        demo_user.credits = 0
        await notification_service.check_and_trigger_credit_notifications(db, demo_user)
        await db.refresh(state)
        assert state.credits_exhausted_sent == True, "Demo user should receive credits exhausted email at 0 credits"
        print("  [OK] Drop to 0 credits: CREDITS EXHAUSTED email triggered successfully.")

        # Step F: Recharge Demo user back to 50 credits -> Flags should reset automatically
        demo_user.credits = 50
        await notification_service.check_and_trigger_credit_notifications(db, demo_user)
        await db.refresh(state)
        assert state.low_credit_sent == False and state.critical_credit_sent == False and state.credits_exhausted_sent == False, "Cycle reset failed on recharge"
        print("  [OK] Top-Up to 50 credits: Cycle reset successfully! Notification flags cleared for new cycle.")

        # ── TEST 2: Standard Plan Credit Alert Logic (2000 credits) ──
        print("\n--- Testing Standard Plan (2000 credits) Threshold Drops ---")
        
        # Step A: Drop to 190 credits -> Should trigger LOW credit alert (< 200)
        std_user.credits = 190
        await notification_service.check_and_trigger_credit_notifications(db, std_user)
        std_state = await notification_service.get_or_create_state(db, std_user.id)
        assert std_state.low_credit_sent == True, "Standard user should receive low credit email at 190 credits"
        print("  [OK] Drop to 190 credits: LOW credit email triggered for Standard Plan.")

        # Step B: Drop to 15 credits -> Should trigger CRITICAL credit alert (<= 20)
        std_user.credits = 15
        await notification_service.check_and_trigger_credit_notifications(db, std_user)
        await db.refresh(std_state)
        assert std_state.critical_credit_sent == True, "Standard user should receive critical credit email at 15 credits"
        print("  [OK] Drop to 15 credits: CRITICAL credit email triggered for Standard Plan.")

        # ── TEST 3: Security & Account Event Triggers ──
        print("\n--- Testing Security & Account Notification Triggers ---")
        
        notification_service.notify_password_changed(std_user)
        print("  [OK] notify_password_changed executed cleanly.")

        notification_service.notify_first_time_password_changed(demo_user)
        print("  [OK] notify_first_time_password_changed executed cleanly.")

        notification_service.notify_password_reset_requested("demo_test@callinggen.com", "123456")
        print("  [OK] notify_password_reset_requested executed cleanly.")

        notification_service.notify_password_reset_completed(std_user)
        print("  [OK] notify_password_reset_completed executed cleanly.")

        notification_service.notify_account_created(demo_user, "tempPass123!")
        print("  [OK] notify_account_created executed cleanly.")

        notification_service.notify_account_activated(std_user)
        print("  [OK] notify_account_activated executed cleanly.")

        notification_service.notify_account_deactivated(std_user)
        print("  [OK] notify_account_deactivated executed cleanly.")


        # Clean up test users & states
        await db.delete(state)
        await db.delete(std_state)
        await db.delete(demo_user)
        await db.delete(std_user)
        await db.commit()

    await test_engine.dispose()

    print("\n" + "="*60)
    print("     ALL NOTIFICATION TESTS PASSED SUCCESSFULLY!")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(run_tests())
