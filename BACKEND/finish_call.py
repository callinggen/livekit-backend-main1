import asyncio
from typing import Any

from livekit.agents import function_tool
from livekit import api

from app.services.conversation_state import ACTIVE_CALLS
from backend_client import notify_call_complete

GOODBYE_PHRASE = "Thank you for your time. Have a great day! Goodbye."


def _build_transcript(session: Any) -> str:
    """
    Extract the conversation transcript from the AgentSession.
    """
    try:
        chat_ctx = getattr(session, "chat_ctx", None)
        if chat_ctx is None:
            chat_ctx = getattr(session, "history", None)
            
        if chat_ctx is None:
            print("Warning – session.chat_ctx/history is None, transcript will be empty.")
            return ""

        messages: Any = []
        if hasattr(chat_ctx, "messages"):
            raw_msgs = getattr(chat_ctx, "messages", [])
            res = raw_msgs() if callable(raw_msgs) else raw_msgs
            if isinstance(res, (list, tuple)):
                messages = res
        elif isinstance(chat_ctx, (list, tuple)):
            messages = chat_ctx

        lines = []
        for msg in messages:
            # Handle dictionary vs object
            if isinstance(msg, dict):
                role = str(msg.get("role", "")).split(".")[-1].lower()
                content = msg.get("content", "")
                text = content if isinstance(content, str) else " ".join(str(c) for c in content)
            else:
                role = str(getattr(msg, "role", "")).split(".")[-1].lower()
                text = getattr(msg, "text_content", None)
                if not text:
                    raw = getattr(msg, "content", [])
                    if isinstance(raw, str):
                        text = raw
                    elif isinstance(raw, list):
                        text = " ".join(c for c in raw if isinstance(c, str))
                    else:
                        text = str(raw)

            if role in ("system", "tool"):
                continue

            if text and text.strip():
                clean_t = text.strip()
                lower_t = clean_t.lower()
                # Filter out Sarvam STT static audio model benchmark hallucinations
                if "wave of covid" in lower_t or "second wave" in lower_t or "third wave" in lower_t:
                    continue
                lines.append(f"{role}: {clean_t}")

        # Fallback to real-time buffer if chat_ctx is entirely empty
        if not lines:
            lines = getattr(session, "_transcript_lines", [])
            if not lines and hasattr(session, "room"):
                room_name = getattr(session.room, "name", None)
                if room_name and room_name in ACTIVE_CALLS:
                    lines = ACTIVE_CALLS[room_name].get("lines", [])

        customer_lines = sum(1 for line in lines if line.startswith("user:"))
        agent_lines = sum(1 for line in lines if line.startswith("assistant:"))
        return lines, customer_lines, agent_lines

    except Exception as e:
        import traceback
        print(f"Warning – could not build transcript: {e}\n{traceback.format_exc()}")
        return ""


def request_call_finish(
    room_name: str,
    reason: str,
    customer_name: str = "",
    appointment_date: str = "",
    appointment_time: str = "",
    is_voicemail: bool = False,
    detection_metadata: dict = None,
    outcome: str = None,
    failure_reason: str = None,
):
    state = ACTIVE_CALLS.get(room_name)
    if not state:
        print(f"[CALL END REQUEST] call_id=unknown reason={reason} accepted=false")
        return False

    if reason == "llm_tool":
        phase = state.get("call_phase")
        first_audio = state.get("first_audio_received", False)
        customer_has_spoken = state.get("customer_has_spoken", False)
        if phase == "greeting" or (not first_audio and not customer_has_spoken):
            print(f"[CALL END BLOCKED]\ncall_id={state.get('call_id')}\nreason={reason}\nphase={phase}\nfirst_audio_received={first_audio}\ncustomer_has_spoken={customer_has_spoken}\n")
            return "Call termination is not allowed during the initial greeting. Continue the conversation."

    if state.get("finishing"):
        print(f"[CALL END REQUEST] call_id={state.get('call_id')} reason={reason} accepted=false")
        return False

    state["finishing"] = True
    print(f"[CALL END REQUEST] call_id={state.get('call_id')} reason={reason} accepted=true")
    
    asyncio.create_task(terminate_call_once(
        room_name=room_name,
        reason=reason,
        customer_name=customer_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        is_voicemail=is_voicemail,
        detection_metadata=detection_metadata,
        outcome=outcome,
        failure_reason=failure_reason,
    ))
    return True


