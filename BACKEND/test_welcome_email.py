import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.notification_service import notification_service

async def test_welcome():
    user = User(
        full_name="Khushi Panwar",
        email="khushipanwar060@gmail.com",
        subscription_plan="Standard",
        credits=2000,
        company_name="CallingGen Corp"
    )
    print("Sending Welcome Email with Subscription & Credit details...")
    notification_service.notify_account_created(user, "@Khushi999")
    print("Welcome Email sent successfully!")

if __name__ == "__main__":
    asyncio.run(test_welcome())
