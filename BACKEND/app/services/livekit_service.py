from dotenv import load_dotenv
load_dotenv()

from livekit import api
from livekit.protocol.sip import (
    CreateSIPParticipantRequest,
    CreateSIPInboundTrunkRequest,
    SIPInboundTrunkInfo,
    SIPDispatchRule,
    SIPDispatchRuleIndividual,
    CreateSIPDispatchRuleRequest,
)
from livekit.protocol.agent_dispatch import RoomAgentDispatch, CreateAgentDispatchRequest
from livekit.protocol.room import RoomConfiguration, CreateRoomRequest

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
    _BAD_TRUNKS = ("ST_3yaCewggPpAs", "ST_yZR7oi5aS79a")
    if not sip_trunk_id or sip_trunk_id in _BAD_TRUNKS:
        env_trunk = os.getenv("SIP_TRUNK_ID", "")
        # Also skip known-bad env values
        sip_trunk_id = env_trunk if env_trunk and env_trunk not in _BAD_TRUNKS else "ST_3iPMqSQPX8z5"
    if not sip_trunk_id:
        sip_trunk_id = "ST_3iPMqSQPX8z5"
        
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

    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "callinggen-outbound-agent")

    # Step 1: Pre-create the room with agent dispatch config
    try:
        room_req = CreateRoomRequest(
            name=room_name,
            empty_timeout=300,
            departure_timeout=30,
            agents=[RoomAgentDispatch(agent_name=agent_name)],
        )
        await lkapi.room.create_room(room_req)
        print(f"[livekit_service] Pre-created room '{room_name}' with RoomAgentDispatch(agent_name='{agent_name}')")
    except Exception as room_err:
        print(f"[livekit_service] Room create notice for '{room_name}': {room_err}")

    req = CreateSIPParticipantRequest(
        sip_trunk_id=sip_trunk_id,
        sip_call_to=clean_phone,
        sip_number=clean_sip_from,
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


async def create_inbound_sip_trunk(numbers: list[str]) -> str:
    """
    Create a LiveKit SIP Inbound Trunk for the given numbers.
    Returns the created trunk's sip_trunk_id.
    """
    lkapi = api.LiveKitAPI()
    try:
        # Resolve clean numbers to match
        numbers_to_match = []
        for num in numbers:
            clean_num = "".join(c for c in num if c.isdigit() or c == "+")
            if not clean_num.startswith("+"):
                if len(clean_num) == 10:
                    clean_num = f"+91{clean_num}"
                else:
                    clean_num = f"+{clean_num}"
            numbers_to_match.append(clean_num)
            raw_digits = "".join(c for c in clean_num if c.isdigit())
            if clean_num.startswith("+"):
                numbers_to_match.append(raw_digits)
                if clean_num.startswith("+91") and len(raw_digits) == 12:
                    numbers_to_match.append(raw_digits[2:])
        # Unique list
        numbers_to_match = list(set(numbers_to_match))
        
        name = f"Inbound-Trunk-{numbers[0]}" if numbers else "Inbound-Trunk"
        trunk_info = SIPInboundTrunkInfo(
            name=name,
            numbers=numbers_to_match
        )
        trunk_req = CreateSIPInboundTrunkRequest(trunk=trunk_info)
        trunk_res = await lkapi.sip.create_inbound_trunk(trunk_req)
        return trunk_res.sip_trunk_id
    finally:
        await lkapi.aclose()


async def create_inbound_dispatch_rule(trunk_ids: list[str], room_prefix: str = "inbound-call-") -> str:
    """
    Create a LiveKit SIP Dispatch Rule mapping the given trunk_ids to rooms starting with room_prefix.
    Returns the created dispatch rule ID.
    """
    lkapi = api.LiveKitAPI()
    try:
        rule = SIPDispatchRule(
            dispatch_rule_individual=SIPDispatchRuleIndividual(
                room_prefix=room_prefix
            )
        )
        agent_name = os.getenv("LIVEKIT_AGENT_NAME", "")
        room_config = RoomConfiguration(
            agents=[RoomAgentDispatch(agent_name=agent_name)]
        )
        first_trunk = trunk_ids[0] if trunk_ids else "rule"
        dispatch_req = CreateSIPDispatchRuleRequest(
            name=f"Inbound-Rule-{first_trunk}",
            rule=rule,
            room_config=room_config,
            trunk_ids=trunk_ids
        )
        res = await lkapi.sip.create_dispatch_rule(dispatch_req)
        return res.sip_dispatch_rule_id
    finally:
        await lkapi.aclose()


async def setup_inbound_sip(phone_number: str):
    """
    Register an inbound SIP Trunk and SIP Dispatch Rule in LiveKit for the given phone number.
    This routes any incoming calls on the phone number to individual rooms prefixed with "inbound-call-".
    """
    try:
        trunk_id = await create_inbound_sip_trunk([phone_number])
        await create_inbound_dispatch_rule([trunk_id], room_prefix="inbound-call-")
        print(f"[livekit_service] Configured LiveKit Inbound SIP trunk {trunk_id} and dispatch rule for {phone_number}")
        return True
    except Exception as e:
        print(f"[livekit_service] Error provisioning LiveKit inbound SIP for {phone_number}: {e}")
        return False