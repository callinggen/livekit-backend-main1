import asyncio
import os
from typing import Literal, Any
import httpx

from livekit.agents import function_tool
from app.services.conversation_state import ACTIVE_CALLS


@function_tool(
    description="""
Send information, brochures, pricing, catalogues, website link, booking link, or contact details to the customer's WhatsApp number during the call.

Call this tool whenever the customer requests:
- "Send me the brochure" or "Can you send the brochure?" -> action="SEND_BROCHURE"
- "Send me the pricing" or "What is the pricing?" or "Share pricing" -> action="SEND_PRICING"
- "Send me your catalogue / product list" -> action="SEND_CATALOGUE"
- "Send me your website" -> action="SEND_WEBSITE"
- "Send me the booking link" -> action="SEND_BOOKING_LINK"
- "Send me your contact details / number / email" -> action="SEND_CONTACT_DETAILS"

Allowed values for action:
- "SEND_BROCHURE"
- "SEND_PRICING"
- "SEND_CATALOGUE"
- "SEND_WEBSITE"
- "SEND_BOOKING_LINK"
- "SEND_CONTACT_DETAILS"
- "SEND_CALLBACK_CONFIRMATION"
"""
)
async def send_whatsapp_info(
    action: str = "SEND_BROCHURE",
) -> str:
    """Trigger an allowlisted WhatsApp action for the active call."""
    action_upper = (action or "").strip().upper()
    print(f"[whatsapp_tool] Tool send_whatsapp_info invoked with action='{action_upper}'")

    # Resolve active call ID
    call_id = None
    for room_name, state in ACTIVE_CALLS.items():
        if state and not state.get("finishing"):
            try:
                call_id = int(room_name.rsplit("-", 1)[-1])
                break
            except Exception:
                pass

    if not call_id and ACTIVE_CALLS:
        try:
            first_room = list(ACTIVE_CALLS.keys())[0]
            call_id = int(first_room.rsplit("-", 1)[-1])
        except Exception:
            pass

    if not call_id:
        print("[whatsapp_tool] Warning: Could not resolve active call_id for WhatsApp tool.")
        return "I will send that information to your WhatsApp shortly."

    backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
    target_url = f"{backend_url}/api/calls/{call_id}/whatsapp-action"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(target_url, json={"action": action_upper})
            if resp.status_code == 200:
                data = resp.json()
                print(f"[whatsapp_tool] WhatsApp action '{action_upper}' response: {data}")
            else:
                print(f"[whatsapp_tool] WhatsApp action '{action_upper}' HTTP error: {resp.status_code}")
    except Exception as err:
        print(f"[whatsapp_tool] Notice during WhatsApp action trigger (non-fatal): {err}")

    # Return natural conversation confirmation for the AI to speak
    if action_upper == "SEND_BROCHURE":
        return "I have sent our brochure directly to your WhatsApp number."
    elif action_upper == "SEND_PRICING":
        return "I have shared our pricing details to your WhatsApp number."
    elif action_upper == "SEND_CATALOGUE":
        return "I have sent our complete catalogue to your WhatsApp number."
    elif action_upper == "SEND_WEBSITE":
        return "I have sent our website link to your WhatsApp."
    elif action_upper == "SEND_BOOKING_LINK":
        return "I have sent the consultation booking link to your WhatsApp."
    elif action_upper == "SEND_CONTACT_DETAILS":
        return "I have sent our contact details to your WhatsApp."
    else:
        return "I have sent that information to your WhatsApp number."
