import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.call import Call
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.whatsapp_action import WhatsAppAction
from app.models.user import User
from whatsapp import service as evolution_service
from whatsapp.config import EVOLUTION_INSTANCE_NAME

# Strict allowlist of permitted WhatsApp actions
ALLOWED_WHATSAPP_ACTIONS = {
    "SEND_BROCHURE",
    "SEND_PRICING",
    "SEND_CATALOGUE",
    "SEND_WEBSITE",
    "SEND_BOOKING_LINK",
    "SEND_CONTACT_DETAILS",
    "SEND_CALLBACK_CONFIRMATION",
    "SEND_MISSED_CALL",
    "SEND_CALLBACK",
    "SEND_VOICEMAIL",
}

# Configurable business assets with clean fallbacks
DEFAULT_ASSET_CONFIG = {
    "website_url": os.getenv("WHATSAPP_WEBSITE_URL", "https://callinggen.ai"),
    "booking_url": os.getenv("WHATSAPP_BOOKING_URL", "https://callinggen.ai/book-demo"),
    "brochure_url": os.getenv("WHATSAPP_BROCHURE_URL", "https://callinggen.ai/assets/brochure.pdf"),
    "pricing_url": os.getenv("WHATSAPP_PRICING_URL", "https://callinggen.ai/assets/pricing.pdf"),
    "catalogue_url": os.getenv("WHATSAPP_CATALOGUE_URL", "https://callinggen.ai/assets/catalogue.pdf"),
    "company_phone": os.getenv("SIP_CALL_FROM", "+917971442271"),
    "company_email": os.getenv("SMTP_FROM", "contact@callinggen.ai"),
}


def format_whatsapp_number(number: str) -> str:
    """Normalize phone number to international WhatsApp format (e.g. 917656807447)."""
    clean = "".join(c for c in str(number or "") if c.isdigit())
    if len(clean) == 10:
        clean = "91" + clean
    return clean


def mask_phone(phone: str) -> str:
    """Mask phone number for safe structured logging."""
    if not phone or len(phone) < 6:
        return "***"
    return phone[:3] + "X" * (len(phone) - 5) + phone[-2:]


