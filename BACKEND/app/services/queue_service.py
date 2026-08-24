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
CALL_TIMEOUT_MINUTES = 30  # 3-minute watchdog timeout for in_progress calls


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
            elif active_call.status == "in_progress":
                if active_call.room_name:
                    try:
                        from livekit import api
                        lk_url = os.getenv("LIVEKIT_URL", "").replace("ws://", "http://").replace("wss://", "https://")
                        lkapi = api.LiveKitAPI(
                            url=lk_url,
                            api_key=os.getenv("LIVEKIT_API_KEY"),
                            api_secret=os.getenv("LIVEKIT_API_SECRET")
                        )
                        try:
                            req = api.ListRoomsRequest(names=[active_call.room_name])
                            res = await lkapi.room.list_rooms(list=req)
                            if not res.rooms:
                                # Room does not exist. If call is old enough to not be a startup race condition, clean it up.
                                if call_age > timedelta(minutes=2):
                                    print(f"Watchdog: Room '{active_call.room_name}' does not exist but call {active_call.id} is in_progress. Failing call.")
                                    timeout_triggered = True
                            else:
                                # Room exists. Keep call active indefinitely as long as room is alive.
                                pass
                        finally:
                            await lkapi.aclose()
                    except Exception as e:
                        print(f"Watchdog LiveKit room lookup error for call {active_call.id}: {e}")
                        # Room lookup error -> do not immediately fail. Just wait and retry later.
                else:
                    # No room name? Very rare, but fallback to absolute timeout
                    if call_age > timedelta(minutes=CALL_TIMEOUT_MINUTES):
                        timeout_triggered = True

            if timeout_triggered:
                if not active_call.sip_was_active:
                    print(
                        f"Call {active_call.id} has been in '{active_call.status}' "
                        f"for {int(call_age.total_seconds())}s without answering — marking as no_answer."
                    )
                    await CallService.fail_call(db=db, call_id=active_call.id)
                else:
                    print(
                        f"Call {active_call.id} has been in '{active_call.status}' "
                        f"for {int(call_age.total_seconds())}s (was active) — marking as failed (timeout)."
                    )
                    await CallService.fail_call(db=db, call_id=active_call.id, failure_reason="timeout_error")
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

        campaign = await db.get(Campaign, job.campaign_id)
        if campaign and campaign.user_id:
            from app.models.user import User
            user = await db.get(User, campaign.user_id)
            if user and user.credits < 15:
                print(f"User {user.id} has insufficient credits ({user.credits} < 15). Failing contact {contact.id} and pausing campaign.")
                contact.status = "failed"
                contact.response = "Insufficient Credits"
                job.status = "paused"
                campaign.status = "paused"
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

        # Dynamic user assigned telephony lookup
        sip_trunk_id = None
        sip_call_from = None
        campaign = await db.get(Campaign, job.campaign_id)
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
                print(f"Using assigned dynamic telephony for User #{campaign.user_id}: Phone={sip_call_from}, SIP_Trunk={sip_trunk_id}")

        # ── [CALL DB READY] VERIFICATION ──────────────────────────────────────
        # Ensure the row is committed and readable before dispatching to LiveKit
        from app.database import AsyncSessionLocal
        db_exists = False
        async with AsyncSessionLocal() as verify_db:
            verify_call = await verify_db.get(Call, call.id)
            if verify_call:
                db_exists = True

        print(f"\n[CALL DB READY]")
        print(f"call_id={call.id}")
        print(f"exists={db_exists}")
        print(f"job_id={job.id}")
        print(f"campaign_id={job.campaign_id}\n")

        if not db_exists:
            print(f"[FATAL ERROR] Call {call.id} failed DB ready verification. Aborting dispatch.")
            return False
        # ──────────────────────────────────────────────────────────────────────

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
            # SIP dial failed — could be declined, busy, or timeout from LiveKit
            error_str = str(result.get('error', '')).lower()
            print(f"SIP dial failed for call {call.id}: {error_str}")
            
            # Differentiate true system errors from normal telephony outcomes
            is_system_error = "auth" in error_str or "connection" in error_str or "internal" in error_str
            
            if is_system_error:
                await CallService.fail_call(db=db, call_id=call.id, failure_reason="sip_system_error")
            else:
                # Normal no_answer / declined / busy outcome
                # By passing no failure_reason, the classifier correctly maps this to ended/no_answer
                
                # Try to extract a specific outcome from the error message
                outcome_override = "no_answer"
                if "decline" in error_str or "reject" in error_str or "486" in error_str or "603" in error_str:
                    outcome_override = "declined"
                elif "busy" in error_str:
                    outcome_override = "busy"
                    
                await CallService.fail_call(db=db, call_id=call.id, outcome=outcome_override)

        return True
