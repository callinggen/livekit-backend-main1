from dotenv import load_dotenv
import os

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)

from livekit.plugins import openai
from livekit.plugins import sarvam

load_dotenv()


class VoiceAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""
            You are a helpful AI voice assistant.

            Keep answers short.
            Speak naturally.
            """
        )


async def entrypoint(ctx: JobContext):

    await ctx.connect()

    session = AgentSession(
        stt=sarvam.STT(),
        llm=openai.LLM(
            model="gpt-4o-mini"
        ),
        tts=sarvam.TTS(),
    )

    await session.start(
        room=ctx.room,
        agent=VoiceAgent(),
    )

    await session.generate_reply(
        instructions="Introduce yourself."
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint
        )
    )