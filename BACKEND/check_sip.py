from dotenv import load_dotenv
load_dotenv()

import asyncio
from livekit import api


async def main():
    lkapi = api.LiveKitAPI()

    try:
        print("\n========== INBOUND TRUNKS ==========")

        trunks = await lkapi.sip.list_inbound_trunk(
            api.ListSIPInboundTrunkRequest()
        )

        for trunk in trunks.items:
            print("ID:", trunk.sip_trunk_id)
            print("Name:", trunk.name)
            print("Numbers:", list(trunk.numbers))
            print()

        print("\n========== DISPATCH RULES ==========")

        rules = await lkapi.sip.list_dispatch_rule(
            api.ListSIPDispatchRuleRequest()
        )

        for rule in rules.items:
            print("ID:", rule.sip_dispatch_rule_id)
            print("Name:", rule.name)
            print("Trunk IDs:", list(rule.trunk_ids))
            print("Rule:", rule.rule)
            print("Room Config:", rule.room_config)
            print()

    finally:
        await lkapi.aclose()


asyncio.run(main())