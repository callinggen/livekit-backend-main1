import json
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
from app.services.email_service import EmailService

email_service = EmailService()


# ---------------------------------------------------------------------------
# Predefined automation email templates
# ---------------------------------------------------------------------------

EMAIL_AUTOMATION_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "hot_lead_followup",
        "name": "🔥 Hot Lead Follow-Up",
        "description": "Sent to hot leads immediately after the call.",
        "subject": "Great speaking with you, {{name}}!",
        "body": (
            "Hi {{name}},\n\n"
            "Thank you for taking the time to speak with us today regarding {{campaign_name}}.\n\n"
            "Based on our conversation, we believe you would be a great fit for what we offer. "
            "Our team will reach out shortly with more details tailored specifically for you.\n\n"
            "In the meantime, feel free to reply to this email with any questions.\n\n"
            "Best regards,\nThe {{campaign_name}} Team"
        ),
    },
    {
        "id": "appointment_confirmation",
        "name": "📅 Appointment Confirmed",
        "description": "Sent when an appointment is booked during the call.",
        "subject": "Your appointment is confirmed, {{name}}!",
        "body": (
            "Hi {{name}},\n\n"
            "Your appointment has been successfully scheduled.\n\n"
            "Date: {{appointment_date}}\n"
            "Time: {{appointment_time}}\n\n"
            "Please make sure you are available at the scheduled time. "
            "If you need to reschedule, simply reply to this email.\n\n"
            "Looking forward to connecting with you!\n\n"
            "Best regards,\nThe {{campaign_name}} Team"
        ),
    },
    {
        "id": "callback_reminder",
        "name": "🔁 Callback Scheduled",
        "description": "Sent when the contact requests a callback.",
        "subject": "We'll call you back, {{name}}",
        "body": (
            "Hi {{name}},\n\n"
            "Thank you for speaking with us. As requested, our team will reach back out to you soon.\n\n"
            "We noted your interest and will ensure our next call is well-prepared for you.\n\n"
            "If you'd like to speak sooner, feel free to reply to this email.\n\n"
            "Best regards,\nThe {{campaign_name}} Team"
        ),
    },
    {
        "id": "not_answered_followup",
        "name": "📵 Missed Call Follow-Up",
        "description": "Sent when the call was not answered.",
        "subject": "We tried to reach you, {{name}}",
        "body": (
            "Hi {{name}},\n\n"
            "We tried to connect with you earlier today regarding {{campaign_name}} but were unable to reach you.\n\n"
            "We'd love to share some important information with you. "
            "Please feel free to reply to this email or let us know a good time to call back.\n\n"
            "Best regards,\nThe {{campaign_name}} Team"
        ),
    },
    {
        "id": "general_followup",
        "name": "👋 General Thank You",
        "description": "Sent after any completed call as a thank-you.",
        "subject": "Thank you for your time, {{name}}",
        "body": (
            "Hi {{name}},\n\n"
            "Thank you for speaking with us today about {{campaign_name}}.\n\n"
            "We appreciate your time and look forward to being of service. "
            "Please don't hesitate to reach out if you have any questions.\n\n"
            "Best regards,\nThe {{campaign_name}} Team"
        ),
    },
    {
        "id": "custom",
        "name": "✏️ Custom Template",
        "description": "Write your own subject and email body.",
        "subject": "",
        "body": "",
    },
]


def resolve_email_personalization(text: str, variables: Dict[str, str]) -> str:
    """Replace {{placeholders}} in subject/body with contact-specific values."""
    if not text:
        return ""
    result = text
    for key, val in variables.items():
        result = result.replace(f"{{{{{key}}}}}", val or "")
    return result


