import asyncio
import os
from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest

load_dotenv()

async def test_call():
    lkapi = api.LiveKitAPI()
    
    # We will test using the actual registered trunk ID
    trunk_id = "ST_3yaCewggPpAs"
    caller_id = "+917971442271"
    
    # We can inspect the method parameters
    print(f"Trunk: {trunk_id}")
    print(f"Caller ID: {caller_id}")

    await lkapi.aclose()

if __name__ == "__main__":
    asyncio.run(test_call())
