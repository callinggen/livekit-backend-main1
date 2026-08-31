import os
import sys

# Ensure backend root is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.email_service import EmailService

def main():
    print("\n" + "="*60)
    print("      TESTING RESEND EMAIL SERVICE INTEGRATION")
    print("="*60 + "\n")

    svc = EmailService()
    print(f"Resend Configured: {svc.is_configured()}")
    print(f"From Address:     {svc.from_email}")
    print(f"API Key Present:  {bool(svc.api_key)}")

    test_recipient = os.getenv("TEST_RECIPIENT_EMAIL", "delivered@resend.dev")

    print("\n1. Testing Low Credit Email...")
    svc.send_low_credit_email(
        to_email=test_recipient,
        full_name="Alex Mercer",
        company_name="Acme Corp",
        remaining_credits=150,
        plan_name="Standard",
    )

    print("\n2. Testing Critical Credit Email...")
    svc.send_critical_credit_email(
        to_email=test_recipient,
        full_name="Alex Mercer",
        company_name="Acme Corp",
        remaining_credits=18,
        plan_name="Standard",
    )

    print("\n3. Testing Credits Exhausted Email...")
    svc.send_credits_exhausted_email(
        to_email=test_recipient,
        full_name="Alex Mercer",
        company_name="Acme Corp",
        plan_name="Standard",
    )

    print("\n4. Testing Welcome Account Email...")
    svc.send_welcome_email(
        to_email=test_recipient,
        temp_password="TempPassword!234",
        full_name="Alex Mercer",
        company_name="Acme Corp",
        plan_name="Standard",
        credits=2000,
    )

    print("\n5. Testing Password Reset Verification Code Email...")
    svc.send_password_reset_email(
        to_email=test_recipient,
        reset_code="849201",
    )

    print("\n6. Testing Purchase Invoice Email...")
    svc.send_payment_invoice_email(
        to_email=test_recipient,
        full_name="Alex Mercer",
        plan_name="Standard",
        amount=1999,
        credits=2000,
        order_id="order_demo_12345",
        payment_id="pay_demo_67890",
    )

    print("\n7. Testing Email Marketing Campaign Message...")
    svc.send_marketing_email(
        to_email=test_recipient,
        subject="Special AI Announcement for Alex Mercer",
        html_content="<p>Hi Alex Mercer, check out our new CallingGen AI voice agents!</p>",
        from_name="CallingGen Team",
    )

    print("\n" + "="*60)
    print("  ALL EMAIL SERVICE HOOKS EXECUTED SUCCESSFULLY")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
