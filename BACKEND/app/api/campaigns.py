from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.schemas.campaign import CampaignCreate
from app.services.campaign_service import CampaignService
from app.models.campaign import Campaign
from app.models.job import Job
from app.models.contact import Contact

router = APIRouter()


# ── POST /api/campaigns ────────────────────────────────────────────────────

@router.post("/campaigns")
async def create_campaign(
    campaign: CampaignCreate,
    db: AsyncSession = Depends(get_db),
):
    created_campaign = await CampaignService.create_campaign(
        db=db,
        data=campaign,
    )
    return {
        "message": "Campaign created successfully",
        "campaign_id": created_campaign.id,
    }


# ── POST /api/campaigns/{campaign_id}/launch ───────────────────────────────

@router.post("/campaigns/{campaign_id}/launch")
async def launch_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    job, total_contacts = await CampaignService.launch_campaign(
        db=db,
        campaign_id=campaign_id,
    )
    
    if job is None:
        return {
            "message": "Campaign scheduled successfully",
            "job_id": -1,
            "total_contacts": total_contacts,
        }

    return {
        "message": "Campaign launched successfully",
        "job_id": job.id,
        "total_contacts": total_contacts,
    }


# ── GET /api/campaigns ─────────────────────────────────────────────────────

@router.get("/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    """
    Return all campaigns with aggregated stats pulled from their latest job.
    Used by the Campaigns page table.
    """
    result = await db.execute(select(Campaign).order_by(Campaign.id.desc()))
    campaigns = result.scalars().all()

    out = []
    for c in campaigns:
        # Latest job for this campaign
        job_result = await db.execute(
            select(Job)
            .where(Job.campaign_id == c.id)
            .order_by(Job.id.desc())
            .limit(1)
        )
        job = job_result.scalars().first()

        # Count contacts
        contact_result = await db.execute(
            select(func.count()).where(Contact.campaign_id == c.id)
        )
        total_contacts = contact_result.scalar() or 0

        completed = job.completed_contacts if job else 0
        failed = job.failed_contacts if job else 0

        out.append({
            "id": str(c.id),
            "name": c.campaign_name,
            "date": c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
            "schedule": f"{c.schedule_date} {c.schedule_time}",
            "sheetName": "—",
            "totalCalls": total_contacts,
            "completedCalls": completed,
            "failedCalls": failed,
            "interested": 0,
            "callbacks": 0,
            "creditsUsed": 0,
            "agent": c.agent,
            "status": _map_status(c.status),
            "script": c.script,
            "uploadSource": "API",
            "notes": "",
        })
    return out


# ── GET /api/campaigns/{campaign_id} ──────────────────────────────────────

@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        return {"error": "Not found"}

    job_result = await db.execute(
        select(Job).where(Job.campaign_id == campaign_id).order_by(Job.id.desc()).limit(1)
    )
    job = job_result.scalars().first()

    contacts_result = await db.execute(
        select(Contact).where(Contact.campaign_id == campaign_id)
    )
    contacts = contacts_result.scalars().all()

    return {
        "id": str(campaign.id),
        "name": campaign.campaign_name,
        "agent": campaign.agent,
        "script": campaign.script,
        "schedule_date": campaign.schedule_date,
        "schedule_time": campaign.schedule_time,
        "status": campaign.status,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else "",
        "job": {
            "total_contacts": job.total_contacts if job else len(contacts),
            "completed_contacts": job.completed_contacts if job else 0,
            "failed_contacts": job.failed_contacts if job else 0,
            "status": job.status if job else "queued",
        },
        "contacts": [
            {
                "id": ct.id,
                "name": ct.name,
                "phone": ct.phone,
                "status": ct.status,
                "response": ct.response,
                "customer_name": ct.customer_name,
                "appointment_date": ct.appointment_date,
                "appointment_time": ct.appointment_time,
                "transcript": ct.transcript,
                "duration": ct.duration,
                "metadata_fields": ct.metadata_fields,
            }
            for ct in contacts
        ],
    }


# ── GET /api/campaigns/{campaign_id}/contacts ─────────────────────────────

@router.get("/campaigns/{campaign_id}/contacts")
async def get_campaign_contacts(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Contact).where(Contact.campaign_id == campaign_id)
    )
    contacts = result.scalars().all()
    return [
        {
            "id": ct.id,
            "name": ct.name,
            "phone": ct.phone,
            "status": ct.status,
            "response": ct.response or "—",
            "datetime": "",
            "metadata_fields": ct.metadata_fields,
        }
        for ct in contacts
    ]


# ── GET /api/campaigns/{campaign_id}/live ─────────────────────────────────────
# BUG-007: Real-time per-contact status counts for the Live Journey panel.

@router.get("/campaigns/{campaign_id}/live")
async def get_campaign_live(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Return per-contact status counts so the frontend can update the live tracking panel."""
    result = await db.execute(
        select(Contact).where(Contact.campaign_id == campaign_id)
    )
    contacts = result.scalars().all()

    campaign = await db.get(Campaign, campaign_id)
    job_result = await db.execute(
        select(Job).where(Job.campaign_id == campaign_id).order_by(Job.id.desc()).limit(1)
    )
    job = job_result.scalars().first()

    return {
        "registry": len(contacts),
        "standby":  sum(1 for c in contacts if c.status == "pending"),
        "dialer":   sum(1 for c in contacts if c.status in ("calling", "in_progress", "dialing")),
        "analysis": sum(1 for c in contacts if c.status in ("completed", "failed", "busy", "no_answer")),
        "completed": sum(1 for c in contacts if c.status == "completed"),
        "failed":    sum(1 for c in contacts if c.status in ("failed", "busy", "no_answer")),
        "campaign_status": _map_status(campaign.status) if campaign else "Unknown",
        "schedule_date": campaign.schedule_date if campaign else "",
        "schedule_time": campaign.schedule_time if campaign else "",
        "total_contacts": job.total_contacts if job else len(contacts),
        "contacts": [
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone,
                "status": c.status,
                "response": c.response or "—",
            }
            for c in contacts
        ]
    }


# ── GET /api/campaigns/{campaign_id}/status ───────────────────────────────────
# BUG-024: Lightweight status endpoint for fast polling without fetching all data.

@router.get("/campaigns/{campaign_id}/status")
async def get_campaign_status(campaign_id: int, db: AsyncSession = Depends(get_db)):
    """Lightweight endpoint to poll campaign + job status."""
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        return {"error": "Not found"}

    job_result = await db.execute(
        select(Job).where(Job.campaign_id == campaign_id).order_by(Job.id.desc()).limit(1)
    )
    job = job_result.scalars().first()

    return {
        "status": _map_status(campaign.status),
        "completed": job.completed_contacts if job else 0,
        "failed": job.failed_contacts if job else 0,
        "total": job.total_contacts if job else 0,
    }


# ── helpers ────────────────────────────────────────────────────────────────

def _map_status(status: str) -> str:
    """Map backend status values to the capitalized strings the frontend uses."""
    return {
        "pending":   "Scheduled",
        "running":   "Running",
        "completed": "Completed",
        "failed":    "Failed",
        "paused":    "Paused",
    }.get(status, status.capitalize())