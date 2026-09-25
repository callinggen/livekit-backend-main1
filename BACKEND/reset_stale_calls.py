"""
reset_stale_calls.py
--------------------
One-shot utility: marks any call stuck in 'dialing' or 'in_progress' as
'failed' so the worker can move on immediately.

Run once:
    python reset_stale_calls.py
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.call import Call
from app.models.contact import Contact
from app.models.job import Job


async def reset():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Call).where(Call.status.in_(["dialing", "in_progress"]))
        )
        stale_calls = result.scalars().all()

        if not stale_calls:
            print("No stale calls found — nothing to do.")
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for call in stale_calls:
            age = now - (call.started_at or now)
            print(f"Resetting Call {call.id} (status={call.status}, age={int(age.total_seconds())}s)")

            call.status = "failed"
            call.ended_at = now
            if call.billing_status == "pending":
                call.billing_status = "not_billable"

            # Mark contact as failed (NOT pending) so the worker does NOT retry it
            contact = await db.get(Contact, call.contact_id)
            if contact:
                contact.status = "failed"
                print(f"  -> Contact {contact.id} marked as 'failed'")

            # Update job failed counter
            job = await db.get(Job, call.job_id)
            if job:
                job.failed_contacts += 1
                print(f"  -> Job {job.id} failed_contacts = {job.failed_contacts}")

        await db.commit()
        print(f"\nDone — reset {len(stale_calls)} stale call(s).")


if __name__ == "__main__":
    asyncio.run(reset())
