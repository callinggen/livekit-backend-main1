from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from app.database import get_db
from app.schemas.campaign import CampaignCreate
from app.services.campaign_service import CampaignService
from app.models.campaign import Campaign
from app.models.job import Job
from app.models.contact import Contact
from app.models.call import Call
from app.models.user import User
from app.core.security import get_current_user

router = APIRouter()


# ── POST /api/campaigns ────────────────────────────────────────────────────

@router.post("/campaigns")
async def create_campaign(
    campaign: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.credits <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your credits have exhausted. Please recharge in order to continue."
        )

    created_campaign = await CampaignService.create_campaign(
        db=db,
        data=campaign,
        user_id=current_user.id,
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
async def list_campaigns(
    type: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all campaigns for the current user with aggregated stats pulled from their latest job.
    Used by the Campaigns page table.
    """
    query = select(Campaign).where(or_(Campaign.user_id == current_user.id, Campaign.user_id.is_(None))).where(Campaign.campaign_name != "Website Demo Requests")
    
    if type == "pending":
        query = query.where(Campaign.campaign_type == "pending")
    elif type == "normal":
        query = query.where(Campaign.campaign_type == "normal")
        
    query = query.order_by(Campaign.id.desc())
    
    result = await db.execute(query)
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

        # Count calls instead of just contacts to match call logs accurately
        total_calls_result = await db.execute(
            select(func.count())
            .select_from(Call)
            .join(Contact, Call.contact_id == Contact.id)
            .where(Contact.campaign_id == c.id)
        )
        total_contacts = total_calls_result.scalar() or 0

        completed_result = await db.execute(
            select(func.count())
            .select_from(Call)
            .join(Contact, Call.contact_id == Contact.id)
            .where(Contact.campaign_id == c.id, Call.status == "completed")
        )
        completed = completed_result.scalar() or 0

        failed_result = await db.execute(
            select(func.count())
            .select_from(Call)
            .join(Contact, Call.contact_id == Contact.id)
            .where(Contact.campaign_id == c.id, Call.status.in_(["failed", "incomplete"]))
        )
        failed = failed_result.scalar() or 0

        credits_result = await db.execute(
            select(func.sum(Call.credits_deducted))
            .join(Contact, Call.contact_id == Contact.id)
            .where(Contact.campaign_id == c.id)
        )
        credits_used = credits_result.scalar() or 0

        contacts_count_result = await db.execute(
            select(func.count(Contact.id)).where(Contact.campaign_id == c.id)
        )
        contact_count = contacts_count_result.scalar() or 0
        
        # Get parent campaign name if it's a pending campaign
        parent_campaign_name = None
        if c.parent_campaign_id:
            parent_result = await db.execute(select(Campaign.campaign_name).where(Campaign.id == c.parent_campaign_id))
            parent_campaign_name = parent_result.scalar()

        out.append({
            "id": str(c.id),
            "name": c.campaign_name,
            "date": c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
            "schedule": f"{c.schedule_date} {c.schedule_time}",
            "sheetName": c.sheet_name or "—",
            "totalCalls": total_contacts,
            "contactCount": contact_count,
            "completedCalls": completed,
            "failedCalls": failed,
            "interested": 0,
            "callbacks": 0,
            "creditsUsed": credits_used,
            "agent": c.agent,
            "status": _map_status(c.status),
            "script": c.script,
            "uploadSource": c.upload_source or "API",
            "notes": "",
            "campaignType": c.campaign_type,
            "parentCampaignId": c.parent_campaign_id,
            "parentCampaignName": parent_campaign_name,
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
        select(Contact, Call)
        .outerjoin(Call, Contact.id == Call.contact_id)
        .where(Contact.campaign_id == campaign_id)
    )
    results = contacts_result.all()

    credits_result = await db.execute(
        select(func.sum(Call.credits_deducted))
        .join(Contact, Call.contact_id == Contact.id)
        .where(Contact.campaign_id == campaign_id)
    )
    credits_used = credits_result.scalar() or 0

    calls_result = await db.execute(
        select(Call.status)
        .join(Contact, Call.contact_id == Contact.id)
        .where(Contact.campaign_id == campaign_id)
    )
    call_statuses = calls_result.scalars().all()

    # Get a list of unique contacts to avoid duplicates if multiple calls exist
    seen_contacts = set()
    unique_results = []
    for ct, call in results:
        if ct.id not in seen_contacts:
            seen_contacts.add(ct.id)
            unique_results.append((ct, call))

    # Build scheduled_at as a proper UTC ISO string so the browser can convert to local time
    scheduled_at = None
    if campaign.schedule_date and campaign.schedule_time:
        try:
            # schedule_time is stored as HH:MM or HH:MM:SS in UTC
            scheduled_at = f"{campaign.schedule_date}T{campaign.schedule_time}Z"
        except Exception:
            scheduled_at = None

    return {
        "id": str(campaign.id),
        "name": campaign.campaign_name,
        "agent": campaign.agent,
        "script": campaign.script,
        "schedule_date": campaign.schedule_date,
        "schedule_time": campaign.schedule_time,
        "scheduled_at": scheduled_at,
        "status": campaign.status,
        "created_at": (campaign.created_at.isoformat() + "Z") if campaign.created_at else "",
        "creditsUsed": credits_used,
        "upload_source": campaign.upload_source,
        "sheet_name": campaign.sheet_name,
        "job": {
            "total_contacts": len(call_statuses) if call_statuses else len(unique_results),
            "completed_contacts": sum(1 for s in call_statuses if s == "completed"),
            "failed_contacts": sum(1 for s in call_statuses if s in ("failed", "incomplete")),
            "status": job.status if job else "queued",
            "started_at": job.started_at.isoformat() + "Z" if (job and job.started_at) else None,
            "finished_at": job.finished_at.isoformat() + "Z" if (job and job.finished_at) else None,
        },
        "contacts": [
            {
                "id": ct.id,
                "name": ct.name,
                "phone": ct.phone,
                "status": ct.status,
                "response": ct.response or "—",
                "customer_name": ct.customer_name,
                "appointment_date": ct.appointment_date,
                "appointment_time": ct.appointment_time,
                "transcript": ct.transcript,
                "duration": call.duration if call else 0,
                # Use actual call start time, or scheduled time, or campaign creation time
                "datetime": (
                    call.started_at.strftime("%Y-%m-%d %I:%M %p IST")
                    if (call and call.started_at)
                    else (scheduled_at or (campaign.created_at.strftime("%Y-%m-%d %I:%M %p IST") if campaign.created_at else ""))
                ),
                "credits": call.credits_deducted if call else 0,
                "metadata_fields": ct.metadata_fields,
            }
            for ct, call in unique_results
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
        "analysis": sum(1 for c in contacts if c.status in ("completed", "failed", "busy", "no_answer", "incomplete")),
        "completed": sum(1 for c in contacts if c.status == "completed"),
        "failed":    sum(1 for c in contacts if c.status in ("failed", "busy", "no_answer", "incomplete")),
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

    # Count dynamically for full accuracy based on Calls
    calls_result = await db.execute(
        select(Call.status)
        .join(Contact, Call.contact_id == Contact.id)
        .where(Contact.campaign_id == campaign_id)
    )
    statuses = calls_result.scalars().all()
    completed_count = sum(1 for s in statuses if s == "completed")
    failed_count = sum(1 for s in statuses if s in ("failed", "incomplete"))

    return {
        "status": _map_status(campaign.status),
        "completed": completed_count,
        "failed": failed_count,
        "total": job.total_contacts if job else len(statuses),
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