from typing import Optional, List
from datetime import datetime, timezone, timedelta

# India Standard Time offset
IST = timezone(timedelta(hours=5, minutes=30))

def _to_ist(dt: Optional[datetime]) -> str:
    """Convert a naive-UTC datetime to IST string (YYYY-MM-DD HH:MM IST)."""
    if dt is None:
        return ""
    # Treat stored datetimes as UTC (they are naive but UTC)
    aware = dt.replace(tzinfo=timezone.utc)
    ist_dt = aware.astimezone(IST)
    return ist_dt.strftime("%Y-%m-%d %H:%M IST")

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.call import Call
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.services.call_service import CallService

router = APIRouter()


class CallCompleteRequest(BaseModel):
    """All fields are optional so the endpoint works with an empty body too."""
    transcript: Optional[str] = None
    customer_name: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    recording_url: Optional[str] = None
    is_voicemail: bool = False
    detection_metadata: Optional[dict] = None


class HumanResponseRequest(BaseModel):
    human_response: Optional[str] = None


@router.patch("/calls/{call_id}/human-response")
async def update_human_response(
    call_id: int,
    req: HumanResponseRequest,
    db: AsyncSession = Depends(get_db)
):
    call = await db.get(Call, call_id)
    if not call:
        return {"error": "Call not found"}
    
    val = req.human_response.strip() if req.human_response else None
    if val == "":
        val = None
        
    call.human_response = val
    await db.commit()
    return {"success": True, "human_response": call.human_response}


def _fmt_duration(seconds: int) -> str:
    """Convert integer seconds → 'MM:SS' string."""
    m, s = divmod(max(0, seconds), 60)
    return f"{m:02d}:{s:02d}"


def _parse_transcript(raw: Optional[str]) -> list:
    """
    Convert flat transcript string  →  [{speaker, text}] list the frontend expects.
    Format stored in DB:  "assistant: Hello\nuser: Hi there"
    """
    if not raw:
        return []
    lines = []
    for line in raw.strip().splitlines():
        if ": " in line:
            speaker, _, text = line.partition(": ")
            lines.append({"speaker": speaker.strip(), "text": text.strip()})
    return lines


# ── POST /api/calls/{call_id}/complete ─────────────────────────────────────

