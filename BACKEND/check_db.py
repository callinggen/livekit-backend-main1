import asyncio
import sys
sys.path.append('c:/Users/hp/Desktop/Calling/livekit-backend-main1/BACKEND')
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.call import Call
from app.models.contact import Contact

async def main():
    async with AsyncSessionLocal() as db:
        calls = (await db.execute(select(Call))).scalars().all()
        contacts = (await db.execute(select(Contact))).scalars().all()
        print(f'Total Calls in DB: {len(calls)}')
        print(f'Calls with status completed: {len([c for c in calls if c.status=="completed"])}')
        print(f'Total Contacts in DB: {len(contacts)}')
        print(f'Contacts with status completed: {len([c for c in contacts if c.status=="completed"])}')

asyncio.run(main())
