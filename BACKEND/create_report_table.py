import asyncio

from app.database import Base, engine

# Import all models so SQLAlchemy knows about them
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call
from app.models.user import User
from app.models.password_reset import PasswordReset
from app.models.report import Report


async def create_report_table():
   async with engine.begin() as conn:
    # create_all only creates tables that don't exist yet, it won't drop existing ones
    await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(create_report_table())
    print("✅ Report table created successfully!")
