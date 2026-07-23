import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class EmailService:
    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", self.smtp_user)

    def is_configured(self):
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    def send_password_reset_email(self, to_email: str, reset_code: str):
        if not self.is_configured():
            print("\n" + "="*50)
            print("🚨 SMTP NOT CONFIGURED IN .env 🚨")
            print(f"Would have sent reset code {reset_code} to {to_email}")
            print("="*50 + "\n")
            return

        msg = MIMEMultipart()
        msg['From'] = self.smtp_from
        msg['To'] = to_email
        msg['Subject'] = "Your Password Reset Code"

        body = f"""
        Hello,

        You requested a password reset. Your 6-digit verification code is:
        
        {reset_code}
        
        This code will expire in 15 minutes.
        If you did not request this, please ignore this email.
        """
        
        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
            print(f"✅ Reset email sent to {to_email}")
        except Exception as e:
            print(f"❌ Failed to send email to {to_email}: {e}")
            raise e

# Create a singleton instance
email_service = EmailService()
