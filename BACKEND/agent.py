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
from finish_call import finish_call, _build_transcript, request_call_finish

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

load_dotenv(override=True)

# ── Safe Async Task Wrapper ────────────────────────────────────────────────
def _safe_create_task(coro, name: str, call_id: int = -1):
    task = asyncio.create_task(coro, name=name)
    def handle_exception(t):
        try:
            exc = t.exception()
            if exc:
                print(f"[TASK FAILURE] task='{name}' call_id={call_id} exception={type(exc).__name__}: {exc}")
        except asyncio.CancelledError:
            pass
    task.add_done_callback(handle_exception)
    return task
# ─────────────────────────────────────────────────────────────────────────────

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
  1. Say a polite goodbye AND IMMEDIATELY CALL THE `finish_call` TOOL!
- IF A CALLBACK OR APPOINTMENT IS CONFIRMED:
  1. Confirm the date/time and say a polite goodbye.
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
You MUST reply with a polite concluding message (e.g., "Thank you, your appointment is confirmed. Goodbye.") AND invoke the `finish_call` tool AT THE SAME TIME.

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


async def record_track(track: rtc.Track, call_id: int, speaker: str = "customer", answered_event: asyncio.Event | None = None, disconnected_event: asyncio.Event | None = None):
    """Record an audio track (customer or agent) into a local WAV file."""
    if answered_event:
        await answered_event.wait()
        
    os.makedirs("recordings", exist_ok=True)
    filename = f"recordings/call_{call_id}_{speaker}.wav"
    
    print(f"[recorder] Started recording {speaker} track for call {call_id} -> {filename}")
    audio_stream = rtc.AudioStream(track)
    wav_file = None
    first_frame_logged = False
    try:
        async for frame_event in audio_stream:
            if disconnected_event and disconnected_event.is_set():
                break
            frame = frame_event.frame
            
            if speaker == "agent" and not first_frame_logged:
                first_frame_logged = True
                import time
                from app.services.conversation_state import ACTIVE_CALLS
                t_frame = time.monotonic()
                r_name = None
                for rn, st in ACTIVE_CALLS.items():
                    if st and st.get("call_id") == call_id:
                        r_name = rn
                        break
                if r_name and r_name in ACTIVE_CALLS:
                    ACTIVE_CALLS[r_name]["first_audio_received"] = True
                    ACTIVE_CALLS[r_name]["first_audio_frame_at"] = t_frame
                    if ACTIVE_CALLS[r_name].get("call_phase") == "greeting":
                        ACTIVE_CALLS[r_name]["call_phase"] = "waiting_for_customer"
                    print(f"\n[AI AUDIO]")
                    print(f"call_id={call_id}")
                    print(f"speech_started_at={ACTIVE_CALLS[r_name].get('speech_started_at')}")
                    print(f"first_audio_frame_at={t_frame:.3f}")
                    print(f"first_audio_received=true\n")
                    
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
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.timeout:
                return None
            
            # _build_transcript always returns a tuple (lines, customer_count, agent_count)
            transcript_tuple = _build_transcript(self.session)
            if isinstance(transcript_tuple, tuple):
                transcript_lines_list = transcript_tuple[0]
            else:
                transcript_lines_list = transcript_tuple if transcript_tuple else []

            if not transcript_lines_list:
                await asyncio.sleep(1.0)
                continue

            transcript_text = "\n".join(transcript_lines_list)
            if not transcript_text.strip():
                await asyncio.sleep(1.0)
                continue

            lower_transcript = transcript_text.lower()
            
            # Stop detecting if it looks like a real conversation (multiple turns)
            if transcript_text.count('\n') >= 8:
                print(f"[VOICEMAIL DETECTOR] result_type=conversation transcript_length={len(transcript_text)} detected=false")
                return None
                
            for phrase in self.trigger_phrases:
                if phrase in lower_transcript:
                    print(f"[VOICEMAIL DETECTOR] result_type=voicemail transcript_length={len(transcript_text)} detected=true")
                    return {
                        "type": "voicemail",
                        "trigger": phrase,
                        "confidence": 99.0,
                        "credits_charged": False
                    }
                    
            await asyncio.sleep(1.0)



