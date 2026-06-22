from dotenv import load_dotenv
import os

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)

from livekit.plugins import sarvam, openai

load_dotenv()


class AppointmentBookingAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
You are a professional appointment booking assistant.

Rules:
- Keep responses short.
- Maximum 2 sentences.
- Ask only for:
    1. Name
    2. Appointment date
    3. Appointment time

- Do not discuss unrelated topics.
- Do not hallucinate.
- Be polite and professional.

Flow:

Step 1:
Ask the user's name.

Step 2:
Ask appointment date.

Step 3:
Ask appointment time.

Step 4:
Confirm all details.

Then say:

"Thank you. Your appointment request has been recorded."
"""
        )


async def entrypoint(ctx: JobContext):

    print("===================================")
    print("JOB RECEIVED")
    print("===================================")

    await ctx.connect()

    print(f"Connected to room: {ctx.room.name}")

    print("SARVAM KEY:", bool(os.getenv("SARVAM_API_KEY")))
    print("DEEPSEEK KEY:", bool(os.getenv("DEEPSEEK_API_KEY")))

    session = AgentSession(
        stt=sarvam.STT(),

        llm=openai.LLM(
            model="deepseek-chat",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",

            extra_kwargs={
                "temperature": 0.75,
                "max_tokens": 220,
            },
        ),

        tts=sarvam.TTS(),
    )

    print("Starting session...")

    await session.start(
        room=ctx.room,
        agent=AppointmentBookingAgent(),
    )

    print("Session started")

    await session.generate_reply(
        instructions="""
        Introduce yourself.
        Welcome the caller.
        Ask for their name.
        """
    )

    print("Greeting sent")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="appointment-agent",
        )
    )