from dotenv import load_dotenv
load_dotenv()
import asyncio
from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest


TRUNK_ID = "ST_TSqKbTXKy7wA"


async def main():
    lkapi = api.LiveKitAPI()

    room_name = "ai-call-room"

    req = CreateSIPParticipantRequest(
        sip_trunk_id=TRUNK_ID,
        sip_call_to="+917075671166",
        room_name=room_name,
        participant_identity="customer",
        participant_name="Customer",
        wait_until_answered=True,
    )

    try:
        participant = await lkapi.sip.create_sip_participant(req)

        print("CALL CONNECTED")
        print(participant)

    except Exception as e:
        print("CALL FAILED")
        print(e)

    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())