class DynamicAgent(Agent):
    """Agent whose behaviour is fully driven by the campaign configuration."""

    def __init__(
        self,
        agent_type: str,
        custom_script: str,
        customer_name: str,
        greeting_instructions: str = "",
        call_answered_event: asyncio.Event | None = None,
    ):
        instructions = build_agent_instructions(agent_type, custom_script, customer_name)
        self._greeting_instructions = greeting_instructions
        self._call_answered_event = call_answered_event
        super().__init__(
            instructions=instructions,
            tools=[finish_call],
        )

    async def on_enter(self) -> None:
        """Deliver the greeting when the agent session is active AND the customer has answered.
        Uses session.say() to go straight to TTS — no LLM needed for the opening line.
        This is faster and more reliable than generate_reply for the initial greeting.
        """
        if self._greeting_instructions:
            # Wait for call to be answered (customer picks up the phone or joins)
            if self._call_answered_event is not None and not self._call_answered_event.is_set():
                try:
                    await asyncio.wait_for(self._call_answered_event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    print("[on_enter] Timeout waiting for call_answered_event. Delivering greeting now as failsafe.")

            # Small buffer to ensure WebRTC audio track subscription is live on the SIP gateway
            await asyncio.sleep(0.5)
            print(f"[on_enter] Delivering greeting via session.say() (direct TTS, text: '{self._greeting_instructions[:80]}...')")
            try:
                handle = self.session.say(
                    self._greeting_instructions,
                    allow_interruptions=False,
                )
                await handle
                print("[on_enter] Greeting delivered successfully.")
                if hasattr(self.session, "_transcript_lines"):
                    lines = getattr(self.session, "_transcript_lines")
                    if not any(self._greeting_instructions[:30] in l for l in lines):
                        lines.append(f"assistant: {self._greeting_instructions}")
            except Exception as e:
                print(f"[on_enter] say() error: {e}. Falling back to generate_reply.")
                try:
                    await self.session.generate_reply(
                        instructions=self._greeting_instructions,
                        allow_interruptions=False,
                    )
                except Exception as e2:
                    print(f"[on_enter] generate_reply fallback also failed: {e2}")


async def _get_campaign_info(call_id: int) -> dict[str, Any] | None:
    """
    Look up the campaign/agent and contact for a given call_id so the agent
    can use the correct script, agent type, and customer name.
    Returns a dict with keys: agent_type, script, customer_name, voice.
    """
    try:
        async with AsyncSessionLocal() as db:
            call = await db.get(Call, call_id)
            if call is None:
                print(f"[agent] Warning: call {call_id} not found in DB")
                return {"agent_type": "Voice-E (Tax Agent)", "script": "", "customer_name": "", "metadata_fields": {}, "voice": "Meera"}

            # Inbound call routing logic
            if call.direction == "inbound":
                agent_obj = None
                if call.agent_id:
                    agent_obj = await db.get(AgentModel, call.agent_id)
                
                voice_profile = "Meera"
                agent_type = "Sales Agent"
                script = ""
                if agent_obj:
                    voice_profile = agent_obj.voice
                    agent_type = agent_obj.name
                    script = agent_obj.script
                    
                contact = None
                if call.contact_id:
                    contact = await db.get(Contact, call.contact_id)

                return {
                    "agent_type": agent_type,
                    "script": script,
                    "customer_name": contact.customer_name or contact.name if contact else "",
                    "metadata_fields": {},
                    "voicemail_detection": {"enabled": False},
                    "voice": voice_profile,
                    "direction": "inbound",
                }

            # Outbound call routing logic
            contact = None
            if call.contact_id:
                contact = await db.get(Contact, call.contact_id)

            # Trace up to campaign via job
            from app.models.job import Job
            job = await db.get(Job, call.job_id) if call.job_id else None
            campaign = await db.get(Campaign, job.campaign_id) if job else None

            voice_profile = "Meera"  # Default fallback
            agent_obj = None
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
                "job_id": job.id if job else None,
                "campaign_id": campaign.id if campaign else None,
                "agent_id": agent_obj.id if agent_obj else None,
                "agent_type": campaign.agent if campaign else "Voice-E (Tax Agent)",
                "script": campaign.script if campaign else "",
                "customer_name": contact.name if contact else "",
                "metadata_fields": contact.metadata_fields if contact else {},
                "voicemail_detection": campaign.voicemail_detection if campaign else None,
                "voice": voice_profile,
                "direction": "outbound",
            }
    except Exception as e:
        print(f"[agent] Warning: could not fetch campaign info for call {call_id}: {e}")
        return {"agent_type": "Voice-E (Tax Agent)", "script": "", "customer_name": "", "metadata_fields": {}, "voice": "Meera", "direction": "outbound"}


