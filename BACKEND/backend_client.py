import os
from typing import Any

import httpx
from dotenv import load_dotenv

# Load env variables at module import time
load_dotenv()


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
    try:
        call_id = int(room_name.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        print(f"[backend_client] Could not parse call_id from room name: {room_name}")
        return False

    backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
    url = f"{backend_url}/api/calls/{call_id}/complete"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload or {})
            resp.raise_for_status()
            print(f"[backend_client] Backend notified: call {call_id} marked complete.")
            return True
    except Exception as e:
        print(f"[backend_client] Failed to notify backend for call {call_id}: {e}")
        return False

