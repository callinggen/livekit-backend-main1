from dotenv import load_dotenv
load_dotenv()

from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest

import os


async def make_livekit_call(
    phone: str,
    room_name: str,
):
    lkapi = api.LiveKitAPI()
    
    # Sanitize the phone number to remove spaces, dashes, parentheses
    clean_phone = "".join(c for c in phone if c.isdigit() or c == "+")
    if not clean_phone.startswith("+"):
        if len(clean_phone) == 10:
            clean_phone = f"+91{clean_phone}"
        else:
            clean_phone = f"+{clean_phone}"

    sip_trunk_id = os.getenv("SIP_TRUNK_ID", "ST_yZR7oi5aS79a")
    sip_call_from = os.getenv("SIP_CALL_FROM", "+917971442271")
    req = CreateSIPParticipantRequest(
        sip_trunk_id=sip_trunk_id,
        sip_call_to=clean_phone,
        sip_number=sip_call_from,
        room_name=room_name,
        participant_identity="customer",
        participant_name="Customer",
        wait_until_answered=False,
    )

    try:
        participant = await lkapi.sip.create_sip_participant(req)

        return {
            "success": True,
            "participant_id": participant.participant_id,
            "room": room_name,
            "phone": phone,
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }

    finally:
        await lkapi.aclose()