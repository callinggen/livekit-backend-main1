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
from app.models.whatsapp_send_job import WhatsAppSendJob
from app.models.whatsapp_send_recipient import WhatsAppSendRecipient
from app.services.whatsapp_credit_service import WhatsAppCreditService
from app.services.whatsapp_automation_service import WhatsAppAutomationService


async def run_required_credit_tests():
    print("=" * 80)
    print("CALLINGGEN — CREDIT SYSTEM FINAL OVERRIDE VERIFICATION SUITE")
    print("=" * 80)

    # ── 1. Centralized Calculation Unit Tests (Tests 1 through 8) ────────────
    print("\n--- Running Tests 1-8: Centralized Backend Credit Calculator ---")

    # Item Type Costs
    text_cost = WhatsAppCreditService.calculate_item_credits("text")
    image_cost = WhatsAppCreditService.calculate_item_credits("image")
    doc_cost = WhatsAppCreditService.calculate_item_credits("document")

    assert text_cost == 1, f"Expected 1, got {text_cost}"
    assert image_cost == 2, f"Expected 2, got {image_cost}"
    assert doc_cost == 3, f"Expected 3, got {doc_cost}"

    # Test 1: 1 recipient + Text -> 1 credit
    t1 = WhatsAppCreditService.calculate_total_credits([{"type": "text"}], 1)
    assert t1 == 1, f"Test 1 failed: Expected 1, got {t1}"
    print(f"[TEST 1 PASS] 1 recipient + Text: {t1} credit (Expected: 1)")

    # Test 2: 1 recipient + Image -> 2 credits
    t2 = WhatsAppCreditService.calculate_total_credits([{"type": "image"}], 1)
    assert t2 == 2, f"Test 2 failed: Expected 2, got {t2}"
    print(f"[TEST 2 PASS] 1 recipient + Image: {t2} credits (Expected: 2)")

    # Test 3: 1 recipient + Document -> 3 credits
    t3 = WhatsAppCreditService.calculate_total_credits([{"type": "document"}], 1)
    assert t3 == 3, f"Test 3 failed: Expected 3, got {t3}"
    print(f"[TEST 3 PASS] 1 recipient + Document: {t3} credits (Expected: 3)")

    # Test 4: 100 recipients + Text -> 100 credits
    t4 = WhatsAppCreditService.calculate_total_credits([{"type": "text"}], 100)
    assert t4 == 100, f"Test 4 failed: Expected 100, got {t4}"
    print(f"[TEST 4 PASS] 100 recipients + Text: {t4} credits (Expected: 100)")

    # Test 5: 100 recipients + Image -> 200 credits
    t5 = WhatsAppCreditService.calculate_total_credits([{"type": "image"}], 100)
    assert t5 == 200, f"Test 5 failed: Expected 200, got {t5}"
    print(f"[TEST 5 PASS] 100 recipients + Image: {t5} credits (Expected: 200)")

    # Test 6: 100 recipients + Document -> 300 credits
    t6 = WhatsAppCreditService.calculate_total_credits([{"type": "document"}], 100)
    assert t6 == 300, f"Test 6 failed: Expected 300, got {t6}"
    print(f"[TEST 6 PASS] 100 recipients + Document: {t6} credits (Expected: 300)")

    # Test 7: 100 recipients + Text + Image -> 300 credits ((1 + 2) * 100)
    t7 = WhatsAppCreditService.calculate_total_credits([{"type": "text"}, {"type": "image"}], 100)
    assert t7 == 300, f"Test 7 failed: Expected 300, got {t7}"
    print(f"[TEST 7 PASS] 100 recipients + Text + Image: {t7} credits (Expected: 300)")

    # Test 8: 100 recipients + Text + Image + Document -> 600 credits ((1 + 2 + 3) * 100)
    t8 = WhatsAppCreditService.calculate_total_credits([{"type": "text"}, {"type": "image"}, {"type": "document"}], 100)
    assert t8 == 600, f"Test 8 failed: Expected 600, got {t8}"
    print(f"[TEST 8 PASS] 100 recipients + Text + Image + Document: {t8} credits (Expected: 600)")

    # ── 2. Insufficient Credits Safety (Test 9) ──────────────────────────────
    print("\n--- Running Test 9: Insufficient Credits Safety & Balance Protection ---")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        import uuid
        # Create user with insufficient balance (user has 5 credits, requires 6 credits for 2 recipients x (1+2+3))
        test_user = User(
            email=f"insufficient_{uuid.uuid4().hex[:6]}@callinggen.ai",
            full_name="Credit Safety Tester",
            hashed_password="fakehashedpassword",
            credits=5,
        )
        db.add(test_user)
        await db.commit()
        await db.refresh(test_user)

        required = WhatsAppCreditService.calculate_total_credits(
            [{"type": "text"}, {"type": "image"}, {"type": "document"}], 2
        )
        assert required == 12, f"Expected 12, got {required}"

        # Direct verification method should raise 400
        from fastapi import HTTPException
        caught_exception = False
        try:
            await WhatsAppCreditService.verify_and_reserve_credits(db, test_user, required)
        except HTTPException as exc:
            caught_exception = True
            assert exc.status_code == 400
            assert "Insufficient WhatsApp credits" in exc.detail

        assert caught_exception is True, "Expected HTTPException for insufficient credits"
        await db.refresh(test_user)
        assert test_user.credits == 5, f"Credits should remain untouched (5), got {test_user.credits}"
        print(f"[TEST 9 PASS] Insufficient credits safely rejected. Required: {required}, Available: {test_user.credits}. Balance untouched.")

    # ── 3. End-to-End Automation & History Credit Tracking Test ──────────────
    print("\n--- Testing End-to-End Campaign Automation & History Record ---")
    from unittest.mock import patch, AsyncMock
    with patch("app.services.whatsapp_automation_service.evolution_service.send_text_message", new_callable=AsyncMock) as mock_txt, \
         patch("app.services.whatsapp_automation_service.evolution_service.send_media_message", new_callable=AsyncMock) as mock_media:
        mock_txt.return_value = {"status": "SUCCESS", "id": "evo_txt_123"}
        mock_media.return_value = {"status": "SUCCESS", "id": "evo_img_456"}

        async with AsyncSessionLocal() as db:
            user_auto = User(
                email=f"auto_credit_{uuid.uuid4().hex[:6]}@callinggen.ai",
                full_name="Auto User",
                hashed_password="fakehashedpassword",
                credits=500,
            )
            db.add(user_auto)
            await db.commit()
            await db.refresh(user_auto)

            campaign = Campaign(
                user_id=user_auto.id,
                campaign_name="Real Estate Campaign",
                agent="Sarah",
                script="Script",
                schedule_date="2026-08-25",
                schedule_time="10:00",
                whatsapp_automation={
                    "enabled": True,
                    "rules": [
                        {
                            "id": "rule_img",
                            "category": "ai_classification",
                            "value": "Interested",
                            "message_text": "Here is the property photo",
                            "attachments": [{"type": "image", "url": "https://example.com/property.png", "title": "Property"}],
                            "enabled": True,
                        }
                    ],
                },
            )
            db.add(campaign)
            await db.flush()

            contact = Contact(
                campaign_id=campaign.id,
                name="Aarav",
                phone="919876543210",
                status="completed",
            )
            db.add(contact)
            await db.flush()

            call = Call(
                campaign_id=campaign.id,
                contact_id=contact.id,
                job_id=1,
                phone="919876543210",
                status="completed",
                category="HOT",
            )
            db.add(call)
            await db.commit()
            await db.refresh(call)

            # 1 text + 1 image = 1 + 2 = 3 credits
            res = await WhatsAppAutomationService.process_call_automation(call.id)
            assert res is not None
            assert res.get("credits_deducted") == 3, f"Expected 3 credits deducted (1 text + 2 image), got {res.get('credits_deducted')}"

            # Verify Send Job & History Record contains actual credits deducted
            job_id = res.get("job_id")
            send_job = await db.get(WhatsAppSendJob, job_id)
            assert send_job is not None
            assert send_job.credits_deducted == 3, f"Send Job history should store 3 credits, got {send_job.credits_deducted}"

            await db.refresh(user_auto)
            assert user_auto.credits == 497, f"Expected 497 credits, got {user_auto.credits}"
            print(f"[E2E PASS] Automation executed: 1 text + 1 image deducted {send_job.credits_deducted} credits. History preserved.")

    print("\n" + "=" * 80)
    print("ALL 9 REQUIRED CREDIT TESTS & E2E INTEGRATIONS PASSED WITH 100% SUCCESS!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_required_credit_tests())
