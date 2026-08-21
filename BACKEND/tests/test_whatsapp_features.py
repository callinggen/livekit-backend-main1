import asyncio
import os
import sys

# Ensure backend root is in PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import AsyncSessionLocal, engine, Base
from app.models.user import User
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.call import Call
from app.models.whatsapp_material import WhatsAppMaterial
from app.models.whatsapp_action import WhatsAppAction
from app.services.whatsapp_actions import WhatsAppActionService
from app.services.call_service import CallService


async def run_tests():
    print("\n=== STARTING WHATSAPP FEATURES & CREDIT RULE VERIFICATION ===")

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        # 1. Setup Test User
        import uuid
        test_email = f"test_{uuid.uuid4().hex[:8]}@callinggen.ai"
        user = User(
            email=test_email,
            full_name="WhatsApp Test User",
            hashed_password="fakehashedpassword",
            credits=100,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"[TEST 1] Created test user id={user.id} with credits={user.credits}")

        # 2. Material Base: Create Text Material
        mat_text = WhatsAppMaterial(
            user_id=user.id,
            title="Follow-up Message",
            type="text",
            content="Hi {{name}}, thanks for your time!",
            tags="Sales,Followup",
        )
        db.add(mat_text)

        # Material Base: Create Image Material
        mat_img = WhatsAppMaterial(
            user_id=user.id,
            title="Company Brochure Banner",
            type="image",
            file_url="/api/whatsapp/materials/file/fake_banner.png",
            mime_type="image/png",
            file_size=102400,
            tags="Banner",
        )
        db.add(mat_img)

        # Material Base: Create Document Material
        mat_doc = WhatsAppMaterial(
            user_id=user.id,
            title="Pricing Sheet PDF",
            type="document",
            file_url="/api/whatsapp/materials/file/fake_pricing.pdf",
            mime_type="application/pdf",
            file_size=204800,
            tags="Pricing",
        )
        db.add(mat_doc)
        await db.commit()
        print("[TEST 2] Successfully created Text, Image, and Document materials in Material Base.")

        # 3. Setup Campaign and Contact for call tests
        campaign = Campaign(
            user_id=user.id,
            campaign_name="August Real Estate Test",
            agent="Sarah",
            script="Real estate script",
            schedule_date="2026-08-21",
            schedule_time="10:00",
            status="running",
        )
        db.add(campaign)
        await db.flush()

        contact = Contact(
            campaign_id=campaign.id,
            name="Rahul Sharma",
            phone="+919876543210",
            status="pending",
        )
        db.add(contact)
        await db.flush()

        from app.models.job import Job
        job = Job(
            campaign_id=campaign.id,
            status="running",
            total_contacts=1,
            completed_contacts=0,
            failed_contacts=0,
        )
        db.add(job)
        await db.flush()

        call = Call(
            job_id=job.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            phone="+919876543210",
            status="in_progress",
            credits_deducted=0,
        )
        db.add(call)
        await db.commit()
        await db.refresh(campaign)
        await db.refresh(job)
        await db.refresh(contact)
        await db.refresh(call)
        print(f"[TEST 3] Created campaign id={campaign.id}, job id={job.id}, contact id={contact.id}, call id={call.id}")

        # 4. Verify Missed-Call & AI WhatsApp Action Credit Deductions
        # Mock Evolution API service to return success
        from unittest.mock import patch, AsyncMock
        with patch("whatsapp.service.send_text_message", new_callable=AsyncMock) as mock_send_txt, \
             patch("whatsapp.service.send_media_message", new_callable=AsyncMock) as mock_send_media:
            mock_send_txt.return_value = {"status": "SUCCESS", "id": "evo_123"}
            mock_send_media.return_value = {"status": "SUCCESS", "id": "evo_456"}

            # Send Missed Call follow-up -> should deduct exactly 1 credit!
            init_credits = user.credits
            res_missed = await WhatsAppActionService.execute_action(
                call_id=call.id,
                action="SEND_MISSED_CALL",
            )
            await db.refresh(user)
            assert res_missed["success"] is True, "Missed call action failed"
            assert user.credits == init_credits - 1, f"Expected {init_credits - 1} credits, got {user.credits}"
            print(f"[TEST 4] SEND_MISSED_CALL deducted 1 credit successfully! Current credits: {user.credits}")

            # Send Brochure -> should deduct exactly 1 credit!
            curr_credits = user.credits
            res_brochure = await WhatsAppActionService.execute_action(
                call_id=call.id,
                action="SEND_BROCHURE",
            )
            await db.refresh(user)
            assert res_brochure["success"] is True, "Brochure action failed"
            assert user.credits == curr_credits - 1, f"Expected {curr_credits - 1} credits, got {user.credits}"
            print(f"[TEST 5] SEND_BROCHURE deducted 1 credit successfully! Current credits: {user.credits}")

            # Test Idempotency: duplicate SEND_BROCHURE should skip and NOT deduct credits
            curr_credits = user.credits
            res_dup = await WhatsAppActionService.execute_action(
                call_id=call.id,
                action="SEND_BROCHURE",
            )
            await db.refresh(user)
            assert res_dup["status"] == "skipped_duplicate", "Idempotency failed"
            assert user.credits == curr_credits, f"Credits should remain {curr_credits}, got {user.credits}"
            print(f"[TEST 6] Duplicate SEND_BROCHURE skipped cleanly with 0 extra credit deduction. Credits: {user.credits}")

        # 5. Verify Voice Call Completion Credit Deduction remains 1 credit
        curr_credits = user.credits
        # Complete the call with real customer transcript
        await CallService.complete_call(
            db=db,
            call_id=call.id,
            transcript="User: Yes I am interested.",
            customer_name="Rahul Sharma",
        )
        await db.refresh(user)
        await db.refresh(call)
        assert call.credits_deducted == 1, "Call credits_deducted should be 1"
        assert user.credits == curr_credits - 1, f"Voice call completion should deduct 1 credit. Expected {curr_credits - 1}, got {user.credits}"
        print(f"[TEST 7] Completed voice call deducted 1 credit as expected! Credits: {user.credits}")

        # Clean up test rows
        from sqlalchemy import delete
        await db.execute(delete(WhatsAppAction).where(WhatsAppAction.call_id == call.id))
        await db.delete(call)
        await db.delete(contact)
        await db.delete(job)
        await db.delete(campaign)
        await db.delete(mat_text)
        await db.delete(mat_img)
        await db.delete(mat_doc)
        await db.delete(user)
        await db.commit()
        print("[CLEANUP] Test data cleaned up successfully.")

    print("\n=== ALL BACKEND TESTS PASSED WITH 100% SUCCESS! ===")


if __name__ == "__main__":
    asyncio.run(run_tests())
