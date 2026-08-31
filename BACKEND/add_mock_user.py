import asyncio
from app.database import AsyncSessionLocal
from app.models.user import User
from app.core.security import get_password_hash
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.email == "hrishitacallinggen@gmail.com")
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            existing.hashed_password = get_password_hash("Callinggen@123")
            existing.credits = 2000
            existing.subscription_plan = "Standard"
            existing.is_active = True
            existing.is_admin = True
            existing.is_first_login = False
            print("Existing user updated with password: Callinggen@123")
        else:
            new_user = User(
                email="hrishitacallinggen@gmail.com",
                full_name="Hrishita",
                phone_number=None,
                hashed_password=get_password_hash("Callinggen@123"),
                credits=2000,
                subscription_plan="Standard",
                company_name="CallingGen",
                is_first_login=False,
                is_admin=True,
                is_active=True,
            )
            session.add(new_user)
            print("User hrishitacallinggen@gmail.com created successfully with password: Callinggen@123")
        await session.commit()

if __name__ == "__main__":
    asyncio.run(main())