async def terminate_call_once(
    room_name: str,
    reason: str,
    customer_name: str = "",
    appointment_date: str = "",
    appointment_time: str = "",
    is_voicemail: bool = False,
    detection_metadata: dict = None,
    outcome: str = None,
    failure_reason: str = None,
):
    import os
    import time
    from livekit import api
    
    print(f"--- EXECUTING TERMINATE CALL ONCE: room={room_name}, reason={reason} ---")

    state = ACTIVE_CALLS.get(room_name)
    if state is None:
        return

    finish_call_requested_at = time.monotonic()
    room_delete_started_at = None
    room_delete_completed_at = None
    duration_calculated_at = None
    backend_notify_started_at = None
    backend_notify_completed_at = None
    
    session = state.get("session")
    if session:
        setattr(session, "_is_finishing", True)

    try:
        call_id = int(room_name.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        call_id = -1
        
    ans_at = state.get("answered_at")
    sip_was_active = ans_at is not None

    # Stop recording
    disc_event = state.get("disconnected_event")
    if disc_event:
        disc_event.set()

    # ── Step 1: Delay room deletion to allow final TTS to play ──
    if reason == "llm_tool":
        print("Waiting 4.5 seconds to allow final agent response to play before hanging up...")
        await asyncio.sleep(4.5)
        print("Grace period finished. Proceeding to hang up.")

    # ── Step 2: ALWAYS delete the LiveKit room to hang up the call FIRST ──
    room_delete_started_at = time.monotonic()
    try:
        print("Deleting LiveKit room (hanging up SIP call)...")
        lk_url = os.getenv("LIVEKIT_URL", "").replace("ws://", "http://").replace("wss://", "https://")
        lk_key = os.getenv("LIVEKIT_API_KEY")
        lk_secret = os.getenv("LIVEKIT_API_SECRET")
        
        if lk_url:
            lkapi = api.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret)
        else:
            lkapi = api.LiveKitAPI()

        try:
            await lkapi.room.delete_room(
                api.DeleteRoomRequest(room=room_name)
            )
            print("Room deleted successfully — call hung up.")
        finally:
            await lkapi.aclose()
    except Exception as e:
        print(f"Warning – room deletion error: {e}")
    room_delete_completed_at = time.monotonic()

    # ── Step 2: Wait for actual SIP disconnect ──
    sip_disconnected_event = state.get("sip_disconnected_event")
    if sip_disconnected_event:
        try:
            await asyncio.wait_for(sip_disconnected_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

    duration_calculated_at = time.monotonic()
    
    if not sip_was_active:
        duration = 0
        state["duration_source"] = "zero_unanswered"
    else:
        if state.get("sip_ended_at") is not None:
            duration = int(state["sip_ended_at"] - ans_at)
        else:
            duration = int(duration_calculated_at - ans_at)
            state["duration_source"] = "timeout_fallback"
            print(f"[SIP END FALLBACK] call_id={call_id} source=timeout reason=sip_disconnect_event_missing duration_is_approximate=true")

    state["duration"] = duration

    # ── Step 3: Build transcript ────────
    transcript_str = ""
    customer_lines = 0
    agent_lines = 0
    if session:
        res = _build_transcript(session)
        if isinstance(res, tuple) and len(res) == 3:
            lines, customer_lines, agent_lines = res
            transcript_str = "\n".join(lines)
        else:
            transcript_str = res
    print(f"Transcript lines: {len(transcript_str.splitlines())}")

    customer_has_spoken = state.get("customer_has_spoken", False)
    first_audio_received = state.get("first_audio_received", False)

    print(f"\n[TRANSCRIPT]")
    print(f"call_id={call_id}")
    print(f"customer_lines={customer_lines}")
    print(f"agent_lines={agent_lines}")
    print(f"total_lines={len(transcript_str.splitlines())}\n")

    # Determine if this was an agent_no_response
    if not outcome and sip_was_active and first_audio_received and not customer_has_spoken:
        if reason == "customer_silence":
            outcome = "agent_no_response"
            print("-> Reclassifying outcome to: agent_no_response")

    # ── Step 4: Mix WAV tracks ────────
    if call_id != -1:
        try:
            await asyncio.sleep(1.5)  # give recorder time to flush & close on Windows
            from agent import mix_wav_files
            mix_wav_files(
                f"recordings/call_{call_id}_customer.wav",
                f"recordings/call_{call_id}_agent.wav",
                f"recordings/call_{call_id}.wav"
            )
        except Exception as mix_err:
            print(f"Warning – mixing audio failed: {mix_err}")

    print(f"\n[TRANSCRIPT VALIDATION]")
    print(f"customer_lines={customer_lines}")
    print(f"agent_lines={agent_lines}")
    print(f"total_lines={customer_lines + agent_lines}")
    if agent_lines == 0 and sip_was_active:
        print("[WARNING] Transcript shows 0 agent lines for an active call!")

    # ── Step 5: Notify backend with full payload ──────────────────────
    payload = {
        "transcript": transcript_str or None,
        "customer_name": customer_name or None,
        "appointment_date": appointment_date or None,
        "appointment_time": appointment_time or None,
        "recording_url": f"/api/recordings/call_{call_id}.wav" if call_id != -1 else None,
        "duration": duration,
    }
    if is_voicemail:
        payload["is_voicemail"] = True
        payload["detection_metadata"] = detection_metadata
    if outcome:
        payload["outcome"] = outcome
    if failure_reason:
        payload["failure_reason"] = failure_reason
        
    print(f"\n[CALL TIMING]")
    print(f"call_id={call_id}")
    print(f"answered_at={ans_at}")
    print(f"finish_call_requested_at={finish_call_requested_at}")
    print(f"room_delete_started_at={room_delete_started_at}")
    print(f"room_delete_completed_at={room_delete_completed_at}")
    print(f"sip_disconnect_at={state.get('sip_ended_at')}")
    print(f"duration_calculated_at={duration_calculated_at}")
    print(f"duration={duration}")
    print(f"duration_source={state.get('duration_source')}")
    
    backend_notify_started_at = time.monotonic()
    print(f"backend_notify_started_at={backend_notify_started_at}")
    
    try:
        print(f"Notifying backend that the call is complete (reason={reason})...")
        success = await notify_call_complete(room_name, payload=payload)
        if not success:
            print(f"[finish_call] FORENSIC ALERT: notify_call_complete returned FALSE for room '{room_name}'!")
    except Exception as e:
        print(f"[finish_call] ERROR notifying backend: {e}")
        
    backend_notify_completed_at = time.monotonic()
    print(f"backend_notify_completed_at={backend_notify_completed_at}\n")

    # Close session cleanly to prevent background TTS processing
    if session:
        try:
            await asyncio.wait_for(session.aclose(), timeout=3.0)
        except Exception as e:
            pass

    # Remove active call state
    ACTIVE_CALLS.pop(room_name, None)


@function_tool(
    description="""
Call this tool ONLY when the conversation is completely finished.
MUST BE CALLED IMMEDIATELY IN THESE CASES:
- When the customer says "not interested", "no thanks", "don't call me", or declines.
- When an appointment or callback date/time is requested or confirmed.
- When the customer says goodbye, thank you, or indicates they want to hang up.

Calling this tool will automatically hang up the SIP call.

CRITICAL RULES:
- Do not call during the initial greeting.
- Do not call before the customer responds.
- Do not call merely because the greeting is complete.
- Use only after a legitimate conversation-ending condition.

Pass any details collected during the conversation:
- customer_name: the customer's full name
- appointment_date: the date if an appointment or callback was requested (e.g. "2026-08-07")
- appointment_time: the time if an appointment or callback was requested (e.g. "05:30 PM")
"""
)
async def finish_call(
    customer_name: str = "",
    appointment_date: str = "",
    appointment_time: str = "",
):
    import os
    print("-" * 50)
    print("AGENT: finish_call TOOL INVOKED")
    print(f"PID: {os.getpid()}")
    print("-" * 50)

    # Dynamic multi-channel room resolution: match room for active call
    room_name = None
    for r_name, st in ACTIVE_CALLS.items():
        if st and not st.get("finishing"):
            room_name = r_name
            break

    if not room_name and ACTIVE_CALLS:
        room_name = list(ACTIVE_CALLS.keys())[0]

    if not room_name:
        return "No active call found."

    room_str = room_name

    state = ACTIVE_CALLS.get(room_str)
    if not state:
        return "No active call state found."

    # ── Guard against duplicate invocations ─────────────────────────────────
    # The LLM can call finish_call a second time while the first is still running
    # (e.g. customer says goodbye again). Ignore the duplicate.
    if state.get("finishing"):
        print("finish_call already in progress — ignoring duplicate invocation.")
        return "Call finish already in progress."

    # Mark as finishing so agent.py knows to wait for us before shutting down
    state["finishing"] = True
    session = state["session"]

    print(f"Room: {room_name}")

    # Determine appropriate goodbye phrase dynamically based on customer conversation context
    transcript = _build_transcript(session)
    lower_t = transcript.lower()

    if (appointment_date and appointment_date.strip()) or (appointment_time and appointment_time.strip()):
        if "call me" in lower_t or "call back" in lower_t or "reschedule" in lower_t or "later" in lower_t or "after" in lower_t:
            goodbye_phrase = "Thank you. I have scheduled your callback. Goodbye!"
        else:
            goodbye_phrase = "Thank you. Your appointment has been booked. Goodbye!"
    else:
        goodbye_phrase = "Thank you for your time. Have a great day! Goodbye."

    try:
        # ── Step 1: Speak the goodbye phrase via TTS ──────────────────────
        try:
            print(f"Speaking goodbye: '{goodbye_phrase}'")
            speech = session.say(goodbye_phrase, allow_interruptions=False)
            if speech:
                try:
                    await asyncio.wait_for(speech, timeout=8.0)
                except Exception:
                    pass
            # Calculate dynamic audio playback buffer based on character count (approx 10 chars/sec + 1.5s overhead)
            play_buffer = max(5.0, min(9.0, len(goodbye_phrase) * 0.11 + 1.5))
            print(f"Waiting {play_buffer:.1f}s for goodbye audio streaming to complete on SIP line...")
            await asyncio.sleep(play_buffer)
            print("Goodbye spoken successfully.")
        except Exception as e:
            print(f"Warning – could not speak goodbye (non-fatal): {e}")

        # ── Step 2: Build transcript (after goodbye is in history) ────────
        transcript = _build_transcript(session)
        print(f"Transcript lines: {len(transcript.splitlines())}")

        # ── Step 3: Close the agent session ──────────────────────────────
        try:
            print("Closing AgentSession...")
            await asyncio.wait_for(session.aclose(), timeout=5.0)
            print("AgentSession closed.")
        except Exception as e:
            print(f"Warning – session.aclose() error (non-fatal): {e}")

        # ── Step 4: Notify backend with full payload ──────────────────────
        call_id = state.get("call_id", -1) if state else -1
        if call_id == -1 or call_id is None:
            try:
                call_id = int(room_str.rsplit("-", 1)[-1])
            except (ValueError, IndexError):
                call_id = -1

        if call_id == -1 or call_id is None:
            try:
                from app.database import AsyncSessionLocal
                from app.models.call import Call
                from sqlalchemy import select
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Call).where(Call.room_name == room_str))
                    call = result.scalars().first()
                    if call:
                        call_id = call.id
            except Exception as db_err:
                print(f"[finish_call] Database lookup failed for room {room_str}: {db_err}")
                call_id = -1

        payload = {
            "transcript": transcript or None,
            "customer_name": customer_name or None,
            "appointment_date": appointment_date or None,
            "appointment_time": appointment_time or None,
            "recording_url": f"/api/recordings/call_{call_id}.wav" if call_id != -1 else None,
        }

        with open("finish_call_debug.log", "a") as f:
            f.write(f"\n--- FINISH CALL INVOKED ---\n")
            f.write(f"Room: {room_str}\n")
            f.write(f"Transcript generated: '{transcript}'\n")
            f.write(f"Payload: {payload}\n")

        # Mix WAV tracks — sleep briefly so recorder coroutine can close file handles
        if call_id != -1:
            try:
                await asyncio.sleep(1.5)  # give recorder time to flush & close on Windows
                from agent import mix_wav_files
                mix_wav_files(
                    f"recordings/call_{call_id}_customer.wav",
                    f"recordings/call_{call_id}_agent.wav",
                    f"recordings/call_{call_id}.wav"
                )
            except Exception as mix_err:
                print(f"Warning – mixing audio failed: {mix_err}")

        try:
            print("Notifying backend that the call is complete...")
            success = await notify_call_complete(room_str, payload=payload)
            if not success:
                print(f"[finish_call] FORENSIC ALERT: notify_call_complete returned FALSE for room '{room_str}'!")
        except Exception as e:
            print(f"[finish_call] ERROR notifying backend: {e}")

    finally:
        # ── Step 5: ALWAYS delete the LiveKit room to hang up the call ──
        try:
            print("Deleting LiveKit room (hanging up SIP call)...")
            import os
            lk_url = os.getenv("LIVEKIT_URL", "").replace("ws://", "http://").replace("wss://", "https://")
            lk_key = os.getenv("LIVEKIT_API_KEY")
            lk_secret = os.getenv("LIVEKIT_API_SECRET")
            
            if lk_url:
                lkapi = api.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret)
            else:
                lkapi = api.LiveKitAPI()

            try:
                await lkapi.room.delete_room(
                    api.DeleteRoomRequest(room=room_str)
                )
                with open("finish_call_debug.log", "a") as f: f.write(f"Room deleted successfully — call hung up.\n")
                print("Room deleted successfully — call hung up.")
            finally:
                await lkapi.aclose()
        except Exception as e:
            with open("finish_call_debug.log", "a") as f: f.write(f"Warning – room deletion error: {e}\n")
            print(f"Warning – room deletion error: {e}")

        # Remove active call state
        ACTIVE_CALLS.pop(room_str, None)
    res = request_call_finish(
        room_name=room_name,
        reason="llm_tool",
        customer_name=customer_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
    )

    if isinstance(res, str):
        return res

    state = ACTIVE_CALLS.get(room_name)
    if state:
        state["call_phase"] = "finishing"

    return "Call ended successfully."