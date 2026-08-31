"""
Email Campaign Service
Handles creation, launching, and bulk-sending of email marketing campaigns.
"""
import asyncio
from datetime import datetime, timezone
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.email_campaign import EmailCampaign
from app.models.email_contact import EmailContact
from app.schemas.email_campaign import EmailCampaignCreate
from app.services.email_service import email_service


class EmailCampaignService:

    @staticmethod
    async def create_campaign(
        db: AsyncSession,
        data: EmailCampaignCreate,
        user_id: int,
    ) -> EmailCampaign:
        """Create a new email campaign (status=draft) with all its contacts."""
        from_email_val = None
        if data.from_email and data.from_email.strip():
            raw_from_email = data.from_email.strip()
            clean_email = (
                raw_from_email.split("<")[1].split(">")[0].strip()
                if "<" in raw_from_email and ">" in raw_from_email
                else raw_from_email.strip()
            )
            email_domain = clean_email.split("@")[-1].lower().strip()
            
            # Check if using custom domain vs default platform domain
            default_from = email_service.from_email
            default_domain = (
                default_from.split("@")[-1].replace(">", "").strip().lower()
                if "@" in default_from
                else "callinggen.in"
            )
            if email_domain != default_domain and email_domain != "callinggen.in":
                from app.models.custom_domain import CustomEmailDomain
                from sqlalchemy import and_
                stmt = select(CustomEmailDomain).where(
                    and_(
                        CustomEmailDomain.user_id == user_id,
                        CustomEmailDomain.domain == email_domain,
                        CustomEmailDomain.is_verified == True,
                    )
                )
                verified_dom = (await db.execute(stmt)).scalars().first()
                if not verified_dom:
                    raise ValueError(
                        f"Domain '@{email_domain}' is not verified for sending on your account. "
                        "Please add and verify this domain in Sending Domains first."
                    )
            from_email_val = clean_email

        campaign = EmailCampaign(
            user_id=user_id,
            name=data.name,
            subject=data.subject,
            html_body=data.html_body,
            from_name=data.from_name,
            from_email=from_email_val,
            reply_to=data.reply_to,
            schedule_date=data.schedule_date,
            schedule_time=data.schedule_time,
            status="draft",
        )
        db.add(campaign)
        await db.flush()  # get campaign.id

        for contact in data.contacts:
            ec = EmailContact(
                email_campaign_id=campaign.id,
                name=contact.name,
                email=contact.email,
                status="pending",
            )
            db.add(ec)

        await db.commit()
        await db.refresh(campaign)
        return campaign

    @staticmethod
    async def launch_campaign(db: AsyncSession, campaign_id: int) -> EmailCampaign:
        """
        Launch an email campaign immediately.
        Sets status=running, sends emails in background, updates per-contact status.
        """
        campaign = await db.get(EmailCampaign, campaign_id)
        if campaign is None:
            raise ValueError(f"Email campaign {campaign_id} not found")

        if campaign.status in ("running", "completed"):
            raise ValueError(f"Campaign is already {campaign.status}")

        # Mark as running
        campaign.status = "running"
        await db.commit()

        # Load contacts
        result = await db.execute(
            select(EmailContact).where(EmailContact.email_campaign_id == campaign_id)
        )
        contacts: List[EmailContact] = list(result.scalars().all())

        # Fire-and-forget bulk send in background
        asyncio.create_task(
            EmailCampaignService._bulk_send(campaign_id, campaign, contacts)
        )

        return campaign

    @staticmethod
    async def _bulk_send(
        campaign_id: int,
        campaign: EmailCampaign,
        contacts: List[EmailContact],
    ):
        """
        Send emails to all contacts, throttled at ~5/sec, updating status in a
        fresh DB session per contact to avoid session conflicts.
        """
        from app.database import AsyncSessionLocal

        sent = 0
        failed = 0

        for contact in contacts:
            try:
                personalized_body = EmailCampaignService._personalize(
                    campaign.html_body,
                    contact_name=contact.name,
                    contact_email=contact.email,
                )
                personalized_subject = EmailCampaignService._personalize(
                    campaign.subject,
                    contact_name=contact.name,
                    contact_email=contact.email,
                )
                final_html_body = EmailCampaignService._wrap_if_fragment(
                    personalized_body,
                    title=personalized_subject,
                    subtitle=campaign.from_name or "AI Voice Calling & Automation Platform"
                )
                email_service.send_marketing_email(
                    to_email=contact.email,
                    subject=personalized_subject,
                    html_content=final_html_body,
                    from_name=campaign.from_name,
                    from_email=campaign.from_email,
                    reply_to=campaign.reply_to,
                )
                async with AsyncSessionLocal() as db:
                    ec = await db.get(EmailContact, contact.id)
                    if ec:
                        ec.status = "sent"
                        ec.sent_at = datetime.now(timezone.utc)
                        await db.commit()
                sent += 1
            except Exception as e:
                async with AsyncSessionLocal() as db:
                    ec = await db.get(EmailContact, contact.id)
                    if ec:
                        ec.status = "failed"
                        ec.error_message = str(e)[:500]
                        await db.commit()
                failed += 1

            # Throttle between sends
            await asyncio.sleep(0.5)

        # Update campaign totals + mark completed
        async with AsyncSessionLocal() as db:
            c = await db.get(EmailCampaign, campaign_id)
            if c:
                c.total_sent = sent
                c.total_failed = failed
                c.status = "completed" if failed < len(contacts) else "failed"
                await db.commit()

    @staticmethod
    def _personalize(
        text: str,
        contact_name: str = "",
        contact_email: str = "",
        company_name: str = "",
    ) -> str:
        """Replace personalization variables safely."""
        if not text:
            return ""
        name_val = contact_name.strip() if contact_name else "there"
        email_val = contact_email.strip() if contact_email else ""
        company_val = company_name.strip() if company_name else "Your Company"

        result = text.replace("{{name}}", name_val).replace("{{Name}}", name_val)
        result = result.replace("{{email}}", email_val).replace("{{Email}}", email_val)
        result = result.replace("{{company}}", company_val).replace("{{Company}}", company_val)
        return result

    @staticmethod
    def _wrap_if_fragment(
        body: str,
        title: str = "",
        subtitle: str = "AI Voice Calling & Automation Platform",
    ) -> str:
        """Wrap raw body fragments into CallingGen's modern SaaS card layout if not already full HTML."""
        if not body:
            return ""
        if "<!DOCTYPE" in body or "<html" in body:
            return body

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
        table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
        img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
        @media screen and (max-width: 620px) {{
            .container {{ width: 100% !important; border-radius: 0 !important; }}
            .content-padding {{ padding: 24px 18px !important; }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 36px 10px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #334155; -webkit-font-smoothing: antialiased; line-height: 1.6;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc;">
        <tr>
            <td align="center">
                <table role="presentation" class="container" width="560" border="0" cellspacing="0" cellpadding="0" style="max-width: 560px; width: 100%; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.06); text-align: left;">
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #ffffff; padding: 28px 32px 20px 32px; text-align: center; border-bottom: 2px solid #2563eb;">
                            <div style="font-size: 26px; font-weight: 800; letter-spacing: -0.5px; color: #0f172a;">
                                Calling<span style="color: #2563eb;">Gen</span>
                            </div>
                            <div style="font-size: 11px; color: #64748b; margin-top: 4px; letter-spacing: 1.2px; text-transform: uppercase; font-weight: 600;">
                                {subtitle}
                            </div>
                        </td>
                    </tr>
                    
                    <!-- Content Area -->
                    <tr>
                        <td class="content-padding" style="padding: 32px 32px 28px 32px; font-size: 14.5px; color: #334155; line-height: 1.65;">
                            {body}
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; padding: 20px 32px; border-top: 1px solid #f1f5f9; text-align: center;">
                            <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748b;">
                                &copy; 2026 CallingGen Inc. All rights reserved.
                            </p>
                            <p style="margin: 0; font-size: 11px; color: #94a3b8;">
                                Sent via <a href="https://callinggen.in" style="color: #2563eb; text-decoration: none; font-weight: 600;">CallingGen</a> &bull; <a href="#" style="color: #94a3b8; text-decoration: underline;">Unsubscribe</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

