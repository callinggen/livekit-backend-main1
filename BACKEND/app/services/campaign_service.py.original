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
        job = Job(
            campaign_id=campaign.id,
            status="queued",
            total_contacts=len(contacts),
            completed_contacts=0,
            failed_contacts=0,
        )
        db.add(job)
        campaign.status = "running"
        await db.commit()
        await db.refresh(job)
        return job

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
            )

            contacts.append(contact)

        db.add_all(contacts)

        await db.commit()

        await db.refresh(campaign)

        return campaign