from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from sqlalchemy import select
from app.services.queue_service import QueueService
from app.models.job import Job
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.schemas.campaign import CampaignCreate


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
        # If scheduled for the future (beyond a 10s network grace period), mark scheduled
        if schedule_dt and schedule_dt > now + timedelta(seconds=10):
            campaign.status = "scheduled"
            await db.commit()
            return None, len(contacts)

        job = await CampaignService.queue_campaign_job(db, campaign, len(contacts))
        return job, len(contacts)

    @staticmethod
    async def create_campaign(
        db: AsyncSession,
        data: CampaignCreate,
    ) -> Campaign:

        campaign = Campaign(
            campaign_name=data.campaign_name,
            agent=data.agent,
            script=data.script,
            schedule_date=data.schedule_date,
            schedule_time=data.schedule_time,
            status="pending",
        )

        db.add(campaign)

        await db.flush()

        contacts = []

        for item in data.contacts:

            contact = Contact(
                campaign_id=campaign.id,
                name=item.name,
                phone=item.phone,
                status="pending",
                metadata_fields=item.metadata_fields,
            )

            contacts.append(contact)

        db.add_all(contacts)

        await db.commit()

        await db.refresh(campaign)

        return campaign