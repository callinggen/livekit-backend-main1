import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.services.campaign_service import CampaignService
from app.services.call_service import CallService
from app.services.queue_service import QueueService
from app.schemas.campaign import CampaignCreate, ContactCreate
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call

@pytest.mark.asyncio
async def test_campaign_service_create_and_launch(db_session: AsyncSession):
    data = CampaignCreate(
        campaign_name="Service Campaign",
        agent="Voice-A (Sales)",
        script="Script",
        schedule_date="2026-07-15",
        schedule_time="14:00",
        contacts=[
            ContactCreate(name="Alice", phone="+123")
        ]
    )
    
    # Test Create
    campaign = await CampaignService.create_campaign(db_session, data)
    assert campaign.id is not None
    assert campaign.campaign_name == "Service Campaign"
    assert campaign.status == "pending"
    
    # Verify contact is created
    res = await db_session.execute(
        select(Contact).where(Contact.campaign_id == campaign.id)
    )
    contacts = res.scalars().all()
    assert len(contacts) == 1
    assert contacts[0].name == "Alice"
    
    # Test Launch
    job = await CampaignService.launch_campaign(db_session, campaign.id)
    assert job.id is not None
    assert job.status == "queued"
    assert job.total_contacts == 1
    assert campaign.status == "running"

@pytest.mark.asyncio
async def test_call_service_complete_and_fail(db_session: AsyncSession):
    # Setup database records for a campaign with a single contact and call
    campaign = Campaign(
        campaign_name="Service Call Test Campaign",
        agent="Voice-A (Sales)",
        script="Script text",
        schedule_date="2026-07-15",
        schedule_time="10:00",
        status="running"
    )
    db_session.add(campaign)
    await db_session.commit()
    
    contact = Contact(
        campaign_id=campaign.id,
        name="Bob",
        phone="+987",
        status="calling"
    )
    db_session.add(contact)
    await db_session.commit()
    
    job = Job(
        campaign_id=campaign.id,
        status="processing",
        total_contacts=2, # Total contacts is 2
        completed_contacts=0,
        failed_contacts=0
    )
    db_session.add(job)
    await db_session.commit()
    
    call1 = Call(
        job_id=job.id,
        contact_id=contact.id,
        phone=contact.phone,
        status="in_progress"
    )
    db_session.add(call1)
    await db_session.commit()
    
    # Test complete_call on Call 1
    call1 = await CallService.complete_call(
        db=db_session,
        call_id=call1.id,
        transcript="assistant: hello",
        customer_name="Bob Jr",
        appointment_date="2026-07-15",
        appointment_time="15:00"
    )
    assert call1.status == "completed"
    assert contact.status == "completed"
    assert contact.customer_name == "Bob Jr"
    assert job.completed_contacts == 1
    assert job.status == "running" or job.status == "processing"
    assert campaign.status == "running"
    
    # Setup Call 2
    contact2 = Contact(
        campaign_id=campaign.id,
        name="Charlie",
        phone="+456",
        status="calling"
    )
    db_session.add(contact2)
    await db_session.commit()
    
    call2 = Call(
        job_id=job.id,
        contact_id=contact2.id,
        phone=contact2.phone,
        status="in_progress"
    )
    db_session.add(call2)
    await db_session.commit()
    
    # Test fail_call on Call 2
    call2 = await CallService.fail_call(db=db_session, call_id=call2.id)
    assert call2.status == "failed"
    assert contact2.status == "failed"
    assert job.failed_contacts == 1
    
    # Since completed (1) + failed (1) >= total (2), check if job & campaign are completed
    assert job.status == "completed"
    assert campaign.status == "completed"

@pytest.mark.asyncio
async def test_queue_service_process_job_mocks(db_session: AsyncSession):
    # Setup campaign, contact, job
    campaign = Campaign(
        campaign_name="Queue Campaign",
        agent="Voice-A (Sales)",
        script="Script",
        schedule_date="2026-07-15",
        schedule_time="10:00",
        status="running"
    )
    db_session.add(campaign)
    await db_session.commit()
    
    contact = Contact(
        campaign_id=campaign.id,
        name="Eve",
        phone="+777",
        status="pending"
    )
    db_session.add(contact)
    await db_session.commit()
    
    job = Job(
        campaign_id=campaign.id,
        status="processing",
        total_contacts=1,
        completed_contacts=0,
        failed_contacts=0
    )
    db_session.add(job)
    await db_session.commit()
    
    # Mock make_livekit_call to succeed
    with patch("app.services.queue_service.make_livekit_call", new_callable=AsyncMock) as mock_make_call:
        mock_make_call.return_value = {
            "success": True,
            "participant_id": "mock_participant_123"
        }
        
        # Process job should trigger call dialing and transition call to in_progress
        result = await QueueService.process_job(db_session, job.id)
        assert result is True
        
        # Verify make_livekit_call is called with right parameters
        mock_make_call.assert_called_once()
        called_kwargs = mock_make_call.call_args[1]
        assert called_kwargs["phone"] == "+777"
        assert called_kwargs["room_name"].startswith("call-")
        
        # Verify call is in_progress in DB
        res = await db_session.execute(
            select(Call).where(Call.job_id == job.id)
        )
        calls = res.scalars().all()
        assert len(calls) == 1
        assert calls[0].status == "in_progress"
        assert calls[0].livekit_participant_id == "mock_participant_123"
