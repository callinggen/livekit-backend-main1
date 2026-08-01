import asyncio
from sqlalchemy import update
from app.database import AsyncSessionLocal
from app.models.contact import Contact
async def fix():
    async with AsyncSessionLocal() as db:
        await db.execute(update(Contact).where(Contact.status=='calling').values(status='failed'))
        await db.commit()
    print('Fixed stuck contacts')
asyncio.run(fix())
