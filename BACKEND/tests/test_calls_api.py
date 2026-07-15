import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call

@pytest.mark.asyncio
async def test_complete_and_list_calls(client: AsyncClient, db_session: AsyncSession):
    # Setup database records for a campaign with a single contact and call
    campaign = Campaign(
        campaign_name="Calls API Test Campaign",
        agent="Voice-A (Sales)",
        script="Script text",
        schedule_date="2026-07-15",
        schedule_time="10:00",
        status="running"
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    
    contact = Contact(
        campaign_id=campaign.id,
        name="Dave",
        phone="+1555000111",
        status="calling"
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)
    
    job = Job(
        campaign_id=campaign.id,
        status="processing",
        total_contacts=1,
        completed_contacts=0,
        failed_contacts=0
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    
    call = Call(
        job_id=job.id,
        contact_id=contact.id,
        phone=contact.phone,
        status="in_progress"
    )
    db_session.add(call)
    await db_session.commit()
    await db_session.refresh(call)
    
    # Complete call API payload
    payload = {
        "transcript": "assistant: Welcome\nuser: Thank you\nassistant: End call",
        "customer_name": "Dave Smith",
        "appointment_date": "2026-07-20",
        "appointment_time": "15:00"
    }
    
    # Run POST /api/calls/{call_id}/complete
    complete_resp = await client.post(f"/api/calls/{call.id}/complete", json=payload)
    assert complete_resp.status_code == 200
    complete_data = complete_resp.json()
    assert complete_data["success"] is True
    assert complete_data["call_id"] == call.id
    
    # Verify database updates
    await db_session.refresh(call)
    await db_session.refresh(contact)
    await db_session.refresh(job)
    await db_session.refresh(campaign)
    
    assert call.status == "completed"
    assert call.ended_at is not None
    assert call.duration >= 0
    assert call.transcript == payload["transcript"]
    
    assert contact.status == "completed"
    assert contact.customer_name == "Dave Smith"
    assert contact.appointment_date == "2026-07-20"
    assert contact.appointment_time == "15:00"
    assert contact.response == "Appointment booked"
    
    assert job.status == "completed"
    assert job.completed_contacts == 1
    assert campaign.status == "completed"
    
    # Run GET /api/calls to verify listed responses
    list_resp = await client.get("/api/calls")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert len(list_data) >= 1
    
    found = [c for c in list_data if c["id"] == str(call.id)]
    assert len(found) == 1
    assert found[0]["name"] == "Dave Smith"
    assert found[0]["phone"] == "+1555000111"
    assert found[0]["status"] == "Completed"
    assert found[0]["response"] == "Appointment booked"
    assert len(found[0]["transcript"]) == 3
    assert found[0]["transcript"][0] == {"speaker": "assistant", "text": "Welcome"}
