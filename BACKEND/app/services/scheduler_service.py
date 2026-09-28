import asyncio
from datetime import datetime, timezone, timedelta
import os
from typing import Optional

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.user import User
from app.models.saved_contact import SavedContact
from app.services.campaign_service import CampaignService


SCHEDULER_POLL_INTERVAL = int(os.getenv("SCHEDULER_POLL_INTERVAL", "15"))


class SchedulerService:
    """
    Decoupled background campaign & automation scheduler.
    Extracted from the HTTP API tier so that web instances remain purely stateless
    and background polling runs exclusively within the worker tier.
    """

    @classmethod
    async def poll_scheduled_campaigns(cls, db) -> None:
        """
        Check for scheduled campaigns that are either:
        1. ~2 minutes away from launch (send pre-start notification email)
        2. Ready to launch (queue campaign job and start dispatching)
        """
        result = await db.execute(
            select(Campaign).where(Campaign.status == "scheduled")
        )
        campaigns = result.scalars().all()
        now = datetime.now(timezone.utc)

        for campaign in campaigns:
            try:
                if not campaign.schedule_date:
                    continue

                iso_str = campaign.schedule_date.replace("Z", "+00:00")
                schedule_dt = datetime.fromisoformat(iso_str)
                if schedule_dt.tzinfo is None:
                    schedule_dt = schedule_dt.replace(tzinfo=timezone.utc)

                # 1. Check if campaign is ~2 minutes away from launch (Pre-start email alert)
                if (
                    schedule_dt > now
                    and (schedule_dt - now) <= timedelta(minutes=2)
                    and not getattr(campaign, "pre_start_notified", False)
                ):
                    campaign.pre_start_notified = True
                    if campaign.user_id:
                        user = await db.get(User, campaign.user_id)
                        if user and user.email:
                            c_res = await db.execute(
                                select(Contact).where(Contact.campaign_id == campaign.id)
                            )
                            contacts = c_res.scalars().all()
                            try:
                                from app.services.email_service import email_service
                                asyncio.create_task(
                                    asyncio.to_thread(
                                        email_service.send_campaign_started_email,
                                        to_email=user.email,
                                        user_name=user.full_name or "Client",
                                        campaign_name=campaign.campaign_name,
                                        total_contacts=len(contacts),
                                        agent_name=campaign.agent or "AI Voice Agent",
                                        is_pre_alert=True,
                                    )
                                )
                                print(f"[SchedulerService] Sent 2-min pre-launch alert to {user.email} for '{campaign.campaign_name}'")
                            except Exception as email_err:
                                print(f"[SchedulerService] Failed sending pre-launch alert: {email_err}")
                    await db.commit()

                # 2. Time arrived, queue and start campaign
                if schedule_dt <= now:
                    c_res = await db.execute(
                        select(Contact).where(Contact.campaign_id == campaign.id)
                    )
                    contacts = c_res.scalars().all()
                    if contacts:
                        print(f"[SchedulerService] Triggering scheduled campaign #{campaign.id} ('{campaign.campaign_name}') with {len(contacts)} contacts")
                        await CampaignService.queue_campaign_job(db, campaign, len(contacts))
            except Exception as e:
                print(f"[SchedulerService] Error processing campaign #{campaign.id}: {e}")

    @classmethod
    async def poll_whatsapp_schedules(cls) -> None:
        """Process due scheduled WhatsApp broadcasts."""
        try:
            from app.services.whatsapp_scheduler_service import WhatsAppSchedulerService
            await WhatsAppSchedulerService.process_due_jobs()
        except Exception as wa_err:
            print(f"[SchedulerService] WhatsApp scheduler error: {wa_err}")

    @classmethod
    async def poll_google_sheet_sync(cls, db, now: datetime) -> None:
        """Periodic Google Sheet Auto-Sync (runs every ~5 minutes)."""
        if int(now.timestamp()) % 300 < SCHEDULER_POLL_INTERVAL:
            try:
                sheet_stmt = select(
                    SavedContact.user_id,
                    SavedContact.tag,
                    SavedContact.metadata_fields,
                ).where(
                    SavedContact.source == "Google Sheet",
                    SavedContact.metadata_fields.isnot(None),
                )
                sheet_res = await db.execute(sheet_stmt)
                seen_pairs = set()
                for u_id, tag_name, mf in sheet_res.all():
                    if (u_id, tag_name) in seen_pairs:
                        continue
                    seen_pairs.add((u_id, tag_name))
                    if isinstance(mf, dict):
                        raw_url = mf.get("google_sheet_url") or mf.get("sheet_url")
                        if raw_url and tag_name and u_id:
                            sheet_url = str(raw_url).strip()
                            from app.services.google_sheet_service import sync_google_sheet_for_tag
                            try:
                                await sync_google_sheet_for_tag(db, int(u_id), str(tag_name), sheet_url)
                                print(f"[SchedulerService] Synced Google Sheet tag '{tag_name}' for user {u_id}")
                            except Exception as gs_err:
                                print(f"[SchedulerService] Error syncing sheet for tag '{tag_name}': {gs_err}")
            except Exception as gs_loop_err:
                print(f"[SchedulerService] Google Sheet loop error: {gs_loop_err}")

    @classmethod
    async def run_scheduler_loop(cls, poll_interval: Optional[int] = None) -> None:
        """
        Continuous scheduler poller loop designed to run inside the worker tier.
        """
        interval = poll_interval or SCHEDULER_POLL_INTERVAL
        print(f"[SchedulerService] Background scheduler poller started (interval: {interval}s)")

        while True:
            try:
                now = datetime.now(timezone.utc)
                async with AsyncSessionLocal() as db:
                    # 1. Scheduled campaigns
                    await cls.poll_scheduled_campaigns(db)

                    # 2. Periodic Google Sheet sync
                    await cls.poll_google_sheet_sync(db, now)

                # 3. Scheduled WhatsApp broadcasts
                await cls.poll_whatsapp_schedules()

            except asyncio.CancelledError:
                print("[SchedulerService] Scheduler loop cancelled.")
                break
            except Exception as loop_err:
                print(f"[SchedulerService] Poller loop error: {loop_err}")

            await asyncio.sleep(interval)
