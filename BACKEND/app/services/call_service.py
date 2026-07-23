from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.contact import Contact
from app.models.job import Job
from app.models.campaign import Campaign


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
    ):
        call = await db.get(Call, call_id)

        if call is None:
            return None

        # Prevent double completion
        if call.status == "completed":
            return call

        # ── Determine if it's a success or failure ────────────────────
        is_success = transcript is not None and len(transcript.strip()) > 0
        call.status = "completed" if is_success else "failed"
        
        if recording_url:
            call.recording_url = recording_url
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # store as naive UTC to match existing rows
        call.ended_at = now
        if call.started_at:
            call.duration = int((now - call.started_at).total_seconds())

        # Check if appointment_date is a real, valid date string
        has_valid_appointment = (
            appointment_date is not None 
            and str(appointment_date).strip().lower() not in ("", "none", "null", "n/a", "undefined", "false")
        )

        # Check transcript for refusal / do not call signals
        lower_tx = (transcript or "").lower()
        is_opt_out = any(phrase in lower_tx for phrase in [
            "do not call", "don't call", "stop calling", "remove my number",
            "not interested", "no assistance", "don't need", "no thanks",
            "refuse", "declined", "never call"
        ])

        # ── Contact ───────────────────────────────────────────────────
        contact = await db.get(Contact, call.contact_id)
        if contact:
            contact.status = "completed" if is_success else "failed"
            contact.duration = str(call.duration)
            if transcript:
                contact.transcript = transcript
            if customer_name:
                contact.customer_name = customer_name

            if is_opt_out:
                contact.response = "Do Not Call / Refusal"
            elif has_valid_appointment:
                contact.appointment_date = appointment_date
                if appointment_time:
                    contact.appointment_time = appointment_time
                contact.response = "Appointment Booked"
            else:
                contact.response = "Answered" if is_success else "No Answer / Cut"

        business_outcome = contact.response if contact else "None"

        # ── Transcript & AI ────────────────────────────────────────────
        if transcript:
            call.transcript = transcript
            
            # Generate AI Classification & Category in parallel
            try:
                import os
                import asyncio
                from openai import AsyncOpenAI
                
                deepseek_key = os.getenv("DEEPSEEK_API_KEY")
                if deepseek_key and len(transcript) > 20:
                    client = AsyncOpenAI(
                        api_key=deepseek_key,
                        base_url="https://api.deepseek.com/v1"
                    )
                    
                    # Request 1: Topic Classification
                    prompt_class = (
                        "Analyze the following call transcript and identify the main customer intent/topic.\n"
                        "If the customer refuses, asks not to be called, or says not interested, output 'Do Not Call Request'.\n"
                        "Otherwise output ONLY a 2-4 word classification (e.g., 'ITR Filing Query', 'Tax Notice Assistance', 'Tax Planning').\n"
                        "DO NOT output full sentences. Return pure text, no markdown, no quotes, no periods at the end.\n\n"
                        f"Transcript:\n{transcript}"
                    )
                    
                    # Request 2: Sales Pipeline Category
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
                    
                    # Run in parallel
                    res_class, res_cat = await asyncio.gather(task_class, task_cat)
                    
                    # Process Classification
                    raw_summary = res_class.choices[0].message.content or ""
                    clean_summary = raw_summary.strip().strip("'\".").replace("\n", " ")
                    if is_opt_out:
                        call.summary = "Do Not Call Request"
                    elif len(clean_summary.split()) > 6 or not clean_summary:
                        call.summary = "Classification Pending"
                    else:
                        call.summary = clean_summary
                        
                    # Process Category
                    if is_opt_out:
                        call.category = "COLD"
                    else:
                        raw_cat = res_cat.choices[0].message.content or ""
                        clean_cat = raw_cat.strip().strip("'\".").upper()
                        if clean_cat in ["HOT", "WARM", "COLD"]:
                            call.category = clean_cat
                        else:
                            call.category = "UNCATEGORIZED"
                        
                else:
                    call.summary = "Do Not Call Request" if is_opt_out else "General Tax Inquiry"
                    call.category = "COLD" if is_opt_out else "UNCATEGORIZED"
            except Exception as e:
                print(f"Failed to generate AI data: {e}")
                call.summary = "Do Not Call Request" if is_opt_out else "Classification Unavailable"
                call.category = "COLD" if is_opt_out else "UNCATEGORIZED"
        else:
            call.summary = "General Tax Inquiry"
            call.category = "UNCATEGORIZED"

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
                    campaign.status = "completed"

        await db.commit()

        return call

    @staticmethod
    async def fail_call(
        db: AsyncSession,
        call_id: int,
    ):
        """
        Mark a call as failed and advance the campaign to the next contact.
        Called when a SIP dial attempt fails (no answer, trunk error, etc.).
        """
        call = await db.get(Call, call_id)
        if call is None:
            return None

        if call.status in ("completed", "failed"):
            return call

        call.status = "failed"
        call.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)

        contact = await db.get(Contact, call.contact_id)
        if contact:
            contact.status = "failed"

        job = await db.get(Job, call.job_id)
        if job:
            job.failed_contacts += 1
            # Mark job & campaign complete when all contacts are processed
            if (job.completed_contacts + job.failed_contacts) >= job.total_contacts:
                job.status = "completed"
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                campaign = await db.get(Campaign, job.campaign_id)
                if campaign:
                    campaign.status = "completed"

        await db.commit()
        return call