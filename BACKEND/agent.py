from dotenv import load_dotenv
import asyncio
import os
import wave
import re
import socket
import sys
from datetime import datetime, timezone

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
# ── Agent type → base system prompt ───────────────────────────────────────────
AGENT_BASE_PROMPTS: dict[str, str] = {
    "Voice-E (Tax Agent)": (
        "You are a professional and knowledgeable tax advisor making outbound calls. "
        "Your goal is to assist customers with their tax filing requirements, answer questions about "
        "deductions, and schedule appointments with tax professionals if needed."
    ),
    "Meera (Morning Tax)": (
        "You are Meera, a friendly and professional tax consultant calling on behalf of Morning Tax. "
        "Your goal is to educate prospects about tax savings opportunities — including amended return reviews, "
        "year-end tax planning, IRS notice resolution, and cross-border tax services — and to book a "
        "fifteen-minute consultation with a Senior Tax Strategist. "
        "Speak at a moderate pace, never interrupt the customer, keep responses under two to three sentences, "
        "ask one question at a time, and always wait for the customer's response before continuing. "
        "Never guarantee refunds, never promise tax savings, and never provide legal or tax advice."
    ),
    "Raj (Morning Tax)": (
        "You are Raj, a friendly and professional tax consultant calling on behalf of Morning Tax. "
        "Your goal is to educate prospects about tax savings opportunities — including amended return reviews, "
        "year-end tax planning, IRS notice resolution, and cross-border tax services — and to book a "
        "fifteen-minute consultation with a Senior Tax Strategist. "
        "Speak at a moderate pace, never interrupt the customer, keep responses under two to three sentences, "
        "ask one question at a time, and always wait for the customer's response before continuing. "
        "Never guarantee refunds, never promise tax savings, and never provide legal or tax advice."
    ),
    "John (Morning Tax)": (
        "You are Meera, a friendly and professional tax consultant calling on behalf of Morning Tax. "
        "Your goal is to educate prospects about tax savings opportunities — including amended return reviews, "
        "year-end tax planning, IRS notice resolution, and cross-border tax services — and to book a "
        "fifteen-minute consultation with a Senior Tax Strategist. "
        "Speak at a moderate pace, never interrupt the customer, keep responses under two to three sentences, "
        "ask one question at a time, and always wait for the customer's response before continuing. "
        "Never guarantee refunds, never promise tax savings, and never provide legal or tax advice."
    ),
}

# ── Date/time validation rules injected into every agent ──────────────────────
DATE_TIME_VALIDATION_RULES = """
TIME & APPOINTMENT VALIDATION RULES:
- If the customer mentions a time without AM or PM (e.g. "3 o'clock" or "10:30"), ask: "Is that AM or PM?"
- When calling finish_call, pass appointment_date in YYYY-MM-DD format (e.g. "2026-07-29") and appointment_time with AM/PM (e.g. "02:00 PM").
"""


def build_agent_instructions(
    agent_type: str,
    custom_script: str,
    customer_name: str,
) -> str:
    """
    Compose the full system prompt for the agent from:
    - base persona (derived from agent_type)
    - dynamic real-time date/time context (IST)
    - the campaign-specific custom script
    - the pre-known customer name
    - mandatory date/time validation rules
    """
    from datetime import datetime, timezone, timedelta

    # Dynamic real-time date resolution in IST (UTC+5:30)
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_date = ist_now.strftime("%Y-%m-%d")
    today_readable = ist_now.strftime("%A, %B %d, %Y")
    today_time = ist_now.strftime("%I:%M %p IST")
    tomorrow_date = (ist_now + timedelta(days=1)).strftime("%Y-%m-%d (%A, %B %d)")
    day_after_date = (ist_now + timedelta(days=2)).strftime("%Y-%m-%d (%A, %B %d)")

    date_context = f"""
CURRENT DATE & TIME INFORMATION (DYNAMIC REAL-TIME CONTEXT):
- Today's Date: {today_readable} (ISO: {today_date})
- Today's Time: {today_time}
- Current Year: {ist_now.year}
- Calculated Relative Dates for Reference:
  * "tomorrow" = {tomorrow_date}
  * "day after tomorrow" = {day_after_date}

DATE RESOLUTION RULES:
- You know today's exact date is {today_readable}.
- When a customer mentions relative dates like "tomorrow", "day after tomorrow", "this Thursday", or "next Monday", automatically resolve the exact date without asking the customer for the year or date!
- Assume the current year ({ist_now.year}) for any date mentioned by the customer. NEVER ask the customer "what year?" or "which year?".
- If the customer specifies only a day and month (e.g., "August 5th"), assume {ist_now.year} automatically.
- If the customer specifies a date in the past relative to Today ({today_date}), politely inform them: "I'm sorry, that date has already passed. Could you please provide a future date?"
"""

    base = AGENT_BASE_PROMPTS.get(agent_type, AGENT_BASE_PROMPTS["Meera (Morning Tax)"])
    name_clause = (
        f"\nIMPORTANT: You already know the customer's name is '{customer_name}'. "
        "Do NOT ask them for their name — address them by name when appropriate."
        if customer_name.strip()
        else ""
    )

    return f"""{base}
{name_clause}

{date_context}

CRITICAL MANDATORY TOOL CALL RULE:
You have access to a tool named `finish_call`.
Whenever the customer says goodbye, declines, says not interested, confirms an appointment, or indicates the conversation is over:
You MUST call the `finish_call` tool immediately! Do NOT reply with text when concluding — invoke the `finish_call` tool instead.

RULES:
- Keep every response under 2 sentences.
- Be polite and professional.
- Do not hallucinate or invent details.
- Do not discuss unrelated topics.
- Follow the custom script below faithfully.

CAMPAIGN-SPECIFIC SCRIPT:
{custom_script}

{DATE_TIME_VALIDATION_RULES}

REMINDER ON HANGUP:
Whenever the conversation reaches its end (whether appointment booked, customer declined, or customer says goodbye), call `finish_call` immediately with:
  - customer_name: the customer's name
  - appointment_date: the confirmed future date (formatted as YYYY-MM-DD, e.g. "{today_date}")
  - appointment_time: the confirmed time (with AM/PM, if booked)
"""


