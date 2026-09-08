from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.services.call_service import CallService


class LiveKitEventService:

    @staticmethod
    async def room_finished(
        db: AsyncSession,
        room_name: str,
    ):

        result = await db.execute(
            select(Call).where(
                Call.room_name == room_name
            )
        )

        call = result.scalars().first()

        if call is None:
            print("Call not found")
            return

        print(f"Room finished: {room_name}")

        await CallService.complete_call(
            db=db,
            call_id=call.id,
        )