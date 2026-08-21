from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime

from app.database import get_db
from app.schemas.email_campaign import EmailCampaignCreate
from app.services.email_campaign_service import EmailCampaignService
from app.models.email_campaign import EmailCampaign
from app.models.email_contact import EmailContact
from app.models.user import User
from app.core.security import get_current_user

router = APIRouter()


# ── POST /api/email-campaigns ──────────────────────────────────────────────

@router.post("/email-campaigns")
async def create_email_campaign(
    data: EmailCampaignCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new email campaign with a list of contacts."""
    if not data.contacts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one contact is required.",
        )

    campaign = await EmailCampaignService.create_campaign(
        db=db, data=data, user_id=current_user.id
    )
    return {
        "message": "Email campaign created successfully",
        "campaign_id": campaign.id,
    }


# ── POST /api/email-campaigns/{campaign_id}/launch ─────────────────────────

@router.post("/email-campaigns/{campaign_id}/launch")
async def launch_email_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Launch an email campaign — sends emails to all contacts asynchronously."""
    try:
        campaign = await EmailCampaignService.launch_campaign(db=db, campaign_id=campaign_id)
        return {
            "message": "Email campaign launched successfully",
            "campaign_id": campaign.id,
            "status": campaign.status,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ── GET /api/email-campaigns ───────────────────────────────────────────────

@router.get("/email-campaigns")
async def list_email_campaigns(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all email campaigns for the current user with aggregated stats."""
    result = await db.execute(
        select(EmailCampaign)
        .where(EmailCampaign.user_id == current_user.id)
        .order_by(EmailCampaign.id.desc())
    )
    campaigns = result.scalars().all()

    out = []
    for c in campaigns:
        total_result = await db.execute(
            select(func.count(EmailContact.id)).where(
                EmailContact.email_campaign_id == c.id
            )
        )
        total = total_result.scalar() or 0

        sent_result = await db.execute(
            select(func.count(EmailContact.id)).where(
                EmailContact.email_campaign_id == c.id,
                EmailContact.status == "sent",
            )
        )
        sent = sent_result.scalar() or 0

        failed_result = await db.execute(
            select(func.count(EmailContact.id)).where(
                EmailContact.email_campaign_id == c.id,
                EmailContact.status == "failed",
            )
        )
        failed = failed_result.scalar() or 0

        out.append({
            "id": c.id,
            "name": c.name,
            "subject": c.subject,
            "from_name": c.from_name or "",
            "status": c.status,
            "schedule_date": c.schedule_date or "",
            "schedule_time": c.schedule_time or "",
            "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
            "total": total,
            "sent": sent,
            "failed": failed,
            "pending": total - sent - failed,
        })
    return out


# ── GET /api/email-campaigns/{campaign_id} ─────────────────────────────────

@router.get("/email-campaigns/{campaign_id}")
async def get_email_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get full details of an email campaign including per-contact status."""
    campaign = await db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Email campaign not found")

    result = await db.execute(
        select(EmailContact).where(EmailContact.email_campaign_id == campaign_id)
    )
    contacts = result.scalars().all()

    total = len(contacts)
    sent = sum(1 for c in contacts if c.status == "sent")
    failed = sum(1 for c in contacts if c.status == "failed")
    pending = sum(1 for c in contacts if c.status == "pending")

    return {
        "id": campaign.id,
        "name": campaign.name,
        "subject": campaign.subject,
        "html_body": campaign.html_body,
        "from_name": campaign.from_name or "",
        "reply_to": campaign.reply_to or "",
        "status": campaign.status,
        "schedule_date": campaign.schedule_date or "",
        "schedule_time": campaign.schedule_time or "",
        "created_at": campaign.created_at.isoformat() if campaign.created_at else "",
        "stats": {
            "total": total,
            "sent": sent,
            "failed": failed,
            "pending": pending,
        },
        "contacts": [
            {
                "id": c.id,
                "name": c.name,
                "email": c.email,
                "status": c.status,
                "sent_at": c.sent_at.isoformat() if c.sent_at else None,
                "error_message": c.error_message or "",
            }
            for c in contacts
        ],
    }


# ── GET /api/email-campaigns/{campaign_id}/status ──────────────────────────

@router.get("/email-campaigns/{campaign_id}/status")
async def get_email_campaign_status(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Lightweight poll endpoint for live status updates."""
    campaign = await db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Not found")

    result = await db.execute(
        select(EmailContact.status).where(
            EmailContact.email_campaign_id == campaign_id
        )
    )
    statuses = result.scalars().all()

    total = len(statuses)
    sent = sum(1 for s in statuses if s == "sent")
    failed = sum(1 for s in statuses if s == "failed")

    return {
        "status": campaign.status,
        "total": total,
        "sent": sent,
        "failed": failed,
        "pending": total - sent - failed,
    }


# ── DELETE /api/email-campaigns/{campaign_id} ──────────────────────────────

@router.delete("/email-campaigns/{campaign_id}")
async def delete_email_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an email campaign and all its contacts."""
    campaign = await db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Email campaign not found")
    if campaign.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    await db.delete(campaign)
    await db.commit()
    return {"message": "Email campaign deleted"}
