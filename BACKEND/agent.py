from typing import Any
from dotenv import load_dotenv
import asyncio
import os
import wave
import re
import socket
import sys

from app.services.conversation_state import ACTIVE_CALLS
from backend_client import notify_call_complete
from finish_call import finish_call, _build_transcript
from whatsapp_tool import send_whatsapp_info

from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)

from livekit.plugins import sarvam, openai, silero

# Database access to read campaign + contact at runtime
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.call import Call
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.models.agent import Agent as AgentModel

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

# ── Date/time & Call Termination rules injected into every agent ─────────────
DATE_TIME_VALIDATION_RULES = """
TIME & APPOINTMENT VALIDATION RULES:
- If the customer mentions a time without AM or PM (e.g. "3 o'clock" or "10:30"), ask: "Is that AM or PM?"
- When calling finish_call, pass appointment_date in YYYY-MM-DD format (e.g. "2026-07-29") and appointment_time with AM/PM (e.g. "02:00 PM").

CALL TERMINATION & FINISH_CALL RULES:
- IF THE CUSTOMER SAYS "NOT INTERESTED", "NO THANKS", "DON'T CALL ME", "NOT REQUIRED", OR DECLINES:
  1. Say: "No problem at all. Thank you for your time, and have a great day!"
  2. IMMEDIATELY CALL THE `finish_call` TOOL! NEVER CONTINUE ASKING QUESTIONS OR PROLONG THE CALL AFTER DECLINE.
- IF THE CUSTOMER SAYS "GOODBYE", "BYE", "THANK YOU", OR INDICATES HANGUP:
  1. IMMEDIATELY CALL THE `finish_call` TOOL!
- IF A CALLBACK OR APPOINTMENT IS CONFIRMED:
  1. Confirm the date/time.
  2. IMMEDIATELY CALL THE `finish_call` TOOL!
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
    # Dynamic day-of-week mapping for the next 7 days
    days_mapping = []
    for i in range(1, 8):
        future_dt = ist_now + timedelta(days=i)
        day_name = future_dt.strftime("%A")
        iso_date = future_dt.strftime("%Y-%m-%d")
        days_mapping.append(f"  * \"this {day_name}\" / \"next {day_name}\" = {iso_date} ({future_dt.strftime('%B %d, %Y')})")
    days_context_str = "\n".join(days_mapping)

    date_context = f"""
CURRENT DATE & TIME INFORMATION (DYNAMIC REAL-TIME CONTEXT):
- Today's Date: {today_readable} (ISO: {today_date})
- Today's Time: {today_time}
- Current Year: {ist_now.year}
- Calculated Relative Dates for Reference:
  * "today" = {today_date} ({today_readable})
  * "tomorrow" = {tomorrow_date}
  * "day after tomorrow" = {day_after_date}
{days_context_str}

DATE & CALLBACK RESOLUTION RULES:
- You know today's exact date is {today_readable} (ISO: {today_date}).
- IF THE CUSTOMER ASKS TO BE CALLED BACK TODAY (e.g. "call me today at 5:30 PM", "after 5:30 PM today", or "today 5 PM"):
  * ACCEPT TODAY IMMEDIATELY! Do NOT suggest tomorrow, do NOT ask "would tomorrow work better?".
  * Use today's date ({today_date}) and confirm the requested time.
- When the customer mentions a day of the week (e.g. "this Friday", "next Monday", "this Thursday"):
  * Automatically resolve it to the exact calculated date listed above without asking the customer for the year or date.
