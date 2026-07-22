import asyncio
from app.database import AsyncSessionLocal
from app.models.user import User
from app.core.security import get_password_hash

async def main():
    async with AsyncSessionLocal() as session:
        new_user = User(
            email="admin@example.com",
            full_name="Admin User",
            phone_number=None,
            hashed_password=get_password_hash("password123!"),
            is_first_login=False,
            is_admin=True,
        )
        session.add(new_user)
        await session.commit()
        print("Mock admin user created! Email: admin@example.com, Password: password123!")

if __name__ == "__main__":
    asyncio.run(main())
