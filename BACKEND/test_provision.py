import asyncio
from app.database import AsyncSessionLocal
from app.services.user_provisioning import provision_user

async def main():
    async with AsyncSessionLocal() as session:
        result = await provision_user(
            db=session,
            full_name="Hrishita",
            email="hrishitacallinggen@gmail.com",
            phone_number="+918888888889"
        )
        print("Provisioning result:", result)

if __name__ == "__main__":
    asyncio.run(main())