import shutil
import numpy as np
import time


def mix_wav_files(file1: str, file2: str, output_file: str):
    """Mix two WAV files of the same sample rate and format into a single WAV file."""
    w1, w2 = None, None
    for attempt in range(5):
        try:
            if os.path.exists(file1) and os.path.exists(file2):
                w1 = wave.open(file1, 'rb')
                w2 = wave.open(file2, 'rb')
                break
        except Exception as e:
            if attempt < 4:
                time.sleep(0.4)
            else:
                print(f"[mixer] Error opening files to mix after retries: {e}")

    if w1 is None or w2 is None:
        if w1: w1.close()
        if w2: w2.close()
        # If one file fails to open, copy the other one as fallback
        for f in (file1, file2):
            try:
                if os.path.exists(f):
                    shutil.copy(f, output_file)
                    print(f"[mixer] Copied single track {f} -> {output_file}")
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
                return {"agent_type": "Voice-A (Sales)", "script": "", "customer_name": "", "metadata_fields": {}}

            contact = await db.get(Contact, call.contact_id)

            # Trace up to campaign via job
            from app.models.job import Job
            job = await db.get(Job, call.job_id)
            campaign = await db.get(Campaign, job.campaign_id) if job else None

            return {
                "agent_type": campaign.agent if campaign else "Voice-E (Tax Agent)",
                "script": campaign.script if campaign else "",
                "customer_name": contact.name if contact else "",
                "metadata_fields": contact.metadata_fields if contact else {},
            }
    except Exception as e:
        print(f"[agent] Warning: could not fetch campaign info for call {call_id}: {e}")
        return {"agent_type": "Voice-E (Tax Agent)", "script": "", "customer_name": "", "metadata_fields": {}}


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
        # If finish_call already handled this room (intentional disconnect), skip.
        # Otherwise the room dropped unexpectedly (SIP timeout, network failure, trunk drop).
        state = ACTIVE_CALLS.get(room_name)
        if state is not None and not state.get("finishing"):
            # Room dropped before finish_call ran — save transcript and notify backend
            asyncio.create_task(_handle_room_disconnect())
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
        base_script   = campaign_info["script"]
        customer_name = campaign_info["customer_name"]
        metadata      = campaign_info["metadata_fields"] or {}
        
        # Include customer_name in metadata for uniform replacement
        metadata_dict = {k.lower(): str(v) for k, v in metadata.items()}
        metadata_dict["customer_name"] = customer_name
        metadata_dict["customer name"] = customer_name

        def _replace_placeholder(match):
            key = match.group(1).strip().lower()
            return metadata_dict.get(key, "")

        custom_script = re.sub(r"\{\{(.*?)\}\}", _replace_placeholder, base_script)

        print(f"[agent] Agent type   : {agent_type}")
        print(f"[agent] Customer name: {customer_name}")
        print(f"[agent] Script length: {len(custom_script)} chars")

        async def _handle_room_disconnect():
            """
            Called when the LiveKit room itself disconnects unexpectedly
            (SIP trunk timeout, network drop, server-side room deletion).
            Saves partial transcript and notifies backend so the worker can advance.
            """
            state = ACTIVE_CALLS.pop(room_name, None)
            if state is None:
                return  # finish_call already handled cleanup

            print(
                f"[agent] Room disconnected unexpectedly — saving transcript and notifying backend."
            )

            session = state.get("session")
            transcript = _build_transcript(session) if session else ""

            # Mix WAV tracks — sleep so recorder coroutine can close file handles
            if call_id != -1:
                try:
                    await asyncio.sleep(1.5)
                    mix_wav_files(
                        f"recordings/call_{call_id}_customer.wav",
                        f"recordings/call_{call_id}_agent.wav",
                        f"recordings/call_{call_id}.wav"
                    )
                except Exception as mix_err:
                    print(f"Warning – mixing audio failed: {mix_err}")

            try:
                await notify_call_complete(
                    room_name,
                    payload={
                        "transcript": transcript or None,
                        "customer_name": None,
                        "appointment_date": None,
                        "appointment_time": None,
                        "recording_url": f"/api/recordings/call_{call_id}.wav" if call_id != -1 else None,
                    },
                )
            except Exception as e:
                print(f"Warning – backend notify error: {e}")

            # Close session cleanly
            if session:
                try:
                    await asyncio.wait_for(session.aclose(), timeout=5.0)
                except Exception:
                    pass

        async def _handle_unexpected_disconnect(reason: str):
            # If finish_call is already running, let it complete — don't race with it.
            state_peek = ACTIVE_CALLS.get(room_name)
            if state_peek is not None and state_peek.get("finishing"):
                print(
                    f"Customer disconnected but finish_call is already running — "
                    f"letting finish_call handle cleanup."
                )
                return

            # If finish_call already completed, ACTIVE_CALLS entry is gone.
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

            # Mix WAV tracks — sleep so recorder coroutine can close file handles
            if call_id != -1:
                try:
                    await asyncio.sleep(1.5)
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

            # Delete the LiveKit room to hang up any remaining SIP leg
            try:
                lk_url = os.getenv("LIVEKIT_URL", "").replace("ws://", "http://").replace("wss://", "https://")
                lk_key = os.getenv("LIVEKIT_API_KEY")
                lk_secret = os.getenv("LIVEKIT_API_SECRET")
                lkapi = api.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret) if lk_url else api.LiveKitAPI()
                try:
                    await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
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

        # Dynamic voice selection based on agent_type
        speaker_voice = "ashutosh" if "Raj" in agent_type else "shreya"
        print(f"[agent] Selected TTS speaker: {speaker_voice} for agent: {agent_type}")

        session = AgentSession(
            stt=sarvam.STT(),

            llm=openai.LLM(
                model="deepseek-chat",
                api_key=os.getenv("DEEPSEEK_API_KEY") or "",
                base_url="https://api.deepseek.com/v1",
            ),

            tts=sarvam.TTS(
                speaker=speaker_voice,
                speech_sample_rate=16000,
            ),
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
        print("Waiting for customer participant to pick up call...")
        customer_joined = False
        for i in range(120):  # wait up to 60 seconds (120 * 0.5s)
            participants = ctx.room.remote_participants
            customer_p = next((p for p in participants.values() if p.identity == "customer"), None)
            if customer_p:
                # Customer participant exists. Check if audio track published or fallback after 3s of presence
                if len(customer_p.track_publications) > 0 or i >= 6:
                    customer_joined = True
                    print(f"Customer connected (tracks={len(customer_p.track_publications)}) — starting greeting.")
                    if call_id != -1:
                        try:
                            async with AsyncSessionLocal() as db:
                                c = await db.get(Call, call_id)
                                if c:
                                    c.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                                    await db.commit()
                        except Exception as err:
                            print(f"[agent] Failed to set started_at for call {call_id}: {err}")
                    break
            await asyncio.sleep(0.5)

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
        state = ACTIVE_CALLS.get(ctx.room.name)
        if state and state.get("finishing"):
            print(f"[{ctx.room.name}] Agent shutting down, but finish_call is running. Waiting up to 10s...")
            for _ in range(10):
                if ctx.room.name not in ACTIVE_CALLS:
                    print(f"[{ctx.room.name}] finish_call completed successfully.")
                    break
                await asyncio.sleep(1)
            else:
                print(f"[{ctx.room.name}] Timeout waiting for finish_call. Force shutting down.")
        else:
            # Give any pending disconnect callbacks time to finish saving transcripts
            await asyncio.sleep(2)

        # Safety cleanup in case finish_call never ran or timed out.
        ACTIVE_CALLS.pop(ctx.room.name, None)
        print(f"Removed active call: {ctx.room.name}")


if __name__ == "__main__":
    # Prevent multiple agent processes from running simultaneously
    try:
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        lock_socket.bind(('127.0.0.1', 49152))
    except socket.error:
        print("Error: Another instance of the agent is already running.")
        print("Please stop it before starting a new one to prevent multiple agents in a call.")
        sys.exit(1)

    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "callinggen-agent-prod")
    print(f"[agent] Registering LiveKit agent worker with name: '{agent_name}'")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=agent_name,
        )
    )