@router.post("/calls/{call_id}/complete")
async def complete_call(
    call_id: int,
    body: Optional[CallCompleteRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    import os
    print("-" * 50)
    print("BACKEND API: POST /api/calls/{call_id}/complete RECEIVED")
    print(f"PID: {os.getpid()}")
    print(f"Call ID: {call_id}")
    print(f"Body: {body.model_dump() if body else None}")
    print("-" * 50)

    call = await CallService.complete_call(
        db=db,
        call_id=call_id,
        transcript=body.transcript if body else None,
        customer_name=body.customer_name if body else None,
        appointment_date=body.appointment_date if body else None,
        appointment_time=body.appointment_time if body else None,
        recording_url=body.recording_url if body else None,
        is_voicemail=body.is_voicemail if body else False,
        detection_metadata=body.detection_metadata if body else None,
    )

    if call is None:
        print(f"[API complete_call] Returning failure: Call {call_id} not found")
        return {"success": False, "message": "Call not found"}

    print(f"[API complete_call] Returning HTTP 200 success for Call {call_id}")
    return {"success": True, "call_id": call.id}


# ── GET /api/calls ──────────────────────────────────────────────────────────

from sqlalchemy import select, or_
from app.models.user import User
from app.core.security import get_current_user

@router.get("/calls")
async def list_calls(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all calls for current user joined with their contact and campaign info.
    Used by the Responses page.
    """
    result = await db.execute(
        select(Call, Contact, Campaign)
        .join(Contact, Call.contact_id == Contact.id)
        .join(Campaign, Contact.campaign_id == Campaign.id)
        .where(Campaign.user_id == current_user.id)
        .order_by(Call.id.desc())
    )
    rows = result.all()

    calls = []
    for call, contact, campaign in rows:
        is_active = call.status in ("dialing", "in_progress")

        response_display = contact.response or "—"
        if is_active:
            response_display = "In Progress"
        elif response_display == (contact.customer_name or ""):
            response_display = "Interested" if contact.appointment_date else "—"

        cat_upper = (call.category or "").upper()
        resp_lower = (contact.response or "").lower()
        summary_lower = (call.summary or "").lower()
        
        if is_active:
            sentiment = "Neutral"
        elif cat_upper == "COLD" or any(p in resp_lower or p in summary_lower for p in ["do not call", "refusal", "not interested", "no answer", "cut"]):
            sentiment = "Negative"
        elif cat_upper == "HOT" or any(p in resp_lower for p in ["appointment", "booked", "interested"]):
            sentiment = "Positive"
        else:
            sentiment = "Neutral"

        status_display = call.status.capitalize()
        if is_active:
            status_display = "In Progress"
        elif call.status == "incomplete" or (contact.response and "voicemail" in contact.response.lower()):
            status_display = "Voicemail"
            response_display = "Voicemail"
        elif call.status == "failed" or (contact.response and "no answer" in contact.response.lower()):
            status_display = "Missed Call"
            if response_display in ("—", "", "Failed"):
                response_display = "No Answer"

        calls.append({
            "id": str(call.id),
            "name": contact.customer_name or contact.name,
            "phone": contact.phone,
            "status": status_display,
            "response": response_display,
            "datetime": _to_ist(call.started_at),  # BUG-001: IST timestamp
            "campaign": campaign.campaign_name,
            "duration": _fmt_duration(call.duration or 0) if not is_active else "—",
            "transcript": _parse_transcript(call.transcript),
            "summary": call.summary or "",
            "category": call.category or "UNCATEGORIZED",
            "sentiment": sentiment,
            "human_response": call.human_response,
            "notes": f"Appointment: {contact.appointment_date or '—'} at {contact.appointment_time or '—'}",
            "appointment_date": contact.appointment_date or "",
            "appointment_time": contact.appointment_time or "",
            "customer_name": contact.customer_name or contact.name,
            "recording_url": call.recording_url or "",
            "creditsDeducted": call.credits_deducted,
        })
    return calls


# ── WhatsApp Phase 1 Endpoints ─────────────────────────────────────────────

class WhatsAppActionRequest(BaseModel):
    action: str
    custom_payload: Optional[dict] = None


@router.post("/calls/{call_id}/whatsapp-action")
async def trigger_whatsapp_action(
    call_id: int,
    body: WhatsAppActionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger a structured WhatsApp action for a call with allowlist validation,
    idempotency protection, and call isolation.
    """
    from app.services.whatsapp_actions import WhatsAppActionService
    result = await WhatsAppActionService.execute_action(
        call_id=call_id,
        action=body.action,
        custom_payload=body.custom_payload,
    )
    return result


@router.get("/calls/{call_id}/whatsapp-actions")
async def get_call_whatsapp_actions(
    call_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all WhatsApp actions and delivery statuses executed for a call."""
    from app.models.whatsapp_action import WhatsAppAction
    res = await db.execute(
        select(WhatsAppAction)
        .where(WhatsAppAction.call_id == call_id)
        .order_by(WhatsAppAction.created_at.desc())
    )
    actions = res.scalars().all()
    return [
        {
            "id": a.id,
            "call_id": a.call_id,
            "contact_id": a.contact_id,
            "phone": a.phone,
            "action": a.action,
            "status": a.status,
            "payload": a.payload,
            "error": a.error,
            "created_at": _to_ist(a.created_at),
            "sent_at": _to_ist(a.sent_at) if a.sent_at else None,
        }
        for a in actions
    ]


@router.get("/calls/whatsapp-hub/conversations")
async def get_whatsapp_hub_conversations(
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve structured contacts and call context for the WhatsApp Hub UI.
    """
    from app.models.whatsapp_action import WhatsAppAction
    res = await db.execute(
        select(Call, Contact, Campaign)
        .join(Contact, Call.contact_id == Contact.id)
        .join(Campaign, Contact.campaign_id == Campaign.id)
        .order_by(Call.id.desc())
        .limit(30)
    )
    rows = res.all()

    # Also fetch all WhatsApp actions
    actions_res = await db.execute(select(WhatsAppAction).order_by(WhatsAppAction.created_at.desc()))
    all_actions = actions_res.scalars().all()
    actions_by_call = {}
    for a in all_actions:
        actions_by_call.setdefault(a.call_id, []).append({
            "action": a.action,
            "status": a.status,
            "sent_at": _to_ist(a.sent_at) if a.sent_at else _to_ist(a.created_at),
        })

    conversations = []
    seen_contacts = set()

    for call, contact, campaign in rows:
        if contact.id in seen_contacts:
            continue
        seen_contacts.add(contact.id)

        cat = (call.category or "UNCATEGORIZED").upper()
        lead_score = 92 if cat == "HOT" else (74 if cat == "WARM" else 45)

        # Parse messages
        parsed_transcript = _parse_transcript(call.transcript)
        messages = []
        for msg in parsed_transcript:
            is_agent = msg["speaker"].lower() in ("assistant", "agent")
            messages.append({
                "sender": "agent" if is_agent else "customer",
                "text": msg["text"],
                "time": _to_ist(call.started_at).split(" ")[1] if " " in _to_ist(call.started_at) else "Today",
                "is_ai": is_agent,
            })

        # Append sent WhatsApp actions as messages in timeline
        call_actions = actions_by_call.get(call.id, [])
        for act in call_actions:
            messages.append({
                "sender": "agent",
                "text": f"Shared {act['action'].replace('SEND_', '').title()} with customer via WhatsApp.",
                "time": act["sent_at"],
                "is_ai": True,
                "action_badge": act["action"],
                "status": act["status"],
            })

        last_msg = messages[-1]["text"] if messages else "Call completed."
        if len(last_msg) > 60:
            last_msg = last_msg[:57] + "..."

        conversations.append({
            "call_id": call.id,
            "contact_id": contact.id,
            "name": contact.customer_name or contact.name,
            "phone": contact.phone,
            "campaign_name": campaign.campaign_name,
            "status": call.status,
            "category": cat,
            "lead_score": lead_score,
            "last_message": last_msg,
            "datetime": _to_ist(call.started_at),
            "summary": call.summary or "Call completed.",
            "notes": contact.response or "Answered",
            "messages": messages,
            "whatsapp_actions": call_actions,
        })

    return conversations