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
# failed (agent crashed, room was deleted, SIP trunk timed out, etc.)
CALL_TIMEOUT_MINUTES = 10  # BUG-023/029: was 2, too short for real calls


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

        # If a call from this job is still in flight, don't dial another
        # contact yet — just tell the worker to come back later. This is
        # what keeps calls sequential instead of firing them all at once.
        result = await db.execute(
            select(Call).where(
                Call.job_id == job.id,
                Call.status.in_(["dialing", "in_progress"]),
            )
        )
        active_call = result.scalars().first()

        if active_call is not None:
            # Check if the call has been stuck for too long (agent crash,
            # room deleted, SIP timeout, etc.) and auto-fail it.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            call_age = now - (active_call.started_at or now)
            if call_age > timedelta(minutes=CALL_TIMEOUT_MINUTES):
                print(
                    f"Call {active_call.id} has been in '{active_call.status}' "
                    f"for {int(call_age.total_seconds() // 60)} min — marking as failed (timeout)."
                )
                await CallService.fail_call(db=db, call_id=active_call.id)
                # Fall through to pick the next contact on this same tick
            else:
                print(f"Call {active_call.id} still in progress, waiting...")
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
            print("No pending contacts")
            job.status = "completed"

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