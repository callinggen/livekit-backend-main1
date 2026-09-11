import os
import re
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc

from app.database import get_db
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.call import Call
from app.models.user import User
from app.models.whatsapp_material import WhatsAppMaterial
from app.models.whatsapp_send_job import WhatsAppSendJob
from app.models.whatsapp_send_recipient import WhatsAppSendRecipient
from app.services.whatsapp_credit_service import WhatsAppCreditService
from app.core.security import get_optional_current_user
from whatsapp import service as evolution_service
from whatsapp.config import resolve_instance_name, EVOLUTION_INSTANCE_NAME

router = APIRouter()


def normalize_whatsapp_phone(raw_phone: str) -> str:
    """Normalize phone number to international WhatsApp format (e.g. 917656807447)."""
    clean = "".join(c for c in str(raw_phone or "") if c.isdigit())
    if len(clean) == 10:
        clean = "91" + clean
    return clean


def extract_error_detail(err: Exception) -> str:
    """Extract clear human-readable error from Evolution API responses."""
    if hasattr(err, "response") and getattr(err, "response", None) is not None:
        try:
            raw_text = err.response.text
            if "exists': false" in raw_text.lower() or 'exists": false' in raw_text.lower() or '"exists":false' in raw_text.lower():
                return "Phone number is not registered on WhatsApp"
            data = err.response.json()
            if isinstance(data, dict):
                resp_inner = data.get("response", {}) if isinstance(data, dict) else {}
                msg = (resp_inner.get("message") if isinstance(resp_inner, dict) else None) or data.get("message") or data.get("error")
                if isinstance(msg, list) and len(msg) > 0:
                    return str(msg[0])
                if msg:
                    return str(msg)
            return raw_text[:200]
        except Exception:
            pass
    return str(err)



class RecipientItem(BaseModel):
    name: Optional[str] = "there"
    phone: str
    contact_id: Optional[int] = None


class MessageContentItem(BaseModel):
    type: str  # "text", "image", "document"
    text: Optional[str] = None
    media_url: Optional[str] = None
    mime_type: Optional[str] = None
    file_name: Optional[str] = None
    caption: Optional[str] = None
    title: Optional[str] = None
    save_to_material: Optional[bool] = False


class SendBulkRequest(BaseModel):
    recipients: List[RecipientItem]
    items: List[MessageContentItem]
    instance_name: Optional[str] = None
    source_type: Optional[str] = "manual"  # "campaign_manual", "excel_csv", "manual"
    source_name: Optional[str] = None
    campaign_id: Optional[int] = None
    scheduled_for: Optional[str] = None  # ISO timestamp string for scheduling


# ── GET /api/whatsapp/campaign-contacts-filtered ───────────────────────────

@router.get("/campaign-contacts-filtered")
async def get_campaign_contacts_filtered(
    campaign_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_current_user),
):
    """
    Fetch contacts for a campaign with rich call logs and outcome data
    for filtering in the Send Message page.
    """
    campaign = await db.get(Campaign, campaign_id)
    if not campaign or (campaign.user_id and campaign.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Campaign not found")

    contacts_query = select(Contact, Call).outerjoin(Call, Contact.id == Call.contact_id).where(Contact.campaign_id == campaign_id)
    result = await db.execute(contacts_query)
    rows = result.all()

    seen_contacts = set()
    contacts_list = []

    for contact, call in rows:
        if contact.id in seen_contacts:
            continue
        seen_contacts.add(contact.id)

        # Determine Call Type
        call_type = "Outbound"

        # Determine AI Classification (HOT, WARM, COLD, etc.)
        cat = (call.category if call else "UNCATEGORIZED") or "UNCATEGORIZED"
        cat_upper = cat.upper()
        if cat_upper == "HOT":
            ai_classification = "Hot Lead"
        elif cat_upper == "WARM":
            ai_classification = "Warm Lead"
        elif cat_upper == "COLD":
            ai_classification = "Cold Lead"
        elif contact.appointment_date:
            ai_classification = "Appointment"
        elif contact.response and "callback" in contact.response.lower():
            ai_classification = "Callback"
        elif contact.response and "interested" in contact.response.lower():
            ai_classification = "Interested"
        else:
            ai_classification = "Other"

        # Determine Response
        resp_raw = (contact.response or "").strip()
        if not resp_raw or resp_raw == "—":
            if call and call.status == "completed":
                response_label = "Answered"
            elif call and call.status in ("failed", "incomplete"):
                response_label = "Not Answered"
            else:
                response_label = "Not Answered"
        elif "appointment" in resp_raw.lower():
            response_label = "Appointment Booked"
        elif "callback" in resp_raw.lower() or "rescheduled" in resp_raw.lower():
            response_label = "Callback"
        elif "not interested" in resp_raw.lower() or "declined" in resp_raw.lower() or "refusal" in resp_raw.lower():
            response_label = "Declined"
        elif "cut" in resp_raw.lower() or "disconnected" in resp_raw.lower():
            response_label = "Cut/Disconnected"
        elif "answered" in resp_raw.lower() or "completed" in resp_raw.lower():
            response_label = "Answered"
        else:
            response_label = resp_raw

        # Determine Status
        status_label = "Completed" if contact.status == "completed" else ("Failed" if contact.status in ("failed", "incomplete") else "In Progress")

        # Check phone validity (minimum 10 digits)
        clean_phone = "".join(c for c in contact.phone if c.isdigit())
        is_valid_phone = len(clean_phone) >= 10

        contacts_list.append({
            "id": contact.id,
            "name": contact.customer_name or contact.name or "Contact",
            "phone": contact.phone,
            "formatted_phone": normalize_whatsapp_phone(contact.phone),
            "is_valid_phone": is_valid_phone,
            "call_type": call_type,
            "ai_classification": ai_classification,
            "response": response_label,
            "status": status_label,
            "appointment_date": contact.appointment_date or "",
            "appointment_time": contact.appointment_time or "",
            "duration": call.duration if call else 0,
        })

    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.campaign_name,
        "total_contacts": len(contacts_list),
        "contacts": contacts_list,
    }


