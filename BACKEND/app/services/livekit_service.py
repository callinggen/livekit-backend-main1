from dotenv import load_dotenv
load_dotenv()

from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest

import os


async def make_livekit_call(
    phone: str,
    room_name: str,
    sip_trunk_id: str | None = None,
    sip_call_from: str | None = None,
):
    lkapi = api.LiveKitAPI()
    
    # Sanitize the destination phone number
    clean_phone = "".join(c for c in phone if c.isdigit() or c == "+")
    if not clean_phone.startswith("+"):
        if len(clean_phone) == 10:
            clean_phone = f"+91{clean_phone}"
        else:
            clean_phone = f"+{clean_phone}"

    # Use dynamic SIP Trunk ID if provided, otherwise fallback to env / system trunk
    if not sip_trunk_id:
        sip_trunk_id = os.getenv("SIP_TRUNK_ID", "ST_3yaCewggPpAs")
    if sip_trunk_id == "ST_yZR7oi5aS79a":
        sip_trunk_id = "ST_3yaCewggPpAs"
        
    # Use dynamic assigned caller ID if provided, otherwise fallback to system caller ID
    if not sip_call_from:
        sip_call_from = os.getenv("SIP_CALL_FROM", "+917971442271")

    clean_sip_from = "".join(c for c in sip_call_from if c.isdigit() or c == "+")
    if clean_sip_from and not clean_sip_from.startswith("+"):
        if len(clean_sip_from) == 10:
            clean_sip_from = f"+91{clean_sip_from}"
        else:
            clean_sip_from = f"+{clean_sip_from}"
    elif not clean_sip_from:
        clean_sip_from = "+917971442271"

    req = CreateSIPParticipantRequest(
        sip_trunk_id=sip_trunk_id,
        sip_call_to=clean_phone,
        sip_number=clean_sip_from,
        room_name=room_name,
        participant_identity="customer",
        participant_name="Customer",
        wait_until_answered=True,
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