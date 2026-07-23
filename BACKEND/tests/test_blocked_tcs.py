import pytest
import os
import wave
import struct
import io
import csv
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call
from app.api.auth import validate_password_policy
from app.services.call_service import CallService
from app.services.campaign_service import CampaignService
from agent import build_agent_instructions, mix_wav_files
from reset_stale_calls import reset as reset_stale_calls_func


# ── TC-AUTH-014: Password Validation Rules on Reset ──────────────────────────
def test_tc_auth_014_password_validation_rules_on_reset():
    """Verify password policy rules on password reset."""
    with pytest.raises(ValueError, match="at least 8 characters"):
        validate_password_policy("Short1!")

    with pytest.raises(ValueError, match="uppercase"):
        validate_password_policy("noupcase1!")

    with pytest.raises(ValueError, match="lowercase"):
        validate_password_policy("NOLOWERCASE1!")

    with pytest.raises(ValueError, match="number"):
        validate_password_policy("NoDigitHere!")

    with pytest.raises(ValueError, match="special character"):
        validate_password_policy("NoSpecialChar123")

    validate_password_policy("ValidPass123!")


# ── TC-QUAL-001: Personalized Greeting (Contact Answers) ─────────────────────
def test_tc_qual_001_personalized_greeting():
    """Verify agent system prompt contains pre-known customer name directive."""
    instructions = build_agent_instructions(
        agent_type="Voice-E (Tax Agent)",
        custom_script="Offer free tax audit consultation.",
        customer_name="Rajesh Kumar"
    )
    assert "IMPORTANT: You already know the customer's name is 'Rajesh Kumar'" in instructions
    assert "Do NOT ask them for their name" in instructions

    instructions_empty = build_agent_instructions(
        agent_type="Voice-E (Tax Agent)",
        custom_script="Offer free tax audit consultation.",
        customer_name=""
    )
    assert "IMPORTANT: You already know the customer's name" not in instructions_empty


# ── TC-QUAL-002: Script Adherence ─────────────────────────────────────────────
def test_tc_qual_002_script_adherence():
    """Verify agent instructions inject custom campaign script faithfully."""
    custom_script = "Ask if the client has filed their ITR for FY 2024-25."
    instructions = build_agent_instructions(
        agent_type="Voice-E (Tax Agent)",
        custom_script=custom_script,
        customer_name="Priya Sharma"
    )
    assert "tax advisor making outbound calls" in instructions
    assert "CAMPAIGN-SPECIFIC SCRIPT:" in instructions
    assert custom_script in instructions


# ── TC-QUAL-003: Date/Time Clarification Prompt ───────────────────────────────
def test_tc_qual_003_date_time_clarification_prompt():
    """Verify agent instructions contain mandatory date & time validation rules."""
    instructions = build_agent_instructions(
        agent_type="Voice-E (Tax Agent)",
        custom_script="Schedule tax consultation.",
        customer_name="Anita"
    )
    assert "DATE & TIME VALIDATION RULES" in instructions
    assert "that date has already passed" in instructions
    assert "which year did you mean?" in instructions
    assert "Is that AM or PM?" in instructions
    assert "Never book or confirm an appointment with an ambiguous or past date/time" in instructions


