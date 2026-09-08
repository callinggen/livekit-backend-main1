import os
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from sqlalchemy import select, update, func
from app.services.queue_service import QueueService
from app.models.job import Job
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.call import Call
from app.schemas.campaign import CampaignCreate


async def _terminate_livekit_room(room_name: str):
    """Deletes a LiveKit room to forcibly disconnect and hang up SIP call."""
    if not room_name:
        return
    try:
        from livekit import api
        lk_url = os.getenv("LIVEKIT_URL", "").replace("ws://", "http://").replace("wss://", "https://")
        lk_key = os.getenv("LIVEKIT_API_KEY")
        lk_secret = os.getenv("LIVEKIT_API_SECRET")
        if lk_url and lk_key and lk_secret:
            lkapi = api.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret)
            try:
                await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
                print(f"[CampaignService] LiveKit room '{room_name}' deleted successfully.")
            finally:
                await lkapi.aclose()
    except Exception as e:
        print(f"[CampaignService] Warning: Error deleting LiveKit room '{room_name}': {e}")


class CampaignService:

    @staticmethod
    async def queue_campaign_job(
        db: AsyncSession,
        campaign: Campaign,
        total_contacts: int,
    ) -> Job:
        """Helper to create a Job only if one doesn't exist."""
        result = await db.execute(
            select(Job).where(
                Job.campaign_id == campaign.id,
                Job.status.in_(["queued", "processing"])
            )
        )
        existing_job = result.scalars().first()
        if existing_job:
            return existing_job

        job = Job(
            campaign_id=campaign.id,
            status="queued",
            total_contacts=total_contacts,
            completed_contacts=0,
            failed_contacts=0,
        )
        db.add(job)
        campaign.status = "running"
        await db.commit()
        await db.refresh(job)
        return job

    @staticmethod
    async def launch_campaign(
        db: AsyncSession,
        campaign_id: int,
    ):
        campaign = await db.get(
            Campaign,
            campaign_id,
        )
        if campaign is None:
            raise HTTPException(
                status_code=404,
                detail="Campaign not found",
            )
        if campaign.status == "running":
            raise HTTPException(
                status_code=400,
                detail="Campaign is already running",
            )
        result = await db.execute(
            select(Contact).where(
                Contact.campaign_id == campaign.id
            )
        )

        contacts = result.scalars().all()
        if len(contacts) == 0:
            raise HTTPException(
                status_code=400,
                detail="Campaign has no contacts",
            )
            
        existing_job_result = await db.execute(
            select(Job).where(
                Job.campaign_id == campaign.id,
                Job.status.in_(["queued", "processing"])
            )
        )
        existing_job = existing_job_result.scalars().first()
        if existing_job:
            return existing_job, len(contacts)

        from datetime import datetime, timezone, timedelta
        
        # Check if it's scheduled for the future
        schedule_dt = None
        try:
            # Parse ISO 8601 UTC string e.g. "2023-11-20T14:30:00Z"
            iso_str = campaign.schedule_date.replace("Z", "+00:00")
            schedule_dt = datetime.fromisoformat(iso_str)
            # Ensure timezone-aware so comparison with UTC now works
            if schedule_dt.tzinfo is None:
                schedule_dt = schedule_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
            
        now = datetime.now(timezone.utc)
        # If scheduled for the future (beyond a 15-second grace period), mark scheduled
        if schedule_dt and schedule_dt > now + timedelta(seconds=15):
            campaign.status = "scheduled"
            await db.commit()
            return None, len(contacts)

        job = await CampaignService.queue_campaign_job(db, campaign, len(contacts))
        return job, len(contacts)

    @staticmethod
    async def create_campaign(
        db: AsyncSession,
        data: CampaignCreate,
        user_id: int | None = None,
    ) -> Campaign:

        campaign = Campaign(
            user_id=user_id,
            campaign_name=data.campaign_name,
            agent=data.agent,
            script=data.script,
            schedule_date=data.schedule_date,
            schedule_time=data.schedule_time,
            outbound_phone_number=getattr(data, "outbound_phone_number", None),
            status="pending",
            campaign_type="normal",
            upload_source=data.upload_source,
            sheet_name=data.sheet_name,
            voicemail_detection=getattr(data, "voicemail_detection", None),
            whatsapp_automation=getattr(data, "whatsapp_automation", None),
        )


        db.add(campaign)
        await db.flush()

        contacts = []
        subset = data.contacts
        remaining = []

        if data.selection_type == "range" and data.start_row and data.end_row:
            start_idx = max(0, data.start_row - 1)
            end_idx = min(len(data.contacts), data.end_row)
            subset = data.contacts[start_idx:end_idx]
            remaining = data.contacts[:start_idx] + data.contacts[end_idx:]

        for item in subset:
            contact = Contact(
                campaign_id=campaign.id,
                name=item.name,
                phone=item.phone,
                status="pending",
                metadata_fields=item.metadata_fields,
                original_row=item.original_row,
            )
            contacts.append(contact)

        db.add_all(contacts)

        # Create pending campaign for remaining contacts
        if remaining:
            pending_campaign = Campaign(
                user_id=user_id,
                campaign_name=f"{data.campaign_name} - Remaining",
                agent=data.agent,
                script=data.script,
                schedule_date=data.schedule_date,
                schedule_time=data.schedule_time,
                status="pending",
                campaign_type="pending",
                parent_campaign_id=campaign.id,
                upload_source=data.upload_source,
                sheet_name=data.sheet_name,
                voicemail_detection=getattr(data, "voicemail_detection", None),
                whatsapp_automation=getattr(data, "whatsapp_automation", None),
            )
            db.add(pending_campaign)
            await db.flush()
            
            pending_contacts = []
            for item in remaining:
                contact = Contact(
                    campaign_id=pending_campaign.id,
                    name=item.name,
                    phone=item.phone,
                    status="pending",
                    metadata_fields=item.metadata_fields,
                    original_row=item.original_row,
                )
                pending_contacts.append(contact)
            
            db.add_all(pending_contacts)

        await db.commit()
        await db.refresh(campaign)

        return campaign

    @staticmethod
    async def pause_campaign(
        db: AsyncSession,
        campaign_id: int,
    ) -> Campaign:
        from app.services.call_service import CallService

        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        if campaign.status not in ("running", "scheduled", "pending"):
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot pause campaign with status '{campaign.status}'"
            )

        campaign.status = "paused"

        # 1. Pause any queued or processing jobs
        job_result = await db.execute(
            select(Job).where(
                Job.campaign_id == campaign_id,
                Job.status.in_(["queued", "processing"])
            )
        )
        jobs = job_result.scalars().all()
        for j in jobs:
            j.status = "paused"

        # 2. Terminate any ongoing calls immediately
        call_result = await db.execute(
            select(Call).where(
                Call.campaign_id == campaign_id,
                Call.status.in_(["dialing", "in_progress"])
            )
        )
        ongoing_calls = call_result.scalars().all()
        print(f"[CampaignService] Pausing campaign {campaign_id}: Terminating {len(ongoing_calls)} ongoing call(s)...")

        for call in ongoing_calls:
            # Drop LiveKit room to immediately hang up telecom call
            if call.room_name:
                await _terminate_livekit_room(call.room_name)

            if call.status == "in_progress" or call.sip_was_active:
                await CallService.complete_call(
                    db=db, 
                    call_id=call.id, 
                    outcome="campaign_paused"
                )
            else:
                # Call was dialing; end call and restore contact to pending so it will be retried upon resume
                await CallService.fail_call(
                    db=db, 
                    call_id=call.id, 
                    outcome="interrupted", 
                    failure_reason="campaign_paused"
                )
                if call.contact_id:
                    contact = await db.get(Contact, call.contact_id)
                    if contact:
                        contact.status = "pending"
                        contact.response = ""

        await db.commit()
        await db.refresh(campaign)
        return campaign

    @staticmethod
    async def resume_campaign(
        db: AsyncSession,
        campaign_id: int,
    ) -> Campaign:
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        if campaign.status != "paused":
            raise HTTPException(
                status_code=400, 
                detail=f"Campaign is not paused (current status: '{campaign.status}')"
            )

        campaign.status = "running"

        # Check if there are paused jobs to resume
        job_result = await db.execute(
            select(Job).where(
                Job.campaign_id == campaign_id,
                Job.status == "paused"
            ).order_by(Job.id.desc())
        )
        paused_job = job_result.scalars().first()
        if paused_job:
            paused_job.status = "queued"
        else:
            # Count pending contacts to see if a new job is required
            pending_res = await db.execute(
                select(func.count(Contact.id)).where(
                    Contact.campaign_id == campaign_id,
                    Contact.status == "pending"
                )
            )
            pending_count = pending_res.scalar() or 0
            if pending_count > 0:
                await CampaignService.queue_campaign_job(db, campaign, pending_count)

        await db.commit()
        await db.refresh(campaign)
        return campaign

    @staticmethod
    async def stop_campaign(
        db: AsyncSession,
        campaign_id: int,
    ) -> Campaign:
        from app.services.call_service import CallService

        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        if campaign.status in ("completed", "stopped"):
            raise HTTPException(
                status_code=400, 
                detail=f"Campaign is already {campaign.status}"
            )

        campaign.status = "stopped"
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # 1. Stop any active or paused jobs
        job_result = await db.execute(
            select(Job).where(
                Job.campaign_id == campaign_id,
                Job.status.in_(["queued", "processing", "paused"])
            )
        )
        jobs = job_result.scalars().all()
        for j in jobs:
            j.status = "stopped"
            j.finished_at = now

        # 2. Terminate all ongoing calls
        call_result = await db.execute(
            select(Call).where(
                Call.campaign_id == campaign_id,
                Call.status.in_(["dialing", "in_progress"])
            )
        )
        ongoing_calls = call_result.scalars().all()
        print(f"[CampaignService] Stopping campaign {campaign_id}: Terminating {len(ongoing_calls)} ongoing call(s)...")

        for call in ongoing_calls:
            if call.room_name:
                await _terminate_livekit_room(call.room_name)

            if call.status == "in_progress" or call.sip_was_active:
                await CallService.complete_call(
                    db=db, 
                    call_id=call.id, 
                    outcome="campaign_stopped"
                )
            else:
                await CallService.fail_call(
                    db=db, 
                    call_id=call.id, 
                    outcome="interrupted", 
                    failure_reason="campaign_stopped"
                )

        # 3. Mark all remaining pending and dialing contacts as stopped
        await db.execute(
            update(Contact)
            .where(Contact.campaign_id == campaign_id)
            .where(Contact.status.in_(["pending", "dialing"]))
            .values(status="failed", response="Campaign Stopped")
        )

        await db.commit()
        await db.refresh(campaign)
        return campaign