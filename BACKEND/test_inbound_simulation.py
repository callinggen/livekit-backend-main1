import asyncio
import sys
import httpx
from datetime import datetime, timezone

# Ensure backend directory is in path
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import AsyncSessionLocal
from app.models.user import User
from app.models.agent import Agent
from app.models.user_phone_number import UserPhoneNumber
from app.models.call import Call
from sqlalchemy import select

async def setup_test_telephony():
    """
    Ensure the DB has a tenant (User ID: 1), an AI Agent, 
    and an Inbound-enabled UserPhoneNumber (+917971442271) assigned to them.
    """
    async with AsyncSessionLocal() as db:
        # 1. Fetch default Admin/User
        user_res = await db.execute(select(User).order_by(User.id.asc()))
        user = user_res.scalars().first()
        if not user:
            print("Creating default test user...")
            user = User(
                email="admin@example.com",
                full_name="Admin User",
                hashed_password="mock_password_hash",
                is_admin=True
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        # 2. Fetch or create an Agent for this user
        agent_res = await db.execute(select(Agent).where(Agent.user_id == user.id))
        agent = agent_res.scalars().first()
        if not agent:
            print("Creating default test Agent...")
            agent = Agent(
                user_id=user.id,
                name="Sales Assistant",
                language="English",
                voice="Meera",
                script="Greet the customer politely."
            )
            db.add(agent)
            await db.commit()
            await db.refresh(agent)

        # 3. Assign an Inbound-enabled phone number to this tenant/agent
        phone_num = "+917971442271"
        pn_res = await db.execute(select(UserPhoneNumber).where(UserPhoneNumber.phone_number == phone_num))
        pn = pn_res.scalars().first()
        if not pn:
            print(f"Creating Phone Line {phone_num}...")
            pn = UserPhoneNumber(
                user_id=user.id,
                phone_number=phone_num,
                region="India (+91)",
                provider_name="Vobiz",
                inbound_enabled=True,
                inbound_agent_id=agent.id,
                is_active=True
            )
            db.add(pn)
        else:
            print(f"Updating Phone Line {phone_num} inbound settings...")
            pn.inbound_enabled = True
            pn.inbound_agent_id = agent.id
            pn.is_active = True
        await db.commit()
        print(f"Setup Complete: User={user.id}, Agent={agent.id}, Phone Line={phone_num} (Inbound Enabled)")


async def run_webhook_simulation():
    """
    Fire HTTP requests to the running FastAPI server to simulate
    1. Incoming call starting (participant_joined)
    2. Incoming call ending (room_finished)
    """
    backend_url = "http://localhost:8000"
    room_name = "inbound-simulated-room-99"
    
    # Payload for participant joining (Vobiz dials in -> LiveKit dispatches SIP participant to room)
    payload_join = {
        "event": "participant_joined",
        "room": {
            "name": room_name
        },
        "participant": {
            "sid": "PA_test_inbound_customer_99",
            "identity": "customer",
            "attributes": {
                "sip.caller": "+919000011111", # customer phone
                "sip.called": "+917971442271"  # our callinggen number
            }
        }
    }
    
    async with httpx.AsyncClient() as client:
        # 1. Simulate incoming call starting
        print("\n1. Simulating incoming call start (sending participant_joined webhook)...")
        try:
            resp = await client.post(f"{backend_url}/api/livekit/webhook", json=payload_join)
            print(f"   Response Status: {resp.status_code}")
            print(f"   Response Body  : {resp.json()}")
        except Exception as e:
            print(f"   Error: Webhook endpoint failed. Is the backend server running? ({e})")
            return

        # Wait 2 seconds
        await asyncio.sleep(2.0)

        # 2. Simulate incoming call ending (room finishes)
        payload_finish = {
            "event": "room_finished",
            "room": {
                "name": room_name
            }
        }
        print("\n2. Simulating incoming call finish (sending room_finished webhook)...")
        try:
            resp = await client.post(f"{backend_url}/api/livekit/webhook", json=payload_finish)
            print(f"   Response Status: {resp.status_code}")
            print(f"   Response Body  : {resp.json()}")
        except Exception as e:
            print(f"   Error sending room_finished webhook: {e}")
            return

    # Verify Call Record in DB
    print("\n3. Verifying Call record in database...")
    async with AsyncSessionLocal() as db:
        call_res = await db.execute(select(Call).where(Call.room_name == room_name))
        call = call_res.scalars().first()
        if call:
            print(f"   SUCCESS! Inbound call logged in DB (ID: {call.id})")
            print(f"   - Direction: {call.direction}")
            print(f"   - Caller: {call.caller_number}")
            print(f"   - Called: {call.called_number}")
            print(f"   - Agent ID: {call.agent_id}")
            print(f"   - Status: {call.status}")
        else:
            print("   FAILED: Call log record was not created.")

if __name__ == "__main__":
    asyncio.run(setup_test_telephony())
    asyncio.run(run_webhook_simulation())
