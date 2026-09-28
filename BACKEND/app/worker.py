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


# Optional Enterprise APM Integration (Sentry)
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    try:
        import sentry_sdk  # type: ignore # pyright: ignore[reportMissingImports]
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=os.getenv("ENVIRONMENT", "staging"),
            traces_sample_rate=0.2,
        )
        print(f"[Worker] Sentry APM tracking initialized (Env: {os.getenv('ENVIRONMENT', 'staging')})")
    except Exception as e:
        print(f"[Worker] Could not initialize Sentry: {e}")


async def job_worker_loop(shutdown_event: Optional[asyncio.Event] = None):
    """
    Continuous worker loop for campaign call queue dispatching.
    Uses PostgreSQL row-level locking (with_for_update skip_locked) to support
    concurrent worker scaling without single-machine socket lock bottlenecks.
    """
    print("[Worker] Campaign Job Worker started")
    is_postgres = "postgresql" in str(engine.url)

    while shutdown_event is None or not shutdown_event.is_set():
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
                    try:
                        await asyncio.wait_for(
                            shutdown_event.wait() if shutdown_event else asyncio.sleep(1.5),
                            timeout=1.5
                        )
                    except asyncio.TimeoutError:
                        pass
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

    print("[Worker] Job worker loop drained and stopped.")


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

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _on_signal():
        print("\n[Worker] Received termination signal (SIGTERM/SIGINT). Draining in-flight tasks gracefully...")
        shutdown_event.set()

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except NotImplementedError:
                pass

    tasks = []

    if mode in ("all", "worker_only"):
        tasks.append(asyncio.create_task(job_worker_loop(shutdown_event), name="job_worker"))

    if mode in ("all", "scheduler_only"):
        tasks.append(asyncio.create_task(SchedulerService.run_scheduler_loop(), name="scheduler"))

    if not tasks:
        print("[Worker] Error: No tasks selected to run.")
        return

    # Wait until shutdown event is triggered or tasks complete
    try:
        if sys.platform != "win32":
            await shutdown_event.wait()
        else:
            await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print("[Worker] Shutting down worker tier...")
        shutdown_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print("[Worker] Worker tier shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Worker] Process interrupted by user. Exiting.")