import os
import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.database import AsyncSessionLocal
from app.models.user import User
from app.models.whatsapp_send_job import WhatsAppSendJob
from app.models.whatsapp_send_recipient import WhatsAppSendRecipient
from app.services.whatsapp_credit_service import WhatsAppCreditService
from whatsapp import service as evolution_service
from whatsapp.config import resolve_instance_name


class WhatsAppSchedulerService:
    """
    Background worker service that periodically processes scheduled WhatsApp broadcast jobs.
    Runs every scheduler tick, checks for past/due scheduled jobs, and dispatches them via Evolution API.
    """

    @classmethod
    async def process_due_jobs(cls):
        """
        Poll and execute all WhatsAppSendJob records with status='scheduled' whose scheduled_for has arrived.
        """
        now = datetime.now(timezone.utc)

        try:
            async with AsyncSessionLocal() as db:
                # Find all scheduled jobs
                result = await db.execute(
                    select(WhatsAppSendJob).where(WhatsAppSendJob.status == "scheduled")
                )
                jobs = result.scalars().all()

                for job in jobs:
                    try:
                        # Check if scheduled time has arrived
                        if job.scheduled_for:
                            job_time = job.scheduled_for
                            if job_time.tzinfo is None:
                                job_time = job_time.replace(tzinfo=timezone.utc)
                            if job_time > now:
                                # Not yet due
                                continue

                        print(f"[WhatsAppScheduler] 🚀 Executing scheduled WhatsApp Broadcast Job #{job.id}: '{job.source_name}'")

                        # Mark as in_progress
                        job.status = "in_progress"
                        await db.commit()

                        # Get user
                        user = None
                        if job.user_id:
                            user = await db.get(User, job.user_id)
                        if not user:
                            u_res = await db.execute(select(User).order_by(User.id).limit(1))
                            user = u_res.scalars().first()

                        # Fetch recipients
                        rec_res = await db.execute(
                            select(WhatsAppSendRecipient).where(WhatsAppSendRecipient.send_job_id == job.id)
                        )
                        recipients = rec_res.scalars().all()

                        inst = resolve_instance_name(None)
                        backend_base_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

                        total_sent = 0
                        total_failed = 0
                        total_credits_deducted = 0

                        for rec in recipients:
                            rec_phone = "".join(c for c in (rec.phone or "") if c.isdigit())
                            if len(rec_phone) == 10:
                                rec_phone = "91" + rec_phone

                            rec_name = rec.name or "there"
                            rec_item_statuses = []
                            rec_has_error = False
                            last_error = None

                            # 1. Send Text if present
                            if job.message_text and job.message_text.strip():
                                personalized_text = job.message_text.replace("{{name}}", rec_name).replace("{{customer_name}}", rec_name)
                                try:
                                    res = await evolution_service.send_text_message(
                                        instance_name=inst,
                                        number=rec_phone,
                                        text=personalized_text,
                                    )
                                    if user and user.credits >= WhatsAppCreditService.CREDIT_PER_TEXT:
                                        user.credits -= WhatsAppCreditService.CREDIT_PER_TEXT
                                        total_credits_deducted += WhatsAppCreditService.CREDIT_PER_TEXT
                                        total_sent += 1
                                        rec_item_statuses.append({"type": "text", "status": "sent", "response": res})
                                    else:
                                        rec_item_statuses.append({"type": "text", "status": "failed", "error": "Insufficient credits"})
                                        total_failed += 1
                                        rec_has_error = True
                                except Exception as err:
                                    print(f"[WhatsAppScheduler] Text error for {rec_phone}: {err}")
                                    rec_item_statuses.append({"type": "text", "status": "failed", "error": str(err)})
                                    total_failed += 1
                                    rec_has_error = True
                                    last_error = str(err)

                            # 2. Send Attachments if present
                            if job.attachments and isinstance(job.attachments, list):
                                for att in job.attachments:
                                    if not isinstance(att, dict):
                                        continue
                                    media_url = att.get("url") or ""
                                    if media_url.startswith("/"):
                                        full_media_url = f"{backend_base_url}{media_url}"
                                    else:
                                        full_media_url = media_url

                                    mtype = att.get("type", "document")
                                    item_cost = WhatsAppCreditService.calculate_item_credits(mtype)

                                    try:
                                        res = await evolution_service.send_media_message(
                                            instance_name=inst,
                                            number=rec_phone,
                                            media_url=full_media_url,
                                            media_type=mtype,
                                            mimetype=att.get("mime_type") or ("image/png" if mtype == "image" else "application/pdf"),
                                            caption=att.get("caption"),
                                            file_name=att.get("file_name") or ("image.png" if mtype == "image" else "document.pdf"),
                                        )
                                        if user and user.credits >= item_cost:
                                            user.credits -= item_cost
                                            total_credits_deducted += item_cost
                                            total_sent += 1
                                            rec_item_statuses.append({"type": mtype, "status": "sent", "response": res})
                                        else:
                                            rec_item_statuses.append({"type": mtype, "status": "failed", "error": "Insufficient credits"})
                                            total_failed += 1
                                            rec_has_error = True
                                    except Exception as err:
                                        print(f"[WhatsAppScheduler] Media error for {rec_phone}: {err}")
                                        rec_item_statuses.append({"type": mtype, "status": "failed", "error": str(err)})
                                        total_failed += 1
                                        rec_has_error = True
                                        last_error = str(err)

                            # Update recipient record
                            rec_status = "sent" if not rec_has_error else ("partial" if any(i.get("status") == "sent" for i in rec_item_statuses) else "failed")
                            rec.status = rec_status
                            rec.error_message = last_error
                            rec.details = {"items": rec_item_statuses}
                            rec.sent_at = datetime.now(timezone.utc)

                        # Update Job summary
                        job.sent_count = total_sent
                        job.failed_count = total_failed
                        job.credits_deducted = total_credits_deducted
                        job.status = "completed" if total_failed == 0 else ("partial" if total_sent > 0 else "failed")
                        job.completed_at = datetime.now(timezone.utc)

                        await db.commit()
                        print(f"[WhatsAppScheduler] ✓ Job #{job.id} completed! Sent: {total_sent}, Failed: {total_failed}, Credits: {total_credits_deducted}")

                    except Exception as job_err:
                        print(f"[WhatsAppScheduler] Error executing job #{job.id}: {job_err}")
                        job.status = "failed"
                        await db.commit()

        except Exception as loop_err:
            print(f"[WhatsAppScheduler] Loop error: {loop_err}")
