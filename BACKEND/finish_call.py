import asyncio
from typing import Any

from livekit.agents import function_tool
from livekit import api

from app.services.conversation_state import ACTIVE_CALLS
from backend_client import notify_call_complete

GOODBYE_PHRASE = "Thank you. Your appointment request has been recorded. Goodbye."


def _build_transcript(session: Any) -> str:
    """
    Extract the conversation transcript from the AgentSession.

    LiveKit Agents v1.6 API:
      session.history          → ChatContext
      chat_ctx.messages()      → list[ChatMessage]   (method, not property)
      msg.role                 → ChatRole enum  (e.g. ChatRole.USER)
      msg.text_content         → str | None
    """
    try:
        chat_ctx = getattr(session, "history", None)
        if chat_ctx is None:
            print("Warning – session.history is None, transcript will be empty.")
            return ""

        # .messages() is a method in v1.6, not a property
        messages = chat_ctx.messages()

        lines = []
        for msg in messages:
            # ChatRole enum → "ChatRole.USER" → keep just "user"
            role = str(getattr(msg, "role", "")).split(".")[-1].lower()
            if role in ("system", "tool"):
                continue

            # text_content is the convenience property that joins all str content
            text = getattr(msg, "text_content", None)
            if not text:
                # Fallback: join any raw string items in .content list
                raw = getattr(msg, "content", [])
                text = " ".join(c for c in raw if isinstance(c, str))

            if text and text.strip():
                lines.append(f"{role}: {text.strip()}")

        return "\n".join(lines)

    except Exception as e:
        print(f"Warning – could not build transcript: {e}")
        return ""


@function_tool(
    description="""
Call this tool when the appointment has been fully confirmed by the customer.
The tool will automatically say the goodbye message and end the call.

Pass the details you collected during the conversation:
- customer_name: the customer's full name
- appointment_date: the date they gave (e.g. "15th July" or "2024-07-15")
- appointment_time: the time they gave (e.g. "10 AM" or "14:00")
"""
)
async def finish_call(
    customer_name: str = "",
    appointment_date: str = "",
    appointment_time: str = "",
):
    print("=" * 60)
    print("FINISH CALL TOOL CALLED")
    print(f"  customer_name   : {customer_name}")
    print(f"  appointment_date: {appointment_date}")
    print(f"  appointment_time: {appointment_time}")
    print("=" * 60)

    if not ACTIVE_CALLS:
        print("No active calls.")
        return "No active call found."

    # Temporary: one active call at a time.
    room_name = list(ACTIVE_CALLS.keys())[0]
    state = ACTIVE_CALLS.get(room_name)

    if state is None:
        print("State already removed.")
        return "No active call found."

    # Mark as finishing so agent.py knows to wait for us before shutting down
    state["finishing"] = True
    session = state["session"]

    print(f"Room: {room_name}")

    # ── Step 1: Speak the goodbye phrase via TTS, then wait for it ────
    # This guarantees the customer hears it regardless of whether the LLM
    # generated it or not.
    try:
        print(f"Speaking goodbye: '{GOODBYE_PHRASE}'")
        speech = session.say(GOODBYE_PHRASE, allow_interruptions=False)
        # SpeechHandle is awaitable — wait until audio playback is done
        await speech
        print("Goodbye spoken successfully.")
    except Exception as e:
        print(f"Warning – could not speak goodbye (non-fatal): {e}")
        # Still give a moment for any in-progress speech to drain
        await asyncio.sleep(2)

    # ── Step 2: Build transcript (after goodbye is in history) ────────
    transcript = _build_transcript(session)
    print(f"Transcript lines: {len(transcript.splitlines())}")

    # ── Step 3: Close the agent session ──────────────────────────────
    try:
        print("Closing AgentSession...")
        await asyncio.wait_for(session.aclose(), timeout=5.0)
        print("AgentSession closed.")
    except asyncio.TimeoutError:
        print("Warning – session.aclose() timed out (non-fatal, continuing...)")
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

    # Mix WAV tracks
    if call_id != -1:
        try:
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
        await notify_call_complete(room_name, payload=payload)
    except Exception as e:
        print(f"Warning – backend notify error (non-fatal): {e}")

    # ── Step 5: Delete the LiveKit room to hang up the SIP call ──────
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
        print(f"Warning – room deletion error (non-fatal): {e}")

    # Now that everything is done, we can remove the state
    ACTIVE_CALLS.pop(room_name, None)
    return "Call ended successfully."