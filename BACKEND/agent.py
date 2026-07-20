from dotenv import load_dotenv
import asyncio
import os
import wave

from app.services.conversation_state import ACTIVE_CALLS
from backend_client import notify_call_complete
from finish_call import finish_call, _build_transcript

from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)

from livekit.plugins import sarvam, openai

# Database access to read campaign + contact at runtime
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.call import Call
from app.models.contact import Contact
from app.models.campaign import Campaign

load_dotenv()

# ── Agent type → base system prompt ───────────────────────────────────────────
AGENT_BASE_PROMPTS: dict[str, str] = {
    "Voice-A (Sales)": (
        "You are a professional sales representative making outbound calls. "
        "Your goal is to pitch the product/service, handle objections politely, "
        "and move the prospect toward a purchase or demo booking."
    ),
    "Voice-B (Support)": (
        "You are a friendly customer support agent making outbound calls. "
        "Your goal is to resolve the customer's issue, answer questions accurately, "
        "and ensure the customer feels heard and satisfied."
    ),
    "Voice-C (Followup)": (
        "You are a follow-up agent making outbound calls to warm leads or past customers. "
        "Your goal is to re-engage the contact, check on their needs, "
        "and guide them toward the next step."
    ),
    "Voice-D (Survey)": (
        "You are a survey agent conducting a satisfaction or market research call. "
        "Ask each question clearly, wait for the customer's answer, record it accurately, "
        "and keep the conversation brief and focused."
    ),
    "Voice-E (Tax Agent)": (
        "You are a professional and knowledgeable tax advisor making outbound calls. "
        "Your goal is to assist customers with their tax filing requirements, answer questions about "
        "deductions, and schedule appointments with tax professionals if needed."
    ),
}

# ── Date/time validation rules injected into every agent ──────────────────────
DATE_TIME_VALIDATION_RULES = """
DATE & TIME VALIDATION RULES (MANDATORY — never skip these):
- If the customer mentions a date that is in the past, say: "I'm sorry, that date has already passed. Could you please provide a future date?"
- If the customer gives a date without a year (e.g. "July 15th"), always ask: "Could you confirm — which year did you mean?"
- If the customer mentions only a time without AM or PM (e.g. "3 o'clock" or "10:30"), always ask: "Is that AM or PM?"
- Never book or confirm an appointment with an ambiguous or past date/time.
- Always confirm the full date (including year) and time (including AM/PM) before calling the finish_call tool.
"""


def build_agent_instructions(
    agent_type: str,
    custom_script: str,
    customer_name: str,
) -> str:
    """
    Compose the full system prompt for the agent from:
    - base persona (derived from agent_type)
    - the campaign-specific custom script
    - the pre-known customer name
    - mandatory date/time validation rules
    """
    base = AGENT_BASE_PROMPTS.get(agent_type, AGENT_BASE_PROMPTS["Voice-A (Sales)"])
    name_clause = (
        f"\nIMPORTANT: You already know the customer's name is '{customer_name}'. "
        "Do NOT ask them for their name — address them by name when appropriate."
        if customer_name.strip()
        else ""
    )

    return f"""{base}
{name_clause}

RULES:
- Keep every response under 2 sentences.
- Be polite and professional.
- Do not hallucinate or invent details.
- Do not discuss unrelated topics.
- Follow the custom script below faithfully.

CAMPAIGN-SPECIFIC SCRIPT:
{custom_script}

{DATE_TIME_VALIDATION_RULES}

When calling finish_call, pass:
  - customer_name: the customer's name
  - appointment_date: the confirmed future date (with year)
  - appointment_time: the confirmed time (with AM/PM)

Do NOT say anything else after the customer confirms — just call the tool.
The tool will handle the goodbye message automatically.
"""


import shutil
import numpy as np


def mix_wav_files(file1: str, file2: str, output_file: str):
    """Mix two WAV files of the same sample rate and format into a single WAV file."""
    try:
        w1 = wave.open(file1, 'rb')
        w2 = wave.open(file2, 'rb')
    except Exception as e:
        print(f"[mixer] Error opening files to mix: {e}")
        # If one file fails to open, copy the other one as fallback
        for f in (file1, file2):
            try:
                if os.path.exists(f):
                    shutil.copy(f, output_file)
                    print(f"[mixer] Copied single track {f} -> {output_file}")
                    # Clean up the original
                    os.remove(f)
                    return
            except Exception as copy_err:
                print(f"[mixer] Copy fallback failed for {f}: {copy_err}")
        return

    try:
        params = w1.getparams()
        
        f1_data = w1.readframes(w1.getnframes())
        f2_data = w2.readframes(w2.getnframes())
        
        w1.close()
        w2.close()
        
        # Convert to signed 16-bit PCM arrays
        a1 = np.frombuffer(f1_data, dtype=np.int16)
        a2 = np.frombuffer(f2_data, dtype=np.int16)
        
        # Pad shorter array with zeros to match lengths
        max_len = max(len(a1), len(a2))
        if len(a1) < max_len:
            a1 = np.pad(a1, (0, max_len - len(a1)), 'constant')
        if len(a2) < max_len:
            a2 = np.pad(a2, (0, max_len - len(a2)), 'constant')
            
        # Sum the signals (as int32 to avoid overflow) and clip to 16-bit range
        mixed = a1.astype(np.int32) + a2.astype(np.int32)
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        
        out = wave.open(output_file, 'wb')
        out.setparams(params)
        out.writeframes(mixed.tobytes())
        out.close()
        print(f"[mixer] Successfully mixed {file1} and {file2} into {output_file}")
        
        # Clean up temporary individual files
        os.remove(file1)
        os.remove(file2)
    except Exception as e:
        print(f"[mixer] Error mixing WAV files: {e}")


