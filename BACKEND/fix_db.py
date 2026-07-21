import asyncio
from app.database import AsyncSessionLocal
from sqlalchemy import text

async def fix_db():
    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE jobs SET status = 'completed' WHERE status IN ('queued', 'processing');"))
        await db.execute(text("UPDATE calls SET status = 'completed' WHERE status IN ('dialing', 'in_progress');"))
        await db.commit()
        print("Successfully marked jobs and calls as completed.")

if __name__ == "__main__":
    asyncio.run(fix_db())
