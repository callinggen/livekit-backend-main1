import asyncio
from app.database import AsyncSessionLocal
from app.models.user import User
from app.core.security import get_password_hash

async def main():
    async with AsyncSessionLocal() as session:
        new_user = User(
            email="khushicallinggen@gmail.com",
            full_name="Khushi Panwar",
            phone_number=None,
            hashed_password=get_password_hash("Khushi@123"),
            is_first_login=False,
            is_admin=True,
        )
        session.add(new_user)
        await session.commit()
        print("Mock admin user created!!")

if __name__ == "__main__":
    asyncio.run(main())