async def record_track(track: rtc.Track, call_id: int, speaker: str = "customer"):
    """Record an audio track (customer or agent) into a local WAV file."""
    os.makedirs("recordings", exist_ok=True)
    filename = f"recordings/call_{call_id}_{speaker}.wav"
    
    print(f"[recorder] Started recording {speaker} track for call {call_id} -> {filename}")
    audio_stream = rtc.AudioStream(track)
    wav_file = None
    try:
        async for frame_event in audio_stream:
            frame = frame_event.frame
            if wav_file is None:
                wav_file = wave.open(filename, 'wb')
                wav_file.setnchannels(frame.num_channels)
                wav_file.setsampwidth(2)  # 16-bit PCM is 2 bytes
                wav_file.setframerate(frame.sample_rate)
            wav_file.writeframes(frame.data)
    except Exception as e:
        print(f"[recorder] Error recording {speaker} for call {call_id}: {e}")
    finally:
        if wav_file:
            wav_file.close()
        print(f"[recorder] Finished recording {speaker} track for call {call_id}")


class DynamicAgent(Agent):
    """Agent whose behaviour is fully driven by the campaign configuration."""

    def __init__(self, agent_type: str, custom_script: str, customer_name: str):
        instructions = build_agent_instructions(agent_type, custom_script, customer_name)
        super().__init__(
            instructions=instructions,
            tools=[finish_call],
        )


async def _get_campaign_info(call_id: int) -> dict:
    """
    Look up the campaign and contact for a given call_id so the agent
    can use the correct script, agent type, and customer name.
    Returns a dict with keys: agent_type, script, customer_name.
    """
    try:
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if call is None:
                print(f"[agent] Warning: call {call_id} not found in DB")
                return {"agent_type": "Voice-A (Sales)", "script": "", "customer_name": ""}

            contact = await db.get(Contact, call.contact_id)

            # Trace up to campaign via job
            from app.models.job import Job
            job = await db.get(Job, call.job_id)
            campaign = await db.get(Campaign, job.campaign_id) if job else None

            return {
                "agent_type": campaign.agent if campaign else "Voice-A (Sales)",
                "script": campaign.script if campaign else "",
                "customer_name": contact.name if contact else "",
            }
    except Exception as e:
        print(f"[agent] Warning: could not fetch campaign info for call {call_id}: {e}")
        return {"agent_type": "Voice-A (Sales)", "script": "", "customer_name": ""}


