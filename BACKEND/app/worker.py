import asyncio
import socket
import sys

import app.models
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.job import Job
from app.services.queue_service import QueueService


async def worker():

    print("Worker started")

    while True:

        async with AsyncSessionLocal() as db:

            result = await db.execute(
                select(Job)
                .where(Job.status.in_(["queued", "processing"]))
                .order_by(Job.id)
            )

            job = result.scalars().first()

            if job is None:
                print("Found 0 active jobs")

            else:
                # Save the ID before any database operations
                job_id = job.id

                try:
                    print(f"Processing Job {job_id}")

                    job.status = "processing"
                    await db.commit()

                    call_started = await QueueService.process_job(
                        db=db,
                        job_id=job_id,
                    )

                    if not call_started:
                        print(f"Job {job_id} completed")

                except Exception as e:
                    # Reset the SQLAlchemy session
                    await db.rollback()

                    print(f"Error processing job {job_id}: {e}")

                    # Reload the job after rollback
                    job = await db.get(Job, job_id)

                    if job:
                        job.status = "failed"
                        await db.commit()

        await asyncio.sleep(1)


if __name__ == "__main__":
    # Prevent multiple worker processes from running simultaneously
    try:
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        lock_socket.bind(('127.0.0.1', 59153))
    except socket.error:
        print("Error: Another instance of the worker is already running.")
        print("Please stop it before starting a new one to prevent duplicate processing.")
        sys.exit(1)

    asyncio.run(worker())