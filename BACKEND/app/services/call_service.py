from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.contact import Contact
from app.models.job import Job
from app.models.campaign import Campaign
from app.models.user import User


async def _get_credit_owner_for_call(db: AsyncSession, call: Call) -> Optional[User]:
    """
    Resolve the user owning this call for credit deduction.
    Traces: call → job → campaign → user
    """
    from sqlalchemy import select
    from app.models.job import Job
    from app.models.campaign import Campaign
    job = await db.get(Job, call.job_id)
    if job is None:
        return None
    campaign = await db.get(Campaign, job.campaign_id)
    if campaign is None or campaign.user_id is None:
        return None
    result = await db.execute(select(User).where(User.id == campaign.user_id))
    return result.scalars().first()


async def _analyze_and_update_summary(call_id: int, transcript: str, business_outcome: str, is_opt_out: bool):
    """Background task to run DeepSeek classification after DB commit."""
    try:
        from app.database import AsyncSessionLocal
        import os
        import asyncio
        from openai import AsyncOpenAI

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if not deepseek_key or len(transcript) <= 20:
            return

        client = AsyncOpenAI(
            api_key=deepseek_key,
            base_url="https://api.deepseek.com/v1"
        )
        
        prompt_class = (
            "Analyze the following call transcript and provide a very short, 2 to 3 word summary "
            "that explains the entire conversation.\n"
            "There are no predefined categories. Just use your own words to best describe the conversation in 2-3 words.\n"
            "DO NOT output full sentences. Return pure text, no markdown, no quotes, no periods at the end.\n\n"
            f"Transcript:\n{transcript}"
        )
        
        prompt_cat = (
            "Analyze the following call transcript and the Business Outcome to determine the Sales Pipeline Category.\n"
            "The category MUST be exactly one of the following words: HOT, WARM, or COLD.\n"
            "- HOT = High-priority lead with strong/immediate intent, appointment or consultation booked, or clearly ready to proceed.\n"
            "- WARM = Medium-priority lead showing interest but requiring more information, consideration, or follow-up.\n"
            "- COLD = Low-priority lead, refusal, opt-out, 'do not call', not needing service, or no conversion potential.\n"
            "Output ONLY the single word (HOT, WARM, or COLD). No markdown, no punctuation.\n\n"
            f"Business Outcome: {business_outcome}\n"
            f"Transcript:\n{transcript}"
        )
        
        task_class = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt_class}],
            max_tokens=10,
            temperature=0.3
        )
        
        task_cat = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt_cat}],
            max_tokens=10,
            temperature=0.3
        )
        
        res_class, res_cat = await asyncio.gather(task_class, task_cat)
        
        raw_summary = res_class.choices[0].message.content or ""
        clean_summary = raw_summary.strip().strip("'\".").replace("\n", " ")
        
        raw_cat = res_cat.choices[0].message.content or ""
        clean_cat = raw_cat.strip().strip("'\".").upper()
        
        async with AsyncSessionLocal() as bg_db:
            bg_call = await bg_db.get(Call, call_id)
            if bg_call:
                if is_opt_out:
                    bg_call.summary = "Do Not Call Request"
                    bg_call.category = "COLD"
                else:
                    if clean_summary and len(clean_summary.split()) <= 6:
                        bg_call.summary = clean_summary
                    if clean_cat in ["HOT", "WARM", "COLD"]:
                        bg_call.category = clean_cat
                await bg_db.commit()
                print(f"[CallService] Background AI classification updated for Call {call_id}: summary='{bg_call.summary}', category='{bg_call.category}'")
    except Exception as e:
        print(f"[CallService] Background DeepSeek analysis error (non-fatal): {e}")


