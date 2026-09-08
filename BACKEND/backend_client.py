import os
from typing import Any

import httpx
from dotenv import load_dotenv

# Load env variables at module import time
load_dotenv()


async def notify_call_active(room_name: str) -> bool:
    """
    Tell the FastAPI backend that the call behind this room has become active,
    meaning the SIP participant successfully joined and connected.
    """
    import asyncio
    try:
        call_id = int(room_name.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        print(f"[backend_client] Could not parse call_id from room name: {room_name}")
        return False

    backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    url = f"{backend_url}/api/calls/{call_id}/active"

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url)
                if resp.is_success:
                    print(f"[backend_client] Backend notified: call {call_id} marked active.")
                    return True
        except Exception as e:
            print(f"[backend_client] Error notifying call active for call {call_id}: {e}")
        await asyncio.sleep(1.0)
    return False


async def notify_call_complete(
    room_name: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    """
    Tell the FastAPI backend that the call behind this room has finished,
    so it can update Call/Contact/Job/Campaign and let the worker move on
    to the next contact.

    Room names are created as f"call-{call.id}" in queue_service.py, so
    the call_id is recovered from the room name here.

    Optional ``payload`` is forwarded as the JSON request body and may
    contain: transcript, customer_name, appointment_date, appointment_time.
    """
    import asyncio

    try:
        call_id = int(room_name.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        call_id = -1

    if call_id == -1:
        try:
            from app.database import AsyncSessionLocal
            from app.models.call import Call
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Call).where(Call.room_name == room_name))
                call = result.scalars().first()
                if call:
                    call_id = call.id
        except Exception as db_err:
            print(f"[backend_client] Database lookup failed for room {room_name}: {db_err}")
            call_id = -1

    if call_id == -1:
        print(f"[backend_client] Could not parse or find call_id for room name: {room_name}")
        return False

    # ── [COMPLETION CHECK] ────────────────────────────────────────────────────
    from app.database import AsyncSessionLocal
    from app.models.call import Call
    db_exists = False
    try:
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if call:
                db_exists = True
    except Exception as e:
        print(f"[backend_client] Error checking DB for call {call_id}: {e}")
        
    print(f"\n[COMPLETION CHECK]")
    print(f"call_id={call_id}")
    print(f"db_exists={db_exists}\n")
    
    if not db_exists:
        print(f"[COMPLETION ABORTED]")
        print(f"reason=call_row_missing")
        return False
    # ──────────────────────────────────────────────────────────────────────────

    backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    url = f"{backend_url}/api/calls/{call_id}/complete"

    print("-" * 50)
    print("AGENT / BACKEND CLIENT: notify_call_complete START")
    print(f"PID: {os.getpid()}")
    print(f"DATABASE_URL: {os.getenv('DATABASE_URL')}")
    print(f"Target URL: {url}")
    print(f"Payload keys: {list((payload or {}).keys())}")
    print("-" * 50)

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload or {})
                print(f"[backend_client] Attempt {attempt}/{max_attempts} -> HTTP Status: {resp.status_code}")
                if resp.is_success:
                    try:
                        data = resp.json()
                        if data.get("success"):
                            print(f"[backend_client] Backend notified: call {call_id} marked complete successfully.")
                            return True
                        else:
                            print(f"[backend_client] Backend returned 200 but logical failure: {data.get('message')}")
                    except Exception as e:
                        print(f"[backend_client] Failed to parse JSON from backend: {e}")
                else:
                    print(f"[backend_client] Attempt {attempt}/{max_attempts} -> Non-2xx response: {resp.status_code} Body: {resp.text}")
        except Exception as e:
            print(f"[backend_client] Error on attempt {attempt}/{max_attempts} for call {call_id}: {e}")

        if attempt < max_attempts:
            print(f"[backend_client] Retrying in 1 second (attempt {attempt + 1}/{max_attempts})...")
            await asyncio.sleep(1.0)

    print(f"[backend_client] ALL {max_attempts} notification attempts FAILED for call {call_id}.")
    return False