# ── POST /api/whatsapp/send-bulk ───────────────────────────────────────────

@router.post("/send-bulk")
async def send_bulk_whatsapp(
    req: SendBulkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_current_user),
):
    """
    Execute controlled bulk WhatsApp sending to selected contacts.
    Deducts exactly 1 credit per text/image/document message item per recipient.
    Creates a full Send Job with recipient-level tracking for history.
    """
    if not req.recipients:
        raise HTTPException(status_code=400, detail="No recipients provided.")
    if not req.items:
        raise HTTPException(status_code=400, detail="No message content or attachments provided.")

    inst = resolve_instance_name(req.instance_name, user_id=current_user.id)
    backend_base_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

    # Filter recipients with valid phone numbers
    valid_recipients = []
    for r in req.recipients:
        clean_num = normalize_whatsapp_phone(r.phone)
        if len(clean_num) >= 10:
            valid_recipients.append({
                "name": (r.name or "there").strip(),
                "phone": clean_num,
                "contact_id": r.contact_id,
            })

    if not valid_recipients:
        raise HTTPException(status_code=400, detail="None of the selected recipients have a valid phone number.")

    # Convert items to dict for credit service
    items_dicts = [item.dict() for item in req.items]

    # Calculate total required credits: (Text=1, Image=2, Document=3) per recipient
    total_required_credits = WhatsAppCreditService.calculate_total_credits(items_dicts, len(valid_recipients))

    # Authoritative credit check
    await WhatsAppCreditService.verify_and_reserve_credits(db, current_user, total_required_credits)

    # Save to Material Base if requested
    for item in req.items:
        if item.type == "text" and item.save_to_material and item.text:
            try:
                title = item.title or f"Template {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                mat = WhatsAppMaterial(
                    user_id=current_user.id,
                    title=title.strip(),
                    type="text",
                    content=item.text.strip(),
                )
                db.add(mat)
                await db.commit()
            except Exception as mat_err:
                print(f"[SendBulk] Notice: Could not save material template: {mat_err}")

    # Determine Content Type & Extract Main Message Text
    text_items = [i for i in req.items if i.type == "text" and i.text]
    media_items = [i for i in req.items if i.type in ("image", "document")]

    main_text = text_items[0].text if text_items else (media_items[0].caption if media_items and media_items[0].caption else None)

    if text_items and media_items:
        content_type_str = "Mixed"
    elif media_items:
        types_set = set(m.type.title() for m in media_items)
        content_type_str = " & ".join(sorted(types_set))
    else:
        content_type_str = "Text"

    # Determine source name
    source_name = req.source_name
    if not source_name:
        if req.source_type in ("campaign", "campaign_manual") and req.campaign_id:
            camp = await db.get(Campaign, req.campaign_id)
            source_name = camp.campaign_name if camp else "Campaign Send"
        elif req.source_type == "excel_csv":
            source_name = "Contact File Upload"
        else:
            source_name = "Manual Send"

    # Build attachments metadata
    attachments_meta = []
    for m in media_items:
        attachments_meta.append({
            "title": m.title or m.file_name or "Attachment",
            "type": m.type,
            "url": m.media_url,
            "file_name": m.file_name,
            "mime_type": m.mime_type,
        })

    # Parse scheduled_for timestamp if provided
    scheduled_dt = None
    if req.scheduled_for and req.scheduled_for.strip():
        try:
            cleaned_iso = req.scheduled_for.replace("Z", "+00:00")
            parsed_dt = datetime.fromisoformat(cleaned_iso)
            # Ensure future time
            if parsed_dt:
                scheduled_dt = parsed_dt
        except Exception as dt_err:
            print(f"[SendBulk] Error parsing scheduled_for: {dt_err}")

    # If scheduled for future, create scheduled job and recipients without sending now
    if scheduled_dt:
        send_job = WhatsAppSendJob(
            user_id=current_user.id,
            source_type=req.source_type or "manual",
            source_name=source_name,
            campaign_id=req.campaign_id,
            content_type=content_type_str,
            message_text=main_text,
            attachments=attachments_meta,
            total_contacts=len(valid_recipients),
            sent_count=0,
            failed_count=0,
            credits_deducted=0,
            status="scheduled",
            scheduled_for=scheduled_dt,
        )
        db.add(send_job)
        await db.flush()

        for rec in valid_recipients:
            rec_log = WhatsAppSendRecipient(
                send_job_id=send_job.id,
                contact_id=rec.get("contact_id"),
                name=rec["name"],
                phone=rec["phone"],
                status="scheduled",
                error_message=None,
                details={"items": [{"type": item.type, "status": "scheduled"} for item in req.items]},
                sent_at=None,
            )
            db.add(rec_log)

        await db.commit()

        return {
            "success": True,
            "status": "scheduled",
            "job_id": send_job.id,
            "scheduled_for": scheduled_dt.isoformat(),
            "total_recipients": len(valid_recipients),
            "total_credits_deducted": 0,
            "message": f"Broadcast successfully scheduled for {scheduled_dt.strftime('%d %b %Y, %I:%M %p')}.",
        }

    # Create Immediate Send Job record
    send_job = WhatsAppSendJob(
        user_id=current_user.id,
        source_type=req.source_type or "manual",
        source_name=source_name,
        campaign_id=req.campaign_id,
        content_type=content_type_str,
        message_text=main_text,
        attachments=attachments_meta,
        total_contacts=len(valid_recipients),
        sent_count=0,
        failed_count=0,
        credits_deducted=0,
        status="in_progress",
    )
    db.add(send_job)
    await db.flush()

    # Track results
    total_sent = 0
    total_failed = 0
    total_credits_deducted = 0
    recipient_results = []

    for rec in valid_recipients:
        rec_name = rec["name"]
        rec_phone = rec["phone"]
        rec_item_statuses = []
        rec_has_error = False
        last_rec_error = None

        for item in req.items:
            # ── 1. Text Message ──────────────────────────────────────────────
            if item.type == "text":
                raw_text = item.text or ""
                # Replace placeholders
                personalized_text = raw_text.replace("{{name}}", rec_name).replace("{{customer_name}}", rec_name)
                
                try:
                    res = await evolution_service.send_text_message(
                        instance_name=inst,
                        number=rec_phone,
                        text=personalized_text,
                    )
                    # Successful send -> Deduct 1 credit
                    if current_user.credits >= WhatsAppCreditService.CREDIT_PER_TEXT:
                        current_user.credits -= WhatsAppCreditService.CREDIT_PER_TEXT
                        total_credits_deducted += WhatsAppCreditService.CREDIT_PER_TEXT
                        total_sent += 1
                        rec_item_statuses.append({"type": "text", "status": "sent", "response": res})
                    else:
                        rec_item_statuses.append({"type": "text", "status": "failed", "error": "Credit exhausted during send"})
                        total_failed += 1
                        rec_has_error = True
                except Exception as send_err:
                    err_clean = extract_error_detail(send_err)
                    print(f"[SendBulk] Text send error for {rec_phone}: {err_clean}")
                    rec_item_statuses.append({"type": "text", "status": "failed", "error": err_clean})
                    total_failed += 1
                    rec_has_error = True
                    last_rec_error = err_clean

            # ── 2. Image or Document Message ─────────────────────────────────
            elif item.type in ("image", "document"):
                media_url = item.media_url or ""
                # Ensure fully qualified URL for Evolution API
                if media_url.startswith("/"):
                    full_media_url = f"{backend_base_url}{media_url}"
                else:
                    full_media_url = media_url

                caption = item.caption
                if caption:
                    caption = caption.replace("{{name}}", rec_name).replace("{{customer_name}}", rec_name)

                item_cost = WhatsAppCreditService.calculate_item_credits(item.type)
                try:
                    res = await evolution_service.send_media_message(
                        instance_name=inst,
                        number=rec_phone,
                        media_url=full_media_url,
                        media_type=item.type,
                        mimetype=item.mime_type or ("image/png" if item.type == "image" else "application/pdf"),
                        caption=caption,
                        file_name=item.file_name or ("image.png" if item.type == "image" else "document.pdf"),
                    )
                    # Successful send -> Deduct exact credits (1 credit per image/document)
                    if current_user.credits >= item_cost:
                        current_user.credits -= item_cost
                        total_credits_deducted += item_cost
                        total_sent += 1
                        rec_item_statuses.append({"type": item.type, "status": "sent", "response": res})
                    else:
                        rec_item_statuses.append({"type": item.type, "status": "failed", "error": "Credit exhausted during send"})
                        total_failed += 1
                        rec_has_error = True
                except Exception as media_err:
                    err_clean = extract_error_detail(media_err)
                    print(f"[SendBulk] Media send error for {rec_phone}: {err_clean}")
                    rec_item_statuses.append({"type": item.type, "status": "failed", "error": err_clean})
                    total_failed += 1
                    rec_has_error = True
                    last_rec_error = err_clean

        # Create recipient log linked to send job
        rec_status = "sent" if not rec_has_error else ("partial" if any(i.get("status") == "sent" for i in rec_item_statuses) else "failed")
        rec_log = WhatsAppSendRecipient(
            send_job_id=send_job.id,
            contact_id=rec.get("contact_id"),
            name=rec_name,
            phone=rec_phone,
            status=rec_status,
            error_message=last_rec_error,
            details={"items": rec_item_statuses},
            sent_at=datetime.now(timezone.utc),
        )
        db.add(rec_log)

        recipient_results.append({
            "name": rec_name,
            "phone": rec_phone,
            "status": rec_status,
            "items": rec_item_statuses,
        })

    # Update Send Job summary
    send_job.sent_count = total_sent
    send_job.failed_count = total_failed
    send_job.credits_deducted = total_credits_deducted
    send_job.status = "completed" if total_failed == 0 else ("partial" if total_sent > 0 else "failed")
    send_job.completed_at = datetime.now(timezone.utc)

    # Save credit balance and job update to DB
    await db.commit()

    return {
        "success": True,
        "message": f"WhatsApp messages processed. Sent: {total_sent}, Failed: {total_failed}, Credits Deducted: {total_credits_deducted}",
        "job_id": send_job.id,
        "total_recipients": len(valid_recipients),
        "total_messages_sent": total_sent,
        "total_failed": total_failed,
        "total_credits_deducted": total_credits_deducted,
        "remaining_credits": current_user.credits,
        "details": recipient_results,
    }


