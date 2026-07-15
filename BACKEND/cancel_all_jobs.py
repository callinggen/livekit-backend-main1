"""
cancel_all_jobs.py
------------------
Emergency stop: cancels all queued/processing jobs and fails any
in-flight calls so the worker stops placing new calls immediately.

Run:
    python cancel_all_jobs.py
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.call import Call
from app.models.job import Job
from app.models.contact import Contact
from app.models.campaign import Campaign


async def cancel_all():
    async with AsyncSessionLocal() as db:

        # 1. Fail any in-flight calls
        call_result = await db.execute(
            select(Call).where(Call.status.in_(["dialing", "in_progress"]))
        )
        stale_calls = call_result.scalars().all()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for call in stale_calls:
            print(f"Failing Call {call.id} (status={call.status})")
            call.status = "failed"
            call.ended_at = now

            contact = await db.get(Contact, call.contact_id)
            if contact:
                contact.status = "failed"
                print(f"  -> Contact {contact.id} marked failed")

            job = await db.get(Job, call.job_id)
            if job:
                job.failed_contacts += 1

        # 2. Cancel all queued/processing jobs
        job_result = await db.execute(
            select(Job).where(Job.status.in_(["queued", "processing"]))
        )
        active_jobs = job_result.scalars().all()

        for job in active_jobs:
            print(f"Cancelling Job {job.id} (status={job.status})")
            job.status = "cancelled"
            job.finished_at = now

            campaign = await db.get(Campaign, job.campaign_id)
            if campaign:
                campaign.status = "failed"
                print(f"  -> Campaign {campaign.id} ('{campaign.campaign_name}') status set to failed")

        await db.commit()
        print(f"\nDone — failed {len(stale_calls)} call(s), cancelled {len(active_jobs)} job(s).")
        print("Worker will now idle until a new job is queued.")


if __name__ == "__main__":
    asyncio.run(cancel_all())