async def entrypoint(ctx: JobContext):

    print("=" * 60)
    print("JOB RECEIVED")
    print("=" * 60)

    room_name = ctx.room.name

    # ── Extract call_id from room name (format: "call-{call_id}") ────────────
    try:
        call_id = int(room_name.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        call_id = -1
        print(f"[agent] Warning: could not parse call_id from room name: {room_name}")

    # Register event listeners BEFORE connecting to ensure we don't miss early events
    shutdown_event = asyncio.Event()

    @ctx.room.on("disconnected")
    def on_room_disconnected(*args):
        shutdown_event.set()

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity == "customer":
            asyncio.create_task(record_track(track, call_id))

    try:
        await ctx.connect()
        print(f"Connected to room: {ctx.room.name}")

        # Scan for already subscribed audio tracks from pre-existing customer participant
        for participant in ctx.room.remote_participants.values():
            if participant.identity == "customer":
                for publication in participant.track_publications.values():
                    if publication.subscribed and publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                        print(f"[recorder] Found pre-existing subscribed customer audio track: {publication.track.sid}")
                        asyncio.create_task(record_track(publication.track, call_id))

        # ── Fetch campaign info to drive the agent's behaviour ───────────────────
        campaign_info = await _get_campaign_info(call_id)
        agent_type    = campaign_info["agent_type"]
        custom_script = campaign_info["script"]
        customer_name = campaign_info["customer_name"]

        print(f"[agent] Agent type   : {agent_type}")
        print(f"[agent] Customer name: {customer_name}")
        print(f"[agent] Script length: {len(custom_script)} chars")

        async def _handle_unexpected_disconnect(reason: str):
            # If finish_call already ran, ACTIVE_CALLS entry is already gone.
            state = ACTIVE_CALLS.pop(room_name, None)
            if state is None:
                return

            print(
                f"Customer disconnected before finish_call ran ({reason}). "
                f"Notifying backend so the campaign can continue."
            )

            # Try to save a partial transcript even for unexpected disconnects.
            session = state.get("session")
            transcript = _build_transcript(session) if session else ""

            # Mix WAV tracks
            if call_id != -1:
                try:
                    mix_wav_files(
                        f"recordings/call_{call_id}_customer.wav",
                        f"recordings/call_{call_id}_agent.wav",
                        f"recordings/call_{call_id}.wav"
                    )
                except Exception as mix_err:
                    print(f"Warning – mixing audio failed: {mix_err}")

            await notify_call_complete(
                room_name,
                payload={
                    "transcript": transcript or None,
                    "customer_name": None,
                    "appointment_date": None,
                    "appointment_time": None,
                    "recording_url": f"/api/recordings/call_{call_id}.wav",
                },
            )

            # Close the agent session cleanly
            if session:
                try:
                    print("Closing AgentSession...")
                    await asyncio.wait_for(session.aclose(), timeout=5.0)
                    print("AgentSession closed.")
                except Exception as e:
                    print(f"Warning – session.aclose() error: {e}")

            # Delete the LiveKit room to hang up the SIP call
            try:
                print("Deleting LiveKit room (this hangs up the SIP call)...")
                lkapi = api.LiveKitAPI()
                try:
                    await lkapi.room.delete_room(
                        api.DeleteRoomRequest(room=room_name)
                    )
                    print("Room deleted successfully.")
                finally:
                    await lkapi.aclose()
            except Exception as e:
                print(f"Warning – room deletion error: {e}")


        @ctx.room.on("participant_disconnected")
        def on_participant_disconnected(participant: rtc.RemoteParticipant):
            if participant.identity == "customer":
                asyncio.create_task(
                    _handle_unexpected_disconnect("customer hung up")
                )

        session = AgentSession(
            stt=sarvam.STT(),

            llm=openai.LLM(
                model="deepseek-chat",
                api_key=os.getenv("DEEPSEEK_API_KEY") or "",
                base_url="https://api.deepseek.com/v1",
            ),

            tts=sarvam.TTS(),
        )

        await session.start(
            room=ctx.room,
            agent=DynamicAgent(
                agent_type=agent_type,
                custom_script=custom_script,
                customer_name=customer_name,
            ),
        )

        print("Session started")

        # Identify the local agent track to record it as well
        agent_track = None
        for _ in range(30):  # Wait up to 3 seconds
            for pub in ctx.room.local_participant.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    agent_track = pub.track
                    break
            if agent_track:
                break
            await asyncio.sleep(0.1)

        if agent_track:
            asyncio.create_task(record_track(agent_track, call_id, speaker="agent"))
        else:
            print("[agent] Warning: local agent audio track not found for recording")

        ACTIVE_CALLS[ctx.room.name] = {
            "session": session,
        }

        print(f"Registered active call: {ctx.room.name}")

        # Wait for the SIP customer to actually answer and join the room.
        # Since wait_until_answered=False, the room exists before the call
        # is picked up, so we must not greet until the participant is present.
        print("Waiting for customer participant to join...")
        customer_joined = False
        for _ in range(60):  # wait up to 60 seconds
            participants = ctx.room.remote_participants
            if any(p.identity == "customer" for p in participants.values()):
                customer_joined = True
                print("Customer participant joined — starting greeting.")
                break
            await asyncio.sleep(1)

        if not customer_joined:
            print("Timeout: customer never joined. Notifying backend and exiting.")
            ACTIVE_CALLS.pop(room_name, None)
            await notify_call_complete(
                room_name,
                payload={
                    "transcript": None,
                    "customer_name": None,
                    "appointment_date": None,
                    "appointment_time": None,
                },
            )
            shutdown_event.set()
        else:
            # Small buffer to let audio pipeline stabilize
            await asyncio.sleep(0.5)

            # Build a personalised greeting using customer name if available
            greeting_instructions = (
                f"Greet the customer by name ('{customer_name}') and introduce yourself. "
                "Then follow the campaign script to begin the conversation."
                if customer_name.strip()
                else
                "Introduce yourself and begin the conversation following the campaign script."
            )

            await session.generate_reply(instructions=greeting_instructions)

            print("Greeting sent")

        # Keep the entrypoint alive until the room is deleted.
        # finish_call deletes the LiveKit room → LiveKit fires the
        # 'disconnected' event → shutdown_event is set → we exit here.
        await shutdown_event.wait()

        print("Entrypoint shutting down.")

    except Exception as e:
        print(f"[agent] Fatal error in entrypoint: {e}")
        if call_id != -1:
            try:
                from app.services.call_service import CallService
                async with AsyncSessionLocal() as db:
                    await CallService.fail_call(db=db, call_id=call_id)
                    print(f"[agent] Call {call_id} marked as failed in DB due to crash.")
            except Exception as db_err:
                print(f"[agent] Failed to mark call {call_id} as failed in DB: {db_err}")
        raise e

    finally:
        # Safety cleanup in case finish_call never ran (e.g. connection error).
        ACTIVE_CALLS.pop(ctx.room.name, None)
        print(f"Removed active call: {ctx.room.name}")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )