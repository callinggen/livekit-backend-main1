from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, case

from app.models.call import Call
from app.models.contact import Contact
from app.models.job import Job
from app.models.campaign import Campaign
from app.models.user import User
from app.services.notification_service import notification_service


def classify_call_end(sip_was_active: bool, disconnect_reason: Optional[str], outcome_override: Optional[str], failure_reason: Optional[str]):
    """
    Definitive central classifier for call states and outcomes.
    Returns: (status, outcome, failure_reason)
    """
    if failure_reason:
        return "failed", "unknown", failure_reason

    # Pre-Answer Cases
    if not sip_was_active:
        if outcome_override in ("declined", "busy", "voicemail"):
            return "ended", outcome_override, None
        return "ended", "no_answer", None

    # Post-Answer Cases (sip_was_active == True -> NEVER allow no_answer)
    
    # Clean outcome_override if it was somehow passed as no_answer
    if outcome_override == "no_answer":
        outcome_override = None

    if outcome_override in ("appointment_booked", "rescheduled", "not_interested", "customer_no_response", "agent_no_response", "customer_hangup", "agent_hangup"):
        return "completed", outcome_override, None

    if disconnect_reason == "customer_disconnect":
        return "completed", "customer_hangup", None
    elif disconnect_reason in ("agent_hangup", "llm_tool"):
        if outcome_override:
            return "completed", outcome_override, None
        return "completed", "agent_hangup", None
    
    if outcome_override:
        return "completed", outcome_override, None
        
    return "completed", "answered", None
async def _get_credit_owner_for_call(db: AsyncSession, call: Call) -> Optional[User]:
    """
    Resolve the user owning this call for credit deduction.
    Traces: call → job → campaign → user
    Or: call → campaign → user
    Or: call → contact → campaign → user
    Or: call → tenant_id / user_id
    """
    from sqlalchemy import select
    from app.models.job import Job
    from app.models.campaign import Campaign
    from app.models.contact import Contact

    if call.direction == "inbound" and call.tenant_id:
        result = await db.execute(select(User).where(User.id == call.tenant_id))
        return result.scalars().first()

    # 1. Tracing via job
    if call.job_id:
        job = await db.get(Job, call.job_id)
        if job and job.campaign_id:
            campaign = await db.get(Campaign, job.campaign_id)
            if campaign and campaign.user_id:
                result = await db.execute(select(User).where(User.id == campaign.user_id))
                user = result.scalars().first()
                if user:
                    return user

    # 2. Direct campaign lookup
    campaign_id = getattr(call, "campaign_id", None)
    if not campaign_id and call.contact_id:
        contact = await db.get(Contact, call.contact_id)
        if contact:
            campaign_id = contact.campaign_id
    if campaign_id:
        campaign = await db.get(Campaign, campaign_id)
        if campaign and campaign.user_id:
            result = await db.execute(select(User).where(User.id == campaign.user_id))
            user = result.scalars().first()
            if user:
                return user

    # 3. Direct user_id or tenant_id on call
    user_id = getattr(call, "user_id", None) or getattr(call, "tenant_id", None)
    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()

    return None


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

        from datetime import timedelta
        ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        today_ref = ist_now.strftime("%Y-%m-%d (%A)")

        prompt_appt = (
            f"Reference call date: {today_ref} (IST).\n"
            "Analyze the following call transcript. Did the customer and assistant agree on or schedule a specific appointment, consultation, demo, or callback date and time?\n"
            "If YES, return a JSON object with:\n"
            "  \"is_appointment\": true,\n"
            "  \"appointment_date\": \"YYYY-MM-DD\" (resolve relative terms like 'tomorrow', 'this Friday', 'next Monday' to actual YYYY-MM-DD based on the reference call date),\n"
            "  \"appointment_time\": \"hh:mm AM/PM\" (e.g. \"02:30 PM\"),\n"
            "  \"type\": \"appointment\" or \"reschedule\"\n"
            "If NO appointment or callback was scheduled, return:\n"
            "  {\"is_appointment\": false}\n"
            "Return ONLY the valid JSON object. No markdown, no other text.\n\n"
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

        task_appt = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt_appt}],
            max_tokens=120,
            temperature=0.1
        )
        
        res_class, res_cat, res_appt = await asyncio.gather(task_class, task_cat, task_appt, return_exceptions=True)
        
        raw_summary = ""
        if res_class and not isinstance(res_class, BaseException):
            try:
                raw_summary = res_class.choices[0].message.content or ""
            except Exception:
                pass
        clean_summary = raw_summary.strip().strip("'\".").replace("\n", " ")
        
        raw_cat = ""
        if res_cat and not isinstance(res_cat, BaseException):
            try:
                raw_cat = res_cat.choices[0].message.content or ""
            except Exception:
                pass
        clean_cat = raw_cat.strip().strip("'\".").upper()

        appt_data = {}
        if res_appt and not isinstance(res_appt, BaseException):
            try:
                import json
                raw_appt = res_appt.choices[0].message.content or "{}"
                # Clean possible markdown block
                raw_appt = raw_appt.strip().replace("```json", "").replace("```", "").strip()
                appt_data = json.loads(raw_appt)
            except Exception as j_err:
                print(f"[CallService] JSON parse error for appointment extraction: {j_err}")
        
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

                # If DeepSeek discovered an appointment or callback scheduled
                if appt_data.get("is_appointment") and appt_data.get("appointment_date"):
                    parsed_date = str(appt_data["appointment_date"]).strip()
                    parsed_time = str(appt_data.get("appointment_time") or "10:00 AM").strip()
                    is_meeting = (appt_data.get("type") != "reschedule")
                    
                    contact = None
                    if bg_call.contact_id:
                        contact = await bg_db.get(Contact, bg_call.contact_id)
                    if contact:
                        if not contact.appointment_date:
                            contact.appointment_date = parsed_date
                            contact.appointment_time = parsed_time
                            contact.response = "Appointment Booked" if is_meeting else "Rescheduled"
                            print(f"[CallService] AI extracted appointment for Contact {contact.id}: {parsed_date} at {parsed_time}")
                    bg_call.outcome = "appointment_booked" if is_meeting else "rescheduled"
                    bg_call.category = "HOT"

                await bg_db.commit()
                print(f"[CallService] Background AI classification updated for Call {call_id}: summary='{bg_call.summary}', category='{bg_call.category}'")

                # Trigger campaign WhatsApp automation rules now that classification is finalized
                try:
                    from app.services.whatsapp_automation_service import WhatsAppAutomationService
                    await WhatsAppAutomationService.process_call_automation(call_id)
                except Exception as wa_err:
                    print(f"[CallService] WhatsApp automation error after AI classification for Call {call_id}: {wa_err}")
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
        duration: Optional[int] = None,
        outcome: Optional[str] = None,
        failure_reason: Optional[str] = None,
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

        # Store previous status to handle watchdog race condition stats
        was_failed = (call.status == "failed")

        # Prevent double completion
        if call.status == "completed":
            print(f"[CallService] Call {call_id} is ALREADY completed")
            return call

        # ── Pre-calculate signals from transcript & appointments ──────
        customer_lines = 0
        if transcript:
            for line in transcript.strip().splitlines():
                if line.strip().lower().startswith("user:"):
                    customer_lines += 1

        # Normalize appointment_date if passed in ISO or other formats
        if appointment_date and isinstance(appointment_date, str):
            appointment_date = appointment_date.strip()
            if "T" in appointment_date:
                appointment_date = appointment_date.split("T")[0]
            elif " " in appointment_date and len(appointment_date.split(" ")[0]) == 10 and "-" in appointment_date.split(" ")[0]:
                appointment_date = appointment_date.split(" ")[0]

        has_valid_appointment = (
            appointment_date is not None 
            and appointment_date.strip().lower() not in ("", "none", "null", "n/a", "undefined", "false")
        )

        connected = bool(
            call.sip_was_active 
            or call.answered_at 
            or (duration is not None and duration > 0)
            or (call.duration and call.duration > 0)
            or customer_lines > 0
            or has_valid_appointment
        )

        # ── Calculate timestamps and duration ─────────────────────────
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # store as naive UTC to match existing rows
        call.ended_at = now
        
        if duration is not None and duration > 0:
            call.duration = duration
        elif connected:
            ans_time = call.answered_at or call.started_at
            if ans_time:
                ans_time = ans_time.replace(tzinfo=None) if (hasattr(ans_time, "tzinfo") and ans_time.tzinfo) else ans_time
                call.duration = max(1, int((now - ans_time).total_seconds()))
            else:
                call.duration = max(1, call.duration or 1)
        else:
            call.duration = 0

        if connected:
            call.sip_was_active = True

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

        # Check transcript for response signals (do this first to inform outcome_override)
        lower_tx = (transcript or "").lower()
        is_not_interested = any(phrase in lower_tx for phrase in [
            "not interested", "no interest", "don't want", "dont want", "no thanks",
            "not needing", "no assistance", "refuse", "declined", "stop calling",
            "do not call", "don't call", "never call", "remove my number"
        ])
        # Fix keyword list: remove bare 'after' which caused false positives on 'after filing'
        is_reschedule = any(phrase in lower_tx for phrase in [
            "call me", "call back", "reschedule", "later today", "tomorrow at",
            "call later", "call after", "reach me later", "later this week", "next week", "would work better"
        ])

        outcome_override = outcome
        if is_voicemail:
            outcome_override = "voicemail"
        elif has_valid_appointment:
            outcome_override = "appointment_booked"
        elif is_not_interested:
            outcome_override = "not_interested"
        elif is_reschedule:
            outcome_override = "rescheduled"

        # Note: disconnect_reason is not explicitly passed as a distinct field right now,
        # but 'outcome' usually contains 'customer_hangup' or 'agent_hangup' from agent.py if a disconnect occurs.
        # We can map that.
        disconnect_reason = None
        if outcome in ("customer_hangup", "agent_hangup"):
            disconnect_reason = outcome

        final_status, final_outcome, final_failure = classify_call_end(
            sip_was_active=call.sip_was_active,
            disconnect_reason=disconnect_reason,
            outcome_override=outcome_override,
            failure_reason=failure_reason
        )

        # A call that was never answered is only one where neither SIP was active nor answered_at was recorded
        if not connected and not is_voicemail and not has_valid_appointment:
            final_status = "ended"
            final_outcome = "no_answer"
        else:
            final_status = "completed"
            if outcome_override:
                final_outcome = outcome_override
            elif final_outcome in ("no_answer", "unknown", None):
                final_outcome = "customer_hangup" if disconnect_reason == "customer_disconnect" or outcome in ("customer_hangup", "customer_silence") else "answered"
                print(f"[CLASSIFICATION] Connected call {call_id} auto-resolved outcome to '{final_outcome}' (status: {final_status})")
        
        print(f"\n[CLASSIFICATION INVARIANT]")
        print(f"call_id={call_id}")
        print(f"customer_lines={customer_lines}")
        print(f"connected={connected}")
        print(f"sip_was_active={call.sip_was_active}")
        print(f"answered_at={call.answered_at}")
        print(f"outcome={final_outcome}\n")

        call.status = final_status
        call.outcome = final_outcome
        call.failure_reason = final_failure
        
        # A successful call is any post-answer call that didn't fail
        is_success = call.status == "completed"
        
        if detection_metadata:
            call.detection_metadata = detection_metadata
            
        # Determine if we should deduct credits
        # Deduct if billing is pending OR if the call succeeded and hasn't been billed yet (e.g. premature watchdog status transition)
        if is_success and not is_voicemail:
            if call.billing_status != "billed" or not call.credits_deducted:
                import math
                credits_to_deduct = math.floor(max(0, call.duration) / 4)
                
                if credits_to_deduct > 0:
                    owner = await _get_credit_owner_for_call(db, call)
                    if owner:
                        owner.credits -= credits_to_deduct
                        call.credits_deducted = credits_to_deduct
                        
                        try:
                            await notification_service.check_and_trigger_credit_notifications(db, owner)
                        except Exception as e:
                            print(f"Error checking credit notifications: {e}")
                
                call.billing_status = "billed"
        else:
            if call.billing_status == "pending":
                call.billing_status = "not_billable"

        # Default fallbacks before async background LLM enrichment
        if transcript:
            call.transcript = transcript
            if is_not_interested:
                call.summary = "Not Interested"
                call.category = "COLD"
            elif call.outcome == "no_answer":
                call.summary = "Unanswered Call"
                call.category = "UNANSWERED"
            else:
                call.summary = "General Inquiry"
                call.category = "UNCATEGORIZED"
        else:
            if call.outcome == "no_answer":
                call.summary = "Unanswered Call"
                call.category = "UNANSWERED"
            else:
                call.summary = "General Inquiry"
                call.category = "UNCATEGORIZED"

        # ── Contact ───────────────────────────────────────────────────
        contact = None
        if call.contact_id:
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
                contact.response = "Voicemail"
            elif is_not_interested:
                contact.response = "Not Interested"
            elif has_valid_appointment:
                contact.appointment_date = appointment_date
                if appointment_time:
                    contact.appointment_time = appointment_time
                contact.response = "Rescheduled" if is_reschedule else "Appointment Booked"
            elif is_reschedule:
                contact.response = "Rescheduled"
            else:
                if call.status == "failed":
                    contact.response = "Failed"
                else:
                    contact.response = call.outcome.replace("_", " ").title() if call.outcome else "Unknown"

        business_outcome = contact.response if contact else "None"

        # ── Job / Campaign ────────────────────────────────────────────
        job = None
        if call.job_id:
            job = await db.get(Job, call.job_id)
        if job:
            if is_success:
                job.completed_contacts += 1
                if was_failed:
                    job.failed_contacts = max(0, job.failed_contacts - 1)
            else:
                if not was_failed:
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

        if not job and contact and contact.campaign_id:
            campaign = await db.get(Campaign, contact.campaign_id)
            if campaign and campaign.status in ("running", "scheduled", "pending"):
                contact_count_res = await db.execute(
                    select(
                        func.count(Contact.id),
                        func.count(case((Contact.status.in_(["pending", "dialing"]), Contact.id)))
                    ).where(Contact.campaign_id == campaign.id)
                )
                row = contact_count_res.first()
                if row:
                    total_cnt, remaining_cnt = row
                    if remaining_cnt == 0 and total_cnt > 0:
                        campaign.status = "completed"

        # ── BACKEND GUARD ─────────────────────────────────────────────
        if call.sip_was_active or call.answered_at or (call.duration and call.duration > 0):
            if call.outcome == "no_answer":
                call.outcome = "customer_hangup"
                call.status = "completed"
                
        if call.outcome in ("no_answer", "declined", "busy") and not (call.answered_at or call.sip_was_active or (call.duration and call.duration > 0)):
            call.duration = 0
            call.credits_deducted = 0
            if contact:
                contact.duration = "0"

        # ── Demo Lead Sync ────────────────────────────────────────────
        try:
            from app.models.demo_lead import DemoLead
            lead_stmt = select(DemoLead).where(DemoLead.call_id == call.id)
            lead_res = await db.execute(lead_stmt)
            demo_lead = lead_res.scalars().first()
            if demo_lead:
                if is_success or call.status == "completed" or (call.duration and call.duration > 0):
                    demo_lead.status = "completed"
                elif is_voicemail or call.outcome in ("no_answer", "busy", "declined"):
                    demo_lead.status = "no_answer"
                else:
                    demo_lead.status = "failed"
        except Exception as dl_err:
            print(f"[CallService] Demo lead sync error (non-fatal): {dl_err}")

        # ── IMMEDIATE COMMIT ──────────────────────────────────────────
        await db.commit()

        print("-" * 50)
        print("BACKEND: CallService.complete_call COMMIT SUCCESSFUL")
        print(f"Call ID {call.id} Status -> {call.status}")
        print(f"Contact ID {call.contact_id} Status -> {contact.status if contact else 'N/A'}")
        print(f"Job ID {call.job_id} Completed Contacts -> {job.completed_contacts if job else 0}")
        print("-" * 50)

        # ── Spawn DeepSeek Analysis & WhatsApp Automation (Non-blocking) ──────
        import asyncio
        if transcript and len(transcript.strip()) > 20:
            asyncio.create_task(
                _analyze_and_update_summary(call.id, transcript, business_outcome, is_not_interested)
            )
        else:
            try:
                from app.services.whatsapp_automation_service import WhatsAppAutomationService
                asyncio.create_task(WhatsAppAutomationService.process_call_automation(call.id))
            except Exception as wa_err:
                print(f"[CallService] Error dispatching WhatsApp automation for Call {call.id}: {wa_err}")

        return call

    @staticmethod
    async def fail_call(
        db: AsyncSession,
        call_id: int,
        outcome: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ):
        """
        Mark a call as failed/no_answer and advance the campaign to the next contact.
        Called when a SIP dial attempt fails, user declines, no answer, or timeout occurs.
        """
        call = await db.get(Call, call_id)
        if call is None:
            return None

        if call.status in ("completed", "failed", "ended"):
            return call

        final_status, final_outcome, final_failure = classify_call_end(
            sip_was_active=call.sip_was_active,
            disconnect_reason=None,
            outcome_override=outcome,
            failure_reason=failure_reason
        )

        call.status = final_status
        call.outcome = final_outcome
        call.failure_reason = final_failure
        
        if call.billing_status == "pending":
            call.billing_status = "not_billable"
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        call.ended_at = now
        if call.started_at:
            call.duration = 0

        contact = None
        if call.contact_id:
            contact = await db.get(Contact, call.contact_id)
        if contact:
            if call.status == "failed":
                contact.status = "failed"
                contact.response = "System Failure"
            else:
                contact.status = "failed" # From campaign's perspective, this contact failed to convert
                has_tx = call.transcript and len(call.transcript.strip()) > 0
                if has_tx and call.outcome == "no_answer":
                    contact.response = "Call Cut / Disconnected"
                else:
                    contact.response = (call.outcome or "no_answer").replace("_", " ").title()

        job = None
        if call.job_id:
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
                    # Safety net: mark any remaining pending/dialing contacts as failed
                    # so campaigns never get stuck in "running" state
                    from app.models.contact import Contact as ContactModel
                    await db.execute(
                        update(ContactModel)
                        .where(ContactModel.campaign_id == job.campaign_id)
                        .where(ContactModel.status.in_(["pending", "dialing"]))
                        .values(status="failed", response="System Failure")
                    )

        # ── Demo Lead Sync ────────────────────────────────────────────
        try:
            from app.models.demo_lead import DemoLead
            lead_stmt = select(DemoLead).where(DemoLead.call_id == call.id)
            lead_res = await db.execute(lead_stmt)
            demo_lead = lead_res.scalars().first()
            if demo_lead:
                demo_lead.status = "failed"
        except Exception as dl_err:
            print(f"[CallService] Demo lead sync error in fail_call (non-fatal): {dl_err}")

        await db.commit()

        # Trigger WhatsApp automation rules for failed/unanswered call
        try:
            import asyncio
            from app.services.whatsapp_automation_service import WhatsAppAutomationService
            asyncio.create_task(WhatsAppAutomationService.process_call_automation(call_id))
        except Exception as wa_err:
            print(f"[CallService] Error dispatching WhatsApp automation for failed Call {call_id}: {wa_err}")

        return call