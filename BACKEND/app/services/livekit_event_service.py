import asyncio
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.call import Call
from app.services.call_service import CallService


class LiveKitEventService:

    @staticmethod
    async def room_finished(
        room_name: str,
    ):
        print(f"[LiveKitEventService] Room finished event for: {room_name}. Waiting 4 seconds for agent to complete...")
        await asyncio.sleep(4.0)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Call).where(
                    Call.room_name == room_name
                )
            )

            call = result.scalars().first()

            if call is None:
                print(f"[LiveKitEventService] Call for room '{room_name}' not found.")
                return

            if call.status in ("completed", "failed"):
                print(f"[LiveKitEventService] Call {call.id} was already completed/failed by the agent. Webhook completion skipped.")
                return

            print(f"[LiveKitEventService] Room finished: {room_name} (completing stale call)")

            await CallService.complete_call(
                db=db,
                call_id=call.id,
            )