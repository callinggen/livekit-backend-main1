import asyncio
import os
from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import (
    ListSIPOutboundTrunkRequest,
    ListSIPInboundTrunkRequest,
    ListSIPDispatchRuleRequest
)

load_dotenv()

async def inspect_livekit():
    lkapi = api.LiveKitAPI()
    print("Connecting to LiveKit at:", os.getenv("LIVEKIT_URL"))
    
    try:
        # Check outbound trunks
        print("\n--- OUTBOUND TRUNKS ---")
        outbound_trunks = await lkapi.sip.list_sip_outbound_trunk(ListSIPOutboundTrunkRequest())
        for t in outbound_trunks.items:
            print(f"ID: {t.sip_trunk_id} | Name: {t.name} | Address: {t.address} | Numbers: {t.numbers} | Auth Username: {t.auth_username}")
        if not outbound_trunks.items:
            print("No outbound trunks found!")

        # Check inbound trunks
        print("\n--- INBOUND TRUNKS ---")
        inbound_trunks = await lkapi.sip.list_sip_inbound_trunk(ListSIPInboundTrunkRequest())
        for t in inbound_trunks.items:
            print(f"ID: {t.sip_trunk_id} | Name: {t.name} | Numbers: {t.numbers}")
        if not inbound_trunks.items:
            print("No inbound trunks found!")

        # Check dispatch rules
        print("\n--- DISPATCH RULES ---")
        rules = await lkapi.sip.list_sip_dispatch_rule(ListSIPDispatchRuleRequest())
        for r in rules.items:
            print(f"ID: {r.sip_dispatch_rule_id} | Name: {r.name} | Trunk IDs: {r.trunk_ids}")
        if not rules.items:
            print("No dispatch rules found!")

    except Exception as e:
        print(f"Error inspecting LiveKit SIP: {e}")
    finally:
        await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(inspect_livekit())
