from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.whatsapp_send_job import WhatsAppSendJob
from app.models.whatsapp_send_recipient import WhatsAppSendRecipient
from app.core.security import get_current_user

router = APIRouter()


# ── GET /api/whatsapp/history ───────────────────────────────────────────────

@router.get("/history")
async def list_whatsapp_history(
    source: Optional[str] = Query(None, description="Filter by source: campaign, excel, manual, automation"),
    status: Optional[str] = Query(None, description="Filter by status: completed, partial, failed"),
    search: Optional[str] = Query(None, description="Search by source name, message text, or trigger"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List WhatsApp Send Jobs history for the current user.
    Shows previous sending activity with stats, credits deducted, and delivery counts.
    """
    query = select(WhatsAppSendJob).where(
        or_(WhatsAppSendJob.user_id == current_user.id, WhatsAppSendJob.user_id.is_(None))
    )

    if source:
        src_lower = source.lower()
        if src_lower in ("campaign", "campaign_manual"):
            query = query.where(WhatsAppSendJob.source_type.in_(["campaign", "campaign_manual"]))
        elif src_lower in ("automation", "campaign_automation"):
            query = query.where(WhatsAppSendJob.source_type == "campaign_automation")
        elif src_lower in ("excel", "csv", "excel_csv"):
            query = query.where(WhatsAppSendJob.source_type == "excel_csv")
        elif src_lower == "manual":
            query = query.where(WhatsAppSendJob.source_type == "manual")

    if status:
        query = query.where(WhatsAppSendJob.status == status.lower())

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                WhatsAppSendJob.source_name.ilike(term),
                WhatsAppSendJob.message_text.ilike(term),
                WhatsAppSendJob.trigger_event.ilike(term),
                WhatsAppSendJob.content_type.ilike(term),
            )
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    count_res = await db.execute(count_query)
    total_count = count_res.scalar() or 0

    # Fetch paginated jobs
    query = query.order_by(desc(WhatsAppSendJob.created_at)).offset(offset).limit(limit)
    res = await db.execute(query)
    jobs = res.scalars().all()

    items = []
    for j in jobs:
        items.append({
            "id": j.id,
            "date": j.created_at.strftime("%d %b %Y, %I:%M %p") if j.created_at else "—",
            "created_at_raw": j.created_at.isoformat() if j.created_at else "",
            "source_type": j.source_type,
            "source_name": j.source_name,
            "campaign_id": j.campaign_id,
            "trigger_event": j.trigger_event,
            "content_type": j.content_type,
            "total_contacts": j.total_contacts,
            "sent_count": j.sent_count,
            "failed_count": j.failed_count,
            "credits_deducted": j.credits_deducted,
            "status": j.status.title() if j.status else "Completed",
            "message_preview": (j.message_text[:80] + "...") if (j.message_text and len(j.message_text) > 80) else (j.message_text or "—"),
            "attachments_count": len(j.attachments) if j.attachments else 0,
        })

    return {
        "success": True,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "jobs": items,
    }


# ── GET /api/whatsapp/history/{job_id} ──────────────────────────────────────

@router.get("/history/{job_id}")
async def get_whatsapp_history_detail(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get full details for a specific WhatsApp Send Job, including recipient-level statuses.
    """
    job = await db.get(WhatsAppSendJob, job_id)
    if not job or (job.user_id and job.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Send job history record not found.")

    # Fetch recipients
    recipients_res = await db.execute(
        select(WhatsAppSendRecipient).where(WhatsAppSendRecipient.send_job_id == job_id).order_by(WhatsAppSendRecipient.id.asc())
    )
    recipients = recipients_res.scalars().all()

    recipient_items = []
    for r in recipients:
        recipient_items.append({
            "id": r.id,
            "name": r.name,
            "phone": r.phone,
            "status": r.status.title() if r.status else "Sent",
            "error_message": r.error_message,
            "sent_at": r.sent_at.strftime("%d %b %Y, %I:%M %p") if r.sent_at else "—",
            "details": r.details,
        })

    return {
        "success": True,
        "job": {
            "id": job.id,
            "source_type": job.source_type,
            "source_name": job.source_name,
            "campaign_id": job.campaign_id,
            "trigger_event": job.trigger_event,
            "date": job.created_at.strftime("%d %b %Y, %I:%M %p") if job.created_at else "—",
            "completed_at": job.completed_at.strftime("%d %b %Y, %I:%M %p") if job.completed_at else "—",
            "content_type": job.content_type,
            "message_text": job.message_text or "",
            "attachments": job.attachments or [],
            "total_contacts": job.total_contacts,
            "sent_count": job.sent_count,
            "failed_count": job.failed_count,
            "credits_deducted": job.credits_deducted,
            "status": job.status.title() if job.status else "Completed",
            "recipients": recipient_items,
        },
    }
