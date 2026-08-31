import html

def _wrap_email_layout(title: str, body_html: str, button_text: str | None = None, button_url: str | None = None, subtitle: str = "AI Voice Calling & Automation Platform") -> str:
    """Master HTML wrapper for CallingGen branded email templates (Modern Clean SaaS Theme)."""
    
    button_html = ""
    if button_text and button_url:
        button_html = f"""
        <div style="margin-top: 28px; margin-bottom: 24px; text-align: center;">
            <a href="{html.escape(button_url)}" target="_blank" style="background: linear-gradient(135deg, #2563eb 0%, #4f46e5 100%); color: #ffffff; padding: 13px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; display: inline-block; box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25); letter-spacing: 0.2px;">
                {html.escape(button_text)}
            </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
        body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
        table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
        img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
        @media screen and (max-width: 620px) {{
            .container {{ width: 100% !important; border-radius: 0 !important; }}
            .content-padding {{ padding: 28px 20px !important; }}
        }}
    </style>
</head>
<body style="margin: 0; padding: 36px 10px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; color: #334155; -webkit-font-smoothing: antialiased;">
    <table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f8fafc;">
        <tr>
            <td align="center">
                <table role="presentation" class="container" width="560" border="0" cellspacing="0" cellpadding="0" style="max-width: 560px; width: 100%; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.06);">
                    <!-- Header -->
                    <tr>
                        <td style="background-color: #ffffff; padding: 32px 32px 20px 32px; text-align: center; border-bottom: 2px solid #2563eb;">
                            <div style="font-size: 26px; font-weight: 800; letter-spacing: -0.5px; color: #0f172a;">
                                Calling<span style="color: #2563eb;">Gen</span>
                            </div>
                            <div style="font-size: 11.5px; color: #64748b; margin-top: 4px; letter-spacing: 1px; text-transform: uppercase; font-weight: 600;">
                                {html.escape(subtitle)}
                            </div>
                        </td>
                    </tr>
                    
                    <!-- Content Area -->
                    <tr>
                        <td class="content-padding" style="padding: 32px 32px 24px 32px;">
                            <h1 style="color: #0f172a; font-size: 20px; font-weight: 700; margin: 0 0 16px 0; line-height: 1.3; letter-spacing: -0.3px;">{html.escape(title)}</h1>
                            {body_html}
                            {button_html}
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; padding: 20px 32px; border-top: 1px solid #f1f5f9; text-align: center;">
                            <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748b;">
                                &copy; CallingGen Inc. All rights reserved.
                            </p>
                            <p style="margin: 0; font-size: 11px; color: #94a3b8;">
                                Automated System Notification &bull; Support: support@callinggen.in
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

def get_low_credit_html(full_name: str, company_name: str, remaining_credits: int, plan_name: str = "Standard") -> str:
    name_str = html.escape(full_name or "Client")
    company_str = html.escape(company_name or "Your Account")
    plan_str = html.escape(plan_name or "Active Plan")
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>This is an automated notification regarding your CallingGen account for <strong>{company_str}</strong>.</p>
    
    <div style="background-color: #f8fafc; border-left: 4px solid #2563eb; padding: 18px; border-radius: 6px; margin: 20px 0; border: 1px solid #e2e8f0;">
        <div style="font-size: 13px; color: #1e40af; font-weight: bold; text-transform: uppercase;">Low Credit Warning</div>
        <div style="font-size: 28px; font-weight: bold; color: #0f172a; margin-top: 4px;">{remaining_credits} <span style="font-size: 16px; font-weight: normal; color: #64748b;">credits remaining</span></div>
        <div style="font-size: 13px; color: #475569; margin-top: 6px;">Plan: {plan_str}</div>
    </div>

    <p>Your credits are running low. To ensure your automated voice calling campaigns run smoothly without interruption, please top up your credit balance soon.</p>
    """
    return _wrap_email_layout("Low Credit Alert", body, button_text="Top Up Credits", button_url="https://callinggen.in/dashboard")

