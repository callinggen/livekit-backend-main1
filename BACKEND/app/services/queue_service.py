from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.livekit_service import make_livekit_call
from app.services.call_service import CallService
from app.models.job import Job
from app.models.contact import Contact
from app.models.call import Call
from app.models.campaign import Campaign

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

        # ── Watchdog: Check for any stuck calls in this job > 60s ────────────
        result = await db.execute(
            select(Call).where(
                Call.job_id == job.id,
                Call.status.in_(["dialing", "in_progress"]),
            )
        )
        active_call = result.scalars().first()

        if active_call is not None:
            import os
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            call_age = now - (active_call.started_at or now)
            
            print("-" * 50)
            print("QUEUE SERVICE: Active Call Check")
            print(f"PID: {os.getpid()}")
            print(f"DATABASE_URL: {os.getenv('DATABASE_URL')}")
            print(f"Engine URL: {db.bind.url if db.bind else 'N/A'}")
            print(f"Job ID: {job_id}")
            print(f"Active Call ID: {active_call.id} | Status: {active_call.status} | Age: {int(call_age.total_seconds())}s")
            print("-" * 50)

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
                # Fall through to pick the next contact or finish the job
            else:
                print(f"Call {active_call.id} still in progress ({int(call_age.total_seconds())}s), waiting...")
                return True

        print(f"Loaded Job {job.id}")
        result = await db.execute(
            select(Contact).where(
                Contact.campaign_id == job.campaign_id,
                Contact.status == "pending",
            )
        )

        contact = result.scalars().first()

        if contact is None:
            # Check if there are any contacts currently being called
            calling_res = await db.execute(
                select(Contact).where(
                    Contact.campaign_id == job.campaign_id,
                    Contact.status == "calling",
                )
            )
            calling_contact = calling_res.scalars().first()
            if calling_contact is not None:
                print(f"Contact {calling_contact.id} is still in 'calling' state, waiting...")
                return True

            print("No pending contacts, no active calling contacts, and no active calls.")
            job.status = "completed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            
            campaign = await db.get(Campaign, job.campaign_id)
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