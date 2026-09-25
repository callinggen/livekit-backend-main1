import asyncio
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.call import Call
from app.models.contact import Contact
from app.services.call_service import classify_call_end, _get_credit_owner_for_call
import math

async def dry_run():
    async with AsyncSessionLocal() as db:
        call = await db.get(Call, 26)
        if not call:
            print("Call 26 not found!")
            return
            
        print("--- Call 26 Dry Run simulation ---")
        print(f"Original sip_was_active: {call.sip_was_active}")
        print(f"Original answered_at: {call.answered_at}")
        print(f"Original duration: {call.duration}")
        
        # Simulate webhook call_complete (with no transcript/outcome)
        print("\n=== Simulating Webhook complete_call ===")
        transcript = None
        is_voicemail = False
        duration = None
        outcome = None
        failure_reason = None
        
        # Calculate duration
        calculated_duration = 0
        if duration is not None:
            calculated_duration = duration
        elif call.sip_was_active or call.answered_at:
            ans_time = call.answered_at or call.started_at
            if ans_time:
                calculated_duration = max(0, int((call.ended_at - ans_time).total_seconds()))
        print(f"Calculated Duration: {calculated_duration} seconds")
        
        outcome_override = outcome
        # Fallback voicemail
        print(f"outcome_override: {outcome_override}")
        
        final_status, final_outcome, final_failure = classify_call_end(
            sip_was_active=call.sip_was_active,
            disconnect_reason=None,
            outcome_override=outcome_override,
            failure_reason=failure_reason
        )
        print(f"Classified status: {final_status}")
        print(f"Classified outcome: {final_outcome}")
        
        is_success = final_status == "completed"
        print(f"is_success: {is_success}")
        
        # Billing calculation
        # Let's assume billing_status was 'pending'
        simulated_billing_status = "pending"
        if simulated_billing_status == "pending":
            if is_success and not is_voicemail:
                credits_to_deduct = math.floor(max(0, calculated_duration) / 4)
                print(f"Credits to deduct: {credits_to_deduct}")
                owner = await _get_credit_owner_for_call(db, call)
                print(f"Owner resolved: {owner.email if owner else 'None'}")
                simulated_billing_status = "billed"
            else:
                print("Skipped billing (not billable)")
                simulated_billing_status = "not_billable"
        print(f"Final simulated billing status: {simulated_billing_status}")

asyncio.run(dry_run())