async def entrypoint(ctx: JobContext):

    print("=" * 60)
    print("JOB RECEIVED")
    print("=" * 60)

    room_name = ctx.room.name
    
    # ── Resolve call_id by room name from DB or fallback to parsing room name ──
    call_id = -1
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Call).where(Call.room_name == room_name)
            )
            call = result.scalars().first()
            if call:
                call_id = call.id
    except Exception as e:
        print(f"[agent] DB lookup error for room_name '{room_name}': {e}")

    if call_id == -1:
        try:
            call_id = int(room_name.rsplit("-", 1)[-1])
        except (ValueError, IndexError):
            call_id = -1
            print(f"[agent] Warning: could not parse call_id from room name: {room_name}")

    print(f"\n[CALL LIFECYCLE]")
    print(f"call_id={call_id}")
    print(f"room_name={room_name}")
    print(f"worker_pid={os.getpid()}\n")

    # [AGENT LOOKUP] Fetch campaign config with bounded retries
    campaign_info = await _get_campaign_info(call_id)
    if campaign_info is None:
        print(f"[FATAL ERROR] call {call_id} missing or lookup failed. Aborting dispatch.")
        await ctx.room.disconnect()
        return

    print(f"[CALL LIFECYCLE]")
    print(f"db_exists=True")
    print(f"job_id={campaign_info.get('job_id')}")
    print(f"campaign_id={campaign_info.get('campaign_id')}")
    print(f"agent_id={campaign_info.get('agent_id')}")
    print(f"agent_name={campaign_info.get('agent_type')}")

    # Register event listeners BEFORE connecting to ensure we don't miss early events
    shutdown_event = asyncio.Event()
    call_answered_event = asyncio.Event()
    customer_disconnected_event = asyncio.Event()

    def _mark_call_answered(source: str):
        if not call_answered_event.is_set():
            call_answered_event.set()
            t_ans = time.monotonic()
            state = ACTIVE_CALLS.get(room_name)
            if state:
                if state.get("answered_at") is None:
                    state["answered_at"] = t_ans
                    state["call_phase"] = "greeting"
                    from backend_client import notify_call_active
                    _safe_create_task(notify_call_active(room_name), name="notify_call_active", call_id=call_id)
                    print(f"[PERF] sip_active={t_ans:.3f}")
                    print(f"[PERF] answered_at={t_ans:.3f}")
                    print(f"[CALL] answered_at set at {state['answered_at']} (source: {source})")
            else:
                print(f"[CALL] answered_at triggered before state created (source: {source})")

    @ctx.room.on("participant_attributes_changed")
    def on_participant_attributes_changed(changed_attributes: dict, participant: rtc.Participant):
        if getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            status = changed_attributes.get("sip.callStatus")
            if status:
                print(f"[SIP] callStatus changed to: {status}")
                if status == "active":
                    _mark_call_answered("participant_attributes_changed_active")

    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant):
        is_customer = participant.identity == "customer" or "customer" in participant.identity.lower() or (
            participant.identity != ctx.room.local_participant.identity
        )
        if is_customer:
            print(f"[ROOM] Remote participant connected: identity='{participant.identity}' kind={getattr(participant, 'kind', None)}")
            if getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                status = participant.attributes.get("sip.callStatus")
                if status == "active":
                    _mark_call_answered("participant_connected_sip_active")
            else:
                # Web / Direct caller
                _mark_call_answered("participant_connected_direct")

    @ctx.room.on("disconnected")
    def on_room_disconnected(*args):
        # If finish_call already handled this room (intentional disconnect), skip.
        # Otherwise the room dropped unexpectedly (SIP timeout, network failure, trunk drop).
        state = ACTIVE_CALLS.get(room_name)
        
        print(f"\n[LIVEKIT DISCONNECT]")
        print(f"call_id={call_id}")
        print(f"room={room_name}")
        sip_active = state.get("answered_at") is not None if state else False
        print(f"sip_was_active={sip_active}")
        active_tasks = len(asyncio.all_tasks())
        print(f"active_tasks={active_tasks}")
        
        if state is not None:
            if state.get("sip_ended_at") is None:
                state["sip_ended_at"] = time.monotonic()
                state["duration_source"] = "room_disconnected_fallback"
                if "sip_disconnected_event" in state:
                    state["sip_disconnected_event"].set()
                print(f"[SIP END] call_id={call_id} sip_ended_at={state['sip_ended_at']} source=room_disconnected_fallback")
                
            if not state.get("finishing"):
                print(f"reason=unexpected_room_drop")
                # Room dropped before finish_call ran — save transcript and notify backend
                _safe_create_task(_handle_room_disconnect(), name="_handle_room_disconnect", call_id=call_id)
            else:
                print(f"reason=intentional_or_already_finishing")
            
        shutdown_event.set()


    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        is_customer = participant.identity == "customer" or "customer" in participant.identity.lower() or (
            participant.identity != ctx.room.local_participant.identity
        )
        if is_customer:
            # Subscribed audio track from customer -> call is definitely answered & audio active
            _mark_call_answered("track_subscribed")
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(record_track(track, call_id))

    try:
        await ctx.connect()
        print(f"Connected to room: {ctx.room.name}")

        # Scan if remote participant is already in room upon connect
        for participant in ctx.room.remote_participants.values():
            is_customer = participant.identity == "customer" or "customer" in participant.identity.lower() or (
                participant.identity != ctx.room.local_participant.identity
            )
            if is_customer:
                status = participant.attributes.get("sip.callStatus")
                if status == "active" or getattr(participant, "kind", None) != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                    _mark_call_answered("existing_participant_connect")

        # If it's an inbound call and still call_id == -1, initialize it via backend
        if (room_name.startswith("inbound-call-") or "inbound" in room_name) and call_id == -1:
            print("[agent] Inbound room detected. Pre-initializing call via backend...")
            # Wait up to 5 seconds for remote participant to appear
            for _ in range(10):
                if ctx.room.remote_participants:
                    break
                await asyncio.sleep(0.5)

            caller_number = None
            called_number = None
            for p in ctx.room.remote_participants.values():
                caller_number = p.attributes.get("sip.caller") or p.identity
                called_number = p.attributes.get("sip.called")
                if caller_number:
                    break

            if not caller_number:
                caller_number = "Inbound Caller"
            if not called_number:
                called_number = os.getenv("SIP_CALL_FROM", "+917971442271")

            backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
            init_url = f"{backend_url}/api/calls/inbound-init"
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(init_url, json={
                        "room_name": room_name,
                        "caller_number": caller_number,
                        "called_number": called_number
                    })
                    if resp.is_success:
                        res_data = resp.json()
                        if res_data.get("success"):
                            call_id = res_data.get("call_id")
                            print(f"[agent] Inbound call initialized. Call ID: {call_id}")
                    else:
                        print(f"[agent] Failed to init inbound call: {resp.status_code} {resp.text}")
            except Exception as init_err:
                print(f"[agent] Error calling inbound-init API: {init_err}")

        # Cross-process atomic file lock per room to guarantee strictly 1 agent process per call room
        import tempfile
        lock_file_path = os.path.join(tempfile.gettempdir(), f"livekit_room_{ctx.room.name}.lock")
        try:
            lock_fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(lock_fd)
        except FileExistsError:
            print(f"[agent] Atomic lock file '{lock_file_path}' exists! Another agent process is handling room '{ctx.room.name}'. Exiting duplicate process immediately.")
            return

        # Scan for already subscribed audio tracks from pre-existing customer participant
        for participant in ctx.room.remote_participants.values():
            is_customer = participant.identity == "customer" or "customer" in participant.identity.lower() or (
                participant.identity != ctx.room.local_participant.identity
            )
            if is_customer:
                for publication in participant.track_publications.values():
                    if publication.subscribed and publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                        print(f"[recorder] Found pre-existing subscribed customer audio track: {publication.track.sid}")
                        _safe_create_task(record_track(publication.track, call_id, speaker="customer", answered_event=call_answered_event, disconnected_event=customer_disconnected_event), name="record_track_customer_sub", call_id=call_id)

        # ── Fetch campaign info to drive the agent's behaviour ───────────────────
        campaign_info = await _get_campaign_info(call_id)
        if campaign_info is None:
            print(f"[FATAL ERROR] call {call_id} campaign info missing on second lookup. Aborting.")
            await ctx.room.disconnect()
            return
        assert campaign_info is not None

        agent_type    = str(campaign_info.get("agent_type", "Voice-E (Tax Agent)"))
        base_script   = str(campaign_info.get("script", ""))
        customer_name = str(campaign_info.get("customer_name", ""))
        metadata      = campaign_info.get("metadata_fields") or {}
        
        # Include customer_name in metadata for uniform replacement
        metadata_dict = {k.lower(): str(v) for k, v in metadata.items()}
        metadata_dict["customer_name"] = customer_name
        metadata_dict["customer name"] = customer_name

        def _replace_placeholder(match):
            key = match.group(1).strip().lower()
            return metadata_dict.get(key, "")

        custom_script = re.sub(r"\{\{(.*?)\}\}", _replace_placeholder, base_script)

        print(f"[DISPATCH] room={room_name} call_id={call_id} worker_id={os.getenv('LIVEKIT_AGENT_NAME', 'unknown')} PID={os.getpid()}")
        print(f"[DISPATCH] agent_config: job_id={campaign_info.get('job_id')} campaign_id={campaign_info.get('campaign_id')} agent_id={campaign_info.get('agent_id')} voice={campaign_info.get('voice')} agent={agent_type}")
        print(f"[agent] Agent type   : {agent_type}")
        print(f"[agent] Customer name: {customer_name}")
        print(f"[agent] Script length: {len(custom_script)} chars")
        
        async def _handle_voicemail_disconnect(metadata: dict):
            state = ACTIVE_CALLS.get(room_name)
            if state is None:
                return
            print("Voicemail detected. Disconnecting immediately to avoid credits.")
            
            customer_disconnected_event.set()
            request_call_finish(room_name, reason="voicemail", is_voicemail=True, detection_metadata=metadata)

        async def _handle_room_disconnect():
            """
            Called when the LiveKit room itself disconnects unexpectedly
            (SIP trunk timeout, network drop, server-side room deletion).
            Saves partial transcript and notifies backend so the worker can advance.
            """
            state = ACTIVE_CALLS.get(room_name)
            if state is None or state.get("finishing"):
                return  # finish_call already handled cleanup

            customer_disconnected_event.set()
            print(f"[agent] Room disconnected unexpectedly — saving transcript and notifying backend.")
            request_call_finish(room_name, reason="sip_disconnect", failure_reason="livekit_connection_error")

        async def _handle_unexpected_disconnect(reason: str):
            state = ACTIVE_CALLS.get(room_name)
            if state is None or state.get("finishing"):
                print(f"Customer disconnected ({reason}) but finish_call already in progress.")
                return
                
            customer_disconnected_event.set()
            print(f"Customer disconnected before finish_call ran ({reason}). Notifying backend.")
            request_call_finish(room_name, reason="customer_disconnect", outcome="customer_hangup")


        @ctx.room.on("participant_disconnected")
        def on_participant_disconnected(participant: rtc.RemoteParticipant):
            if getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                state = ACTIVE_CALLS.get(room_name)
                if state and state.get("sip_ended_at") is None:
                    state["sip_ended_at"] = time.monotonic()
                    state["duration_source"] = "sip_participant_disconnect"
                    if "sip_disconnected_event" in state:
                        state["sip_disconnected_event"].set()
                    print(f"[SIP END] call_id={call_id} sip_participant={participant.sid} sip_ended_at={state['sip_ended_at']} source=sip_participant_disconnect")

                # Only treat as customer hangup if finish_call is NOT already running
                # (if finishing=True, the agent ended the call — not the customer)
                if state and not state.get("finishing"):
                    _safe_create_task(_handle_unexpected_disconnect("customer hung up"), name="_handle_unexpected_disconnect", call_id=call_id)
                else:
                    print(f"[SIP END] call_id={call_id} — SIP participant left after agent-initiated hangup. Not treating as customer disconnect.")

        # Dynamic voice selection mapping for Sarvam bulbul v2 compatible voices
        ALLOWED_SARVAM_SPEAKERS = {
            "anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh", "aditya",
            "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit",
            "dev", "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa", "kabir",
            "aayan", "shubh", "ashutosh", "advait", "anand", "tanya", "tarun", "sunny",
            "mani", "gokul", "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali"
        }
        SARVAM_VOICE_MAPPING = {
            "meera": "anushka",
            "meera (morning tax)": "anushka",
            "raj": "abhilash",
            "raj (morning tax)": "abhilash",
            "john (morning tax)": "anushka",
            "voice-e": "anushka",
            "voice-e (tax agent)": "anushka",
            "manisha": "manisha",
            "karun": "karun",
            "vidya": "vidya",
            "hitesh": "hitesh",
            "female 1": "anushka",
            "female 2": "anushka",
            "male 1": "abhilash",
            "male 2": "abhilash",
            "nova (elevenlabs)": "anushka",
            "alex": "abhilash",
            "james": "hitesh",
            "sarah": "vidya",
        }
        raw_db_voice = str(campaign_info.get("voice", "Meera")).strip()
        mapped_speaker = SARVAM_VOICE_MAPPING.get(raw_db_voice.lower(), raw_db_voice.lower())
        speaker_voice = mapped_speaker if mapped_speaker in ALLOWED_SARVAM_SPEAKERS else "anushka"
        print(f"[agent] Configured agent voice profile: '{raw_db_voice}' -> mapped to Sarvam speaker: '{speaker_voice}'")

        session = AgentSession(
            vad=silero.VAD.load(
                min_silence_duration=0.5,
                activation_threshold=0.5,
            ),
            stt=sarvam.STT(),

            llm=openai.LLM(
                model="deepseek-chat",
                api_key=os.getenv("DEEPSEEK_API_KEY") or "",
                base_url="https://api.deepseek.com/v1",
            ),

            tts=sarvam.TTS(
                model="bulbul:v2",
                speaker=speaker_voice,
                speech_sample_rate=16000,
            ),
        )



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
                        # ONLY append agent lines here. User lines come from STT below to avoid dupes/misses.
                        if role == "assistant":
                            transcript_lines.append(f"{role}: {clean_t}")
            except Exception:
                pass

        @session.on("user_input_transcribed")
        def _on_user_speech(ev: Any):
            text = getattr(ev, "transcript", "")
            if text and text.strip():
                clean_t = text.strip()
                if not any(h in clean_t.lower() for h in ["wave of covid", "second wave", "third wave"]):
                    _mark_call_answered("user_speech")
                    print(f"[STT] call_id={call_id} customer_track=True speech_start=None speech_end=None transcript_received=True text='{clean_t}'")
                    transcript_lines.append(f"user: {clean_t}")
                    state = ACTIVE_CALLS.get(room_name)
                    if state:
                        state["customer_has_spoken"] = True
                        state["call_phase"] = "conversation"

        # Store session in ACTIVE_CALLS so finish_call can find it
        ACTIVE_CALLS[room_name] = {
            "session": None,
            "call_id": call_id,
            "call_phase": "waiting_for_answer",
            "job_id": campaign_info.get("job_id"),
            "campaign_id": campaign_info.get("campaign_id"),
            "agent_id": campaign_info.get("agent_id"),
            "agent_name": agent_type,
            "script": custom_script[:50] + "..." if custom_script else "",
            "voice_profile": raw_db_voice,
            "lines": transcript_lines,
            "answered_at": None,
            "disconnected_event": customer_disconnected_event,
            "customer_has_spoken": False,
            "first_audio_received": False,
            "speech_started_at": None,
            "first_audio_frame_at": None,
            "sip_ended_at": None,
            "duration_source": None,
            "sip_disconnected_event": asyncio.Event(),
        }


        # Build the actual greeting text to speak (goes to TTS directly via session.say)
        direction = campaign_info.get("direction", "outbound")
        if direction == "inbound":
            # Inbound: generate a natural greeting via instructions
            greeting_text = (
                f"Hello! Thank you for calling Morning Tax. "
                f"I'm {agent_type}, your AI tax consultant. How can I help you today?"
            )
        else:
            # Outbound: find the first actual spoken greeting line from the script.
            # Scripts often start with section headers like "AGENT IDENTITY:", "STEP 1 —", etc.
            # We need to find the first line that is actual speech (not a header/instruction).
            script_lines = [l.strip() for l in custom_script.strip().splitlines() if l.strip()]

            # Priority 1: find a quoted greeting in the script (e.g. "Hi, ..." or 'Hello ...')
            greeting_text = None
            for line in script_lines:
                # Match lines that are or contain a quoted spoken greeting
                if line.startswith('"') or line.startswith("'"):
                    greeting_text = line.strip('"').strip("'").strip()
                    break
                # Match lines that contain a quoted greeting after a colon (e.g. Greet: "Hi...")
                quoted = re.search(r'["\u201c]([^"\u201d]{10,200})["\u201d]', line)
                if quoted and any(w in quoted.group(1).lower() for w in ["hi", "hello", "good morning", "good afternoon", "namaste", "may i speak"]):
                    greeting_text = quoted.group(1).strip()
                    break

            # Priority 2: first non-header, non-instruction line
            if not greeting_text:
                header_patterns = re.compile(
                    r'^(AGENT IDENTITY|STEP \d|OBJECTION|CLOSING|GOAL|INSTRUCTIONS?|NOTE|RULES?|GUIDELINES?)',
                    re.IGNORECASE
                )
                for line in script_lines:
                    if not header_patterns.match(line) and len(line) > 20:
                        greeting_text = line
                        break

            # Final fallback: first line regardless
            if not greeting_text:
                greeting_text = script_lines[0] if script_lines else custom_script[:250].strip()

            # Personalize with customer name if available and not already present
            if customer_name.strip() and customer_name.lower() not in greeting_text.lower():
                personalized = greeting_text.replace("{{customer_name}}", customer_name)
                if personalized == greeting_text:
                    # Try to insert name after "Hi" or "Hello"
                    personalized = re.sub(
                        r'\b(Hi|Hello|Good morning|Good afternoon),?\s*(may I speak with)?',
                        lambda m: f"{m.group(1)}, {customer_name}! " if not m.group(2) else m.group(0),
                        greeting_text, count=1, flags=re.IGNORECASE
                    )
                greeting_text = personalized if personalized != greeting_text else greeting_text
            elif "{{customer_name}}" in greeting_text:
                greeting_text = greeting_text.replace("{{customer_name}}", customer_name or "there")

        if not greeting_text or not greeting_text.strip():
            greeting_text = f"Hello {customer_name if customer_name else ''}, this is {agent_type} calling. How can I help you today?".strip()


        print(f"[agent] Greeting text: '{greeting_text[:100]}...'")

        # Start session immediately so agent audio track is published to LiveKit room
        t_session_start = time.monotonic()
        print(f"[PERF] session_start={t_session_start:.3f}")
        await session.start(
            room=ctx.room,
            agent=DynamicAgent(
                agent_type=agent_type,
                custom_script=custom_script,
                customer_name=customer_name,
                greeting_instructions=greeting_text,
                call_answered_event=call_answered_event,
            ),
        )
        if ACTIVE_CALLS.get(room_name):
            ACTIVE_CALLS[room_name]["session"] = session

        print("Session started and audio track published")

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
            _safe_create_task(record_track(agent_track, call_id, speaker="agent", answered_event=call_answered_event, disconnected_event=customer_disconnected_event), name="record_track_agent", call_id=call_id)
        else:
            print("[agent] Warning: local agent audio track not found for recording")

        print(f"Registered active call: {ctx.room.name}")

        # Register track_subscribed listener to start recording customer track and detect audio readiness
        customer_audio_ready = asyncio.Event()

        @ctx.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            is_customer = participant.identity == "customer" or "customer" in participant.identity.lower() or (
                participant.identity != ctx.room.local_participant.identity
            )
            if track.kind == rtc.TrackKind.KIND_AUDIO and is_customer:
                customer_audio_ready.set()
                asyncio.create_task(record_track(track, call_id, speaker="customer"))

        # Wait for the customer / inbound SIP participant to actually answer the call (not just be in ringing state).
        print("Waiting for customer/inbound participant to answer the call...")
        customer_answered = False
        customer_identity = "customer"

        for _ in range(120):  # Wait up to 60 seconds (0.5s checks)
            for p in ctx.room.remote_participants.values():
                is_customer = p.identity == "customer" or "customer" in p.identity.lower() or (
                    p.identity != ctx.room.local_participant.identity
                )
                if is_customer:
                    customer_identity = p.identity
                    call_status = p.attributes.get("sip.callStatus", "").lower()
                    has_audio_pub = any(pub.kind == rtc.TrackKind.KIND_AUDIO for pub in p.track_publications.values())
                    
                    # If track is subscribed, audio is published, or SIP state is active/connected -> call answered!
                    if customer_audio_ready.is_set() or has_audio_pub or call_status in ("active", "connected", "answered"):
                        customer_answered = True
                        break
                    # If callStatus is explicitly ringing/calling, keep waiting
                    elif call_status in ("calling", "ringing", "dialing"):
                        continue
                    # Fallback: if participant exists and has track publications
                    elif len(p.track_publications) > 0:
                        customer_answered = True
                        break

            if customer_answered:
                call_answered_event.set()
                state = ACTIVE_CALLS.get(room_name)
                if state and state.get("answered_at") is None:
                    state["answered_at"] = time.monotonic()
                    state["call_phase"] = "greeting"
                    from backend_client import notify_call_active
                    _safe_create_task(notify_call_active(room_name), name="notify_call_active", call_id=call_id)
                print(f"Customer/Inbound participant answered ({customer_identity}) — starting greeting.")
                break
            await asyncio.sleep(0.5)

        if not customer_answered:
            print("Timeout: customer never answered. Notifying backend and exiting.")
            ACTIVE_CALLS.pop(room_name, None)
            await notify_call_complete(
                room_name,
                payload={
                    "transcript": None,
                    "customer_name": None,
                    "appointment_date": None,
                    "appointment_time": None,
                    "duration": 0,
                    "outcome": "no_answer",
                },
            )
            shutdown_event.set()
            return
        else:
            print("Waiting for SIP call to become active...")
            try:
                # Wait up to 60 seconds for the call to be answered
                await asyncio.wait_for(call_answered_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                print("Timeout: SIP call never became active. Notifying backend and exiting.")
                ACTIVE_CALLS.pop(room_name, None)
                await notify_call_complete(
                    room_name,
                    payload={"duration": 0, "transcript": None, "customer_name": None, "appointment_date": None, "appointment_time": None, "outcome": "no_answer"}
                )
                shutdown_event.set()
                return
            
            # Start Voicemail Detector
            vd_config = campaign_info.get("voicemail_detection") or {"enabled": True, "timeout": 45}
            if vd_config.get("enabled"):
                async def run_voicemail_detector():
                    detector = VoicemailDetector(session, timeout_seconds=vd_config.get("timeout", 45))
                    result = await detector.run()
                    if result:
                        print(f"Voicemail detected! {result}")
                        await _handle_voicemail_disconnect(result)
                _safe_create_task(run_voicemail_detector(), name="run_voicemail_detector", call_id=call_id)

            print(f"\n[GREETING] Customer answered — greeting will be delivered via on_enter().")
            print(f"call_id={call_id}")
            print(f"session_start={t_session_start:.3f}")

        # Silence detector
        async def silence_detector_loop():
            silence_timer = 0.0
            last_tick = time.monotonic()
            customer_was_speaking = False
            timer_active = False
            customer_has_spoken_once = False

            while not shutdown_event.is_set():
                await asyncio.sleep(0.5)
                now = time.monotonic()
                dt = now - last_tick
                last_tick = now
                
                # If we're already finishing or customer hasn't answered, don't time out
                if getattr(session, "_is_finishing", False) or not customer_answered:
                    continue
                    
                agent_state = str(getattr(session, "agent_state", "")).upper()
                user_state = str(getattr(session, "user_state", "")).upper()
                
                is_ai_busy = "SPEAKING" in agent_state or "THINKING" in agent_state
                is_user_speaking = "SPEAKING" in user_state
                
                if is_user_speaking:
                    if not customer_has_spoken_once:
                        customer_has_spoken_once = True
                    if not customer_was_speaking:
                        customer_was_speaking = True
                    if timer_active:
                        print("[SILENCE] Customer resumed speaking - timer reset")
                        timer_active = False
                    silence_timer = 0.0
                else:
                    if customer_was_speaking:
                        print("[SILENCE] Customer stopped speaking")
                        customer_was_speaking = False
                    
                    # Only start the 10-second timer if the customer has spoken at least once in the conversation
                    if customer_has_spoken_once:
                        if not timer_active:
                            print("[SILENCE] 10-second timer started")
                            timer_active = True
                            silence_timer = 0.0
                        
                        # AI TTS latency or speaking must not cause a false hangup.
                        # We pause the timer while the AI is busy, so the customer gets a full 10s of actual silence.
                        if not is_ai_busy:
                            silence_timer += dt
                            
                        if silence_timer >= 30.0:
                            print("[SILENCE] Customer silent for 30 seconds - ending call")
                            request_call_finish(room_name, reason="customer_silence")
                            break

        if call_answered_event.is_set():
            _safe_create_task(silence_detector_loop(), name="silence_detector_loop", call_id=call_id)

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
                    reason = "agent_error"
                    if "agent_tool_schema_error" in str(e):
                        reason = "agent_tool_schema_error"
                    await CallService.fail_call(db=db, call_id=call_id, failure_reason=reason)
                    print(f"[agent] Call {call_id} marked as failed in DB due to crash ({reason}).")
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

    # Clean up any stale lock files from previous runs
    import glob
    import tempfile
    for lf in glob.glob(os.path.join(tempfile.gettempdir(), "livekit_room_*.lock")):
        try:
            os.remove(lf)
        except Exception:
            pass

    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "callinggen-outbound-agent")
    print(f"[agent] Registering LiveKit agent worker with name: '{agent_name}'")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=agent_name,
            num_idle_processes=2,
            load_threshold=float('inf'),
            load_fnc=lambda: 0.0,
        )
    )