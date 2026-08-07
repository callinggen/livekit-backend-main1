import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def test():
    try:
        engine = create_async_engine('sqlite+aiosqlite:///C:/Users/hp/Desktop/genx/livekit-backend-main1/BACKEND/callinggen.db')
        async with engine.begin() as conn:
            result = await conn.execute(text('SELECT subscription_plan FROM users LIMIT 1'))
            print("SUCCESS:", result.fetchall())
    except Exception as e:
        print("ERROR:", e)

asyncio.run(test())
