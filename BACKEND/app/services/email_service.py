import os
from dotenv import load_dotenv
import importlib
from typing import Any

from app.services import email_templates

load_dotenv()

class EmailService:
    @property
    def api_key(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_API_KEY", "").strip()

    @property
    def from_email(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_FROM_EMAIL", "CallingGen <noreply@callinggen.in>").strip()

    @property
    def template_welcome(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_WELCOME", os.getenv("RESEND_WELCOME_TEMPLATE_ID", "")).strip()

    @property
    def template_password_reset(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_PASSWORD_RESET", os.getenv("RESEND_PASSWORD_RESET_TEMPLATE_ID", "")).strip()

    @property
    def template_low_credit(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_LOW_CREDIT", "").strip()

    @property
    def template_critical_credit(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_CRITICAL_CREDIT", "").strip()

    @property
    def template_credits_exhausted(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_CREDITS_EXHAUSTED", "").strip()

    @property
    def template_invoice(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_INVOICE", "").strip()

    @property
    def template_plan_updated(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_PLAN_UPDATED", "").strip()

    @property
    def template_password_changed(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_PASSWORD_CHANGED", "").strip()

    @property
    def template_booking(self) -> str:
        load_dotenv()
        return os.getenv("RESEND_TEMPLATE_BOOKING", "").strip()

    def is_configured(self):
        k = self.api_key
        return bool(k and not k.startswith("re_your_") and k != "re_your_api_key_here")

    def _send_email(
        self,
        to_email: str,
        subject: str = "",
        body: str = "",
        is_html: bool = False,
        from_override: str | None = None,
        reply_to: str | None = None,
        template_id: str | None = None,
        template_variables: dict | None = None,
    ):
        if not to_email:
            print("[EmailService] No recipient email specified, skipping.")
            return

        if not self.is_configured():
            err_msg = "Resend API Key is not configured in server environment (RESEND_API_KEY in .env). Please set a valid Resend API key."
            print(f"\n[EmailService] ERROR: {err_msg}\n")
            raise RuntimeError(err_msg)

        sender = from_override or self.from_email

        params: dict = {
            "from": sender,
            "to": [to_email] if isinstance(to_email, str) else to_email,
        }

        if subject:
            params["subject"] = subject

        if template_id:
            params["template"] = {
                "id": template_id,
                "variables": template_variables or {},
            }
        elif is_html:
            params["html"] = body
        else:
            params["text"] = body

        if reply_to:
            params["reply_to"] = reply_to

        try:
            resend_mod: Any = importlib.import_module("resend")
            setattr(resend_mod, "api_key", self.api_key)
            response = resend_mod.Emails.send(params)
            print(f"[EmailService] Email sent successfully via Resend to {to_email}. Response: {response}")
            return response
        except Exception as e:
            print(f"[EmailService] Error sending email via Resend to {to_email}: {e}")
            raise Exception(f"Failed to send email via Resend: {e}")


    # --- Credit Notifications ---
    def send_low_credit_email(self, to_email: str, full_name: str, company_name: str, remaining_credits: int, plan_name: str = "Standard"):
        subject = f"Low Credit Warning: {remaining_credits} credits remaining"
        variables = {
            "user_name": full_name or "Client",
            "name": full_name or "Client",
            "company_name": company_name or "Your Account",
            "remaining_credits": remaining_credits,
            "credits": remaining_credits,
            "plan_name": plan_name or "Standard",
            "topup_url": "https://callinggen.in/dashboard",
        }
        if self.template_low_credit:
            return self._send_email(to_email=to_email, subject=subject, template_id=self.template_low_credit, template_variables=variables)
        else:
            html_body = email_templates.get_low_credit_html(full_name, company_name, remaining_credits, plan_name)
            return self._send_email(to_email, subject, html_body, is_html=True)

    def send_critical_credit_email(self, to_email: str, full_name: str, company_name: str, remaining_credits: int, plan_name: str = "Standard"):
        subject = f"CRITICAL: CallingGen credits almost exhausted ({remaining_credits} left)"
        variables = {
            "user_name": full_name or "Client",
            "name": full_name or "Client",
            "company_name": company_name or "Your Account",
            "remaining_credits": remaining_credits,
            "credits": remaining_credits,
            "plan_name": plan_name or "Standard",
            "topup_url": "https://callinggen.in/dashboard",
        }
        if self.template_critical_credit:
            return self._send_email(to_email=to_email, subject=subject, template_id=self.template_critical_credit, template_variables=variables)
        else:
            html_body = email_templates.get_critical_credit_html(full_name, company_name, remaining_credits, plan_name)
            return self._send_email(to_email, subject, html_body, is_html=True)

    def send_credits_exhausted_email(self, to_email: str, full_name: str, company_name: str, plan_name: str = "Standard"):
        subject = "URGENT: CallingGen credits exhausted - Calls disabled"
        variables = {
            "user_name": full_name or "Client",
            "name": full_name or "Client",
            "company_name": company_name or "Your Account",
            "plan_name": plan_name or "Standard",
            "topup_url": "https://callinggen.in/dashboard",
        }
        if self.template_credits_exhausted:
            return self._send_email(to_email=to_email, subject=subject, template_id=self.template_credits_exhausted, template_variables=variables)
        else:
            html_body = email_templates.get_credits_exhausted_html(full_name, company_name, plan_name)
            return self._send_email(to_email, subject, html_body, is_html=True)

    # --- Security Notifications ---
    def send_password_changed_email(self, to_email: str, full_name: str, timestamp_str: str):
        subject = "Your CallingGen Password Was Changed"
        variables = {
            "user_name": full_name or "User",
            "name": full_name or "User",
            "email": to_email,
            "timestamp": timestamp_str,
            "login_url": "https://callinggen.in/login",
        }
        if self.template_password_changed:
            return self._send_email(to_email=to_email, subject=subject, template_id=self.template_password_changed, template_variables=variables)
        else:
            html_body = email_templates.get_password_changed_html(full_name, to_email, timestamp_str)
            return self._send_email(to_email, subject, html_body, is_html=True)

    def send_first_time_password_changed_email(self, to_email: str, full_name: str):
        subject = "CallingGen Account Setup Completed"
        html_body = email_templates.get_first_time_password_changed_html(full_name, to_email)
        return self._send_email(to_email, subject, html_body, is_html=True)

    def send_password_reset_email(self, to_email: str, reset_code: str):
        subject = "CallingGen Password Reset Verification Code"
        variables = {
            "email": to_email,
            "reset_code": reset_code,
            "code": reset_code,
            "login_url": "https://callinggen.in/login",
            "change_password_url": "https://callinggen.in/change-password",
            "reset_url": "https://callinggen.in/login",
        }
        if self.template_password_reset:
            try:
                return self._send_email(
                    to_email=to_email,
                    subject=subject,
                    template_id=self.template_password_reset,
                    template_variables=variables,
                )
            except Exception as e:
                print(f"[EmailService] Dashboard template '{self.template_password_reset}' failed ({e}). Falling back to internal HTML.")
                html_body = email_templates.get_password_reset_code_html(to_email, reset_code)
                return self._send_email(to_email, subject, html_body, is_html=True)
        else:
            html_body = email_templates.get_password_reset_code_html(to_email, reset_code)
            return self._send_email(to_email, subject, html_body, is_html=True)

    def send_password_reset_success_email(self, to_email: str, full_name: str):
        subject = "CallingGen Password Reset Successful"
        html_body = email_templates.get_password_reset_success_html(full_name, to_email)
        return self._send_email(to_email, subject, html_body, is_html=True)

    # --- Account & Admin Notifications ---
    def send_welcome_email(
        self,
        to_email: str,
        temp_password: str,
        full_name: str = "User",
        company_name: str | None = None,
        plan_name: str = "Starter",
        credits: int = 2000
    ):
        subject = "Welcome to CallingGen - Account Credentials"
        variables = {
            "user_name": full_name or "User",
            "name": full_name or "User",
            "email": to_email,
            "password": temp_password,
            "temp_password": temp_password,
            "dashboard_url": "https://callinggen.in/dashboard",
            "login_url": "https://callinggen.in/login",
            "company_name": company_name or "CallingGen",
            "plan_name": plan_name,
            "credits": credits,
        }
        if self.template_welcome:
            try:
                return self._send_email(
                    to_email=to_email,
                    subject=subject,
                    template_id=self.template_welcome,
                    template_variables=variables,
                )
            except Exception as e:
                print(f"[EmailService] Dashboard template '{self.template_welcome}' failed ({e}). Falling back to internal HTML.")
                html_body = email_templates.get_welcome_account_html(full_name, to_email, temp_password, company_name, plan_name, credits)
                return self._send_email(to_email, subject, html_body, is_html=True)
        else:
            html_body = email_templates.get_welcome_account_html(full_name, to_email, temp_password, company_name, plan_name, credits)
            return self._send_email(to_email, subject, html_body, is_html=True)

    def send_plan_credit_updated_email(self, to_email: str, full_name: str, plan_name: str, new_credits: int):
        subject = f"CallingGen Subscription Updated: {plan_name} ({new_credits} credits)"
        variables = {
            "user_name": full_name or "User",
            "name": full_name or "User",
            "email": to_email,
            "plan_name": plan_name,
            "new_credits": new_credits,
            "credits": new_credits,
            "dashboard_url": "https://callinggen.in/dashboard",
        }
        if self.template_plan_updated:
            return self._send_email(to_email=to_email, subject=subject, template_id=self.template_plan_updated, template_variables=variables)
        else:
            html_body = email_templates.get_plan_credit_updated_html(full_name, to_email, plan_name, new_credits)
            return self._send_email(to_email, subject, html_body, is_html=True)

    def send_account_activated_email(self, to_email: str, full_name: str):
        subject = "Your CallingGen Account is Active"
        html_body = email_templates.get_account_activated_html(full_name, to_email)
        return self._send_email(to_email, subject, html_body, is_html=True)

    def send_account_deactivated_email(self, to_email: str, full_name: str):
        subject = "Notice: CallingGen Account Temporarily Deactivated"
        html_body = email_templates.get_account_deactivated_html(full_name, to_email)
        return self._send_email(to_email, subject, html_body, is_html=True)

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
        variables = {
            "user_name": full_name or "Client",
            "name": full_name or "Client",
            "email": to_email,
            "plan_name": plan_name,
            "amount": amount,
            "credits": credits,
            "order_id": order_id,
            "payment_id": payment_id,
            "dashboard_url": "https://callinggen.in/dashboard",
        }
        if self.template_invoice:
            return self._send_email(to_email=to_email, subject=subject, template_id=self.template_invoice, template_variables=variables)
        else:
            html_body = email_templates.get_payment_invoice_html(
                full_name, to_email, plan_name, amount, credits, order_id, payment_id
            )
            return self._send_email(to_email, subject, html_body, is_html=True)

    # --- Email Marketing / Campaigns ---
    def send_marketing_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        from_name: str | None = None,
        from_email: str | None = None,
        reply_to: str | None = None,
    ):
        """
        Sends an email marketing campaign message through Resend, supporting
        verified custom sending domains and custom display names.
        """
        if from_email and from_email.strip():
            clean_email = (
                from_email.split("<")[1].split(">")[0].strip()
                if "<" in from_email and ">" in from_email
                else from_email.strip()
            )
            from_header = f"{from_name} <{clean_email}>" if from_name else clean_email
        else:
            raw_from = self.from_email
            clean_email = (
                raw_from.split("<")[1].split(">")[0].strip()
                if "<" in raw_from and ">" in raw_from
                else raw_from.strip()
            )
            from_header = (
                f"{from_name} <{clean_email}>"
                if from_name
                else raw_from
            )
        return self._send_email(
            to_email=to_email,
            subject=subject,
            body=html_content,
            is_html=True,
            from_override=from_header,
            reply_to=reply_to,
        )

# Create a singleton instance
email_service = EmailService()


