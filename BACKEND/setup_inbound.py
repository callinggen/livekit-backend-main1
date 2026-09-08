import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
 
from livekit import api
from livekit.protocol.sip import CreateSIPInboundTrunkRequest, SIPInboundTrunkInfo, CreateSIPDispatchRuleRequest, SIPDispatchRule, SIPDispatchRuleIndividual
from livekit.protocol.room import RoomConfiguration
from livekit.protocol.agent_dispatch import RoomAgentDispatch

async def create_inbound_setup():
    url = os.getenv("LIVEKIT_URL", "ws://13.232.26.174:7880")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
 
    print(f"Connecting to LiveKit at {url}...")
    lkapi = api.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
 
    try:
        # 1. Create LiveKit Inbound Trunk
        phone_number = os.getenv("SIP_CALL_FROM", "+917971442271")
        
        numbers_to_match = [phone_number]
        raw_digits = "".join(c for c in phone_number if c.isdigit())
        if phone_number.startswith("+"):
            numbers_to_match.append(raw_digits)
            if phone_number.startswith("+91") and len(raw_digits) == 12:
                numbers_to_match.append(raw_digits[2:])
        numbers_to_match = list(set(numbers_to_match))

        trunk_info = SIPInboundTrunkInfo(
            name="Vobiz Inbound Trunk",
            numbers=numbers_to_match
        )
        trunk_req = CreateSIPInboundTrunkRequest(trunk=trunk_info)
        trunk = await lkapi.sip.create_inbound_trunk(trunk_req)
        print(f"✅ Inbound Trunk Created: ID = {trunk.sip_trunk_id}")
     
        # 2. Create SIP Dispatch Rule
        rule = SIPDispatchRule(
            dispatch_rule_individual=SIPDispatchRuleIndividual(
                room_prefix="inbound-call-"
            )
        )
        agent_name = os.getenv("LIVEKIT_AGENT_NAME", "")
        room_config = RoomConfiguration(
            agents=[RoomAgentDispatch(agent_name=agent_name)]
        )
        dispatch_req = CreateSIPDispatchRuleRequest(
            name="Vobiz Inbound Dispatch Rule",
            rule=rule,
            room_config=room_config,
            trunk_ids=[trunk.sip_trunk_id]
        )
        dispatch = await lkapi.sip.create_dispatch_rule(dispatch_req)
        print(f"✅ Dispatch Rule Created: ID = {dispatch.sip_dispatch_rule_id}")
    finally:
        await lkapi.aclose()
 
if __name__ == "__main__":
    asyncio.run(create_inbound_setup())
