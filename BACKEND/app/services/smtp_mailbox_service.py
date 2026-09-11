"""
SMTP Mailbox Service
Manages connected client email accounts (Google Workspace / Gmail, Microsoft 365, Zoho, Custom SMTP)
and dispatches marketing campaign emails directly through client mailboxes using aiosmtplib.
"""
import asyncio
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, parseaddr
from typing import Optional, Dict, Any, List

import aiosmtplib
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_smtp_config import UserSmtpConfig
from app.core.crypto import encrypt_secret, decrypt_secret

logger = logging.getLogger("smtp_mailbox_service")

# Preset SMTP configurations for popular mail providers
PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "gmail": {
        "host": "smtp.gmail.com",
        "port": 587,
        "encryption": "tls",
        "help_title": "Google Workspace / Gmail App Password",
        "help_url": "https://myaccount.google.com/apppasswords",
    },
    "outlook": {
        "host": "smtp.office365.com",
        "port": 587,
        "encryption": "tls",
        "help_title": "Microsoft 365 / Outlook SMTP",
        "help_url": "https://support.microsoft.com",
    },
    "zoho": {
        "host": "smtp.zoho.in",
        "port": 587,
        "encryption": "tls",
        "help_title": "Zoho Mail SMTP",
        "help_url": "https://www.zoho.com/mail/help/smtp-configuration.html",
    },
    "custom": {
        "host": "",
        "port": 587,
        "encryption": "tls",
        "help_title": "Custom cPanel / Private SMTP",
        "help_url": "",
    },
}


