import asyncio
from app.database import engine, Base
from app.models.contact_form_user import ContactFormUser

async def init():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

if __name__ == "__main__":
    asyncio.run(init())
