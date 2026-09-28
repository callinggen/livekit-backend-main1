import asyncio
import os
import signal
import sys
from typing import Optional

import app.models
from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.job import Job
from app.services.queue_service import QueueService
from app.services.scheduler_service import SchedulerService


async def job_worker_loop():
    """
    Continuous worker loop for campaign call queue dispatching.
    Uses PostgreSQL row-level locking (with_for_update skip_locked) to support
    concurrent worker scaling without single-machine socket lock bottlenecks.
    """
    print("[Worker] Campaign Job Worker started")
    is_postgres = "postgresql" in str(engine.url)

    while True:
        try:
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(Job)
                    .where(Job.status.in_(["queued", "processing"]))
                    .order_by(Job.id)
                )
                if is_postgres:
                    # Concurrency safe: other workers skip locked rows
                    stmt = stmt.with_for_update(skip_locked=True)

                result = await db.execute(stmt)
                job = result.scalars().first()

                if job is None:
                    # No active jobs found; brief rest before next polling tick
                    await asyncio.sleep(1.5)
                    continue

                job_id = job.id
                try:
                    if job.status == "queued":
                        job.status = "processing"
                        await db.commit()

                    call_started = await QueueService.process_job(
                        db=db,
                        job_id=job_id,
                    )

                    if not call_started:
                        print(f"[Worker] Job #{job_id} processing cycle finished.")

                except Exception as job_err:
                    await db.rollback()
                    print(f"[Worker] Error processing job #{job_id}: {job_err}")
                    try:
                        failed_job = await db.get(Job, job_id)
                        if failed_job and failed_job.status == "processing":
                            failed_job.status = "failed"
                            await db.commit()
                    except Exception as status_err:
                        print(f"[Worker] Could not update job #{job_id} status to failed: {status_err}")

        except asyncio.CancelledError:
            print("[Worker] Job worker loop cancelled.")
            break
        except Exception as loop_err:
            print(f"[Worker] Outer worker loop error: {loop_err}")
            await asyncio.sleep(2)

        await asyncio.sleep(1)


async def main():
    mode = os.getenv("WORKER_MODE", "all").lower()
    # Check CLI arguments for overrides
    args = sys.argv[1:]
    if "--worker-only" in args:
        mode = "worker_only"
    elif "--scheduler-only" in args:
        mode = "scheduler_only"

    print("=" * 60)
    print(f"CallingGen Decoupled Worker Tier (Mode: {mode})")
    print(f"Database Engine: {'PostgreSQL (Distributed Row Locking)' if 'postgresql' in str(engine.url) else 'SQLite'}")
    print("=" * 60)

    tasks = []

    if mode in ("all", "worker_only"):
        tasks.append(asyncio.create_task(job_worker_loop(), name="job_worker"))

    if mode in ("all", "scheduler_only"):
        tasks.append(asyncio.create_task(SchedulerService.run_scheduler_loop(), name="scheduler"))

    if not tasks:
        print("[Worker] Error: No tasks selected to run.")
        return

    # Keep running until cancelled
    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("[Worker] Shutting down worker tier...")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print("[Worker] Worker tier shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Worker] Process interrupted by user. Exiting.")