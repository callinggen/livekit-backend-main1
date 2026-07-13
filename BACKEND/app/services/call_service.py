from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.contact import Contact
from app.models.job import Job
from app.models.campaign import Campaign


class CallService:

    @staticmethod
    async def complete_call(
        db: AsyncSession,
        call_id: int,
        transcript: Optional[str] = None,
        customer_name: Optional[str] = None,
        appointment_date: Optional[str] = None,
        appointment_time: Optional[str] = None,
    ):
        call = await db.get(Call, call_id)

        if call is None:
            return None

        # Prevent double completion
        if call.status == "completed":
            return call

        # ── Timestamps & duration ──────────────────────────────
        call.status = "completed"
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # store as naive UTC to match existing rows
        call.ended_at = now
        if call.started_at:
            call.duration = int((now - call.started_at).total_seconds())

        # ── Transcript ────────────────────────────────────────────────
        if transcript:
            call.transcript = transcript

        # ── Contact ───────────────────────────────────────────────────
        contact = await db.get(Contact, call.contact_id)
        if contact:
            contact.status = "completed"
            contact.duration = str(call.duration)
            if transcript:
                contact.transcript = transcript
            if customer_name:
                contact.customer_name = customer_name
                # BUG-023: Do NOT overwrite response with customer_name.
                # Set a meaningful response label instead.
                if not contact.response:
                    contact.response = "Answered"
            if appointment_date:
                contact.appointment_date = appointment_date
                contact.response = "Appointment booked"
            if appointment_time:
                contact.appointment_time = appointment_time

        # ── Job / Campaign ────────────────────────────────────────────
        job = await db.get(Job, call.job_id)
        if job:
            job.completed_contacts += 1

            if job.completed_contacts >= job.total_contacts:
                job.status = "completed"
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

                campaign = await db.get(Campaign, job.campaign_id)
                if campaign:
                    campaign.status = "completed"

        await db.commit()

        return call

    @staticmethod
    async def fail_call(
        db: AsyncSession,
        call_id: int,
    ):
        """
        Mark a call as failed and advance the campaign to the next contact.
        Called when a SIP dial attempt fails (no answer, trunk error, etc.).
        """
        call = await db.get(Call, call_id)
        if call is None:
            return None

        if call.status in ("completed", "failed"):
            return call

        call.status = "failed"
        call.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)

        contact = await db.get(Contact, call.contact_id)
        if contact:
            contact.status = "failed"

        job = await db.get(Job, call.job_id)
        if job:
            job.failed_contacts += 1

            # If all contacts are accounted for, close the job
            accounted = job.completed_contacts + job.failed_contacts
            if accounted >= job.total_contacts:
                job.status = "completed"
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

                campaign = await db.get(Campaign, job.campaign_id)
                if campaign:
                    campaign.status = "completed"

        await db.commit()
        return call