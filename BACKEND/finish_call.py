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

        # In some versions it's a list, in others it's an object with .messages or .messages()
        if hasattr(chat_ctx, "messages"):
            messages = chat_ctx.messages() if callable(chat_ctx.messages) else chat_ctx.messages
        else:
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

        return "\n".join(lines)

    except Exception as e:
        import traceback
        print(f"Warning – could not build transcript: {e}\n{traceback.format_exc()}")
        return ""


@function_tool(
    description="""
Call this tool whenever the call/conversation is complete or needs to end.
This includes:
- When an appointment is booked or confirmed by the customer.
- When the customer is not interested, busy, or declines.
- When the customer says goodbye, thank you, or indicates they want to hang up.

Calling this tool will automatically say goodbye and hang up the call.

Pass any details collected during the conversation:
- customer_name: the customer's full name
- appointment_date: the date if an appointment was booked (e.g. "2026-07-15")
- appointment_time: the time if an appointment was booked (e.g. "10:00 AM")
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
    print(f"customer_name   : '{customer_name}'")
    print(f"appointment_date: '{appointment_date}'")
    print(f"appointment_time: '{appointment_time}'")
    print(f"ACTIVE_CALLS keys: {list(ACTIVE_CALLS.keys())}")
    print("-" * 50)

    if not ACTIVE_CALLS:
        print("[finish_call] WARNING: No active calls in ACTIVE_CALLS dictionary.")
        return "No active call found."

    # Temporary: one active call at a time.
    room_name = list(ACTIVE_CALLS.keys())[0]
    state = ACTIVE_CALLS.get(room_name)

    if state is None:
        print(f"[finish_call] WARNING: State for room '{room_name}' already removed.")
        return "No active call found."

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
                    await asyncio.wait_for(speech, timeout=6.0)
                except Exception:
                    pass
            # Give TTS audio 2 seconds to play out to SIP line before closing room
            await asyncio.sleep(2.5)
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
        try:
            call_id = int(room_name.rsplit("-", 1)[-1])
        except (ValueError, IndexError):
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
            f.write(f"Room: {room_name}\n")
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
            success = await notify_call_complete(room_name, payload=payload)
            if not success:
                print(f"[finish_call] FORENSIC ALERT: notify_call_complete returned FALSE for room '{room_name}'!")
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
                    api.DeleteRoomRequest(room=room_name)
                )
                with open("finish_call_debug.log", "a") as f: f.write(f"Room deleted successfully — call hung up.\n")
                print("Room deleted successfully — call hung up.")
            finally:
                await lkapi.aclose()
        except Exception as e:
            with open("finish_call_debug.log", "a") as f: f.write(f"Warning – room deletion error: {e}\n")
            print(f"Warning – room deletion error: {e}")

        # Remove active call state
        ACTIVE_CALLS.pop(room_name, None)

    return "Call ended successfully."