def wrap_email_html(content: str, subject: str, sender_name: str = "", campaign_name: str = "") -> str:
    """Ensure email content is rendered in a responsive, beautifully styled email template."""
    if not content:
        return ""
    
    # If already a full HTML document, return as-is
    if "<!DOCTYPE" in content or "<html" in content.lower():
        return content

    header_title = sender_name or campaign_name or "Follow-up Notification"
    
    # Robust check if content contains HTML tags (e.g. <p>, <p class="...">, <div>, <br>, etc.)
    import re
    has_html_tags = bool(re.search(r"<[a-zA-Z/][^>]*>", content))
    if not has_html_tags:
        import html as html_lib
        escaped = html_lib.escape(content)
        paragraphs = escaped.split("\n\n")
        html_parts = []
        for para in paragraphs:
            lines = para.split("\n")
            html_parts.append("<p style='margin: 0 0 16px 0; line-height: 1.65;'>" + "<br>".join(lines) + "</p>")
        body_content = "\n".join(html_parts)
    else:
        body_content = content
        # Ensure Quill alignment classes translate into inline styles for email client compatibility
        body_content = re.sub(
            r'class="([^"]*ql-align-center[^"]*)"',
            r'class="\1" style="text-align: center; margin: 0 0 16px 0;"',
            body_content
        )
        body_content = re.sub(
            r'class="([^"]*ql-align-right[^"]*)"',
            r'class="\1" style="text-align: right; margin: 0 0 16px 0;"',
            body_content
        )
        body_content = re.sub(
            r'class="([^"]*ql-align-justify[^"]*)"',
            r'class="\1" style="text-align: justify; margin: 0 0 16px 0;"',
            body_content
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
    <style>
        body, table, td, p, a, li, blockquote {{
            -webkit-text-size-adjust: 100%;
            -ms-text-size-adjust: 100%;
        }}
        p {{ margin: 0 0 16px 0; line-height: 1.65; }}
        .ql-align-center {{ text-align: center !important; }}
        .ql-align-right {{ text-align: right !important; }}
        .ql-align-justify {{ text-align: justify !important; }}
        table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
        img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
        @media screen and (max-width: 620px) {{
            .email-container {{ width: 100% !important; border-radius: 0 !important; }}
            .content-padding {{ padding: 24px 18px !important; }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 32px 10px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #334155; line-height: 1.65;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc;">
        <tr>
            <td align="center">
                <table role="presentation" class="email-container" width="580" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; width: 100%; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.05); text-align: left;">
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #ffffff; padding: 24px 32px 16px 32px; border-bottom: 2px solid #2563eb;">
                            <div style="font-size: 18px; font-weight: 700; color: #0f172a; letter-spacing: -0.3px;">
                                {header_title}
                            </div>
                        </td>
                    </tr>
                    
                    <!-- Content Area -->
                    <tr>
                        <td class="content-padding" style="padding: 28px 32px 24px 32px; font-size: 14.5px; color: #334155; line-height: 1.7;">
                            {body_content}
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; padding: 18px 32px; border-top: 1px solid #f1f5f9; font-size: 11.5px; color: #94a3b8; text-align: center;">
                            <p style="margin: 0;">
                                Sent automatically following our conversation &bull; {sender_name or 'Support Team'}
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""


def text_to_html(text: str) -> str:
    """Legacy helper for plain-text body."""
    return wrap_email_html(text, "Follow-up")


class EmailAutomationService:
    """
    Evaluates campaign-level Email automation rules after call completion.
    Mirrors WhatsAppAutomationService exactly — same 4-filter system.
    Only fires if the campaign has email_automation.enabled = true AND
    the contact has a valid email address.
    """

    @classmethod
    async def process_call_automation(cls, call_id: int) -> Optional[Dict[str, Any]]:
        """Evaluate and trigger email automation rules for a finished call."""
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if not call:
                print(f"[EmailAutomation] Call {call_id} not found.")
                return None

            # Resolve campaign
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
                print(f"[EmailAutomation] Could not resolve campaign_id for Call {call_id}.")
                return None

            campaign = await db.get(Campaign, campaign_id)
            if not campaign:
                return None

            # 1. Check if Email Automation is enabled
            automation_config = campaign.email_automation or {}
            if isinstance(automation_config, str):
                try:
                    automation_config = json.loads(automation_config)
                except Exception:
                    automation_config = {}
            if not isinstance(automation_config, dict) or not automation_config.get("enabled", False):
                return None

            rules: List[Dict[str, Any]] = automation_config.get("rules", [])
            if not rules:
                return None

            contact_id = call.contact_id
            contact = await db.get(Contact, contact_id) if contact_id else None

            # Resolve destination email
            dest_email = None
            if contact:
                dest_email = (
                    getattr(contact, "email", None)
                    or getattr(contact, "customer_email", None)
                    or getattr(contact, "contact_email", None)
                )
                if not dest_email and contact.metadata_fields and isinstance(contact.metadata_fields, dict):
                    for k in ("email", "Email", "email_address", "Email Address", "mail", "Mail", "contact_email", "customer_email"):
                        val = contact.metadata_fields.get(k)
                        if val and isinstance(val, str) and "@" in val:
                            dest_email = val.strip()
                            break
            if not dest_email and contact and contact.phone and campaign.user_id:
                try:
                    from app.models.saved_contact import SavedContact
                    from app.api.contacts_book import normalize_phone
                    from sqlalchemy import and_, select
                    norm_phone = normalize_phone(contact.phone)
                    if norm_phone:
                        stmt = select(SavedContact).where(
                            and_(
                                SavedContact.user_id == campaign.user_id,
                                SavedContact.phone == norm_phone,
                            )
                        ).limit(1)
                        sc_res = await db.execute(stmt)
                        sc = sc_res.scalars().first()
                        if sc and sc.email and "@" in sc.email:
                            dest_email = sc.email.strip()
                            print(f"[EmailAutomation] Resolved destination email from SavedContact ({sc.email}) for Call {call_id}")
                except Exception as ex:
                    print(f"[EmailAutomation] SavedContact fallback lookup error: {ex}")

            if not dest_email:
                print(f"[EmailAutomation] No email address for contact on Call {call_id}. Skipping.")
                return None

            # Destination email resolved successfully

            # 2. Extract call outcome attributes (same as WhatsApp)
            cat = (call.category or "UNCATEGORIZED").upper()
            summary_lower = (call.summary or "").lower()
            resp_lower = (contact.response or "").lower() if contact else ""
            call_status = (call.status or "").lower()

            is_interested = "interested" in resp_lower or "interested" in summary_lower or cat in ("HOT", "WARM")
            is_not_interested = "not interested" in resp_lower or "refusal" in resp_lower or "opt-out" in summary_lower
            is_hot = cat == "HOT" or "high purchase" in summary_lower
            is_warm = cat == "WARM"
            is_cold = cat == "COLD" or is_not_interested
            is_appointment = bool(contact and contact.appointment_date) or "appointment" in resp_lower or "appointment" in summary_lower
            is_callback = "callback" in resp_lower or "rescheduled" in resp_lower or "callback" in summary_lower
            is_answered = call_status == "completed" and "answered" in (resp_lower or "answered")
            is_not_answered = call_status in ("failed", "incomplete") or "no answer" in resp_lower or "unreached" in resp_lower
            is_cut = "cut" in resp_lower or "disconnected" in resp_lower

            # 3. Match rules — same 4-filter logic as WhatsApp
            matched_rule = None
            for rule in rules:
                if not rule.get("enabled", True):
                    continue

                ct_filters = [f.strip().lower() for f in (rule.get("call_type_filters") or []) if f]
                ai_filters = [f.strip().lower() for f in (rule.get("ai_class_filters") or []) if f]
                resp_filters = [f.strip().lower() for f in (rule.get("response_filters") or []) if f]
                status_filters = [f.strip().lower() for f in (rule.get("status_filters") or []) if f]

                has_4d_filters = bool(ct_filters or ai_filters or resp_filters or status_filters)
                match = True

                if has_4d_filters:
                    if ct_filters and "all types" not in ct_filters and "all" not in ct_filters:
                        raw_ct = getattr(call, "call_type", None) or getattr(call, "direction", "outbound")
                        call_type = (raw_ct or "outbound").lower()
                        if not any(f in call_type for f in ct_filters):
                            match = False

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
                    # Empty filters -> match all completed calls
                    match = True

                if match:
                    matched_rule = rule
                    break

            if not matched_rule:
                print(f"[EmailAutomation] No matching rule for Call {call_id} (cat={cat}, status={call_status}).")
                return None

            # 4. Resolve template content
            template_id = matched_rule.get("template_id", "general_followup")
            custom_subject = matched_rule.get("custom_subject", "")
            custom_body = matched_rule.get("custom_body", "")

            template = next((t for t in EMAIL_AUTOMATION_TEMPLATES if t["id"] == template_id), None)

            if template and template_id != "custom":
                raw_subject = custom_subject or template["subject"]
                raw_body = custom_body or template["body"]
            else:
                raw_subject = custom_subject or "Following up from our call"
                raw_body = (
                    custom_body
                    or "Hi {{name}},\n\nThank you for speaking with us about {{campaign_name}}.\n\nBest regards,\nThe {{campaign_name}} Team"
                )

            # 5. Personalize
            customer_name = (
                (contact.customer_name if (contact and contact.customer_name) else None)
                or (contact.name if contact else None)
                or "there"
            )
            variables = {
                "name": customer_name,
                "customer_name": customer_name,
                "phone": (contact.phone if contact else call.phone) or "",
                "campaign_name": campaign.campaign_name,
                "appointment_date": (contact.appointment_date if contact else "") or "",
                "appointment_time": (contact.appointment_time if contact else "") or "",
            }

            subject = resolve_email_personalization(raw_subject, variables)
            body_text = resolve_email_personalization(raw_body, variables)

            # 6. Send via connected User SMTP Mailbox (or fallback)
            try:
                from app.models.user_smtp_config import UserSmtpConfig
                from app.services.smtp_mailbox_service import smtp_mailbox_service
                from sqlalchemy import select, and_

                smtp_config = None
                if campaign.user_id:
                    # 1. Try matching rule-specific or automation config sender_email
                    target_email = (
                        matched_rule.get("from_email")
                        or automation_config.get("sender_email")
                        or automation_config.get("from_email")
                    )
                    if target_email:
                        smtp_config = await smtp_mailbox_service.get_user_smtp_config_by_email(
                            db, user_id=campaign.user_id, sender_email=target_email
                        )

                    # 2. If not found, lookup user's default/active verified SMTP config
                    if not smtp_config:
                        stmt = (
                            select(UserSmtpConfig)
                            .where(
                                and_(
                                    UserSmtpConfig.user_id == campaign.user_id,
                                    UserSmtpConfig.is_active == True,
                                    UserSmtpConfig.is_verified == True,
                                )
                            )
                            .order_by(UserSmtpConfig.is_default.desc(), UserSmtpConfig.id.asc())
                            .limit(1)
                        )
                        res = await db.execute(stmt)
                        smtp_config = res.scalars().first()

                from_name = (
                    matched_rule.get("from_name")
                    or automation_config.get("from_name")
                    or (campaign.from_name if hasattr(campaign, "from_name") else None)
                    or (smtp_config.sender_name if smtp_config else None)
                    or "Follow-up Team"
                )
                reply_to = (
                    matched_rule.get("reply_to")
                    or automation_config.get("reply_to")
                    or (smtp_config.sender_email if smtp_config else None)
                )

                body_html = wrap_email_html(
                    body_text,
                    subject=subject,
                    sender_name=from_name,
                    campaign_name=campaign.campaign_name,
                )

                if smtp_config:
                    # Dispatched directly through client's connected authenticated mailbox (Gmail / Outlook / Zoho / Custom SMTP)
                    await smtp_mailbox_service.send_email_via_smtp(
                        smtp_config=smtp_config,
                        to_email=dest_email,
                        subject=subject,
                        html_content=body_html,
                        from_name=from_name,
                        reply_to=reply_to,
                    )
                    print(
                        f"[EmailAutomation] Email sent to {dest_email} via user SMTP ({smtp_config.sender_email}) for Call {call_id} | template={template_id}"
                    )
                    return {
                        "success": True,
                        "call_id": call_id,
                        "to": dest_email,
                        "sender": smtp_config.sender_email,
                        "sender_name": from_name,
                        "method": "smtp",
                        "subject": subject,
                        "template_id": template_id,
                    }
                else:
                    err_msg = (
                        f"No active verified SMTP mailbox configured for user {campaign.user_id}. "
                        "Email automation skipped. Please connect your SMTP mailbox in Connected Mailboxes."
                    )
                    print(f"[EmailAutomation] {err_msg}")
                    return {"success": False, "call_id": call_id, "error": err_msg}
            except Exception as e:
                print(f"[EmailAutomation] Failed to send email for Call {call_id}: {e}")
                return {"success": False, "call_id": call_id, "error": str(e)}
