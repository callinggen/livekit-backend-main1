import asyncio
import os
import sys

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import AsyncSessionLocal, Base, engine
from app.models.call import Call
from app.models.contact import Contact
from app.models.job import Job
from app.models.campaign import Campaign
from app.services.call_service import CallService

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Call).order_by(Call.id.desc()).limit(1))
        call = res.scalars().first()
        
        if not call:
            print("No call found")
            return
            
        print(f"Testing complete_call on Call ID: {call.id}")
        
        try:
            # First reset it to incomplete if it's already completed
            call.status = "in-progress"
            await db.commit()
            
            result = await CallService.complete_call(
                db=db,
                call_id=call.id,
                transcript=None,
                customer_name=None,
                appointment_date=None,
                appointment_time=None,
                recording_url=f"/api/recordings/call_{call.id}.wav",
                is_voicemail=False,
                detection_metadata=None,
            )
            print("Successfully completed call")
        except Exception as e:
            import traceback
            print("EXCEPTION CAUGHT:")
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
