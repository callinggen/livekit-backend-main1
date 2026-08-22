import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

from app.services import email_templates

load_dotenv()

class EmailService:
    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USERNAME", os.getenv("SMTP_USER", ""))
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", self.smtp_user or "notifications@callinggen.com")

    def is_configured(self):
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    def _send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        is_html: bool = False,
        from_override: str | None = None,
        reply_to: str | None = None,
    ):
        if not to_email:
            print("[EmailService] No recipient email specified, skipping.")
            return

        if not self.is_configured():
            print("\n" + "="*50)
            print("SMTP NOT CONFIGURED IN .env")
            print(f"To: {to_email}")
            print(f"Subject: {subject}")
            print(f"Format: {'HTML' if is_html else 'PLAIN'}")
            print("="*50 + "\n")
            # In development/test mode when SMTP isn't set, log cleanly without breaking application flow
            return

        msg = MIMEMultipart("alternative")
        msg['From'] = from_override or self.smtp_from
        msg['To'] = to_email
        msg['Subject'] = subject
        if reply_to:
            msg['Reply-To'] = reply_to

        subtype = 'html' if is_html else 'plain'
        msg.attach(MIMEText(body, subtype, 'utf-8'))

        try:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
            print(f"[EmailService] Email sent successfully to {to_email}")
        except smtplib.SMTPException as e:
            print(f"[EmailService] SMTP Error sending email to {to_email}: {e}")
            raise Exception(f"Failed to send email. SMTP Error: {e}")
        except TimeoutError:
            print(f"[EmailService] Timeout sending email to {to_email}")
            raise Exception("Failed to send email. The connection timed out.")
        except Exception as e:
            print(f"[EmailService] Unexpected Error sending email to {to_email}: {e}")
            raise Exception(f"An unexpected error occurred while sending the email: {e}")

    # --- Credit Notifications ---
    def send_low_credit_email(self, to_email: str, full_name: str, company_name: str, remaining_credits: int, plan_name: str = "Standard"):
        subject = f"Low Credit Warning: {remaining_credits} credits remaining"
        html_body = email_templates.get_low_credit_html(full_name, company_name, remaining_credits, plan_name)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_critical_credit_email(self, to_email: str, full_name: str, company_name: str, remaining_credits: int, plan_name: str = "Standard"):
        subject = f"CRITICAL: CallingGen credits almost exhausted ({remaining_credits} left)"
        html_body = email_templates.get_critical_credit_html(full_name, company_name, remaining_credits, plan_name)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_credits_exhausted_email(self, to_email: str, full_name: str, company_name: str, plan_name: str = "Standard"):
        subject = "URGENT: CallingGen credits exhausted - Calls disabled"
        html_body = email_templates.get_credits_exhausted_html(full_name, company_name, plan_name)
        self._send_email(to_email, subject, html_body, is_html=True)

    # --- Security Notifications ---
    def send_password_changed_email(self, to_email: str, full_name: str, timestamp_str: str):
        subject = "Your CallingGen Password Was Changed"
        html_body = email_templates.get_password_changed_html(full_name, to_email, timestamp_str)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_first_time_password_changed_email(self, to_email: str, full_name: str):
        subject = "CallingGen Account Setup Completed"
        html_body = email_templates.get_first_time_password_changed_html(full_name, to_email)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_password_reset_email(self, to_email: str, reset_code: str):
        subject = "CallingGen Password Reset Verification Code"
        html_body = email_templates.get_password_reset_code_html(to_email, reset_code)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_password_reset_success_email(self, to_email: str, full_name: str):
        subject = "CallingGen Password Reset Successful"
        html_body = email_templates.get_password_reset_success_html(full_name, to_email)
        self._send_email(to_email, subject, html_body, is_html=True)

    # --- Account & Admin Notifications ---
    def send_welcome_email(self, to_email: str, temp_password: str, full_name: str = "User", company_name: str | None = None, plan_name: str = "Starter", credits: int = 2000):
        subject = "Welcome to CallingGen - Account Credentials"
        html_body = email_templates.get_welcome_account_html(full_name, to_email, temp_password, company_name, plan_name, credits)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_plan_credit_updated_email(self, to_email: str, full_name: str, plan_name: str, new_credits: int):
        subject = f"CallingGen Subscription Updated: {plan_name} ({new_credits} credits)"
        html_body = email_templates.get_plan_credit_updated_html(full_name, to_email, plan_name, new_credits)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_account_activated_email(self, to_email: str, full_name: str):
        subject = "Your CallingGen Account is Active"
        html_body = email_templates.get_account_activated_html(full_name, to_email)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_account_deactivated_email(self, to_email: str, full_name: str):
        subject = "Notice: CallingGen Account Temporarily Deactivated"
        html_body = email_templates.get_account_deactivated_html(full_name, to_email)
        self._send_email(to_email, subject, html_body, is_html=True)

    def send_payment_invoice_email(
        self,
        to_email: str,
        full_name: str,
        plan_name: str,
        amount: int,
        credits: int,
        order_id: str,
        payment_id: str
    ):
        subject = f"Receipt for your purchase of {plan_name} Pack - CallingGen"
        html_body = email_templates.get_payment_invoice_html(
            full_name, to_email, plan_name, amount, credits, order_id, payment_id
        )
        self._send_email(to_email, subject, html_body, is_html=True)

# Create a singleton instance

email_service = EmailService()

