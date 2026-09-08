import pytest  # type: ignore
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job

@pytest.mark.asyncio
async def test_create_campaign(client: AsyncClient, db_session: AsyncSession):
    payload = {
        "campaign_name": "Test Automation Campaign",
        "agent": "Voice-A (Sales)",
        "script": "Hello this is a test script.",
        "schedule_date": "2026-07-15",
        "schedule_time": "14:30",
        "contacts": [
            {"name": "Alice", "phone": "+1234567890"},
            {"name": "Bob", "phone": "+9876543210"}
        ]
    }
    
    resp = await client.post("/api/campaigns", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Campaign created successfully"
    assert "campaign_id" in data
    
    # Verify in DB
    campaign_id = data["campaign_id"]
    campaign = await db_session.get(Campaign, campaign_id)
    assert campaign is not None
    assert campaign.campaign_name == "Test Automation Campaign"
    assert campaign.status == "pending"
    
    # Check contacts
    res = await db_session.execute(
        select(Contact).where(Contact.campaign_id == campaign_id)
    )
    contacts = res.scalars().all()
    assert len(contacts) == 2
    assert {c.name for c in contacts} == {"Alice", "Bob"}

@pytest.mark.asyncio
async def test_list_campaigns(client: AsyncClient, db_session: AsyncSession):
    campaign = Campaign(
        campaign_name="List Campaign",
        agent="Voice-B (Support)",
        script="Script text",
        schedule_date="2026-07-15",
        schedule_time="12:00",
        status="pending"
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    
    resp = await client.get("/api/campaigns")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    
    found = [c for c in data if c["id"] == str(campaign.id)]
    assert len(found) == 1
    assert found[0]["name"] == "List Campaign"
    assert found[0]["status"] == "Scheduled"

@pytest.mark.asyncio
async def test_launch_campaign_and_status(client: AsyncClient, db_session: AsyncSession):
    campaign = Campaign(
        user_id=1,
        campaign_name="Launch Campaign",
        agent="Voice-C (Followup)",
        script="Followup script",
        schedule_date="2026-07-15",
        schedule_time="12:00",
        status="pending"
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    
    contact = Contact(
        campaign_id=campaign.id,
        name="Charlie",
        phone="+1112223333",
        status="pending"
    )
    db_session.add(contact)
    await db_session.commit()
    
    # Launch campaign
    resp = await client.post(f"/api/campaigns/{campaign.id}/launch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Campaign launched successfully"
    assert "job_id" in data
    
    # Verify campaign status is now running
    await db_session.refresh(campaign)
    assert campaign.status == "running"
    
    # Verify job is created in DB
    job = await db_session.get(Job, data["job_id"])
    assert job is not None
    assert job.status == "queued"
    assert job.total_contacts == 1
    
    # Test status endpoint
    status_resp = await client.get(f"/api/campaigns/{campaign.id}/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "Running"
    assert status_data["total"] == 1
    assert status_data["completed"] == 0
    assert status_data["failed"] == 0


@pytest.mark.asyncio
async def test_pause_resume_stop_campaign(client: AsyncClient, db_session: AsyncSession):
    # Setup campaign with contact and job
    campaign = Campaign(
        campaign_name="Pause Stop Test Campaign",
        agent="Voice-A",
        script="Script",
        schedule_date="2026-07-15",
        schedule_time="12:00",
        status="running"
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)

    contact = Contact(
        campaign_id=campaign.id,
        name="Dave",
        phone="+9998887777",
        status="pending"
    )
    db_session.add(contact)
    job = Job(
        campaign_id=campaign.id,
        status="queued",
        total_contacts=1,
        completed_contacts=0,
        failed_contacts=0
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    # 1. Pause campaign
    pause_resp = await client.post(f"/api/campaigns/{campaign.id}/pause")
    assert pause_resp.status_code == 200
    pause_data = pause_resp.json()
    assert pause_data["status"] == "Paused"
    await db_session.refresh(campaign)
    assert campaign.status == "paused"
    await db_session.refresh(job)
    assert job.status == "paused"

    # 2. Resume campaign
    resume_resp = await client.post(f"/api/campaigns/{campaign.id}/resume")
    assert resume_resp.status_code == 200
    resume_data = resume_resp.json()
    assert resume_data["status"] == "Running"
    await db_session.refresh(campaign)
    assert campaign.status == "running"
    await db_session.refresh(job)
    assert job.status == "queued"

    # 3. Stop campaign
    stop_resp = await client.post(f"/api/campaigns/{campaign.id}/stop")
    assert stop_resp.status_code == 200
    stop_data = stop_resp.json()
    assert stop_data["status"] == "Stopped"
    await db_session.refresh(campaign)
    assert campaign.status == "stopped"
    await db_session.refresh(job)
    assert job.status == "stopped"
    await db_session.refresh(contact)
    assert contact.status == "failed"
    assert contact.response == "Campaign Stopped"