class WhatsAppActionService:
    """
    Dedicated service for executing validated, idempotent WhatsApp follow-ups
    and in-call actions through Evolution API.
    """

    @staticmethod
    async def execute_action(
        call_id: int,
        action: str,
        contact_id: Optional[int] = None,
        campaign_id: Optional[int] = None,
        phone: Optional[str] = None,
        custom_payload: Optional[Dict[str, Any]] = None,
        instance_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate, deduplicate, and execute a structured WhatsApp action for a specific call.
        Guarantees strict call isolation and idempotency.
        """
        action_upper = (action or "").strip().upper()
        inst = instance_name or EVOLUTION_INSTANCE_NAME or "callinggen"

        print(f"[WHATSAPP_ACTION_REQUESTED] call_id={call_id} action={action_upper} phone={mask_phone(phone or '')}")

        # ── 1. Action Allowlist Validation ──────────────────────────────────
        if action_upper not in ALLOWED_WHATSAPP_ACTIONS:
            error_msg = f"Action '{action}' is not in the allowed action registry."
            print(f"[WHATSAPP_ACTION_REJECTED] call_id={call_id} reason='{error_msg}'")
            return {
                "success": False,
                "status": "rejected",
                "action": action_upper,
                "error": error_msg,
            }

        print(f"[WHATSAPP_ACTION_VALIDATED] call_id={call_id} action={action_upper}")

        async with AsyncSessionLocal() as db:
            # ── 2. Resolve Context Explicitly from DB ───────────────────────
            call = await db.get(Call, call_id)
            if not call:
                print(f"[WHATSAPP_ACTION_FAILED] call_id={call_id} error='Call not found'")
                return {"success": False, "status": "failed", "error": f"Call {call_id} not found."}

            resolved_contact_id = contact_id or call.contact_id
            contact = await db.get(Contact, resolved_contact_id) if resolved_contact_id else None

            resolved_campaign_id = campaign_id or call.campaign_id
            campaign = await db.get(Campaign, resolved_campaign_id) if resolved_campaign_id else None

            raw_phone = phone or (contact.phone if contact else call.phone)
            resolved_phone = format_whatsapp_number(raw_phone)
            customer_name = (contact.customer_name if (contact and contact.customer_name) else (contact.name if contact else "there")) or "there"
            campaign_name = campaign.campaign_name if campaign else "Morning Tax Consultation"

            if not resolved_phone:
                print(f"[WHATSAPP_ACTION_FAILED] call_id={call_id} error='No destination phone number'")
                return {"success": False, "status": "failed", "error": "No phone number available for WhatsApp action."}

            # ── 3. Idempotency Check (Prevent duplicate sends) ──────────────
            existing_action_query = select(WhatsAppAction).where(
                WhatsAppAction.call_id == call_id,
                WhatsAppAction.action == action_upper,
                WhatsAppAction.status.in_(["pending", "sent"]),
            )
            existing_res = await db.execute(existing_action_query)
            already_sent = existing_res.scalars().first()

            if already_sent:
                print(
                    f"[WHATSAPP_ACTION_SKIPPED_DUPLICATE] call_id={call_id} contact_id={resolved_contact_id} "
                    f"action={action_upper} phone={mask_phone(resolved_phone)} (already {already_sent.status} at {already_sent.created_at})"
                )
                return {
                    "success": True,
                    "status": "skipped_duplicate",
                    "action": action_upper,
                    "message": f"Action already {already_sent.status} for this call.",
                }

            # ── 4. Create Pending WhatsAppAction Record ─────────────────────
            action_record = WhatsAppAction(
                call_id=call_id,
                contact_id=resolved_contact_id,
                campaign_id=resolved_campaign_id,
                phone=resolved_phone,
                action=action_upper,
                status="pending",
                payload=custom_payload or {},
            )
            db.add(action_record)
            await db.commit()
            await db.refresh(action_record)

            # ── 5. Build Structured Message / Media Payload ─────────────────
            message_text, media_payload = WhatsAppActionService._build_action_payload(
                action=action_upper,
                customer_name=customer_name,
                campaign_name=campaign_name,
                custom_payload=custom_payload,
            )

            # ── 6. Execute via Evolution API ────────────────────────────────
            print(
                f"[WHATSAPP_SEND_STARTED] call_id={call_id} contact_id={resolved_contact_id} "
                f"action={action_upper} phone={mask_phone(resolved_phone)}"
            )

            try:
                api_res = None
                if media_payload:
                    try:
                        api_res = await evolution_service.send_media_message(
                            instance_name=inst,
                            number=resolved_phone,
                            media_url=media_payload["media_url"],
                            media_type=media_payload.get("media_type", "document"),
                            mimetype=media_payload.get("mimetype", "application/pdf"),
                            caption=message_text,
                            file_name=media_payload.get("file_name", "document.pdf"),
                        )
                    except Exception as media_err:
                        print(f"[WhatsAppService] Media send failed ({media_err}), falling back to text delivery...")
                        api_res = await evolution_service.send_text_message(
                            instance_name=inst,
                            number=resolved_phone,
                            text=message_text,
                        )
                else:
                    api_res = await evolution_service.send_text_message(
                        instance_name=inst,
                        number=resolved_phone,
                        text=message_text,
                    )

                action_record.status = "sent"
                action_record.response = api_res
                action_record.sent_at = datetime.now(timezone.utc).replace(tzinfo=None)

                # ── Deduct 1 WhatsApp Credit upon successful delivery ───────
                try:
                    owner_user_id = campaign.user_id if campaign else None
                    if not owner_user_id and call.job_id:
                        from app.models.job import Job
                        job = await db.get(Job, call.job_id)
                        if job and job.campaign_id:
                            cmp = await db.get(Campaign, job.campaign_id)
                            if cmp:
                                owner_user_id = cmp.user_id

                    if owner_user_id:
                        credit_user = await db.get(User, owner_user_id)
                        if credit_user and credit_user.credits > 0:
                            credit_user.credits -= 1
                            print(f"[WhatsAppService] Deducted 1 credit for user {credit_user.id} ({credit_user.email}). Remaining credits: {credit_user.credits}")
                            from app.services.notification_service import notification_service
                            try:
                                await notification_service.check_and_trigger_credit_notifications(db, credit_user)
                            except Exception as notif_err:
                                print(f"[WhatsAppService] Non-fatal notification check error: {notif_err}")
                except Exception as credit_err:
                    print(f"[WhatsAppService] Non-fatal credit deduction error: {credit_err}")

                await db.commit()

                print(
                    f"[WHATSAPP_SEND_SUCCESS] call_id={call_id} contact_id={resolved_contact_id} "
                    f"action={action_upper} phone={mask_phone(resolved_phone)}"
                )
                return {
                    "success": True,
                    "status": "sent",
                    "action": action_upper,
                    "action_id": action_record.id,
                }

            except Exception as send_err:
                error_str = str(send_err)
                action_record.status = "failed"
                action_record.error = error_str
                await db.commit()

                print(
                    f"[WHATSAPP_SEND_FAILED] call_id={call_id} contact_id={resolved_contact_id} "
                    f"action={action_upper} error='{error_str}'"
                )
                # Failure is isolated; returns safe status without crashing caller
                return {
                    "success": False,
                    "status": "failed",
                    "action": action_upper,
                    "error": error_str,
                }

    @staticmethod
    def _build_action_payload(
        action: str,
        customer_name: str,
        campaign_name: str,
        custom_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, Optional[Dict[str, Any]]]:
        """Construct predefined message text and media payloads for each action."""
        cfg = DEFAULT_ASSET_CONFIG
        c_name = customer_name or "there"

        if action == "SEND_BROCHURE":
            text = (
                f"Hi {c_name},\n\n"
                f"As requested during our call regarding *{campaign_name}*, here is our comprehensive brochure.\n\n"
                f"Feel free to reply here if you have any questions!\n"
                f"— Team CallingGen"
            )
            media = {
                "media_url": cfg["brochure_url"],
                "media_type": "document",
                "mimetype": "application/pdf",
                "file_name": f"{campaign_name.replace(' ', '_')}_Brochure.pdf",
            }
            return text, media

        elif action == "SEND_PRICING":
            text = (
                f"Hi {c_name},\n\n"
                f"Here are the pricing and consultation plans for *{campaign_name}* as discussed on our call.\n\n"
                f"Let us know which plan aligns best with your requirements.\n"
                f"— Team CallingGen"
            )
            media = {
                "media_url": cfg["pricing_url"],
                "media_type": "document",
                "mimetype": "application/pdf",
                "file_name": "Pricing_Plans.pdf",
            }
            return text, media

        elif action == "SEND_CATALOGUE":
            text = (
                f"Hi {c_name},\n\n"
                f"Here is our complete catalogue of services for *{campaign_name}*.\n\n"
                f"— Team CallingGen"
            )
            media = {
                "media_url": cfg["catalogue_url"],
                "media_type": "document",
                "mimetype": "application/pdf",
                "file_name": "Product_Catalogue.pdf",
            }
            return text, media

        elif action == "SEND_WEBSITE":
            text = (
                f"Hi {c_name},\n\n"
                f"Thank you for your interest! You can explore more details about *{campaign_name}* on our website:\n"
                f"🔗 {cfg['website_url']}\n\n"
                f"Please reply here if you would like assistance with anything."
            )
            return text, None

        elif action == "SEND_BOOKING_LINK":
            text = (
                f"Hi {c_name},\n\n"
                f"You can choose a convenient slot for your 1-on-1 consultation here:\n"
                f"📅 {cfg['booking_url']}\n\n"
                f"We look forward to speaking with you!"
            )
            return text, None

        elif action == "SEND_CONTACT_DETAILS":
            text = (
                f"Hi {c_name},\n\n"
                f"Here are the official contact details for *{campaign_name}*:\n"
                f"📞 Phone: {cfg['company_phone']}\n"
                f"✉️ Email: {cfg['company_email']}\n"
                f"🌐 Website: {cfg['website_url']}\n\n"
                f"Feel free to reach out anytime!"
            )
            return text, None

        elif action == "SEND_CALLBACK_CONFIRMATION":
            cb_time = (custom_payload or {}).get("callback_time", "at your requested time")
            text = (
                f"Hi {c_name},\n\n"
                f"We have noted your request for a callback {cb_time} regarding *{campaign_name}*.\n"
                f"Our consultant will reach out to you as scheduled.\n\n"
                f"Thank you!"
            )
            return text, None

        elif action == "SEND_MISSED_CALL":
            text = (
                f"Hi {c_name},\n\n"
                f"We tried reaching you regarding *{campaign_name}*.\n\n"
                f"Please reply whenever you're available or let us know a convenient time to call.\n"
                f"— Team CallingGen"
            )
            return text, None

        elif action == "SEND_VOICEMAIL":
            text = (
                f"Hi {c_name},\n\n"
                f"We tried calling you regarding *{campaign_name}* and reached your voicemail.\n\n"
                f"Please reply whenever you're free or let us know a convenient time to connect.\n"
                f"— Team CallingGen"
            )
            return text, None

        elif action == "SEND_CALLBACK":
            text = (
                f"Hi {c_name},\n\n"
                f"We noticed you were busy when we called regarding *{campaign_name}*.\n\n"
                f"Please reply with a suitable time and we will gladly call you back.\n"
                f"— Team CallingGen"
            )
            return text, None

        else:
            text = f"Hi {c_name}, thank you for speaking with us regarding {campaign_name}."
            return text, None
