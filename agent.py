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


class VoiceAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
            You are a helpful AI voice assistant.

            Keep responses short.
            Speak naturally.
            Answer conversationally.
            """
        )


async def entrypoint(ctx: JobContext):
    print("Connecting to room...")

    await ctx.connect()

    print(f"Connected to room: {ctx.room.name}")

    session = AgentSession(
        stt=sarvam.STT(),

        llm=openai.LLM(
            model="deepseek-chat",
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
        ),

        tts=sarvam.TTS(),
    )

    await session.start(
        room=ctx.room,
        agent=VoiceAgent(),
    )

    await session.generate_reply(
        instructions="Introduce yourself briefly."
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint
        )
    )