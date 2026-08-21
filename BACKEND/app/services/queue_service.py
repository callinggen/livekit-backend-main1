from datetime import datetime, timezone, timedelta
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.livekit_service import make_livekit_call
from app.services.call_service import CallService
from app.models.job import Job
from app.models.contact import Contact
from app.models.call import Call
from app.models.campaign import Campaign
from app.models.user_phone_number import UserPhoneNumber

# If a call stays in dialing/in_progress longer than this, treat it as
# failed (agent crashed, room was deleted, SIP trunk timed out, user declined, etc.)
CALL_TIMEOUT_MINUTES = 3  # 3-minute watchdog timeout for in_progress calls


class QueueService:

    @staticmethod
    async def process_job(
        db: AsyncSession,
        job_id: int,
    ):

        job = await db.get(
            Job,
            job_id,
        )

        if job is None:
            print("Job not found")
            return False

        # ── Dynamic user telephony lookup (must happen before concurrency check) ──
        campaign = await db.get(Campaign, job.campaign_id)
        user_phone: UserPhoneNumber | None = None
        sip_trunk_id: str | None = None
        sip_call_from: str | None = None

        if campaign and campaign.user_id:
            num_stmt = (
                select(UserPhoneNumber)
                .where(UserPhoneNumber.user_id == campaign.user_id)
                .where(UserPhoneNumber.is_active == True)
                .order_by(UserPhoneNumber.is_default.desc(), UserPhoneNumber.id.asc())
            )
            phone_res = await db.execute(num_stmt)
            user_phone = phone_res.scalars().first()

            if user_phone:
                if user_phone.sip_id:
                    sip_trunk_id = user_phone.sip_id
                if user_phone.phone_number:
                    sip_call_from = user_phone.phone_number
                print(
                    f"Using assigned dynamic telephony for User #{campaign.user_id}: "
                    f"Phone={sip_call_from}, SIP_Trunk={sip_trunk_id}, "
                    f"Max Concurrent={user_phone.max_concurrent_calls}"
                )
            else:
                print(
                    f"[WARN] No active phone number assigned to User #{campaign.user_id}. "
                    "Call dispatch will use global env fallback — assign a phone number via Admin to enable dynamic calling."
                )

        # ── Watchdog & Concurrency Check: Check all active calls in dialing/in_progress ────
        result = await db.execute(
            select(Call).where(
                Call.job_id == job.id,
                Call.status.in_(["dialing", "in_progress"]),
            )
        )
        active_calls = result.scalars().all()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        valid_active_calls = []

        for active_call in active_calls:
            call_age = now - (active_call.started_at or now)
            timeout_triggered = False
            if active_call.status == "dialing" and call_age > timedelta(seconds=60):
                timeout_triggered = True
            elif active_call.status == "in_progress" and call_age > timedelta(minutes=CALL_TIMEOUT_MINUTES):
                timeout_triggered = True

            if timeout_triggered:
                print(
                    f"Call {active_call.id} has been in '{active_call.status}' "
                    f"for {int(call_age.total_seconds())}s — marking as failed (timeout)."
                )
                await CallService.fail_call(db=db, call_id=active_call.id)
            else:
                valid_active_calls.append(active_call)

        active_calls_count = len(valid_active_calls)

        # Use per-line max_concurrent_calls if available, else global env fallback
        if user_phone and getattr(user_phone, "max_concurrent_calls", None):
            max_concurrency = user_phone.max_concurrent_calls
        else:
            max_concurrency = int(os.getenv("MAX_CONCURRENT_CALLS", "3"))

        if active_calls_count >= max_concurrency:
            print(
                f"Job {job_id} reached max concurrency "
                f"({active_calls_count}/{max_concurrency} active calls). Waiting for a call slot..."
            )
            return True

        print(f"Loaded Job {job.id} (Active calls: {active_calls_count}/{max_concurrency})")
        result = await db.execute(
            select(Contact).where(
                Contact.campaign_id == job.campaign_id,
                Contact.status == "pending",
            )
        )

        contact = result.scalars().first()

        if contact is None:
            if active_calls_count > 0:
                print(f"No more pending contacts, but {active_calls_count} active call(s) still in progress. Waiting...")
                return True

            print("No pending contacts and no active calls remaining.")
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

            if campaign:
                campaign.status = "completed"

            await db.commit()

            return False

        print("-" * 50)
        print(f"Processing Contact {contact.id}")
        print(f"Name : {contact.name}")
        print(f"Phone: {contact.phone}")
        contact.status = "calling"
        await db.commit()

        call = Call(
            job_id=job.id,
            contact_id=contact.id,
            phone=contact.phone,
            status="dialing",
        )
        db.add(call)

        await db.commit()
        await db.refresh(call)

        # Every call gets its own LiveKit room
        room_name = f"call-{call.id}"
        call.room_name = room_name
        await db.commit()

        print(f"Room Name : {room_name}")
        print("Status -> calling")

        result = await make_livekit_call(
            phone=contact.phone,
            room_name=room_name,
            sip_trunk_id=sip_trunk_id,
            sip_call_from=sip_call_from,
        )

        if result["success"]:
            call.status = "in_progress"
            call.livekit_participant_id = (
                str(result["participant_id"])
                if result["participant_id"] is not None
                else None
            )
            await db.commit()
            print(f"Call {call.id} started")

        else:
            # SIP dial failed — mark call + contact as failed and advance.
            print(f"SIP dial failed for call {call.id}: {result.get('error')}")
            await CallService.fail_call(db=db, call_id=call.id)

        return True