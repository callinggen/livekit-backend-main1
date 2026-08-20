import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from datetime import datetime, timezone

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call
from app.models.whatsapp_action import WhatsAppAction
from app.services.call_service import CallService
from app.services.whatsapp_actions import WhatsAppActionService, ALLOWED_WHATSAPP_ACTIONS


@pytest.mark.asyncio
async def test_allowed_actions_registry():
    """Verify all required Phase 1 WhatsApp actions are registered in allowlist."""
    expected = {
        "SEND_BROCHURE",
        "SEND_PRICING",
        "SEND_CATALOGUE",
        "SEND_WEBSITE",
        "SEND_BOOKING_LINK",
        "SEND_CONTACT_DETAILS",
        "SEND_CALLBACK_CONFIRMATION",
        "SEND_MISSED_CALL",
        "SEND_CALLBACK",
    }
    assert expected.issubset(ALLOWED_WHATSAPP_ACTIONS)


@pytest.mark.asyncio
async def test_in_call_whatsapp_action_success(db_session):
    """Test triggering an in-call WhatsApp action (e.g. SEND_BROCHURE) via Evolution API."""
    # Setup test models
    campaign = Campaign(
        user_id=1,
        campaign_name="Test Tax Advisory",
        agent="Voice-E (Tax Agent)",
        status="running",
    )
    db_session.add(campaign)
    await db_session.flush()

    contact = Contact(
        campaign_id=campaign.id,
        name="Aarav Mehta",
        phone="+919876543210",
        status="running",
    )
    db_session.add(contact)
    await db_session.flush()

    call = Call(
        contact_id=contact.id,
        phone=contact.phone,
        status="in-progress",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(call)
    await db_session.commit()

    # Mock Evolution API send_media_message
    with patch("whatsapp.service.send_media_message", new_callable=AsyncMock) as mock_media:
        mock_media.return_value = {"key": {"id": "EVO_MSG_123"}, "status": "PENDING"}

        result = await WhatsAppActionService.execute_action(
            call_id=call.id,
            action="SEND_BROCHURE",
            contact_id=contact.id,
            phone=contact.phone,
        )

        assert result["success"] is True
        assert result["status"] == "sent"
        assert result["action"] == "SEND_BROCHURE"
        mock_media.assert_called_once()


@pytest.mark.asyncio
async def test_idempotency_duplicate_protection(db_session):
    """Test that duplicate triggers for the same call and action are safely skipped."""
    campaign = Campaign(user_id=1, campaign_name="Tax Campaign", agent="Meera")
    db_session.add(campaign)
    await db_session.flush()

    contact = Contact(campaign_id=campaign.id, name="Pooja Verma", phone="+919811122233")
    db_session.add(contact)
    await db_session.flush()

    call = Call(contact_id=contact.id, phone=contact.phone, status="in-progress")
    db_session.add(call)
    await db_session.commit()

    with patch("whatsapp.service.send_text_message", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = {"key": {"id": "EVO_MSG_456"}}

        # First trigger -> Sent
        res1 = await WhatsAppActionService.execute_action(
            call_id=call.id,
            action="SEND_WEBSITE",
            contact_id=contact.id,
            phone=contact.phone,
        )
        assert res1["success"] is True
        assert res1["status"] == "sent"
        assert mock_text.call_count == 1

        # Second trigger -> Skipped Duplicate
        res2 = await WhatsAppActionService.execute_action(
            call_id=call.id,
            action="SEND_WEBSITE",
            contact_id=contact.id,
            phone=contact.phone,
        )
        assert res2["success"] is True
        assert res2["status"] == "skipped_duplicate"
        # Evolution API should NOT have been called a second time
        assert mock_text.call_count == 1


@pytest.mark.asyncio
async def test_call_isolation_between_customers(db_session):
    """Test that actions for Call A go exclusively to Customer A and Call B to Customer B."""
    camp = Campaign(user_id=1, campaign_name="Multi-Customer Camp", agent="Meera")
    db_session.add(camp)
    await db_session.flush()

    contact_a = Contact(campaign_id=camp.id, name="Customer A", phone="+919000000001")
    contact_b = Contact(campaign_id=camp.id, name="Customer B", phone="+919000000002")
    db_session.add_all([contact_a, contact_b])
    await db_session.flush()

    call_a = Call(contact_id=contact_a.id, phone=contact_a.phone, status="in-progress")
    call_b = Call(contact_id=contact_b.id, phone=contact_b.phone, status="in-progress")
    db_session.add_all([call_a, call_b])
    await db_session.commit()

    with patch("whatsapp.service.send_text_message", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = {"key": {"id": "EVO_MSG"}}

        await WhatsAppActionService.execute_action(
            call_id=call_a.id,
            action="SEND_BOOKING_LINK",
            contact_id=contact_a.id,
            phone=contact_a.phone,
        )

        await WhatsAppActionService.execute_action(
            call_id=call_b.id,
            action="SEND_CONTACT_DETAILS",
            contact_id=contact_b.id,
            phone=contact_b.phone,
        )

        # Check call arguments to ensure correct phone routing
        assert mock_text.call_count == 2
        first_call_kwargs = mock_text.call_args_list[0].kwargs
        second_call_kwargs = mock_text.call_args_list[1].kwargs

        assert "919000000001" in first_call_kwargs["number"]
        assert "919000000002" in second_call_kwargs["number"]


@pytest.mark.asyncio
async def test_whatsapp_failure_isolation(db_session):
    """Test that if Evolution API fails or is offline, voice call data remains intact."""
    camp = Campaign(user_id=1, campaign_name="Fault Tolerance Test", agent="Meera")
    db_session.add(camp)
    await db_session.flush()

    contact = Contact(campaign_id=camp.id, name="Faulty Client", phone="+919876500000")
    db_session.add(contact)
    await db_session.flush()

    call = Call(contact_id=contact.id, phone=contact.phone, status="in-progress")
    db_session.add(call)
    await db_session.commit()

    with patch("whatsapp.service.send_text_message", side_effect=Exception("Evolution API Connection Timeout")):
        result = await WhatsAppActionService.execute_action(
            call_id=call.id,
            action="SEND_MISSED_CALL",
            contact_id=contact.id,
            phone=contact.phone,
        )

        # Action is marked failed gracefully, no unhandled exception
        assert result["success"] is False
        assert result["status"] == "failed"
        assert "Evolution API Connection Timeout" in result["error"]

        # Verify DB record is marked failed
        async with db_session.begin():
            q = select(WhatsAppAction).where(WhatsAppAction.call_id == call.id)
            res = await db_session.execute(q)
            record = res.scalars().first()
            assert record is not None
            assert record.status == "failed"
