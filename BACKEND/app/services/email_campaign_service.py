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
        campaign = EmailCampaign(
            user_id=user_id,
            name=data.name,
            subject=data.subject,
            html_body=data.html_body,
            from_name=data.from_name,
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
                personalized = EmailCampaignService._personalize(
                    campaign.html_body, contact.name
                )
                # Determine From header
                from_header = (
                    f"{campaign.from_name} <{email_service.smtp_from}>"
                    if campaign.from_name
                    else email_service.smtp_from
                )
                email_service._send_email(
                    to_email=contact.email,
                    subject=campaign.subject,
                    body=personalized,
                    is_html=True,
                    from_override=from_header,
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

            # Throttle: ~5 emails per second to avoid SMTP rate limits
            await asyncio.sleep(0.2)

        # Update campaign totals + mark completed
        async with AsyncSessionLocal() as db:
            c = await db.get(EmailCampaign, campaign_id)
            if c:
                c.total_sent = sent
                c.total_failed = failed
                c.status = "completed" if failed < len(contacts) else "failed"
                await db.commit()

    @staticmethod
    def _personalize(html_body: str, contact_name: str) -> str:
        """Replace {{name}} placeholder with the contact's name."""
        return html_body.replace("{{name}}", contact_name).replace("{{Name}}", contact_name)
