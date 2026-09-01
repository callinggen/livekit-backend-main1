import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.call import Call
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.job import Job
from app.models.user import User
from app.models.whatsapp_material import WhatsAppMaterial
from app.models.whatsapp_send_job import WhatsAppSendJob
from app.models.whatsapp_send_recipient import WhatsAppSendRecipient
from app.services.whatsapp_credit_service import WhatsAppCreditService
from whatsapp import service as evolution_service
from whatsapp.config import EVOLUTION_INSTANCE_NAME


def normalize_whatsapp_phone(raw_phone: str) -> str:
    """Normalize phone number to international WhatsApp format (e.g. 917656807447)."""
    clean = "".join(c for c in (raw_phone or "") if c.isdigit())
    if len(clean) == 10:
        clean = "91" + clean
    return clean


def resolve_personalization(text: str, variables: Dict[str, str]) -> str:
    """
    Replace placeholders in message text safely.
    Supported: {{name}}, {{customer_name}}, {{phone}}, {{campaign_name}}, {{appointment_date}}, {{appointment_time}}
    """
    if not text:
        return ""
    result = text
    for key, val in variables.items():
        result = result.replace(f"{{{{{key}}}}}", val or "")
    return result


class WhatsAppAutomationService:
    """
    Evaluates campaign-level WhatsApp automation rules after call completion / result persistence.
    Operates strictly downstream and asynchronously without altering agent or calling workflows.
    """

    @classmethod
    async def process_call_automation(cls, call_id: int) -> Optional[Dict[str, Any]]:
        """
        Evaluate and trigger WhatsApp automation rules for a finished or failed call.
        """
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if not call:
                print(f"[WhatsAppAutomation] Call {call_id} not found in DB.")
                return None

            campaign_id = call.campaign_id
            if not campaign_id and call.job_id:
                job = await db.get(Job, call.job_id)
                if job:
                    campaign_id = job.campaign_id
            if not campaign_id and call.contact_id:
                contact_obj = await db.get(Contact, call.contact_id)
                if contact_obj:
                    campaign_id = contact_obj.campaign_id

            if not campaign_id:
                print(f"[WhatsAppAutomation] Could not resolve campaign_id for Call {call_id}.")
                return None

            campaign = await db.get(Campaign, campaign_id)
            if not campaign:
                return None

            # 1. Check if WhatsApp Automation is enabled on this campaign
            automation_config = campaign.whatsapp_automation or {}
            if isinstance(automation_config, str):
                try:
                    import json
                    automation_config = json.loads(automation_config)
                except Exception:
                    automation_config = {}
            if not isinstance(automation_config, dict) or not automation_config.get("enabled", False):
                # Automation is OFF (default) -> do nothing
                return None

            rules: List[Dict[str, Any]] = automation_config.get("rules", [])
            if not rules:
                return None

            contact_id = call.contact_id
            contact = await db.get(Contact, contact_id) if contact_id else None

            # Resolve user ownership for credits
            user_id = campaign.user_id
            user = await db.get(User, user_id) if user_id else None
            if not user:
                res = await db.execute(select(User).limit(1))
                user = res.scalars().first()

            if not user:
                print(f"[WhatsAppAutomation] No valid user found to deduct credits for Campaign {campaign.id}.")
                return None

            # 2. Extract call outcome attributes
            cat = (call.category or "UNCATEGORIZED").upper()
            summary_lower = (call.summary or "").lower()
            resp_lower = (contact.response or "").lower() if contact else ""
            call_status = (call.status or "").lower()

            # Map to standard classification terms
            is_interested = "interested" in resp_lower or "interested" in summary_lower or cat in ("HOT", "WARM")
            is_not_interested = "not interested" in resp_lower or "refusal" in resp_lower or "opt-out" in summary_lower or "declined" in resp_lower
            is_hot = cat == "HOT" or "high purchase" in summary_lower
            is_warm = cat == "WARM"
            is_cold = cat == "COLD" or is_not_interested
            is_appointment = bool(contact and contact.appointment_date) or "appointment" in resp_lower or "appointment" in summary_lower
            is_callback = "callback" in resp_lower or "rescheduled" in resp_lower or "callback" in summary_lower
            is_answered = call_status == "completed" and "answered" in (resp_lower or "answered")
            is_not_answered = call_status in ("failed", "incomplete") or "no answer" in resp_lower or "unreached" in resp_lower
            is_cut = "cut" in resp_lower or "disconnected" in resp_lower

            # 3. Match against enabled rules
            matched_rule = None
            trigger_description = ""

            for rule in rules:
                if not rule.get("enabled", True):
                    continue

                # 4-Dimension multi-select filters
                ct_filters = [f.strip().lower() for f in (rule.get("call_type_filters") or []) if f]
                ai_filters = [f.strip().lower() for f in (rule.get("ai_class_filters") or []) if f]
                resp_filters = [f.strip().lower() for f in (rule.get("response_filters") or []) if f]
                status_filters = [f.strip().lower() for f in (rule.get("status_filters") or []) if f]

                # Check if multi-dimension filters are in use
                has_4d_filters = bool(ct_filters or ai_filters or resp_filters or status_filters)

                match = True

                if has_4d_filters:
                    # 1. Call Type check (Outbound / Inbound)
                    if ct_filters and "all types" not in ct_filters and "all" not in ct_filters:
                        raw_ct = getattr(call, "call_type", None) or getattr(call, "direction", "outbound")
                        call_type = (raw_ct or "outbound").lower()
                        if not any(f in call_type for f in ct_filters):
                            match = False

                    # 2. AI Class check (Hot Lead, Warm Lead, Cold Lead, Interested, Callback, Appointment)
                    if match and ai_filters and "all leads" not in ai_filters and "all" not in ai_filters:
                        ai_matched = False
                        if any(f in ("hot", "hot lead") for f in ai_filters) and is_hot:
                            ai_matched = True
                        elif any(f in ("warm", "warm lead") for f in ai_filters) and is_warm:
                            ai_matched = True
                        elif any(f in ("cold", "cold lead") for f in ai_filters) and is_cold:
                            ai_matched = True
                        elif "interested" in ai_filters and is_interested:
                            ai_matched = True
                        elif any(f in ("callback", "rescheduled") for f in ai_filters) and is_callback:
                            ai_matched = True
                        elif any(f in ("appointment", "appointment booked") for f in ai_filters) and is_appointment:
                            ai_matched = True
                        if not ai_matched:
                            match = False

                    # 3. Response check (Answered, Not Answered, Appointment Booked, Callback, Declined, Cut/Disconnected)
                    if match and resp_filters and "all responses" not in resp_filters and "all" not in resp_filters:
                        resp_matched = False
                        if any(f in ("answered", "completed") for f in resp_filters) and is_answered:
                            resp_matched = True
                        elif any(f in ("not answered", "no answer", "missed") for f in resp_filters) and is_not_answered:
                            resp_matched = True
                        elif any(f in ("appointment", "appointment booked") for f in resp_filters) and is_appointment:
                            resp_matched = True
                        elif any(f in ("callback", "rescheduled") for f in resp_filters) and is_callback:
                            resp_matched = True
                        elif any(f in ("declined", "not interested") for f in resp_filters) and is_not_interested:
                            resp_matched = True
                        elif any(f in ("cut", "cut/disconnected", "disconnected") for f in resp_filters) and is_cut:
                            resp_matched = True
                        if not resp_matched:
                            match = False

                    # 4. Status check (Completed, Failed, In Progress)
                    if match and status_filters and "all status" not in status_filters and "all" not in status_filters:
                        status_matched = False
                        if "completed" in status_filters and call_status == "completed":
                            status_matched = True
                        elif any(f in ("failed", "unreached") for f in status_filters) and call_status in ("failed", "incomplete"):
                            status_matched = True
                        elif "in progress" in status_filters and call_status in ("in progress", "pending"):
                            status_matched = True
                        if not status_matched:
                            match = False

                else:
                    # Legacy single category & values evaluation
                    trigger_type = (rule.get("trigger_type") or rule.get("category") or "").strip().lower()
                    raw_values = rule.get("values") or [rule.get("trigger_value") or rule.get("value")]
                    trigger_vals = [str(v).strip().lower() for v in raw_values if v]

                    match = False
                    for trigger_val in trigger_vals:
                        if trigger_type in ("ai_classification", "classification"):
                            if trigger_val in ("hot", "hot lead") and is_hot:
                                match = True
                            elif trigger_val in ("warm", "warm lead") and is_warm:
                                match = True
                            elif trigger_val in ("cold", "cold lead") and is_cold:
                                match = True
                            elif trigger_val == "interested" and is_interested:
                                match = True
                            elif trigger_val in ("not interested", "declined") and is_not_interested:
                                match = True
                        elif trigger_type in ("response", "call_response"):
                            if trigger_val in ("answered", "completed") and is_answered:
                                match = True
                            elif trigger_val in ("not answered", "no answer", "missed") and is_not_answered:
                                match = True
                            elif trigger_val in ("appointment", "appointment booked") and is_appointment:
                                match = True
                            elif trigger_val in ("callback", "rescheduled") and is_callback:
                                match = True
                            elif trigger_val in ("declined", "not interested") and is_not_interested:
                                match = True
                            elif trigger_val in ("cut", "cut/disconnected", "disconnected") and is_cut:
                                match = True
                        elif trigger_type == "status":
                            if trigger_val == "completed" and call_status == "completed":
                                match = True
                            elif trigger_val in ("failed", "unreached") and call_status in ("failed", "incomplete"):
                                match = True
                        elif trigger_type == "all" or trigger_val == "any":
                            match = True
                        if match:
                            break

                if match:
                    # Permission / Consent check for promotional attachments & materials
                    has_attachments = bool(rule.get("attachments") or rule.get("material_id"))
                    require_permission = rule.get("require_permission", True)

                    if has_attachments and require_permission:
                        transcript_lower = (call.transcript or "").lower()
                        negative_phrases = ["don't send", "dont send", "no whatsapp", "not on whatsapp", "stop sending", "do not send", "not interested"]
                        if is_not_interested or any(p in transcript_lower for p in negative_phrases) or any(p in resp_lower for p in negative_phrases):
                            print(f"[WhatsAppAutomation] Skipping promotional material for Call {call_id}: Customer declined or gave no affirmative consent.")
                            continue

                    matched_rule = rule
                    trigger_description = f"Filter Match"
                    break

            if not matched_rule:
                print(f"[WhatsAppAutomation] No matching automation rule for Call {call_id} (Category: {cat}, Status: {call_status}).")
                return None

            rule_id = str(matched_rule.get("id") or matched_rule.get("title") or trigger_description)

            # 4. Idempotency Check: Prevent duplicate execution for (call_id, contact_id)
            existing_recipient = await db.execute(
                select(WhatsAppSendRecipient).where(
                    WhatsAppSendRecipient.call_id == call_id,
                    WhatsAppSendRecipient.contact_id == contact_id,
                    WhatsAppSendRecipient.status.in_(["sent", "delivered", "partial", "pending"]),
                )
            )
            if existing_recipient.scalars().first():
                print(f"[WhatsAppAutomation] Duplicate protection: Automation already executed for Call {call_id}.")
                return {"status": "skipped_duplicate", "call_id": call_id}

            # 5. Resolve Contact Details & Phone
            dest_phone = contact.phone if contact else call.phone
            clean_phone = normalize_whatsapp_phone(dest_phone)
            if len(clean_phone) < 10:
                print(f"[WhatsAppAutomation] Invalid destination phone number '{dest_phone}' for Call {call_id}.")
                return None

            customer_name = (contact.customer_name if (contact and contact.customer_name) else (contact.name if contact else "there")) or "there"

            variables = {
                "name": customer_name,
                "customer_name": customer_name,
                "phone": dest_phone or "",
                "campaign_name": campaign.campaign_name,
                "appointment_date": (contact.appointment_date if contact else "") or "",
                "appointment_time": (contact.appointment_time if contact else "") or "",
            }

            # 6. Resolve Message Content & Attachments
            raw_text = matched_rule.get("message_text") or matched_rule.get("message") or ""
            material_id = matched_rule.get("material_id")

            if material_id:
                try:
                    mat = await db.get(WhatsAppMaterial, int(material_id))
                    if mat and mat.content:
                        raw_text = mat.content
                except Exception as e:
                    print(f"[WhatsAppAutomation] Notice: Could not fetch material {material_id}: {e}")

            personalized_text = resolve_personalization(raw_text, variables)

            # Resolve attachments (Images/Documents)
            attachments = matched_rule.get("attachments") or []
            resolved_attachments = []
            backend_base_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

            for att in attachments:
                att_type = att.get("type", "document")
                att_url = att.get("url") or att.get("file_url") or ""
                if att_url.startswith("/"):
                    att_url = f"{backend_base_url}{att_url}"

                resolved_attachments.append({
                    "title": att.get("title") or "Attachment",
                    "type": att_type,
                    "url": att_url,
                    "file_name": att.get("file_name") or ("image.png" if att_type == "image" else "document.pdf"),
                    "mime_type": att.get("mime_type") or ("image/png" if att_type == "image" else "application/pdf"),
                    "file_size": att.get("file_size"),
                })

            # Build item list for credit calculation
            send_items = []
            if personalized_text.strip():
                send_items.append({"type": "text", "text": personalized_text})
            for att in resolved_attachments:
                send_items.append(att)

            if not send_items:
                print(f"[WhatsAppAutomation] No content or attachments configured for rule in Call {call_id}.")
                return None

            # 7. Centralized Credit Calculation & Safety Check (Text=1, Image=2, Document=3)
            total_required_credits = WhatsAppCreditService.calculate_total_credits(send_items, 1)

            await db.refresh(user)
            if user.credits < total_required_credits:
                print(f"[WhatsAppAutomation] Insufficient credits for user {user.id}. Required: {total_required_credits}, Available: {user.credits}.")
                # Record failed send job for audit
                send_job = WhatsAppSendJob(
                    user_id=user.id,
                    source_type="campaign_automation",
                    source_name=f"{campaign.campaign_name} (Automation)",
                    campaign_id=campaign.id,
                    trigger_event=trigger_description,
                    content_type="Mixed" if len(send_items) > 1 else send_items[0]["type"].title(),
                    message_text=personalized_text,
                    attachments=resolved_attachments,
                    total_contacts=1,
                    sent_count=0,
                    failed_count=1,
                    credits_deducted=0,
                    status="failed",
                    completed_at=datetime.now(timezone.utc),
                )
                db.add(send_job)
                await db.flush()

                rec = WhatsAppSendRecipient(
                    send_job_id=send_job.id,
                    contact_id=contact_id,
                    call_id=call_id,
                    name=customer_name,
                    phone=clean_phone,
                    status="insufficient_credits",
                    error_message=f"Required {total_required_credits} credits but only {user.credits} available.",
                )
                db.add(rec)
                await db.commit()
                return {"status": "failed", "reason": "insufficient_credits"}

            # 8. Create Send Job & Recipient Record
            send_job = WhatsAppSendJob(
                user_id=user.id,
                source_type="campaign_automation",
                source_name=f"{campaign.campaign_name} (Automation)",
                campaign_id=campaign.id,
                trigger_event=trigger_description,
                content_type="Mixed" if len(send_items) > 1 else send_items[0]["type"].title(),
                message_text=personalized_text,
                attachments=resolved_attachments,
                total_contacts=1,
                sent_count=0,
                failed_count=0,
                credits_deducted=0,
                status="in_progress",
            )
            db.add(send_job)
            await db.flush()

            recipient_record = WhatsAppSendRecipient(
                send_job_id=send_job.id,
                contact_id=contact_id,
                call_id=call_id,
                name=customer_name,
                phone=clean_phone,
                status="pending",
            )
            db.add(recipient_record)
            await db.commit()

            # 9. Execute Send via Evolution API Service
            inst = EVOLUTION_INSTANCE_NAME or "callinggen"
            item_results = []
            credits_spent = 0
            has_error = False
            last_error = None

            print(f"[WhatsAppAutomation] Sending automation WhatsApp to {clean_phone} for Call {call_id} ({trigger_description})")

            for item in send_items:
                try:
                    if item["type"] == "text":
                        api_res = await evolution_service.send_text_message(
                            instance_name=inst,
                            number=clean_phone,
                            text=item["text"],
                        )
                        credits_spent += WhatsAppCreditService.CREDIT_PER_TEXT
                        item_results.append({"type": "text", "status": "sent", "response": api_res})
                    elif item["type"] in ("image", "document"):
                        api_res = await evolution_service.send_media_message(
                            instance_name=inst,
                            number=clean_phone,
                            media_url=item["url"],
                            media_type=item["type"],
                            mimetype=item.get("mime_type", "application/pdf" if item["type"] == "document" else "image/png"),
                            caption=personalized_text if not any(i["type"] == "text" for i in send_items) else None,
                            file_name=item.get("file_name"),
                        )
                        credits_spent += WhatsAppCreditService.calculate_item_credits(item["type"])
                        item_results.append({"type": item["type"], "status": "sent", "response": api_res})
                except Exception as send_err:
                    print(f"[WhatsAppAutomation] Error sending {item['type']} to {clean_phone}: {send_err}")
                    has_error = True
                    last_error = str(send_err)
                    item_results.append({"type": item["type"], "status": "failed", "error": str(send_err)})

            # 10. Update DB & Deduct Spent Credits
            if credits_spent > 0:
                await WhatsAppCreditService.deduct_credits(db, user, credits_spent)

            recipient_record.status = "sent" if not has_error else ("partial" if credits_spent > 0 else "failed")
            recipient_record.error_message = last_error
            recipient_record.details = {"items": item_results}
            recipient_record.sent_at = datetime.now(timezone.utc)

            send_job.sent_count = 1 if not has_error else (1 if credits_spent > 0 else 0)
            send_job.failed_count = 0 if not has_error else (0 if credits_spent > 0 else 1)
            send_job.credits_deducted = credits_spent
            send_job.status = "completed" if not has_error else ("partial" if credits_spent > 0 else "failed")
            send_job.completed_at = datetime.now(timezone.utc)

            await db.commit()
            print(f"[WhatsAppAutomation] Automation complete for Call {call_id}: Job {send_job.id}, Credits: {credits_spent}")

            return {
                "success": not has_error,
                "job_id": send_job.id,
                "credits_deducted": credits_spent,
                "trigger": trigger_description,
            }

    @classmethod
    async def trigger_in_call_action(cls, call_id: int, action: str) -> Optional[Dict[str, Any]]:
        """
        Trigger an in-call WhatsApp dispatch (e.g. SEND_BROCHURE, SEND_PRICING, SEND_WEBSITE)
        when the AI agent executes send_whatsapp_info during a live call.
        """
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if not call:
                print(f"[WhatsAppAutomation] In-call trigger: Call {call_id} not found.")
                return {"status": "error", "message": "Call not found"}

            campaign_id = call.campaign_id
            if not campaign_id and call.job_id:
                job = await db.get(Job, call.job_id)
                if job:
                    campaign_id = job.campaign_id

            campaign = await db.get(Campaign, campaign_id) if campaign_id else None
            contact = await db.get(Contact, call.contact_id) if call.contact_id else None

            raw_phone = contact.phone if contact else call.phone
            clean_phone = normalize_whatsapp_phone(raw_phone)
            if not clean_phone:
                return {"status": "error", "message": "Invalid phone number"}

            customer_name = (contact.customer_name or contact.name) if contact else ""
            campaign_name = campaign.campaign_name if campaign else "Our Team"

            # Resolve user ownership for credits
            user_id = campaign.user_id if campaign else 1
            user = await db.get(User, user_id) if user_id else None
            if not user:
                res = await db.execute(select(User).limit(1))
                user = res.scalars().first()

            if not user:
                return {"status": "error", "message": "User not found"}

            # Collect attachments and message text from campaign config if present
            automation_config = campaign.whatsapp_automation if campaign else {}
            if isinstance(automation_config, str):
                try:
                    import json
                    automation_config = json.loads(automation_config)
                except Exception:
                    automation_config = {}

            rules = automation_config.get("rules", []) if isinstance(automation_config, dict) else []
            first_rule = rules[0] if rules else {}

            raw_text = first_rule.get("message_text") or f"Hi {customer_name}, here is the information from {campaign_name} you requested during our call."
            variables = {
                "name": customer_name,
                "customer_name": customer_name,
                "phone": raw_phone,
                "campaign_name": campaign_name,
            }
            personalized_text = resolve_personalization(raw_text, variables)

            attachments = first_rule.get("attachments") or []
            resolved_attachments = []
            backend_base_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

            for att in attachments:
                att_type = att.get("type", "document")
                att_url = att.get("url") or att.get("file_url") or ""
                if att_url.startswith("/"):
                    att_url = f"{backend_base_url}{att_url}"
                resolved_attachments.append({
                    "title": att.get("title") or "Brochure",
                    "type": att_type,
                    "url": att_url,
                    "file_name": att.get("file_name") or ("image.png" if att_type == "image" else "document.pdf"),
                    "mime_type": att.get("mime_type") or ("image/png" if att_type == "image" else "application/pdf"),
                    "file_size": att.get("file_size"),
                })

            send_items = []
            if personalized_text.strip():
                send_items.append({"type": "text", "text": personalized_text})
            for att in resolved_attachments:
                send_items.append(att)

            if not send_items:
                send_items.append({"type": "text", "text": f"Hi {customer_name}, thank you for speaking with us! Here is the information regarding {campaign_name}."})

            total_required_credits = WhatsAppCreditService.calculate_total_credits(send_items, 1)
            await db.refresh(user)
            if user.credits < total_required_credits:
                return {"status": "error", "message": "Insufficient WhatsApp credits"}

            inst = EVOLUTION_INSTANCE_NAME or "callinggen"
            has_error = False
            last_error = None
            credits_spent = 0
            item_results = []

            for item in send_items:
                try:
                    if item["type"] == "text":
                        api_res = await evolution_service.send_text_message(
                            instance_name=inst,
                            number=clean_phone,
                            text=item["text"],
                        )
                        credits_spent += WhatsAppCreditService.CREDIT_PER_TEXT
                        item_results.append({"type": "text", "status": "sent", "response": api_res})
                    elif item["type"] in ("image", "document"):
                        api_res = await evolution_service.send_media_message(
                            instance_name=inst,
                            number=clean_phone,
                            media_url=item["url"],
                            media_type=item["type"],
                            mimetype=item.get("mime_type", "application/pdf" if item["type"] == "document" else "image/png"),
                            caption=personalized_text if not any(i["type"] == "text" for i in send_items) else None,
                            file_name=item.get("file_name"),
                        )
                        credits_spent += WhatsAppCreditService.calculate_item_credits(item["type"])
                        item_results.append({"type": item["type"], "status": "sent", "response": api_res})
                except Exception as send_err:
                    print(f"[WhatsAppAutomation] In-call send error to {clean_phone}: {send_err}")
                    has_error = True
                    last_error = str(send_err)
                    item_results.append({"type": item["type"], "status": "failed", "error": str(send_err)})

            if credits_spent > 0:
                await WhatsAppCreditService.deduct_credits(db, user, credits_spent)

            send_job = WhatsAppSendJob(
                user_id=user.id,
                source_type="campaign_in_call",
                source_name=f"{campaign_name} (In-Call {action})",
                campaign_id=campaign_id,
                trigger_event=f"In-Call {action}",
                content_type="Mixed" if len(send_items) > 1 else send_items[0]["type"].title(),
                message_text=personalized_text,
                attachments=resolved_attachments,
                total_contacts=1,
                sent_count=1 if not has_error else 0,
                failed_count=0 if not has_error else 1,
                credits_deducted=credits_spent,
                status="completed" if not has_error else "failed",
                completed_at=datetime.now(timezone.utc),
            )
            db.add(send_job)
            await db.flush()

            rec = WhatsAppSendRecipient(
                send_job_id=send_job.id,
                contact_id=call.contact_id,
                call_id=call_id,
                name=customer_name,
                phone=clean_phone,
                status="sent" if not has_error else "failed",
                error_message=last_error,
                details={"items": item_results},
                sent_at=datetime.now(timezone.utc),
            )
            db.add(rec)
            await db.commit()

            return {
                "success": not has_error,
                "job_id": send_job.id,
                "credits_deducted": credits_spent,
                "action": action,
            }