def get_critical_credit_html(full_name: str, company_name: str, remaining_credits: int, plan_name: str = "Standard") -> str:
    name_str = html.escape(full_name or "Client")
    company_str = html.escape(company_name or "Your Account")
    plan_str = html.escape(plan_name or "Active Plan")
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p><strong>Urgent Notice:</strong> Your available CallingGen credits are almost exhausted for <strong>{company_str}</strong>.</p>
    
    <div style="background-color: #fff7ed; border-left: 4px solid #f97316; padding: 18px; border-radius: 6px; margin: 20px 0; border: 1px solid #fed7aa;">
        <div style="font-size: 13px; color: #c2410c; font-weight: bold; text-transform: uppercase;">Critical Credit Alert</div>
        <div style="font-size: 28px; font-weight: bold; color: #0f172a; margin-top: 4px;">{remaining_credits} <span style="font-size: 16px; font-weight: normal; color: #9a3412;">credits remaining</span></div>
        <div style="font-size: 13px; color: #7c2d12; margin-top: 6px;">Plan: {plan_str}</div>
    </div>

    <p style="color: #c2410c;">Please add credits immediately to avoid unexpected call disruptions or paused campaigns.</p>
    """
    return _wrap_email_layout("Critical Credit Warning", body, button_text="Recharge Balance Now", button_url="https://callinggen.in/dashboard")

def get_credits_exhausted_html(full_name: str, company_name: str, plan_name: str = "Standard") -> str:
    name_str = html.escape(full_name or "Client")
    company_str = html.escape(company_name or "Your Account")
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your CallingGen credit balance for <strong>{company_str}</strong> has reached <strong>0 credits</strong>.</p>
    
    <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 18px; border-radius: 6px; margin: 20px 0; border: 1px solid #fecaca;">
        <div style="font-size: 13px; color: #991b1b; font-weight: bold; text-transform: uppercase;">Status: Calls Disabled</div>
        <div style="font-size: 22px; font-weight: bold; color: #7f1d1d; margin-top: 4px;">0 Credits Available</div>
        <div style="font-size: 13px; color: #b91c1c; margin-top: 6px;">New calls & scheduled campaigns are paused until recharged.</div>
    </div>

    <p>Please top up your CallingGen account credits to resume outbound AI calling functionality.</p>
    """
    return _wrap_email_layout("CallingGen Credits Exhausted", body, button_text="Top Up Credits Immediately", button_url="https://callinggen.in/dashboard")

def get_password_changed_html(full_name: str, email: str, timestamp_str: str) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    time_str = html.escape(timestamp_str)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your password for CallingGen account <strong>{email_str}</strong> was successfully changed on <strong>{time_str}</strong>.</p>
    
    <div style="background-color: #f8fafc; padding: 14px 18px; border-radius: 6px; margin: 20px 0; border: 1px solid #e2e8f0;">
        <span style="color: #64748b; font-size: 13px; font-weight: bold;">Security Notice:</span>
        <p style="margin: 4px 0 0 0; color: #334155; font-size: 14px;">If you did not initiate this change, please contact support immediately to secure your account.</p>
    </div>
    """
    return _wrap_email_layout("Password Changed Successfully", body, subtitle="Security & Authentication")

def get_first_time_password_changed_html(full_name: str, email: str) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Congratulations! You have successfully updated your temporary password and completed account setup for <strong>{email_str}</strong>.</p>
    
    <div style="background-color: #f0fdf4; border-left: 4px solid #10b981; padding: 16px; border-radius: 6px; margin: 20px 0; border: 1px solid #bbf7d0;">
        <div style="font-size: 14px; color: #166534; font-weight: bold;">Account Setup Completed</div>
        <p style="margin: 4px 0 0 0; color: #15803d; font-size: 14px;">Your CallingGen client portal account is now fully active and ready for use.</p>
    </div>
    """
    return _wrap_email_layout("Account Setup Completed", body, button_text="Go to Dashboard", button_url="https://callinggen.in/dashboard")

def get_password_reset_code_html(email: str, reset_code: str) -> str:
    email_str = html.escape(email)
    code_str = html.escape(reset_code)
    
    body = f"""
    <p style="margin: 0 0 20px 0; color: #334155; font-size: 16px; line-height: 1.7;">
        We received a request to reset the password for your CallingGen account associated with <strong>{email_str}</strong>.
    </p>

    <!-- Verification Code Box -->
    <div style="background-color: #f8fafc; border: 1px dashed #2563eb; border-radius: 8px; padding: 24px; margin: 26px 0; text-align: center;">
        <div style="font-size: 12px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 8px;">
            Your 6-Digit Verification Code
        </div>
        <div style="font-size: 34px; font-weight: bold; letter-spacing: 8px; color: #1d4ed8; font-family: monospace;">
            {code_str}
        </div>
        <div style="font-size: 13px; color: #ef4444; margin-top: 10px; font-weight: bold;">
            &#9200; Code expires in 15 minutes
        </div>
    </div>

    <!-- Security Warning -->
    <div style="background-color: #fef2f2; border: 1px solid #fecaca; padding: 14px 18px; border-radius: 6px; margin-top: 24px;">
        <p style="margin: 0; font-size: 14px; line-height: 1.6; color: #991b1b;">
            <strong>Didn't request this?</strong> If you did not initiate this request, you can safely ignore this email. Your password will remain unchanged.
        </p>
    </div>

    <p style="margin: 24px 0 0 0; font-size: 15px; color: #334155;">
        Best regards,<br>
        <strong style="color: #0f172a;">The CallingGen Security Team</strong>
    </p>
    """
    return _wrap_email_layout(
        title="Password Reset Request",
        body_html=body,
        button_text="Change Password Now →",
        button_url="https://callinggen.in/change-password",
        subtitle="Security & Authentication",
    )

