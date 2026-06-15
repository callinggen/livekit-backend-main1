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

            Keep responses concise.
            Speak naturally.
            Be conversational and friendly.
            """
        )


async def entrypoint(ctx: JobContext):

    print("===== JOB RECEIVED =====")

    await ctx.connect()

    print(f"Connected to room: {ctx.room.name}")

    print("SARVAM:", bool(os.getenv("SARVAM_API_KEY")))
    print("DEEPSEEK:", bool(os.getenv("DEEPSEEK_API_KEY")))

    session = AgentSession(
        stt=sarvam.STT(),

        llm=openai.LLM(
            model="deepseek-reasoner",  # DeepSeek R1
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
        ),

        tts=sarvam.TTS(),
    )

    print("Starting session")

    await session.start(
        room=ctx.room,
        agent=VoiceAgent(),
    )

    print("Session started")

    await session.generate_reply(
        instructions="Introduce yourself and ask how you can help."
    )

    print("Greeting sent")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint
        )
    )