import asyncio
import os
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from sqlalchemy import select

from app.database import Base, AsyncSessionLocal, engine
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call
from app.models.whatsapp_action import WhatsAppAction
from app.services.whatsapp_actions import WhatsAppActionService, ALLOWED_WHATSAPP_ACTIONS


async def main():
    print("=" * 60)
    print("CALLINGGEN WHATSAPP INTEGRATION - VERIFICATION SUITE")
    print("=" * 60)

    # 1. Verify Allowed Actions
    print("\n[TEST 1] Testing Allowed Actions Allowlist...")
    expected_actions = [
        "SEND_BROCHURE",
        "SEND_PRICING",
        "SEND_CATALOGUE",
        "SEND_WEBSITE",
        "SEND_BOOKING_LINK",
        "SEND_CONTACT_DETAILS",
        "SEND_CALLBACK_CONFIRMATION",
        "SEND_MISSED_CALL",
        "SEND_CALLBACK",
    ]
    for act in expected_actions:
        assert act in ALLOWED_WHATSAPP_ACTIONS, f"Missing {act}"
    print("[PASS] All 9 Phase 1 WhatsApp Actions registered in allowlist.")

    # 2. Database Sync
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 3. In-Call Info Action (SEND_BROCHURE)
    print("\n[TEST 2] Testing In-Call WhatsApp Action (SEND_BROCHURE)...")
    async with AsyncSessionLocal() as db:
        camp = Campaign(
            user_id=1,
            campaign_name="Verification Campaign",
            agent="Meera",
            script="Hello, test script.",
            schedule_date="2026-08-19",
            schedule_time="10:00 AM",
        )
        db.add(camp)
        await db.commit()
        await db.refresh(camp)

        job = Job(campaign_id=camp.id, total_contacts=1, completed_contacts=0, failed_contacts=0, status="running")
        db.add(job)
        await db.commit()
        await db.refresh(job)

        contact = Contact(campaign_id=camp.id, name="Test Customer", phone="+919876543210")
        db.add(contact)
        await db.commit()
        await db.refresh(contact)

        call = Call(
            job_id=job.id,
            campaign_id=camp.id,
            contact_id=contact.id,
            phone=contact.phone,
            status="in-progress",
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(call)
        await db.commit()
        await db.refresh(call)

    with patch("whatsapp.service.send_media_message", new_callable=AsyncMock) as mock_media:
        mock_media.return_value = {"status": "SUCCESS"}
        res = await WhatsAppActionService.execute_action(call_id=call.id, action="SEND_BROCHURE")
        assert res["success"] is True
        assert res["status"] == "sent"
        assert mock_media.call_count == 1
        print("[PASS] SEND_BROCHURE executed and logged as 'sent'.")

    # 4. Idempotency Check (Duplicate SEND_BROCHURE)
    print("\n[TEST 3] Testing Idempotency & Duplicate Protection...")
    with patch("whatsapp.service.send_media_message", new_callable=AsyncMock) as mock_media:
        res_dup = await WhatsAppActionService.execute_action(call_id=call.id, action="SEND_BROCHURE")
        assert res_dup["success"] is True
        assert res_dup["status"] == "skipped_duplicate"
        assert mock_media.call_count == 0
        print("[PASS] Duplicate SEND_BROCHURE correctly skipped with 'skipped_duplicate'.")

    # 5. Call Isolation Test
    print("\n[TEST 4] Testing Call Isolation Between Customers...")
    async with AsyncSessionLocal() as db:
        contact_a = Contact(campaign_id=camp.id, name="Customer A", phone="+919000000001")
        contact_b = Contact(campaign_id=camp.id, name="Customer B", phone="+919000000002")
        db.add_all([contact_a, contact_b])
        await db.commit()
        await db.refresh(contact_a)
        await db.refresh(contact_b)

        call_a = Call(job_id=job.id, campaign_id=camp.id, contact_id=contact_a.id, phone=contact_a.phone, status="in-progress")
        call_b = Call(job_id=job.id, campaign_id=camp.id, contact_id=contact_b.id, phone=contact_b.phone, status="in-progress")
        db.add_all([call_a, call_b])
        await db.commit()
        await db.refresh(call_a)
        await db.refresh(call_b)

    with patch("whatsapp.service.send_text_message", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = {"status": "SUCCESS"}

        await WhatsAppActionService.execute_action(call_id=call_a.id, action="SEND_WEBSITE")
        await WhatsAppActionService.execute_action(call_id=call_b.id, action="SEND_BOOKING_LINK")

        assert mock_text.call_count == 2
        call_args_1 = mock_text.call_args_list[0].kwargs
        call_args_2 = mock_text.call_args_list[1].kwargs

        assert "919000000001" in call_args_1["number"]
        assert "919000000002" in call_args_2["number"]
        print("[PASS] Call A routed to 919000000001 and Call B routed to 919000000002 without leakage.")

    # 6. Fault-Tolerance & Error Isolation
    print("\n[TEST 5] Testing Error Isolation (Evolution API down)...")
    with patch("whatsapp.service.send_text_message", side_effect=Exception("Connection Refused 503")):
        res_fail = await WhatsAppActionService.execute_action(call_id=call_a.id, action="SEND_MISSED_CALL")
        assert res_fail["success"] is False
        assert res_fail["status"] == "failed"
        assert "503" in res_fail["error"]
        print("[PASS] Failed WhatsApp dispatch logged as 'failed' without crashing voice system.")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! (5/5)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