# ── POST /api/whatsapp/import-google-sheet ──────────────────────────────────

class GoogleSheetImportRequest(BaseModel):
    sheet_url: str


@router.post("/import-google-sheet")
async def import_google_sheet(req: GoogleSheetImportRequest):
    """
    Fetch and parse contacts from a public or shared Google Sheets URL.
    Converts Google Sheet link into a direct CSV export and parses rows into name and phone fields.
    """
    url = req.sheet_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Please provide a valid Google Sheet URL.")

    # Extract Sheet ID
    sheet_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not sheet_match:
        raise HTTPException(
            status_code=400,
            detail="Invalid Google Sheet URL. Format should be: https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit",
        )

    sheet_id = sheet_match.group(1)
    gid_match = re.search(r"[#&]gid=([0-9]+)", url)
    gid = gid_match.group(1) if gid_match else "0"

    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(export_url)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail="Could not access Google Sheet. Please make sure the sheet is shared with 'Anyone with the link can view'.",
                )

            csv_text = resp.text
            # Check if Google returned an HTML login page instead of CSV
            if "<html" in csv_text.lower() or "<!doctype html" in csv_text.lower():
                raise HTTPException(
                    status_code=400,
                    detail="Google Sheet requires Google Login. Please set sharing settings to 'Anyone with the link can view'.",
                )

            import io
            import csv

            csv_reader = csv.DictReader(io.StringIO(csv_text))
            rows = []
            for row in csv_reader:
                rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k is not None})

            return {
                "success": True,
                "total": len(rows),
                "rows": rows,
                "sheet_id": sheet_id,
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[GoogleSheetImport] Error: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to fetch Google Sheet: {str(e)}")
