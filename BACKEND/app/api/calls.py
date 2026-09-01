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

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os

from app.database import get_db
from app.models.call import Call
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.user_phone_number import UserPhoneNumber
from app.services.call_service import CallService
from app.services.livekit_event_service import LiveKitEventService
from livekit import api as lk_api

router = APIRouter()


@router.post("/livekit/webhook")
async def livekit_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    HTTP Webhook endpoint for LiveKit events.
    Handles participant_joined for SIP routing setup and room_finished for call completion.
    """
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    
    # Check signature if keys are configured
    auth_header = request.headers.get("Authorization", "")
    lk_key = os.getenv("LIVEKIT_API_KEY")
    lk_secret = os.getenv("LIVEKIT_API_SECRET")
    
    event_data = None
    if lk_key and lk_secret and auth_header:
        try:
            from livekit.api import WebhookReceiver, TokenVerifier
            verifier = TokenVerifier(lk_key, lk_secret)
            receiver = WebhookReceiver(verifier)
            event = receiver.receive(body_str, auth_header)
            event_data = {
                "event": event.event,
                "room": {"name": event.room.name if event.room else ""},
                "participant": {
                    "sid": event.participant.sid if event.participant else "",
                    "identity": event.participant.identity if event.participant else "",
                    "attributes": dict(event.participant.attributes) if event.participant and event.participant.attributes else {}
                } if event.participant else None
            }
        except Exception as sig_err:
            print(f"[webhook] Webhook signature verification failed: {sig_err}. Parsing as raw JSON.")
            
    if not event_data or not isinstance(event_data, dict):
        import json
        try:
            parsed = json.loads(body_str)
            event_data = parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            print(f"[webhook] Failed to parse webhook payload: {e}")
            return {"error": "Invalid JSON"}

    event_name = event_data.get("event")
    room_dict = event_data.get("room")
    room_name = room_dict.get("name") if isinstance(room_dict, dict) else None
    
    print(f"[webhook] Received event '{event_name}' for room '{room_name}'")
    
    if event_name == "participant_joined":
        part = event_data.get("participant")
        if isinstance(part, dict):
            attributes = part.get("attributes")
            attrs_dict = attributes if isinstance(attributes, dict) else {}
            caller_number = attrs_dict.get("sip.caller")
            called_number = attrs_dict.get("sip.called")
            participant_sid = part.get("sid")
            
            # If both are present, this is a SIP participant!
            if called_number and caller_number:
                # 1. Clean the called number digits
                clean_called = "".join(c for c in called_number if c.isdigit())
                
                # Check if we already have a Call record for this room (e.g. outbound call)
                result = await db.execute(
                    select(Call).where(Call.room_name == room_name)
                )
                existing_call = result.scalars().first()
                
                if existing_call:
                    existing_call.status = "in_progress"
                    if not existing_call.livekit_participant_id and participant_sid:
                        existing_call.livekit_participant_id = str(participant_sid)
                    await db.commit()
                    print(f"[webhook] Outbound call {existing_call.id} participant joined")
                else:
                    # Inbound call! Find phone line mapping
                    print(f"[webhook] Inbound SIP call matching called number: {called_number} (clean: {clean_called})")
                    
                    pn_stmt = select(UserPhoneNumber).where(UserPhoneNumber.is_active == True)
                    pn_res = await db.execute(pn_stmt)
                    all_lines = pn_res.scalars().all()
                    
                    matched_line = None
                    for line in all_lines:
                        line_clean = "".join(c for c in line.phone_number if c.isdigit())
                        if line_clean in clean_called or clean_called in line_clean:
                            matched_line = line
                            break
                            
                    if matched_line and matched_line.inbound_enabled:
                        tenant_id = matched_line.user_id
                        agent_id = matched_line.inbound_agent_id
                        
                        clean_caller = "".join(c for c in caller_number if c.isdigit())
                        contact_stmt = select(Contact)
                        contact_res = await db.execute(contact_stmt)
                        all_contacts = contact_res.scalars().all()
                        
                        matched_contact = None
                        for contact in all_contacts:
                            contact_clean = "".join(c for c in contact.phone if c.isdigit())
                            if contact_clean and (contact_clean in clean_caller or clean_caller in contact_clean):
                                matched_contact = contact
                                break
                        
                        new_call = Call(
                            direction="inbound",
                            caller_number=caller_number,
                            called_number=called_number,
                            phone=caller_number,
                            phone_line_id=matched_line.id,
                            tenant_id=tenant_id,
                            agent_id=agent_id,
                            room_name=room_name,
                            status="in_progress",
                            livekit_participant_id=participant_sid,
                            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                            contact_id=matched_contact.id if matched_contact else None,
                        )
                        db.add(new_call)
                        await db.commit()
                        print(f"[webhook] Created Inbound Call record {new_call.id} for tenant {tenant_id}, agent {agent_id}")
                    else:
                        print(f"[webhook] Rejected inbound call: Phone line not configured or inbound disabled")
                        try:
                            if room_name:
                                lkapi = lk_api.LiveKitAPI()
                                await lkapi.room.delete_room(lk_api.DeleteRoomRequest(room=str(room_name)))
                                await lkapi.aclose()
                        except Exception as e:
                            print(f"[webhook] Failed to reject room {room_name}: {e}")

    elif event_name == "room_finished" and room_name:
        await LiveKitEventService.room_finished(db, str(room_name))

    return {"status": "ok"}


class CallCompleteRequest(BaseModel):
    """All fields are optional so the endpoint works with an empty body too."""
    transcript: Optional[str] = None
    customer_name: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    recording_url: Optional[str] = None
    is_voicemail: bool = False
    detection_metadata: Optional[dict] = None
    duration: Optional[int] = None
    outcome: Optional[str] = None
    failure_reason: Optional[str] = None


class HumanResponseRequest(BaseModel):
    human_response: Optional[str] = None


class InboundInitRequest(BaseModel):
    room_name: str
    caller_number: str
    called_number: str


@router.post("/calls/inbound-init")
async def inbound_init(
    req: InboundInitRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint called by the AI Agent or webhook to pre-initialize an inbound Call record
    and associate it with a Contact.
    """
    room_name = req.room_name
    caller_number = req.caller_number
    called_number = req.called_number

    # 1. Check if Call already exists for this room
    result = await db.execute(
        select(Call).where(Call.room_name == room_name)
    )
    existing_call = result.scalars().first()
    if existing_call:
        print(f"[inbound-init] Call already exists for room {room_name}: {existing_call.id}")
        return {
            "success": True,
            "call_id": existing_call.id,
            "contact_id": existing_call.contact_id,
            "agent_id": existing_call.agent_id,
            "tenant_id": existing_call.tenant_id
        }

    # 2. Find phone line mapping
    clean_called = "".join(c for c in called_number if c.isdigit())
    pn_stmt = select(UserPhoneNumber).where(UserPhoneNumber.is_active == True)
    pn_res = await db.execute(pn_stmt)
    all_lines = pn_res.scalars().all()
    
    matched_line = None
    for line in all_lines:
        line_clean = "".join(c for c in line.phone_number if c.isdigit())
        if line_clean and (line_clean in clean_called or clean_called in line_clean):
            matched_line = line
            break

    if not matched_line:
        # Fallback to first phone line if none matched
        print(f"[inbound-init] No active phone line matched called number {called_number}")
        matched_line = all_lines[0] if all_lines else None

    if not matched_line:
        return {"success": False, "error": "No active phone lines found on the platform."}

    tenant_id = matched_line.user_id
    agent_id = matched_line.inbound_agent_id

    # 3. Find or create Contact
    clean_caller = "".join(c for c in caller_number if c.isdigit())
    contact_stmt = select(Contact).join(Campaign).where(Campaign.user_id == tenant_id)
    contact_res = await db.execute(contact_stmt)
    all_contacts = contact_res.scalars().all()
    
    matched_contact = None
    for contact in all_contacts:
        contact_clean = "".join(c for c in contact.phone if c.isdigit())
        if contact_clean and (contact_clean in clean_caller or clean_caller in contact_clean):
            matched_contact = contact
            break

    if not matched_contact:
        # Find any campaign for the tenant
        campaign_stmt = select(Campaign).where(Campaign.user_id == tenant_id)
        campaign_res = await db.execute(campaign_stmt)
        campaign = campaign_res.scalars().first()

        if not campaign:
            # Create a default campaign for inbound calls
            agent_name = "Sales Agent"
            if agent_id:
                from app.models.agent import Agent as AgentModel
                agent_obj = await db.get(AgentModel, agent_id)
                if agent_obj:
                    agent_name = agent_obj.name

            now_utc = datetime.now(timezone.utc)
            campaign = Campaign(
                user_id=tenant_id,
                campaign_name="Inbound Calls Campaign",
                agent=agent_name,
                script="Thank you for calling Morning Tax. How can I help you?",
                schedule_date=now_utc.strftime("%Y-%m-%d"),
                schedule_time=now_utc.strftime("%H:%M"),
                status="active",
            )
            db.add(campaign)
            await db.commit()
            await db.refresh(campaign)

        matched_contact = Contact(
            campaign_id=campaign.id,
            name="Inbound Caller",
            phone=caller_number,
            status="pending",
        )
        db.add(matched_contact)
        await db.commit()
        await db.refresh(matched_contact)

    # 4. Create Call record
    new_call = Call(
        direction="inbound",
        caller_number=caller_number,
        called_number=called_number,
        phone=caller_number,
        phone_line_id=matched_line.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        room_name=room_name,
        status="in_progress",
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        contact_id=matched_contact.id,
    )
    db.add(new_call)
    await db.commit()
    await db.refresh(new_call)

    print(f"[inbound-init] Created Inbound Call record {new_call.id} for tenant {tenant_id}, agent {agent_id}")
    return {
        "success": True,
        "call_id": new_call.id,
        "contact_id": new_call.contact_id,
        "agent_id": new_call.agent_id,
        "tenant_id": new_call.tenant_id
    }


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
        duration=body.duration if body else None,
        outcome=body.outcome if body else None,
        failure_reason=body.failure_reason if body else None,
    )

    if call is None:
        print(f"[API complete_call] Returning failure: Call {call_id} not found")
        return {"success": False, "message": "Call not found"}

    print(f"[API complete_call] Returning HTTP 200 success for Call {call_id}")
    return {"success": True, "call_id": call.id}