def get_password_reset_success_html(full_name: str, email: str) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your password for <strong>{email_str}</strong> has been successfully reset.</p>
    <p>You can now log in to CallingGen using your new password.</p>
    """
    return _wrap_email_layout("Password Reset Confirmation", body, button_text="Log In to Account", button_url="https://callinggen.in/login")

def get_welcome_account_html(full_name: str, email: str, temp_password: str, company_name: str | None = None, plan_name: str = "Starter", credits: int = 2000) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    pass_str = html.escape(temp_password)
    comp_str = html.escape(company_name or "CallingGen")
    plan_str = html.escape(plan_name or "Starter")
    
    body = f"""
    <p style="margin: 0 0 16px 0; color: #334155; font-size: 15px; line-height: 1.6;">
        Welcome to CallingGen! Your client account has been created under <strong>{comp_str}</strong>. You now have full access to configure AI voice agents, schedule calls, and monitor campaigns.
    </p>

    <!-- Modern Credentials Card -->
    <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-left: 3.5px solid #2563eb; border-radius: 10px; padding: 20px 22px; margin: 24px 0;">
        <div style="font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; color: #1e40af; margin-bottom: 12px;">
            &#128273; Account Details &amp; Credentials
        </div>
        
        <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
                <td style="padding-bottom: 8px; font-size: 13.5px; color: #64748b; width: 110px; font-weight: 600;">Email:</td>
                <td style="padding-bottom: 8px; font-size: 13.5px; color: #0f172a; font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;">{email_str}</td>
            </tr>
            <tr>
                <td style="padding-bottom: 8px; font-size: 13.5px; color: #64748b; font-weight: 600;">Plan:</td>
                <td style="padding-bottom: 8px; font-size: 13.5px; color: #6366f1; font-weight: 700;">{plan_str}</td>
            </tr>
            <tr>
                <td style="padding-bottom: 8px; font-size: 13.5px; color: #64748b; font-weight: 600;">Allocated Credits:</td>
                <td style="padding-bottom: 8px; font-size: 13.5px; color: #059669; font-weight: 700;">{credits} Credits</td>
            </tr>
            <tr>
                <td style="padding-top: 4px; font-size: 13.5px; color: #64748b; font-weight: 600;">Temp Password:</td>
                <td style="padding-top: 4px; font-size: 13.5px; color: #0f172a;">
                    <span style="background-color: #e2e8f0; border: 1px solid #cbd5e1; color: #0f172a; padding: 3px 9px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-weight: 700; font-size: 13px;">
                        {pass_str}
                    </span>
                </td>
            </tr>
        </table>
    </div>

    <!-- Security Tip -->
    <div style="background-color: #eff6ff; border: 1px solid #bfdbfe; padding: 12px 16px; border-radius: 8px; margin-top: 20px;">
        <p style="margin: 0; font-size: 12.5px; line-height: 1.5; color: #1e40af;">
            <strong>Security Tip:</strong> You will be prompted to update your temporary password to a permanent one upon first login.
        </p>
    </div>

    <p style="margin: 24px 0 0 0; font-size: 14px; color: #475569;">
        Best regards,<br>
        <strong style="color: #0f172a;">The CallingGen Team</strong>
    </p>
    """
    return _wrap_email_layout(
        title=f"Welcome to CallingGen, {name_str}! 🎉",
        body_html=body,
        button_text="Log In to CallingGen →",
        button_url="https://callinggen.in/login",
    )

def get_plan_credit_updated_html(full_name: str, email: str, plan_name: str, new_credits: int) -> str:
    name_str = html.escape(full_name or "Client")
    email_str = html.escape(email)
    plan_str = html.escape(plan_name or "Standard")
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your CallingGen account subscription plan and credit allocation have been updated by your account administrator.</p>
    
    <div style="background-color: #1e1b4b; border-left: 4px solid #8b5cf6; padding: 18px; border-radius: 8px; margin: 20px 0;">
        <div style="font-size: 12px; color: #c4b5fd; font-weight: 600; text-transform: uppercase;">Updated Subscription & Balance</div>
        <div style="margin-top: 10px; font-size: 15px; color: #ffffff;"><strong>Active Plan:</strong> <span style="color: #ddd6fe; font-weight: 700;">{plan_str}</span></div>
        <div style="margin-top: 6px; font-size: 22px; font-weight: 800; color: #34d399;">{new_credits} <span style="font-size: 14px; font-weight: 500; color: #a7f3d0;">Total Available Credits</span></div>
    </div>

    <p>You can now use your updated credit balance to launch AI voice calling campaigns.</p>
    """
    return _wrap_email_layout("CallingGen Subscription & Credits Updated", body, button_text="View Account Dashboard", button_url="#/dashboard")

