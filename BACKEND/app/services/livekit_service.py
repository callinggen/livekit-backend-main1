from dotenv import load_dotenv
load_dotenv(override=True)

from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

import os


async def make_livekit_call(
    phone: str,
    room_name: str,
):
    load_dotenv(override=True)
    lkapi = api.LiveKitAPI()
    
    # Sanitize phone number to standard E.164 format
    raw_digits = "".join(c for c in phone if c.isdigit())
    
    # Handle Indian number formats
    if len(raw_digits) == 10:
        clean_phone = f"+91{raw_digits}"
    elif len(raw_digits) == 11 and raw_digits.startswith("0"):
        clean_phone = f"+91{raw_digits[1:]}"
    elif len(raw_digits) == 12 and raw_digits.startswith("91"):
        clean_phone = f"+{raw_digits}"
    elif phone.startswith("+"):
        clean_phone = f"+{raw_digits}"
    else:
        # Fallback to India +91 if likely an Indian number
        clean_phone = f"+91{raw_digits}" if len(raw_digits) <= 10 else f"+{raw_digits}"

    sip_trunk_id = os.getenv("SIP_TRUNK_ID", "ST_3iPMqSQPX8z5")
    sip_call_from = os.getenv("SIP_CALL_FROM", "+917971442271")
    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "callinggen_shreya")

    print(f"[livekit_service] Dispatching SIP call -> To: {clean_phone} | From: {sip_call_from} | Trunk: {sip_trunk_id} | Room: {room_name}")

    try:
        # 1. Explicitly dispatch our dedicated local agent worker to this room
        if agent_name:
            try:
                await lkapi.agent_dispatch.create_dispatch(
                    CreateAgentDispatchRequest(
                        agent_name=agent_name,
                        room=room_name,
                    )
                )
                print(f"[livekit_service] Dispatched agent '{agent_name}' to room '{room_name}'")
            except Exception as dispatch_err:
                print(f"[livekit_service] Agent dispatch notice: {dispatch_err}")

        # 2. Place the SIP outbound call
        req = CreateSIPParticipantRequest(
            sip_trunk_id=sip_trunk_id,
            sip_call_to=clean_phone,
            sip_number=sip_call_from,
            room_name=room_name,
            participant_identity="customer",
            participant_name="Customer",
            wait_until_answered=True,
        )
        participant = await lkapi.sip.create_sip_participant(req)

        print(f"[livekit_service] SIP Participant created successfully: {participant.participant_id}")
        return {
            "success": True,
            "participant_id": participant.participant_id,
            "room": room_name,
            "phone": phone,
        }

    except Exception as e:
        print(f"[livekit_service] Error placing SIP call: {e}")
        return {
            "success": False,
            "error": str(e),
        }

    finally:
        await lkapi.aclose()