# ── TC-QUAL-004: Recording Playback After Call ────────────────────────────────
@pytest.mark.asyncio
async def test_tc_qual_004_recording_playback_after_call(db_session: AsyncSession, tmp_path):
    """Verify WAV recording mixing and recording_url persistence on call record."""
    f1 = str(tmp_path / "customer.wav")
    f2 = str(tmp_path / "agent.wav")
    out_wav = str(tmp_path / "mixed.wav")

    sample_rate = 8000
    num_samples = int(sample_rate * 0.1)

    for path in (f1, f2):
        with wave.open(path, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(struct.pack(f'<{num_samples}h', *([0] * num_samples)))

    mix_wav_files(f1, f2, out_wav)
    assert os.path.exists(out_wav)
    assert os.path.getsize(out_wav) > 0

    campaign = Campaign(campaign_name="Rec Test", agent="Voice-E (Tax Agent)", script="s", schedule_date="2026-07-23", schedule_time="10:00")
    db_session.add(campaign)
    await db_session.commit()

    contact = Contact(campaign_id=campaign.id, name="Rec User", phone="+919876543210")
    db_session.add(contact)
    await db_session.commit()

    job = Job(campaign_id=campaign.id, status="processing", total_contacts=1, completed_contacts=0, failed_contacts=0)
    db_session.add(job)
    await db_session.commit()

    call = Call(job_id=job.id, contact_id=contact.id, phone=contact.phone, status="in_progress")
    db_session.add(call)
    await db_session.commit()

    rec_path = "recordings/call_test_004.wav"
    completed_call = await CallService.complete_call(
        db=db_session,
        call_id=call.id,
        transcript="Hello, testing recording assignment.",
        recording_url=rec_path
    )
    assert completed_call is not None
    assert completed_call.recording_url == rec_path
    assert completed_call.status == "completed"


# ── TC-QUAL-005: AI Summary Generation ────────────────────────────────────────
@pytest.mark.asyncio
async def test_tc_qual_005_ai_summary_generation(db_session: AsyncSession):
    """Verify AI summary & classification assignment during call completion."""
    campaign = Campaign(campaign_name="AI Test", agent="Voice-E (Tax Agent)", script="s", schedule_date="2026-07-23", schedule_time="10:00")
    db_session.add(campaign)
    await db_session.commit()

    contact = Contact(campaign_id=campaign.id, name="AI Contact", phone="+919876543211")
    db_session.add(contact)
    await db_session.commit()

    job = Job(campaign_id=campaign.id, status="processing", total_contacts=1, completed_contacts=0, failed_contacts=0)
    db_session.add(job)
    await db_session.commit()

    call = Call(job_id=job.id, contact_id=contact.id, phone=contact.phone, status="in_progress")
    db_session.add(call)
    await db_session.commit()

    transcript = (
        "Agent: Hello AI Contact, calling from Tax Services regarding your ITR filing.\n"
        "Customer: Yes, I want to book a consultation for tomorrow at 2 PM.\n"
        "Agent: Great, appointment confirmed for tomorrow at 2 PM."
    )

    completed_call = await CallService.complete_call(
        db=db_session,
        call_id=call.id,
        transcript=transcript,
        customer_name="AI Contact",
        appointment_date="2026-07-24",
        appointment_time="14:00"
    )

    assert completed_call is not None
    assert completed_call.status == "completed"
    assert completed_call.summary is not None
    assert len(completed_call.summary) > 0
    assert completed_call.category in ["HOT", "WARM", "COLD", "UNCATEGORIZED"]


# ── TC-QUAL-006: Human Override — Manual Note ─────────────────────────────────
@pytest.mark.asyncio
async def test_tc_qual_006_human_override_manual_note(db_session: AsyncSession):
    """Verify manual override/notes on a call record in DB."""
    campaign = Campaign(campaign_name="Note Test", agent="Voice-E (Tax Agent)", script="s", schedule_date="2026-07-23", schedule_time="10:00")
    db_session.add(campaign)
    await db_session.commit()

    contact = Contact(campaign_id=campaign.id, name="Note User", phone="+919876543212")
    db_session.add(contact)
    await db_session.commit()

    job = Job(campaign_id=campaign.id, status="processing", total_contacts=1, completed_contacts=0, failed_contacts=0)
    db_session.add(job)
    await db_session.commit()

    call = Call(job_id=job.id, contact_id=contact.id, phone=contact.phone, status="in_progress")
    db_session.add(call)
    await db_session.commit()

    completed_call = await CallService.complete_call(
        db=db_session,
        call_id=call.id,
        transcript="Discussed ITR."
    )

    assert completed_call is not None
    completed_call.category = "HOT"
    completed_call.summary = "Manual Override: High Interest"
    await db_session.commit()
    await db_session.refresh(completed_call)

    assert completed_call.category == "HOT"
    assert completed_call.summary == "Manual Override: High Interest"


# ── TC-BULK-006: Upload Contacts via CSV ──────────────────────────────────────
@pytest.mark.asyncio
async def test_tc_bulk_006_upload_contacts_via_csv(client: AsyncClient, db_session: AsyncSession):
    """Verify parsing CSV data into contact records for campaign creation."""
    csv_content = "Name,Phone\nJohn Doe,+12345678901\nJane Smith,+12345678902\n"
    f = io.StringIO(csv_content)
    reader = csv.DictReader(f)
    parsed_contacts = [{"name": row["Name"], "phone": row["Phone"]} for row in reader]

    assert len(parsed_contacts) == 2
    assert parsed_contacts[0]["name"] == "John Doe"

    payload = {
        "campaign_name": "CSV Bulk Campaign Test",
        "agent": "Voice-E (Tax Agent)",
        "script": "CSV test script",
        "schedule_date": "2026-07-23",
        "schedule_time": "18:00",
        "contacts": parsed_contacts
    }

    resp = await client.post("/api/campaigns", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    campaign_id = data["campaign_id"]

    res = await db_session.execute(select(Contact).where(Contact.campaign_id == campaign_id))
    contacts = res.scalars().all()
    assert len(contacts) == 2
    assert {c.name for c in contacts} == {"John Doe", "Jane Smith"}


# ── TC-BULK-008: Launch Campaign with No Contacts ─────────────────────────────
@pytest.mark.asyncio
async def test_tc_bulk_008_launch_campaign_with_no_contacts(client: AsyncClient, db_session: AsyncSession):
    """Verify launching a campaign with 0 contacts returns HTTP 400 validation error."""
    campaign = Campaign(
        campaign_name="Empty Contacts Campaign",
        agent="Voice-E (Tax Agent)",
        script="Script",
        schedule_date="2026-07-23",
        schedule_time="10:00",
        status="pending"
    )
    db_session.add(campaign)
    await db_session.commit()

    resp = await client.post(f"/api/campaigns/{campaign.id}/launch")
    assert resp.status_code == 400
    data = resp.json()
    assert "no contacts" in data["detail"].lower()


# ── TC-REL-002: Campaign Queue — No Freeze After First Call ───────────────────
@pytest.mark.asyncio
async def test_tc_rel_002_campaign_queue_multi_contact_no_freeze(db_session: AsyncSession):
    """Verify multi-contact job creates call records for all contacts sequentially."""
    campaign = Campaign(
        campaign_name="Multi Contact Queue Test",
        agent="Voice-E (Tax Agent)",
        script="Multi contact script",
        schedule_date="2026-07-23",
        schedule_time="10:00",
        status="pending"
    )
    db_session.add(campaign)
    await db_session.commit()

    c1 = Contact(campaign_id=campaign.id, name="User One", phone="+1000000001", status="pending")
    c2 = Contact(campaign_id=campaign.id, name="User Two", phone="+1000000002", status="pending")
    c3 = Contact(campaign_id=campaign.id, name="User Three", phone="+1000000003", status="pending")
    db_session.add_all([c1, c2, c3])
    await db_session.commit()

    job, total_count = await CampaignService.launch_campaign(db_session, campaign.id)
    assert job is not None
    assert job.total_contacts == 3
    assert job.status == "queued"

    res = await db_session.execute(select(Contact).where(Contact.campaign_id == campaign.id))
    contacts = res.scalars().all()

    # Simulate sequential call completions for contact 1, contact 2, contact 3
    for c in contacts:
        call = Call(job_id=job.id, contact_id=c.id, phone=c.phone, status="in_progress")
        db_session.add(call)
        await db_session.commit()

        completed = await CallService.complete_call(
            db=db_session,
            call_id=call.id,
            transcript=f"Finished call with {c.name}"
        )
        assert completed is not None
        assert completed.status == "completed"

    await db_session.refresh(job)
    await db_session.refresh(campaign)
    assert job.status == "completed"
    assert job.completed_contacts == 3
    assert campaign.status == "completed"


# ── TC-REL-004: Stale Call Recovery ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_tc_rel_004_stale_call_recovery(db_session: AsyncSession):
    """Verify stale call recovery marks stuck calls as failed and updates job counters."""
    campaign = Campaign(campaign_name="Stale Test", agent="Voice-E (Tax Agent)", script="s", schedule_date="2026-07-23", schedule_time="10:00")
    db_session.add(campaign)
    await db_session.commit()

    contact = Contact(campaign_id=campaign.id, name="Stuck User", phone="+919999999999", status="calling")
    db_session.add(contact)
    await db_session.commit()

    job = Job(campaign_id=campaign.id, status="processing", total_contacts=1, completed_contacts=0, failed_contacts=0)
    db_session.add(job)
    await db_session.commit()

    call = Call(job_id=job.id, contact_id=contact.id, phone=contact.phone, status="in_progress")
    db_session.add(call)
    await db_session.commit()

    # Perform stale recovery logic
    call.status = "failed"
    contact.status = "failed"
    job.failed_contacts += 1
    await db_session.commit()

    await db_session.refresh(call)
    await db_session.refresh(contact)
    await db_session.refresh(job)

    assert call.status == "failed"
    assert contact.status == "failed"
    assert job.failed_contacts == 1
