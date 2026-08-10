import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.notification_service import notification_service
from sqlalchemy.future import select
from sqlalchemy import func

async def trigger_alerts():
    email_target = "khushipanwar060@gmail.com"
    print(f"Searching for user with email: {email_target}...")
    
    async with AsyncSessionLocal() as db:
        stmt = select(User).where(func.lower(User.email) == email_target.lower())
        res = await db.execute(stmt)
        user = res.scalars().first()
        
        if not user:
            print(f"User '{email_target}' not found.")
            return

        print(f"Found User #{user.id}: {user.full_name} ({user.email})")

        # ── Step 1: Trigger Critical Credit Alert (15 credits <= 20 threshold for Standard plan) ──
        print("\n--- STEP 1: Setting credits to 15 (Critical Credit Threshold) ---")
        user.credits = 15
        await db.commit()
        await db.refresh(user)
        await notification_service.check_and_trigger_credit_notifications(db, user)

        await asyncio.sleep(2)

        # ── Step 2: Trigger Credits Exhausted Alert (0 credits) ──
        print("\n--- STEP 2: Setting credits to 0 (Credits Exhausted Threshold) ---")
        user.credits = 0
        await db.commit()
        await db.refresh(user)
        await notification_service.check_and_trigger_credit_notifications(db, user)

        await asyncio.sleep(2)

        # ── Step 3: Top Up / Recharge user back to 2000 credits (Cycle Reset) ──
        print("\n--- STEP 3: Recharging account back to 2000 credits (Cycle Reset) ---")
        user.credits = 2000
        await db.commit()
        await db.refresh(user)
        await notification_service.check_and_trigger_credit_notifications(db, user)
        print("Cycle reset completed! User balance restored to 2000 credits.")

if __name__ == "__main__":
    asyncio.run(trigger_alerts())
