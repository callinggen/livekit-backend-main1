from dotenv import load_dotenv
import os

from livekit import api

load_dotenv()

import asyncio

async def main():
    lk = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    print("=" * 60)
    print("ROOM SERVICE")
    print("=" * 60)
    print(dir(lk.room))

    print()

    print("=" * 60)
    print("SIP SERVICE")
    print("=" * 60)
    print(dir(lk.sip))
    
    await lk.aclose()

if __name__ == "__main__":
    asyncio.run(main())