def get_account_activated_html(full_name: str, email: str) -> str:
    name_str = html.escape(full_name or "Client")
    email_str = html.escape(email)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Great news! Your CallingGen account (<strong>{email_str}</strong>) is now <strong>active</strong>.</p>
    <p>You can now access your workspace, set up AI voice agents, and launch calling campaigns.</p>
    """
    return _wrap_email_layout("CallingGen Account Activated", body, button_text="Open Workspace", button_url="#/dashboard")

def get_account_deactivated_html(full_name: str, email: str) -> str:
    name_str = html.escape(full_name or "Client")
    email_str = html.escape(email)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your CallingGen account (<strong>{email_str}</strong>) has been temporarily <strong>deactivated</strong> by an administrator.</p>
    
    <div style="background-color: #451a03; border-left: 4px solid #f59e0b; padding: 16px; border-radius: 6px; margin: 20px 0;">
        <div style="font-size: 14px; color: #fde68a; font-weight: 600;">Account Suspended</div>
        <p style="margin: 4px 0 0 0; color: #fef3c7; font-size: 13px;">Active campaigns and access to the client dashboard have been paused.</p>
    </div>

    <p>If you believe this is an error or need assistance reactivating your account, please contact support or your account manager.</p>
    """
    return _wrap_email_layout("Account Deactivated Notice", body)


def get_payment_invoice_html(
    full_name: str,
    email: str,
    plan_name: str,
    amount: int,
    credits: int,
    order_id: str,
    payment_id: str
) -> str:
    name_str = html.escape(full_name or "Client")
    email_str = html.escape(email)
    plan_str = html.escape(plan_name)
    order_str = html.escape(order_id)
    payment_str = html.escape(payment_id)
    price_formatted = f"INR {amount / 100:.2f}"
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Thank you for your purchase! We have successfully received your payment for the <strong>{plan_str} Pack</strong>. Your credits have been topped up in your workspace immediately.</p>
    
    <div style="background-color: #1e1b4b; border-left: 4px solid #4f46e5; padding: 18px; border-radius: 8px; margin: 20px 0; color: #ffffff;">
      <div style="font-size: 12px; color: #c4b5fd; font-weight: 600; text-transform: uppercase; margin-bottom: 10px;">Payment Receipt & Invoice</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px; color: #e2e8f0;">
        <tr>
          <td style="padding: 6px 0; color: #94a3b8; font-weight: 500; text-align: left;">Plan Details:</td>
          <td style="padding: 6px 0; text-align: right; font-weight: bold; color: #ffffff;">{plan_str} Pack</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #94a3b8; font-weight: 500; text-align: left;">Credits Added:</td>
          <td style="padding: 6px 0; text-align: right; font-weight: bold; color: #34d399;">+{credits:,} Credits</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #94a3b8; font-weight: 500; text-align: left;">Total Paid:</td>
          <td style="padding: 6px 0; text-align: right; font-weight: bold; color: #ffffff;">{price_formatted}</td>
        </tr>
        <tr style="border-top: 1px solid #334155;">
          <td style="padding: 8px 0 0 0; color: #94a3b8; font-weight: 500; text-align: left;">Razorpay Order ID:</td>
          <td style="padding: 8px 0 0 0; text-align: right; font-family: monospace; font-size: 12px; color: #cbd5e1;">{order_str}</td>
        </tr>
        <tr>
          <td style="padding: 4px 0; color: #94a3b8; font-weight: 500; text-align: left;">Razorpay Payment ID:</td>
          <td style="padding: 4px 0; text-align: right; font-family: monospace; font-size: 12px; color: #cbd5e1;">{payment_str}</td>
        </tr>
      </table>
    </div>
    
    <p>You can view your updated credit details and campaign limits directly inside your client workspace dashboard.</p>
    """
    return _wrap_email_layout("Payment Invoice Receipt - CallingGen", body, button_text="Open Dashboard", button_url="#/dashboard")