- Assume current year ({ist_now.year}) for any date mentioned. NEVER ask "what year?".
- If the customer specifies a date in the past relative to Today ({today_date}), politely inform them: "I'm sorry, that date has already passed. Could you please provide a future date?"
"""

    if agent_type in AGENT_BASE_PROMPTS:
        base = AGENT_BASE_PROMPTS[agent_type]
    else:
        base = f"You are {agent_type}, a professional AI calling assistant."
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
- Do not repeat questions or greeting lines you have already spoken.
- If the customer confirms (e.g. "Yes", "Speaking", "Hello"), do NOT ask to speak with them again — proceed directly to the introduction.
- Follow the script verbatim — NEVER hallucinate, invent unverified claims, or discuss topics outside the script.
- Follow the custom script below faithfully.

WHATSAPP ACTION TOOL:
You have access to a tool named `send_whatsapp_info`.
If the customer asks to receive information on WhatsApp (e.g. "send brochure", "send pricing", "send website", "send booking link", "send contact details"):
Call `send_whatsapp_info` with the matching action (e.g. action="SEND_BROCHURE", "SEND_PRICING", "SEND_WEBSITE", etc.) and naturally confirm to the customer.

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
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[recorder] Error recording {speaker} for call {call_id}: {e}")
    finally:
        if wav_file:
            try:
                wav_file.close()
            except Exception:
                pass
        try:
            await audio_stream.aclose()
        except Exception:
            pass
        print(f"[recorder] Finished recording {speaker} track for call {call_id}")


class VoicemailDetector:
    def __init__(self, session: AgentSession, timeout_seconds: int = 45):
        self.session = session
        self.timeout = timeout_seconds
        self.trigger_phrases = [
            "please leave a message",
            "leave your message after the tone",
            "leave a message after the beep",
            "the person you are trying to reach is not available",
            "the person you're trying to reach is not available",
            "your call has been forwarded to an automated voice mail",
            "your call has been forwarded to voice mail",
            "record your message",
            "this call is being screened",
            "state your name and why you're calling",
            "leave a brief message after the tone",
            "please leave your name and number",
            "is not available to take your call",
            "google subscriber",
            "textmail subscriber",
        ]

    async def run(self):
        """
        Poll the current transcript. If a voicemail trigger phrase is detected,
        return a detection metadata dict. 
        If timeout is reached or human interaction confident, return None.
        """
        start_time = asyncio.get_event_loop().time()
        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > self.timeout:
                    return None
                
                transcript = _build_transcript(self.session)
                if not transcript:
                    await asyncio.sleep(1.0)
                    continue
                    
                lower_transcript = transcript.lower()
                
                # Stop detecting if it looks like a real conversation (multiple turns)
                if transcript.count('\n') >= 8:
                    return None
                    
                for phrase in self.trigger_phrases:
                    if phrase in lower_transcript:
                        return {
                            "type": "voicemail",
                            "trigger": phrase,
                            "confidence": 99.0,
                            "credits_charged": False
                        }
                        
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass



class DynamicAgent(Agent):
    """Agent whose behaviour is fully driven by the campaign configuration."""

    def __init__(self, agent_type: str, custom_script: str, customer_name: str):
        instructions = build_agent_instructions(agent_type, custom_script, customer_name)
        super().__init__(
            instructions=instructions,
            tools=[finish_call, send_whatsapp_info],
        )


async def _get_campaign_info(call_id: int) -> dict:
    """
    Look up the campaign and contact for a given call_id so the agent
    can use the correct script, agent type, and customer name.
    Returns a dict with keys: agent_type, script, customer_name, voice.
    """
    try:
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if call is None:
                print(f"[agent] Warning: call {call_id} not found in DB")
                return {"agent_type": "Voice-E (Tax Agent)", "script": "", "customer_name": "", "metadata_fields": {}, "voice": "Meera"}

            contact = await db.get(Contact, call.contact_id)

            # Trace up to campaign via job
            from app.models.job import Job
            job = await db.get(Job, call.job_id)
            campaign = await db.get(Campaign, job.campaign_id) if job else None

            voice_profile = "Meera"  # Default fallback
            if campaign:
                agent_stmt = select(AgentModel).where(
                    AgentModel.name == campaign.agent,
                    AgentModel.user_id == campaign.user_id
                )
                agent_res = await db.execute(agent_stmt)
                agent_obj = agent_res.scalars().first()
                if agent_obj:
                    voice_profile = agent_obj.voice
                else:
                    # Fallback for dynamic demo personas
                    if "Alex" in campaign.agent:
                        voice_profile = "Raj"  # Male voice
                    elif "James" in campaign.agent:
                        voice_profile = "Hitesh" # Male voice
                    elif "Sarah" in campaign.agent:
                        voice_profile = "Vidya" # Female voice
                    elif "Voice-E" in campaign.agent:
                        voice_profile = "Meera" # Female voice

            return {
                "agent_type": campaign.agent if campaign else "Voice-E (Tax Agent)",
                "script": campaign.script if campaign else "",
                "customer_name": contact.name if contact else "",
                "metadata_fields": contact.metadata_fields if contact else {},
                "voicemail_detection": campaign.voicemail_detection if campaign else None,
                "voice": voice_profile,
            }
    except Exception as e:
        print(f"[agent] Warning: could not fetch campaign info for call {call_id}: {e}")
        return {"agent_type": "Voice-E (Tax Agent)", "script": "", "customer_name": "", "metadata_fields": {}, "voice": "Meera"}


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
    background_tasks: list[asyncio.Task] = []

    async def _cancel_background_tasks():
        for t in background_tasks:
            if not t.done():
                t.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        background_tasks.clear()

    @ctx.room.on("disconnected")
    def on_room_disconnected(*args):
        # If finish_call already handled this room (intentional disconnect), skip.
        # Otherwise the room dropped unexpectedly (SIP timeout, network failure, trunk drop).
        state = ACTIVE_CALLS.get(room_name)
        if state is not None and not state.get("finishing"):
            # Room dropped before finish_call ran — save transcript and notify backend
            asyncio.create_task(_handle_room_disconnect())
        shutdown_event.set()


    async def _evict_unauthorized_participant(identity: str):
        if identity == "customer" or identity == ctx.room.local_participant.identity:
            return
        try:
            print(f"[agent] Shield: Evicting unauthorized participant '{identity}' from room '{ctx.room.name}'...")
            lk_url = os.getenv("LIVEKIT_URL")
            lk_key = os.getenv("LIVEKIT_API_KEY")
            lk_secret = os.getenv("LIVEKIT_API_SECRET")
            lkapi = api.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret) if lk_url else api.LiveKitAPI()
            try:
                await lkapi.room.remove_participant(
                    api.RoomParticipantIdentity(room=ctx.room.name, identity=identity)
                )
                print(f"[agent] Successfully kicked unauthorized participant '{identity}' from room '{ctx.room.name}'!")
            finally:
                await lkapi.aclose()
        except Exception as err:
            print(f"[agent] Notice during participant eviction: {err}")

    @ctx.room.on("participant_connected")
    def on_participant_connected_shield(p: rtc.RemoteParticipant):
        if p.identity != "customer":
            print(f"[agent] Rogue participant detected on join: '{p.identity}'. Evicting immediately!")
            asyncio.create_task(_evict_unauthorized_participant(p.identity))

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if participant.identity != "customer":
            print(f"[agent] Muting/ignoring audio track from rogue participant '{participant.identity}'")
            try:
                publication.set_subscribed(False)
            except Exception:
                pass
            return
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            t = asyncio.create_task(record_track(track, call_id))
            background_tasks.append(t)

    # Cross-process atomic file lock per room BEFORE connecting to guarantee strictly 1 agent process enters the room
    import tempfile
    lock_file_path = os.path.join(tempfile.gettempdir(), f"livekit_room_{ctx.room.name}.lock")
    try:
        if os.path.exists(lock_file_path):
            # If lock is older than 2 minutes, consider it stale from a dead process
            if time.time() - os.path.getmtime(lock_file_path) > 120:
                try:
                    os.remove(lock_file_path)
                except Exception:
                    pass
        lock_fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(lock_fd)
    except FileExistsError:
        print(f"[agent] Atomic lock file '{lock_file_path}' exists! Another agent process is handling room '{ctx.room.name}'. Rejecting duplicate job BEFORE connect.")
        return

    try:
        await ctx.connect()
        print(f"Connected to room: {ctx.room.name}")

        # Evict any rogue/unauthorized agent participants that entered before us
        for p in list(ctx.room.remote_participants.values()):
            if p.identity != "customer":
                print(f"[agent] Detected pre-existing rogue participant '{p.identity}'. Evicting immediately!")
                asyncio.create_task(_evict_unauthorized_participant(p.identity))

        # Scan for already subscribed audio tracks from pre-existing customer participant
        for participant in ctx.room.remote_participants.values():
            if participant.identity == "customer":
                for publication in participant.track_publications.values():
                    if publication.subscribed and publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                        print(f"[recorder] Found pre-existing subscribed customer audio track: {publication.track.sid}")
                        t = asyncio.create_task(record_track(publication.track, call_id))
                        background_tasks.append(t)

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
        
        async def _handle_voicemail_disconnect(metadata: dict):
            state = ACTIVE_CALLS.pop(room_name, None)
            if state is None:
                return
            print("Voicemail detected. Disconnecting immediately to avoid credits.")
            
            # 1. Notify backend immediately so it isn't cancelled by room deletion
            session = state.get("session")
            transcript = _build_transcript(session) if session else ""
            
            try:
                await notify_call_complete(
                    room_name,
                    payload={
                        "transcript": transcript or None,
                        "customer_name": None,
                        "appointment_date": None,
                        "appointment_time": None,
                        "recording_url": f"/api/recordings/call_{call_id}.wav",
                        "is_voicemail": True,
                        "detection_metadata": metadata,
                    },
                )
            except Exception as notify_err:
                print(f"Warning - notify failed: {notify_err}")

            # 2. Delete room immediately to drop the SIP call instantly (zero latency)
            try:
                lkapi = api.LiveKitAPI()
                try:
                    await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
                finally:
                    await lkapi.aclose()
            except Exception as e:
                print(f"Warning - room deletion error: {e}")
                
            # 3. Close session
            if session:
                try:
                    await asyncio.wait_for(session.aclose(), timeout=3.0)
                except: pass
                
            # 4. Cancel background audio/voicemail tasks so file handles are released
            await _cancel_background_tasks()
            await asyncio.sleep(0.5)
            
            if call_id != -1:
                try:
                    mix_wav_files(
                        f"recordings/call_{call_id}_customer.wav",
                        f"recordings/call_{call_id}_agent.wav",
                        f"recordings/call_{call_id}.wav"
                    )
                except Exception: pass

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

            # Cancel background tasks so audio writers flush and close handles
            await _cancel_background_tasks()
            await asyncio.sleep(0.5)

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

            # Cancel background tasks so audio writers flush and close handles
            await _cancel_background_tasks()
            await asyncio.sleep(0.5)

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

            # 1. ALWAYS notify backend FIRST
            try:
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
            except Exception as e:
                print(f"Warning – notify_call_complete error in disconnect handler: {e}")

            # 2. Close the agent session cleanly
            if session:
                try:
                    print("Closing AgentSession...")
                    await asyncio.wait_for(session.aclose(), timeout=3.0)
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

        # Dynamic voice selection mapping for Sarvam bulbul v2 compatible voices
        SARVAM_VOICE_MAPPING = {
            "Meera": "anushka",
            "Raj": "abhilash",
            "Manisha": "manisha",
            "Karun": "karun",
            "Vidya": "vidya",
            "Hitesh": "hitesh",
            "Female 1": "anushka",
            "Female 2": "anushka",
            "Male 1": "abhilash",
            "Male 2": "abhilash",
            "Nova (ElevenLabs)": "anushka",
        }
        db_voice = campaign_info.get("voice", "Meera")
        speaker_voice = SARVAM_VOICE_MAPPING.get(db_voice, "anushka")
        print(f"[agent] Configured agent voice profile: {db_voice} -> mapped to Sarvam speaker: {speaker_voice}")

        session = AgentSession(
            vad=silero.VAD.load(
                min_speech_duration=0.25,
                min_silence_duration=0.6,
                prefix_padding_duration=0.1,
            ),
            stt=sarvam.STT(
                model="saaras:v3",
                language="en-IN",
                mode="transcribe",
            ),

            llm=openai.LLM(
                model="deepseek-chat",
                api_key=os.getenv("DEEPSEEK_API_KEY") or "",
                base_url="https://api.deepseek.com/v1",
                temperature=0.2,
            ),

            tts=sarvam.TTS(
                model="bulbul:v2",
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

        # Start Voicemail Detector
        vd_config = campaign_info.get("voicemail_detection") or {"enabled": True, "timeout": 45}
        if vd_config.get("enabled"):
            async def run_voicemail_detector():
                detector = VoicemailDetector(session, timeout_seconds=vd_config.get("timeout", 45))
                result = await detector.run()
                if result:
                    print(f"Voicemail detected! {result}")
                    await _handle_voicemail_disconnect(result)
            vd_task = asyncio.create_task(run_voicemail_detector())
            background_tasks.append(vd_task)

        # Real-time transcript buffer for continuous failsafe preservation
        transcript_lines: list[str] = []
        setattr(session, "_transcript_lines", transcript_lines)

        @session.on("conversation_item_added")
        def _on_conversation_item(item: Any):
            try:
                r_val = getattr(item, "role", "")
                role = (r_val if isinstance(r_val, str) else str(r_val)).split(".")[-1].lower()
                text = getattr(item, "text_content", None)
                if not text:
                    raw = getattr(item, "content", [])
                    if isinstance(raw, str):
                        text = raw
                    elif isinstance(raw, list):
                        text = " ".join(c for c in raw if isinstance(c, str))
                    else:
                        text = str(raw)
                if role not in ("system", "tool") and text and text.strip():
                    clean_t = text.strip()
                    if not any(h in clean_t.lower() for h in ["wave of covid", "second wave", "third wave"]):
                        transcript_lines.append(f"{role}: {clean_t}")
            except Exception:
                pass

        # Store session in ACTIVE_CALLS so finish_call can find it
        ACTIVE_CALLS[room_name] = {
            "session": session,
            "call_id": call_id,
            "lines": transcript_lines,
        }

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
            agent_rec_task = asyncio.create_task(record_track(agent_track, call_id, speaker="agent"))
            background_tasks.append(agent_rec_task)
        else:
            print("[agent] Warning: local agent audio track not found for recording")

        print(f"Registered active call: {ctx.room.name}")

        # Wait for the customer / inbound SIP participant to join the room using event-driven detection
        print("Waiting for customer/inbound participant to join...")
        customer_joined_event = asyncio.Event()

        # Check if customer already has subscribed audio tracks
        for p in ctx.room.remote_participants.values():
            if p.identity == "customer":
                for pub in p.track_publications.values():
                    if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                        customer_joined_event.set()
                        break

        @ctx.room.on("track_subscribed")
        def _on_track_sub(track: rtc.Track, pub: rtc.TrackPublication, p: rtc.RemoteParticipant):
            if p.identity == "customer" and track.kind == rtc.TrackKind.KIND_AUDIO:
                print(f"[agent] Customer audio track subscribed: {p.identity} - call answered!")
                customer_joined_event.set()

        @ctx.room.on("participant_connected")
        def _on_cust_connected(p: rtc.RemoteParticipant):
            print(f"Participant connected: {p.identity}")
            if p.identity == "customer":
                # Give a short moment for track publication
                asyncio.create_task(asyncio.sleep(0.5)).add_done_callback(lambda _: customer_joined_event.set())

        try:
            await asyncio.wait_for(customer_joined_event.wait(), timeout=60.0)
            print("Customer/Inbound participant connected — starting greeting immediately.")
        except asyncio.TimeoutError:
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
            # Force the agent to strictly follow STEP 1 of the script verbatim
            greeting_instructions = (
                f"You are now starting the call. The customer's name is '{customer_name}'. "
                "Begin EXACTLY at STEP 1 of the campaign script — say the EXACT words written there, "
                "do NOT paraphrase or improvise. Do not skip any step. Start speaking now."
                if customer_name.strip()
                else
                "You are now starting the call. Begin EXACTLY at STEP 1 of the campaign script — "
                "say the EXACT words written there, do NOT paraphrase or improvise. Start speaking now."
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
        # Cancel and wait for all background tasks (audio recorders, voicemail detector)
        try:
            await _cancel_background_tasks()
        except Exception:
            pass

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
            await asyncio.sleep(1)

        # Safety cleanup in case finish_call never ran or timed out.
        ACTIVE_CALLS.pop(ctx.room.name, None)
        import tempfile
        try:
            lock_file_path = os.path.join(tempfile.gettempdir(), f"livekit_room_{ctx.room.name}.lock")
            if os.path.exists(lock_file_path):
                os.remove(lock_file_path)
        except Exception:
            pass
        print(f"Removed active call: {ctx.room.name}")


if __name__ == "__main__":
    # Prevent multiple agent processes from running simultaneously
    try:
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        lock_socket.bind(('127.0.0.1', 59152))
    except socket.error:
        print("Error: Another instance of the agent is already running.")
        print("Please stop it before starting a new one to prevent multiple agents in a call.")
        sys.exit(1)

    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "callinggen_shreya")
    print(f"[agent] Registering LiveKit agent worker with name: '{agent_name}'")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=agent_name,
            num_idle_processes=5,
        )
    )