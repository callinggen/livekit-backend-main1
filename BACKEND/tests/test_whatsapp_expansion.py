import asyncio
import os
import sys
from datetime import datetime, timezone

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from app.database import Base, AsyncSessionLocal, engine
from app.models.user import User
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.call import Call
from app.models.whatsapp_material import WhatsAppMaterial
from app.models.whatsapp_send_job import WhatsAppSendJob
from app.models.whatsapp_send_recipient import WhatsAppSendRecipient
from app.services.whatsapp_credit_service import WhatsAppCreditService
from app.services.whatsapp_automation_service import WhatsAppAutomationService
from app.api.whatsapp_materials import MAX_FILE_SIZE, ALLOWED_IMAGE_EXTENSIONS, ALLOWED_DOCUMENT_EXTENSIONS


async def run_tests():
    print("=" * 70)
    print("CALLINGGEN — WHATSAPP EXPANSION & UI CLEANUP VERIFICATION SUITE")
    print("=" * 70)

    # 1. Ensure DB Schema is synced
    print("\n[TEST 1] Syncing database schema...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("  -> PASS: Database tables synced successfully.")

    # 2. Test Centralized Credit Rules
    print("\n[TEST 2] Verifying Centralized WhatsApp Credit Rules...")
    # Explicit Rule: Image = exactly 1 credit per recipient
    text_cost = WhatsAppCreditService.calculate_item_credits("text")
    image_cost = WhatsAppCreditService.calculate_item_credits("image")
    doc_cost = WhatsAppCreditService.calculate_item_credits("document")

    assert text_cost == 1, f"Expected 1 credit for text, got {text_cost}"
    assert image_cost == 1, f"CRITICAL: Expected exactly 1 credit for image, got {image_cost}"
    assert doc_cost == 1, f"Expected 1 credit for document, got {doc_cost}"

    # Scenario calculations
    cost_1_text = WhatsAppCreditService.calculate_total_credits([{"type": "text"}], 1)
    cost_1_img = WhatsAppCreditService.calculate_total_credits([{"type": "image"}], 1)
    cost_1_doc = WhatsAppCreditService.calculate_total_credits([{"type": "document"}], 1)
    assert cost_1_text == 1
    assert cost_1_img == 1
    assert cost_1_doc == 1

    cost_100_text = WhatsAppCreditService.calculate_total_credits([{"type": "text"}], 100)
    cost_100_img = WhatsAppCreditService.calculate_total_credits([{"type": "image"}], 100)
    cost_100_doc = WhatsAppCreditService.calculate_total_credits([{"type": "document"}], 100)
    assert cost_100_text == 100
    assert cost_100_img == 100
    assert cost_100_doc == 100

    cost_100_mixed = WhatsAppCreditService.calculate_total_credits([{"type": "text"}, {"type": "image"}], 100)
    assert cost_100_mixed == 200, f"Expected 200 credits for text+image x 100, got {cost_100_mixed}"

    print(f"  -> PASS: Single recipient: Text={cost_1_text}, Image={cost_1_img}, Doc={cost_1_doc}")
    print(f"  -> PASS: 100 recipients: Text={cost_100_text}, Image={cost_100_img}, Doc={cost_100_doc}")
    print(f"  -> PASS: 100 recipients (Text + Image): {cost_100_mixed} credits")

    # 3. Test Material Base Model & File Validation Rules
    print("\n[TEST 3] Testing Material Base Rules & Validation...")
    assert ".png" in ALLOWED_IMAGE_EXTENSIONS and ".jpg" in ALLOWED_IMAGE_EXTENSIONS
    assert ".pdf" in ALLOWED_DOCUMENT_EXTENSIONS and ".docx" in ALLOWED_DOCUMENT_EXTENSIONS
    assert MAX_FILE_SIZE == 25 * 1024 * 1024  # 25 MB

    async with AsyncSessionLocal() as db:
        # Create a test text material
        mat = WhatsAppMaterial(
            user_id=1,
            title="Test Brochure Info",
            type="text",
            content="Hi {{name}}, here is our latest brochure: {{campaign_name}}",
            tags="brochure, sales",
        )
        db.add(mat)
        await db.commit()
        await db.refresh(mat)
        assert mat.id is not None
        print(f"  -> PASS: Material Base record created (ID: {mat.id}, Title: '{mat.title}')")

    # 4. Test Send Job & Recipient Tracking Models
    print("\n[TEST 4] Testing Send Job & Recipient Model Tracking...")
    async with AsyncSessionLocal() as db:
        job = WhatsAppSendJob(
            user_id=1,
            source_type="campaign_manual",
            source_name="Q3 Real Estate Campaign",
            content_type="Text & Image",
            message_text="Hello from Real Estate",
            attachments=[{"title": "Property Flyer", "type": "image", "url": "/test.png"}],
            total_contacts=2,
            sent_count=2,
            failed_count=0,
            credits_deducted=4,
            status="completed",
        )
        db.add(job)
        await db.flush()

        r1 = WhatsAppSendRecipient(
            send_job_id=job.id,
            name="Rahul Sharma",
            phone="919876543210",
            status="sent",
        )
        r2 = WhatsAppSendRecipient(
            send_job_id=job.id,
            name="Priya Patel",
            phone="919876543211",
            status="sent",
        )
        db.add_all([r1, r2])
        await db.commit()
        await db.refresh(job)

        rec_res = await db.execute(
            select(WhatsAppSendRecipient).where(WhatsAppSendRecipient.send_job_id == job.id)
        )
        rec_list = rec_res.scalars().all()

        assert job.id is not None
        assert len(rec_list) == 2
        print(f"  -> PASS: SendJob #{job.id} created with {len(rec_list)} tracked recipients.")

    # 5. Test Campaign Automation: Automation OFF (Default) -> Nothing Happens
    print("\n[TEST 5] Testing Campaign Automation: Default OFF...")
    async with AsyncSessionLocal() as db:
        camp_off = Campaign(
            user_id=1,
            campaign_name="Test Campaign - Automation OFF",
            agent="Meera (Morning Tax)",
            script="Hello",
            schedule_date="2026-08-23",
            schedule_time="10:00 AM",
            whatsapp_automation={"enabled": False, "rules": []},
        )
        db.add(camp_off)
        await db.flush()

        contact_off = Contact(
            campaign_id=camp_off.id,
            name="Anil Kumar",
            phone="919876500001",
            status="completed",
            response="Interested in property",
        )
        db.add(contact_off)
        await db.flush()

        call_off = Call(
            campaign_id=camp_off.id,
            contact_id=contact_off.id,
            job_id=1,
            phone="919876500001",
            status="completed",
            category="HOT",
            summary="High Interest in Consultation",
        )
        db.add(call_off)
        await db.commit()
        await db.refresh(call_off)

        # Trigger automation service
        res_off = await WhatsAppAutomationService.process_call_automation(call_off.id)
        assert res_off is None, f"Expected None when automation is OFF, got {res_off}"
        print("  -> PASS: WhatsApp automation safely skipped when enabled=False.")

    # 6. Test Campaign Automation: Automation ON + Matching Rule + Personalization + Idempotency
    print("\n[TEST 6] Testing Campaign Automation: ON + Match + Idempotency...")
    async with AsyncSessionLocal() as db:
        # Ensure user has credits
        user = await db.get(User, 1)
        if not user:
            user = User(id=1, email="test@callinggen.ai", full_name="Tester", credits=2000)
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            user.credits = 2000
            await db.commit()

        initial_credits = user.credits

        camp_on = Campaign(
            user_id=user.id,
            campaign_name="August Morning Tax Drive",
            agent="Meera (Morning Tax)",
            script="Hello",
            schedule_date="2026-08-23",
            schedule_time="10:00 AM",
            whatsapp_automation={
                "enabled": True,
                "rules": [
                    {
                        "id": "rule_hot_lead",
                        "category": "ai_classification",
                        "value": "Interested",
                        "message_text": "Hi {{name}}, thank you for speaking with {{campaign_name}}! We look forward to assisting you.",
                        "attachments": [
                            {
                                "title": "Tax Overview",
                                "type": "document",
                                "url": "https://callinggen.ai/tax.pdf",
                                "file_name": "tax.pdf",
                            }
                        ],
                        "enabled": True,
                    }
                ],
            },
        )
        db.add(camp_on)
        await db.flush()

        contact_on = Contact(
            campaign_id=camp_on.id,
            name="Vikram Sethi",
            phone="919876500002",
            status="completed",
            response="Interested in tax consultation",
        )
        db.add(contact_on)
        await db.flush()

        call_on = Call(
            campaign_id=camp_on.id,
            contact_id=contact_on.id,
            job_id=1,
            phone="919876500002",
            status="completed",
            category="HOT",
            summary="Wants Tax Consultation",
        )
        db.add(call_on)
        await db.commit()
        await db.refresh(call_on)

        # 1st Execution -> Should Match, Create Send Job, and Deduct Credits (1 text + 1 doc = 2 credits)
        res_on = await WhatsAppAutomationService.process_call_automation(call_on.id)
        assert res_on is not None, "Expected automation result"
        assert res_on.get("job_id") is not None
        print(f"  -> PASS: Automation triggered successfully. Send Job #{res_on.get('job_id')}, Credits Deducted: {res_on.get('credits_deducted')}")

        # 2nd Execution with same call -> Idempotency check should SKIP duplicate
        res_dup = await WhatsAppAutomationService.process_call_automation(call_on.id)
        assert res_dup is not None and res_dup.get("status") == "skipped_duplicate"
        print("  -> PASS: Duplicate protection confirmed (skipped_duplicate).")

    print("\n" + "=" * 70)
    print("ALL VERIFICATION SUITE TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_tests())
