import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

class EmailService:
    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USERNAME", os.getenv("SMTP_USER", ""))
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", self.smtp_user)

    def is_configured(self):
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    def _send_email(self, to_email: str, subject: str, body: str):
        if not self.is_configured():
            print("\n" + "="*50)
            print("🚨 SMTP NOT CONFIGURED IN .env 🚨")
            print(f"Would have sent email to {to_email}")
            print(f"Subject: {subject}")
            print("="*50 + "\n")
            raise Exception("SMTP is not configured on the server. Please contact support.")

        msg = MIMEMultipart()
        msg['From'] = self.smtp_from
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            # Set a 10 second timeout for the SMTP connection
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
            print(f"✅ Email sent successfully to {to_email}")
        except smtplib.SMTPException as e:
            print(f"❌ SMTP Error sending email to {to_email}: {e}")
            raise Exception(f"Failed to send email. SMTP Error.")
        except TimeoutError:
            print(f"❌ Timeout sending email to {to_email}")
            raise Exception("Failed to send email. The connection timed out.")
        except Exception as e:
            print(f"❌ Unexpected Error sending email to {to_email}: {e}")
            raise Exception("An unexpected error occurred while sending the email.")

    def send_password_reset_email(self, to_email: str, reset_code: str):
        subject = "Your Password Reset Code"
        body = f"""Hello,

You requested a password reset. Your 6-digit verification code is:

{reset_code}

This code will expire in 15 minutes.
If you did not request this, please ignore this email.
"""
        self._send_email(to_email, subject, body)

    def send_welcome_email(self, to_email: str, temp_password: str):
        subject = "Welcome to CallingGen - Your Temporary Password"
        body = f"""Hello,

Your account has been created successfully. 

Your temporary password is:
{temp_password}

Please log in with this password. You will be asked to change it immediately upon your first login.

Thank you,
The CallingGen Team
"""
        self._send_email(to_email, subject, body)

# Create a singleton instance
email_service = EmailService()
