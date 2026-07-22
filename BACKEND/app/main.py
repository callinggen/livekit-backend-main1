import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.campaigns import router as campaign_router
from app.api.calls import router as call_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
# Ensure recordings directory exists
os.makedirs("recordings", exist_ok=True)

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

SCHEDULER_POLL_INTERVAL = int(os.getenv("SCHEDULER_POLL_INTERVAL", "15"))

from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.services.campaign_service import CampaignService

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start lightweight scheduler
    task = asyncio.create_task(schedule_poller())
    yield
    # Shutdown: Cancel scheduler
    task.cancel()

async def schedule_poller():
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Campaign).where(Campaign.status == "scheduled")
                )
                campaigns = result.scalars().all()
                now = datetime.now(timezone.utc)
                
                for campaign in campaigns:
                    try:
                        iso_str = campaign.schedule_date.replace("Z", "+00:00")
                        schedule_dt = datetime.fromisoformat(iso_str)
                        if schedule_dt <= now:
                            # Time arrived, queue it
                            c_res = await db.execute(select(Contact).where(Contact.campaign_id == campaign.id))
                            contacts = c_res.scalars().all()
                            if contacts:
                                await CampaignService.queue_campaign_job(db, campaign, len(contacts))
                    except Exception as e:
                        print(f"Scheduler error processing campaign {campaign.id}: {e}")
        except Exception as e:
            print(f"Scheduler loop error: {e}")
        
        await asyncio.sleep(SCHEDULER_POLL_INTERVAL)

app = FastAPI(
    title="Calling Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/api/recordings", StaticFiles(directory="recordings"), name="recordings")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(call_router, prefix="/api", tags=["Calls"])
app.include_router(campaign_router, prefix="/api", tags=["Campaigns"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(admin_router, prefix="/api/admin", tags=["Admin"])


@app.get("/")
def home():
    return {
        "status": "running",
        "message": "Backend is working",
    }