class CallService:

    @staticmethod
    async def complete_call(
        db: AsyncSession,
        call_id: int,
        transcript: Optional[str] = None,
        customer_name: Optional[str] = None,
        appointment_date: Optional[str] = None,
        appointment_time: Optional[str] = None,
        recording_url: Optional[str] = None,
        is_voicemail: bool = False,
        detection_metadata: Optional[dict] = None,
    ):
        import os
        print("-" * 50)
        print("BACKEND: CallService.complete_call START")
        print(f"PID: {os.getpid()}")
        print(f"Call ID: {call_id}")
        print(f"Has Transcript: {bool(transcript and transcript.strip())}")
        print("-" * 50)

        call = await db.get(Call, call_id)

        if call is None:
            print(f"[CallService] Call {call_id} NOT FOUND in DB")
            return None

        # Prevent double completion
        if call.status == "completed":
            print(f"[CallService] Call {call_id} is ALREADY completed")
            return call

        # ── Fallback Voicemail Detection ───────────────────────────────
        if not is_voicemail and transcript:
            lower_tx_vm = transcript.lower()
            voicemail_phrases = [
                "please leave a message",
                "leave your message",
                "at the tone",
                "person you're trying to reach",
                "person you are trying to reach",
                "call has been forwarded",
                "record your message",
                "is being screened",
                "state your name",
                "after the beep",
                "voicemail",
                "textmail subscriber",
                "google subscriber",
                "unavailable"
            ]
            if any(phrase in lower_tx_vm for phrase in voicemail_phrases):
                if transcript.count('\n') < 6:
                    is_voicemail = True
                    if not detection_metadata:
                        detection_metadata = {
                            "type": "voicemail",
                            "trigger": "backend_fallback",
                            "confidence": 90.0,
                            "credits_charged": False
                        }

        # ── Determine if it's a success or failure ────────────────────
        has_transcript = bool(transcript and len(transcript.strip()) > 0)
        has_duration = bool(call.duration and call.duration > 5)
        has_audio = bool(recording_url or (call.recording_url and os.path.exists(call.recording_url.lstrip("/"))))

        is_success = (has_transcript or has_duration or has_audio) and not is_voicemail
        
        # Determine if we should deduct a credit (transitioning to completed and no credit deducted yet)
        should_deduct = is_success and call.credits_deducted == 0 and not is_voicemail

        if is_voicemail:
            call.status = "incomplete"
        else:
            call.status = "completed" if is_success else "failed"
            
        if detection_metadata:
            call.detection_metadata = detection_metadata
        
        if should_deduct:
            owner = await _get_credit_owner_for_call(db, call)
            if owner and owner.credits > 0:
                owner.credits -= 1
                call.credits_deducted = 1
        
        if recording_url:
            call.recording_url = recording_url
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # store as naive UTC to match existing rows
        call.ended_at = now
        if call.started_at:
            started = call.started_at.replace(tzinfo=None) if (hasattr(call.started_at, "tzinfo") and call.started_at.tzinfo) else call.started_at
            call.duration = int((now - started).total_seconds())

        # Check if appointment_date is a real, valid date string
        has_valid_appointment = (
            appointment_date is not None 
            and appointment_date.strip().lower() not in ("", "none", "null", "n/a", "undefined", "false")
        )

        # Check transcript for refusal / do not call signals
        lower_tx = (transcript or "").lower()
        is_opt_out = any(phrase in lower_tx for phrase in [
            "do not call", "don't call", "stop calling", "remove my number",
            "not interested", "no assistance", "don't need", "no thanks",
            "refuse", "declined", "never call"
        ])

        # Default fallbacks before async background LLM enrichment
        if transcript:
            call.transcript = transcript
            call.summary = "Do Not Call Request" if is_opt_out else "General Inquiry"
            call.category = "COLD" if is_opt_out else "UNCATEGORIZED"
        else:
            call.summary = "General Inquiry"
            call.category = "UNCATEGORIZED"

        # ── Contact ───────────────────────────────────────────────────
        contact = await db.get(Contact, call.contact_id)
        if contact:
            if is_voicemail:
                contact.status = "incomplete"
            else:
                contact.status = "completed" if is_success else "failed"
            contact.duration = str(call.duration)
            if transcript:
                contact.transcript = transcript
            if customer_name:
                contact.customer_name = customer_name

            if is_voicemail:
                contact.response = "No Answer"
            elif is_opt_out:
                contact.response = "Do Not Call / Refusal"
            elif has_valid_appointment:
                contact.appointment_date = appointment_date
                if appointment_time:
                    contact.appointment_time = appointment_time
                contact.response = "Appointment Booked"
            else:
                contact.response = "Answered" if is_success else "No Answer / Cut"

        business_outcome = contact.response if contact else "None"

        # ── Job / Campaign ────────────────────────────────────────────
        job = await db.get(Job, call.job_id)
        if job:
            if is_success:
                job.completed_contacts += 1
            else:
                job.failed_contacts += 1
            # Mark job & campaign complete when all contacts are processed
            if (job.completed_contacts + job.failed_contacts) >= job.total_contacts:
                job.status = "completed"
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                campaign = await db.get(Campaign, job.campaign_id)
                if campaign:
                    if job.completed_contacts == 0 and job.failed_contacts > 0:
                        campaign.status = "incomplete"
                    else:
                        campaign.status = "completed"

        # ── IMMEDIATE COMMIT ──────────────────────────────────────────
        await db.commit()

        print("-" * 50)
        print("BACKEND: CallService.complete_call COMMIT SUCCESSFUL")
        print(f"Call ID {call.id} Status -> {call.status}")
        print(f"Contact ID {call.contact_id} Status -> {contact.status if contact else 'N/A'}")
        print(f"Job ID {call.job_id} Completed Contacts -> {job.completed_contacts if job else 0}")
        print("-" * 50)

        # ── Spawn DeepSeek Analysis in Background (Non-blocking) ──────
        if transcript and len(transcript.strip()) > 20:
            import asyncio
            asyncio.create_task(
                _analyze_and_update_summary(call.id, transcript, business_outcome, is_opt_out)
            )

        return call

    @staticmethod
    async def fail_call(
        db: AsyncSession,
        call_id: int,
    ):
        """
        Mark a call as failed/no_answer and advance the campaign to the next contact.
        Called when a SIP dial attempt fails, user declines, no answer, or timeout occurs.
        """
        call = await db.get(Call, call_id)
        if call is None:
            return None

        if call.status in ("completed", "failed"):
            return call

        call.status = "failed"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        call.ended_at = now
        if call.started_at:
            started = call.started_at.replace(tzinfo=None) if (hasattr(call.started_at, "tzinfo") and call.started_at.tzinfo) else call.started_at
            call.duration = int((now - started).total_seconds())

        contact = await db.get(Contact, call.contact_id)
        if contact:
            # Differentiate between no-answer / unreached vs call cut
            has_tx = call.transcript and len(call.transcript.strip()) > 0
            contact.status = "failed"
            contact.response = "Call Cut / Disconnected" if has_tx else "No Answer / Declined"

        job = await db.get(Job, call.job_id)
        if job:
            job.failed_contacts += 1
            # Mark job & campaign complete when all contacts are processed
            if (job.completed_contacts + job.failed_contacts) >= job.total_contacts:
                job.status = "completed"
                job.finished_at = now
                campaign = await db.get(Campaign, job.campaign_id)
                if campaign:
                    if job.completed_contacts == 0 and job.failed_contacts > 0:
                        campaign.status = "incomplete"
                    else:
                        campaign.status = "completed"

        await db.commit()
        return call