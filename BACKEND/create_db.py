import asyncio

from app.database import Base, engine

# Import all models so SQLAlchemy knows about them
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call
from app.models.user import User
from app.models.password_reset import PasswordReset


async def create_tables():
   async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.drop_all)
    await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(create_tables())
    print("✅ Database created successfully!")