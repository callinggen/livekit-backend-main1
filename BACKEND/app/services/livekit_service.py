from dotenv import load_dotenv
load_dotenv()

from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest

import os

TRUNK_ID = os.getenv("SIP_TRUNK_ID", "ST_mmfofL7PdLRq")


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

    sip_call_from = os.getenv("SIP_CALL_FROM", "+917971442271")
    req = CreateSIPParticipantRequest(
        sip_trunk_id=TRUNK_ID,
        sip_call_to=clean_phone,
        sip_number=sip_call_from,
        room_name=room_name,
        participant_identity="customer",
        participant_name="Customer",
        wait_until_answered=False,
    )

    try:
        participant = await lkapi.sip.create_sip_participant(req)

        # ── EXPLICIT AGENT DISPATCH (Fix for agent not joining) ───────────
        try:
            from livekit.api import CreateAgentDispatchRequest
            agent_name = os.getenv("LIVEKIT_AGENT_NAME", "callinggen-agent-prod")
            await lkapi.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=agent_name,
                    room=room_name,
                )
            )
            print(f"Agent dispatch explicitly created for room: {room_name} (agent_name: '{agent_name}')")
        except Exception as job_err:
            print(f"Note: Explicit agent dispatch skipped or failed: {job_err}")
        # ────────────────────────────────────────────────────────────────

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