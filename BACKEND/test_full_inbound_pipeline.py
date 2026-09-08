import asyncio
import sys
import os
import httpx
import math
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import AsyncSessionLocal
from app.models.call import Call
from backend_client import notify_call_active, notify_call_complete
from sqlalchemy import select

async def run_test():
    backend_url = "http://localhost:8000"
    room_name = "inbound-simulated-room-abc"
    
    print("\n=== STEP 1: Sending participant_joined webhook (Incoming call answered) ===")
    payload_join = {
        "event": "participant_joined",
        "room": {
            "name": room_name
        },
        "participant": {
            "sid": "PA_test_inbound_customer_88",
            "identity": "customer",
            "attributes": {
                "sip.caller": "+919988776655", # customer phone
                "sip.called": "+917971442271"  # our inbound number
            }
        }
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{backend_url}/api/livekit/webhook", json=payload_join)
        print(f"Webhook join status: {resp.status_code}, response: {resp.json()}")

    await asyncio.sleep(1.0)

    print("\n=== STEP 2: Running notify_call_active (Simulating agent detecting call answered) ===")
    active_success = await notify_call_active(room_name)
    print(f"notify_call_active output: {active_success}")

    await asyncio.sleep(2.0)

    print("\n=== STEP 3: Running notify_call_complete (Simulating conversation finishing) ===")
    transcript = "user: Hello\nassistant: Hi there! How can I help you?\nuser: Call me back tomorrow.\nassistant: Sure, tomorrow at what time?"
    payload_complete = {
        "transcript": transcript,
        "customer_name": "Test Inbound User",
        "appointment_date": "2026-09-01",
        "appointment_time": "10:00 AM",
        "recording_url": "/api/recordings/call_simulated.wav",
        "duration": 12,
    }
    complete_success = await notify_call_complete(room_name, payload=payload_complete)
    print(f"notify_call_complete output: {complete_success}")

    print("\n=== STEP 4: Sending room_finished webhook (Simulating LiveKit room closed) ===")
    payload_finish = {
        "event": "room_finished",
        "room": {
            "name": room_name
        }
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{backend_url}/api/livekit/webhook", json=payload_finish)
        print(f"Webhook finish status: {resp.status_code}, response: {resp.json()}")

    print("\n=== STEP 5: Waiting 5 seconds for webhook background task to complete ===")
    await asyncio.sleep(5.0)

    print("\n=== STEP 6: Verifying Call Record in Database ===")
    async with AsyncSessionLocal() as db:
        call_res = await db.execute(select(Call).where(Call.room_name == room_name))
        call = call_res.scalars().first()
        if call:
            print("Database Call State:")
            print(f"  - ID: {call.id}")
            print(f"  - Direction: {call.direction}")
            print(f"  - Status: {call.status}")
            print(f"  - Outcome: {call.outcome}")
            print(f"  - Duration: {call.duration}s")
            print(f"  - Billing Status: {call.billing_status}")
            print(f"  - Credits Deducted: {call.credits_deducted}")
            print(f"  - Transcript: {call.transcript is not None} (length: {len(call.transcript) if call.transcript else 0})")
            
            # Assertions to confirm correctness
            assert call.status == "completed", "Status should be completed!"
            assert call.sip_was_active == True, "sip_was_active should be True!"
            assert call.billing_status == "billed", "Billing status should be billed!"
            assert call.credits_deducted == 3, "Credits deducted should be 3 (12s / 4)!"
            print("\nSUCCESS: End-to-End simulation successfully passed!")
        else:
            print("FAILED: Call record not found.")

if __name__ == "__main__":
    asyncio.run(run_test())