# ── POST /api/calls/{call_id}/active ───────────────────────────────────────

@router.post("/calls/{call_id}/active")
async def mark_call_active(
    call_id: int,
    db: AsyncSession = Depends(get_db),
):
    import os
    print("-" * 50)
    print("BACKEND API: POST /api/calls/{call_id}/active RECEIVED")
    print(f"Call ID: {call_id}")
    print("-" * 50)

    call = await db.get(Call, call_id)
    if not call:
        return {"error": "Call not found"}

    if not call.sip_was_active:
        call.sip_was_active = True
        call.answered_at = datetime.now(timezone.utc)
        await db.commit()
        print(f"Call {call_id} marked sip_was_active = True")

    return {"success": True, "call_id": call.id}

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
    from app.models.agent import Agent
    result = await db.execute(
        select(Call, Contact, Campaign, Agent)
        .outerjoin(Contact, Call.contact_id == Contact.id)
        .outerjoin(Campaign, Contact.campaign_id == Campaign.id)
        .outerjoin(Agent, Call.agent_id == Agent.id)
        .where(
            or_(
                Campaign.user_id == current_user.id,
                Call.tenant_id == current_user.id
            )
        )
        .where(or_(Campaign.campaign_name != "Website Demo Requests", Campaign.campaign_name.is_(None)))
        .order_by(Call.id.desc())
    )
    rows = result.all()

    calls = []
    for call, contact, campaign, agent in rows:
        is_active = call.status in ("dialing", "in_progress")

        response_display = contact.response or "—" if contact else "—"
        if is_active:
            response_display = "In Progress"
        elif contact and response_display == (contact.customer_name or ""):
            response_display = "Interested" if contact.appointment_date else "—"

        cat_upper = (call.category or "").upper()
        resp_lower = (contact.response or "").lower() if contact else ""
        summary_lower = (call.summary or "").lower()
        
        if is_active:
            sentiment = "Neutral"
        elif cat_upper == "COLD" or any(p in resp_lower or p in summary_lower for p in ["do not call", "refusal", "not interested", "no answer", "cut"]):
            sentiment = "Negative"
        elif cat_upper == "HOT" or any(p in resp_lower for p in ["appointment", "booked", "interested"]):
            sentiment = "Positive"
        else:
            sentiment = "Neutral"

        contact_name = contact.customer_name or contact.name if contact else (call.caller_number or "Inbound Caller")
        phone_number = contact.phone if contact else (call.caller_number or "")
        campaign_name = campaign.campaign_name if campaign else "Inbound Call"
        agent_name = agent.name if agent else (campaign.agent if campaign else "Sales Agent")

        calls.append({
            "id": str(call.id),
            "name": contact_name,
            "phone": phone_number,
            "status": call.status.capitalize(),  # "completed" → "Completed"
            "response": response_display,
            "datetime": _to_ist(call.started_at),  # BUG-001: IST timestamp
            "campaign": campaign_name,
            "campaign_id": campaign.id if campaign else None,
            "duration": _fmt_duration(call.duration or 0) if not is_active else "—",
            "transcript": _parse_transcript(call.transcript),
            "summary": call.summary or "",
            "category": call.category or "UNCATEGORIZED",
            "sentiment": sentiment,
            "human_response": call.human_response,
            "notes": f"Appointment: {contact.appointment_date or '—'} at {contact.appointment_time or '—'}" if contact else "—",
            "appointment_date": contact.appointment_date or "" if contact else "",
            "appointment_time": contact.appointment_time or "" if contact else "",
            "customer_name": contact_name,
            "recording_url": call.recording_url or "",
            "creditsDeducted": call.credits_deducted,
            "direction": call.direction or "outbound",
            "caller_number": call.caller_number or (contact.phone if contact else ""),
            "called_number": call.called_number or "",
            "agent_name": agent_name,
            "outcome": call.outcome or "",
            "failure_reason": call.failure_reason or "",
            "sip_was_active": call.sip_was_active,
        })
    return calls


@router.post("/calls/{call_id}/whatsapp-action")
async def trigger_call_whatsapp_action(
    call_id: int,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger in-call WhatsApp delivery (e.g. SEND_BROCHURE) requested by the AI during a live call.
    """
    action = payload.get("action", "SEND_BROCHURE")
    from app.services.whatsapp_automation_service import WhatsAppAutomationService
    result = await WhatsAppAutomationService.trigger_in_call_action(call_id, action)
    return result or {"success": True, "action": action}