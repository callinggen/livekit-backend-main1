from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.contact import Contact
from app.models.job import Job
from app.models.campaign import Campaign
from app.models.user import User
from app.services.notification_service import notification_service



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

                # Asynchronously evaluate Campaign WhatsApp Automation (non-blocking)
                try:
                    from app.services.whatsapp_automation_service import WhatsAppAutomationService
                    asyncio.create_task(WhatsAppAutomationService.process_call_automation(call_id))
                except Exception as auto_err:
                    print(f"[CallService] Non-fatal WhatsApp automation trigger error: {auto_err}")
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

        # Prevent double completion / race conditions
        if call.status in ("completed", "failed", "incomplete"):
            print(f"[CallService] Call {call_id} is ALREADY finished with status '{call.status}'")
            return call

        # ── Calculate timestamps and duration FIRST ───────────────────
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # store as naive UTC to match existing rows
        call.ended_at = now
        if call.started_at:
            started = call.started_at.replace(tzinfo=None) if (hasattr(call.started_at, "tzinfo") and call.started_at.tzinfo) else call.started_at
            call.duration = int((now - started).total_seconds())

        if recording_url:
            call.recording_url = recording_url

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

        # ── Determine if customer answered and engaged ────────────────
        has_transcript = bool(transcript and transcript.strip())
        has_customer_speech = False
        if has_transcript:
            lines = (transcript or "").strip().split("\n")
            for line in lines:
                lower_line = line.strip().lower()
                if lower_line.startswith("user:") or lower_line.startswith("customer:"):
                    has_customer_speech = True
                    break
            # If no explicit user role prefix was used, treat as speech if substantial content exists
            if not has_customer_speech and not any(l.strip().lower().startswith("assistant:") for l in lines) and len((transcript or "").strip()) > 10:
                has_customer_speech = True

        is_success = bool(has_transcript and (has_customer_speech or len((transcript or "").strip()) > 40)) and not is_voicemail
        is_missed_call = (not is_success) and (not is_voicemail)
        
        # Determine if we should deduct a credit (only for answered, non-voicemail calls)
        should_deduct = is_success and call.credits_deducted == 0 and not is_voicemail

        if is_voicemail:
            call.status = "incomplete"
        elif is_missed_call:
            call.status = "failed"
        else:
            call.status = "completed"
            
        if detection_metadata:
            call.detection_metadata = detection_metadata
        
        if should_deduct:
            owner = await _get_credit_owner_for_call(db, call)
            if owner and owner.credits > 0:
                owner.credits -= 1
                call.credits_deducted = 1
                try:
                    await notification_service.check_and_trigger_credit_notifications(db, owner)
                except Exception as e:
                    print(f"Error checking credit notifications: {e}")

        # Check if appointment_date is a real, valid date string
        has_valid_appointment = (
            appointment_date is not None 
            and appointment_date.strip().lower() not in ("", "none", "null", "n/a", "undefined", "false")
        )

        # Check transcript for response signals
        lower_tx = (transcript or "").lower()
        is_not_interested = any(phrase in lower_tx for phrase in [
            "not interested", "no interest", "don't want", "dont want", "no thanks",
            "not needing", "no assistance", "refuse", "declined", "stop calling",
            "do not call", "don't call", "never call", "remove my number"
        ])
        is_reschedule = any(phrase in lower_tx for phrase in [
            "call me", "call back", "reschedule", "later today", "tomorrow at",
            "after", "later this week", "next week", "would work better"
        ])

        # Default fallbacks before async background LLM enrichment
        if transcript:
            call.transcript = transcript
            if is_voicemail:
                call.summary = "Voicemail"
                call.category = "COLD"
            elif is_not_interested:
                call.summary = "Not Interested"
                call.category = "COLD"
            elif is_reschedule:
                call.summary = "Callback Requested"
                call.category = "WARM"
            elif is_missed_call:
                call.summary = "No Answer"
                call.category = "COLD"
            else:
                call.summary = "General Inquiry"
                call.category = "UNCATEGORIZED"
        else:
            if is_voicemail:
                call.summary = "Voicemail"
                call.category = "COLD"
            elif is_missed_call:
                call.summary = "No Answer"
                call.category = "COLD"
            else:
                call.summary = "General Inquiry"
                call.category = "UNCATEGORIZED"

        # ── Contact ───────────────────────────────────────────────────
        contact = await db.get(Contact, call.contact_id)
        if contact:
            if is_voicemail:
                contact.status = "incomplete"
                contact.response = "Voicemail"
            elif is_missed_call:
                contact.status = "failed"
                contact.response = "No Answer"
            else:
                contact.status = "completed"
                if is_not_interested:
                    contact.response = "Not Interested"
                elif has_valid_appointment:
                    contact.appointment_date = appointment_date
                    if appointment_time:
                        contact.appointment_time = appointment_time
                    contact.response = "Rescheduled" if is_reschedule else "Appointment Booked"
                elif is_reschedule:
                    contact.response = "Rescheduled"
                else:
                    contact.response = "Answered"

            contact.duration = str(call.duration)
            if transcript:
                contact.transcript = transcript
            if customer_name:
                contact.customer_name = customer_name

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
                _analyze_and_update_summary(call.id, transcript, business_outcome, is_not_interested)
            )
        else:
            # Trigger WhatsApp Automation directly if no long transcript analysis
            try:
                import asyncio
                from app.services.whatsapp_automation_service import WhatsAppAutomationService
                asyncio.create_task(WhatsAppAutomationService.process_call_automation(call.id))
            except Exception as auto_err:
                print(f"[CallService] Non-fatal WhatsApp automation trigger error: {auto_err}")

        # ── Asynchronously trigger WhatsApp follow-up for Voicemail or Missed Call ──
        if is_voicemail or is_missed_call:
            try:
                import asyncio
                from app.services.whatsapp_actions import WhatsAppActionService
                action_type = "SEND_VOICEMAIL" if is_voicemail else "SEND_MISSED_CALL"
                asyncio.create_task(
                    WhatsAppActionService.execute_action(
                        call_id=call.id,
                        action=action_type,
                        contact_id=call.contact_id,
                        phone=call.phone,
                    )
                )
                print(f"[CallService] Queued WhatsApp action '{action_type}' for Call {call.id}")
            except Exception as wa_err:
                print(f"[CallService] Non-fatal WhatsApp trigger error: {wa_err}")

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
            contact.response = "Call Cut / Disconnected" if has_tx else "No Answer"

        call.summary = "No Answer" if not (call.transcript and len(call.transcript.strip()) > 0) else "Call Cut"
        call.category = "COLD"

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

        # Asynchronously evaluate Campaign WhatsApp Automation (non-blocking)
        try:
            import asyncio
            from app.services.whatsapp_automation_service import WhatsAppAutomationService
            asyncio.create_task(WhatsAppAutomationService.process_call_automation(call.id))
        except Exception as auto_err:
            print(f"[CallService] Non-fatal WhatsApp automation fail_call trigger error: {auto_err}")

        # Asynchronously trigger missed call / busy WhatsApp follow-up (non-blocking)
        try:
            import asyncio
            from app.services.whatsapp_actions import WhatsAppActionService
            is_busy = bool(contact and "busy" in (contact.response or "").lower())
            action_type = "SEND_CALLBACK" if is_busy else "SEND_MISSED_CALL"
            asyncio.create_task(
                WhatsAppActionService.execute_action(
                    call_id=call.id,
                    action=action_type,
                    contact_id=call.contact_id,
                    phone=call.phone,
                )
            )
        except Exception as wa_err:
            print(f"[CallService] Non-fatal WhatsApp missed call trigger error: {wa_err}")

        return call