class SmtpMailboxService:

    @staticmethod
    def get_presets() -> Dict[str, Dict[str, Any]]:
        return PROVIDER_PRESETS

    @staticmethod
    async def test_credentials(
        host: str,
        port: int,
        encryption: str,
        username: str,
        password: str,
        sender_name: str,
        sender_email: str,
        recipient_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Attempts to connect, authenticate, and deliver a test verification email via SMTP.
        Returns {'success': bool, 'message': str}.
        """
        target_to = recipient_email or sender_email
        use_tls = encryption.lower() == "ssl" or port == 465
        start_tls = encryption.lower() == "tls" or port in (587, 25)

        # Build test MIME email
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "✅ [CallingGen] Your Mailbox is Successfully Connected!"
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = target_to
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=sender_email.split("@")[-1])

        plain_text = (
            f"Hello {sender_name},\n\n"
            f"This is a test email confirming that your email account ({sender_email}) "
            f"has been successfully connected to CallingGen via SMTP.\n\n"
            f"Host: {host}:{port}\n"
            f"Authentication: Successful\n\n"
            f"You can now launch Email Marketing campaigns directly through your own mailbox.\n\n"
            f"— CallingGen Automation Team"
        )

        html_text = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin: 0; padding: 24px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1e293b;">
  <div style="max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; padding: 32px; box-shadow: 0 4px 15px rgba(0,0,0,0.05);">
    <div style="text-align: center; margin-bottom: 24px;">
      <div style="display: inline-block; background: #ecfdf5; padding: 12px; border-radius: 50%; margin-bottom: 12px;">
        <span style="font-size: 28px;">✅</span>
      </div>
      <h2 style="margin: 0; color: #0f172a; font-size: 20px; font-weight: 700;">Mailbox Successfully Connected!</h2>
      <p style="margin: 6px 0 0 0; color: #64748b; font-size: 13px;">Method 2: Custom Mailbox / SMTP Integration</p>
    </div>
    
    <div style="background: #f1f5f9; border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; margin-bottom: 20px;">
      <div><strong>Connected Email:</strong> {sender_email}</div>
      <div><strong>Sender Name:</strong> {sender_name}</div>
      <div><strong>SMTP Server:</strong> {host}:{port} ({encryption.upper()})</div>
      <div><strong>Verified At:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
    </div>

    <p style="font-size: 13.5px; color: #334155; line-height: 1.5; margin: 0 0 16px 0;">
      Your mailbox is verified and ready. When you launch email campaigns in CallingGen, emails will be delivered directly from your real account with <strong>zero spam penalties</strong> and <strong>100% white-labeling</strong>.
    </p>

    <div style="border-top: 1px solid #e2e8f0; padding-top: 16px; font-size: 11px; color: #94a3b8; text-align: center;">
      CallingGen &bull; AI Voice Calling &amp; Email Automation Platform
    </div>
  </div>
</body>
</html>"""

        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_text, "html", "utf-8"))

        try:
            client = aiosmtplib.SMTP(
                hostname=host,
                port=port,
                use_tls=use_tls,
                start_tls=start_tls,
                timeout=20,
            )
            await client.connect()
            await client.login(username.strip(), password.strip())
            await client.send_message(msg)
            await client.quit()
            return {
                "success": True,
                "message": f"Connection verified successfully! Test email sent to {target_to}",
            }
        except aiosmtplib.errors.SMTPAuthenticationError as e:
            logger.warning(f"SMTP Auth error for {username}@{host}: {e}")
            msg_err = str(e.message if hasattr(e, "message") else e)
            if "Application-specific password required" in msg_err or "535" in msg_err or "534" in msg_err:
                return {
                    "success": False,
                    "message": "Authentication failed. For Google / Gmail accounts, you must use a 16-letter App Password (Google Account -> Security -> 2-Step Verification -> App Passwords).",
                }
            return {
                "success": False,
                "message": f"Authentication failed: Invalid username or password on {host}:{port}.",
            }
        except aiosmtplib.errors.SMTPConnectError as e:
            return {
                "success": False,
                "message": f"Could not connect to SMTP server {host}:{port}. Check host address, port, and firewall.",
            }
        except aiosmtplib.errors.SMTPServerDisconnected as e:
            return {
                "success": False,
                "message": f"Server unexpectedly closed connection on {host}:{port}. Check encryption (TLS on 587 vs SSL on 465).",
            }
        except asyncio.TimeoutError:
            return {
                "success": False,
                "message": f"Connection timed out while reaching {host}:{port}. Please verify the SMTP server address.",
            }
        except Exception as e:
            logger.exception(f"Unexpected SMTP error testing {host}:{port}")
            return {
                "success": False,
                "message": f"SMTP Error: {str(e)}",
            }

    @staticmethod
    async def send_email_via_smtp(
        smtp_config: UserSmtpConfig,
        to_email: str,
        subject: str,
        html_content: str,
        from_name: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> bool:
        """
        Sends a single marketing/transactional email using a stored UserSmtpConfig.
        Decrypts password on the fly, connects via aiosmtplib, and dispatches.
        """
        decrypted_password = decrypt_secret(smtp_config.encrypted_password)
        sender_name = from_name or smtp_config.sender_name
        sender_email = smtp_config.sender_email
        host = smtp_config.smtp_host
        port = smtp_config.smtp_port
        encryption = smtp_config.smtp_encryption or "tls"

        use_tls = encryption.lower() == "ssl" or port == 465
        start_tls = encryption.lower() == "tls" or port in (587, 25)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=sender_email.split("@")[-1])
        if reply_to:
            msg["Reply-To"] = reply_to

        # Fallback plain text extracted simply
        plain_text = f"{subject}\n\n(View in rich HTML email client)\n\nSent from {sender_name}"
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        client = aiosmtplib.SMTP(
            hostname=host,
            port=port,
            use_tls=use_tls,
            start_tls=start_tls,
            timeout=25,
        )
        await client.connect()
        await client.login(smtp_config.username.strip(), decrypted_password.strip())
        await client.send_message(msg)
        await client.quit()
        return True

    @staticmethod
    async def get_user_smtp_config_by_email(
        db: AsyncSession, user_id: int, sender_email: str
    ) -> Optional[UserSmtpConfig]:
        """Looks up a user's verified and active SMTP config by sender email."""
        clean_email = (
            sender_email.split("<")[1].split(">")[0].strip().lower()
            if "<" in sender_email and ">" in sender_email
            else sender_email.strip().lower()
        )
        stmt = select(UserSmtpConfig).where(
            and_(
                UserSmtpConfig.user_id == user_id,
                UserSmtpConfig.sender_email.ilike(clean_email),
                UserSmtpConfig.is_active == True,
                UserSmtpConfig.is_verified == True,
            )
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_default_smtp_config(
        db: AsyncSession, user_id: int
    ) -> Optional[UserSmtpConfig]:
        """Gets the user's primary/default active verified SMTP config."""
        stmt = (
            select(UserSmtpConfig)
            .where(
                and_(
                    UserSmtpConfig.user_id == user_id,
                    UserSmtpConfig.is_active == True,
                    UserSmtpConfig.is_verified == True,
                )
            )
            .order_by(UserSmtpConfig.is_default.desc(), UserSmtpConfig.id.asc())
        )
        result = await db.execute(stmt)
        return result.scalars().first()


smtp_mailbox_service = SmtpMailboxService()
