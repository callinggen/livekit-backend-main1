import html

def _wrap_email_layout(title: str, body_html: str, button_text: str | None = None, button_url: str | None = None) -> str:
    """Master HTML wrapper for CallingGen branded email templates."""
    
    button_html = ""
    if button_text and button_url:
        button_html = f"""
        <div style="margin-top: 24px; margin-bottom: 24px; text-align: center;">
            <a href="{html.escape(button_url)}" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; display: inline-block; box-shadow: 0 4px 6px -1px rgba(37, 99, 235, 0.2);">
                {html.escape(button_text)}
            </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; margin: 0; padding: 20px; color: #e2e8f0;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #111827; border: 1px solid #1f2937; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);">
        <!-- Header -->
        <div style="background-color: #0f172a; padding: 24px 32px; border-bottom: 1px solid #1e293b; text-align: center;">
            <div style="font-size: 24px; font-weight: 800; letter-spacing: -0.5px; color: #ffffff;">
                Calling<span style="color: #3b82f6;">Gen</span>
            </div>
            <div style="font-size: 12px; color: #94a3b8; margin-top: 4px; font-weight: 500; text-transform: uppercase; letter-spacing: 1px;">AI Voice Calling & Automation</div>
        </div>
        
        <!-- Content Area -->
        <div style="padding: 32px 32px 24px 32px; color: #cbd5e1; font-size: 15px; line-height: 1.6;">
            <h2 style="color: #f8fafc; font-size: 20px; font-weight: 700; margin-top: 0; margin-bottom: 16px;">{html.escape(title)}</h2>
            {body_html}
            {button_html}
        </div>
        
        <!-- Footer -->
        <div style="background-color: #0f172a; padding: 20px 32px; border-top: 1px solid #1e293b; text-align: center; font-size: 12px; color: #64748b;">
            <p style="margin: 0 0 8px 0;">If you have any questions or need support, contact your CallingGen administrator.</p>
            <p style="margin: 0;">&copy; CallingGen Inc. All rights reserved.</p>
        </div>
    </div>
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
    
    <div style="background-color: #1e1b4b; border-left: 4px solid #6366f1; padding: 16px; border-radius: 6px; margin: 20px 0;">
        <div style="font-size: 13px; color: #a5b4fc; font-weight: 600; text-transform: uppercase;">Low Credit Warning</div>
        <div style="font-size: 28px; font-weight: 800; color: #ffffff; margin-top: 4px;">{remaining_credits} <span style="font-size: 16px; font-weight: 400; color: #c7d2fe;">credits remaining</span></div>
        <div style="font-size: 13px; color: #93c5fd; margin-top: 6px;">Plan: {plan_str}</div>
    </div>

    <p>Your credits are running low. To ensure your automated voice calling campaigns run smoothly without interruption, please top up your credit balance soon.</p>
    """
    return _wrap_email_layout("Low Credit Alert", body, button_text="Top Up Credits", button_url="#/topup")

def get_critical_credit_html(full_name: str, company_name: str, remaining_credits: int, plan_name: str = "Standard") -> str:
    name_str = html.escape(full_name or "Client")
    company_str = html.escape(company_name or "Your Account")
    plan_str = html.escape(plan_name or "Active Plan")
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p><strong>Urgent Notice:</strong> Your available CallingGen credits are almost exhausted for <strong>{company_str}</strong>.</p>
    
    <div style="background-color: #451a03; border-left: 4px solid #f97316; padding: 16px; border-radius: 6px; margin: 20px 0;">
        <div style="font-size: 13px; color: #fdba74; font-weight: 600; text-transform: uppercase;">Critical Credit Alert</div>
        <div style="font-size: 28px; font-weight: 800; color: #ffffff; margin-top: 4px;">{remaining_credits} <span style="font-size: 16px; font-weight: 400; color: #ffedd5;">credits remaining</span></div>
        <div style="font-size: 13px; color: #fed7aa; margin-top: 6px;">Plan: {plan_str}</div>
    </div>

    <p style="color: #fdba74;">Please add credits immediately to avoid unexpected call disruptions or paused campaigns.</p>
    """
    return _wrap_email_layout("Critical Credit Warning", body, button_text="Recharge Balance Now", button_url="#/topup")

def get_credits_exhausted_html(full_name: str, company_name: str, plan_name: str = "Standard") -> str:
    name_str = html.escape(full_name or "Client")
    company_str = html.escape(company_name or "Your Account")
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your CallingGen credit balance for <strong>{company_str}</strong> has reached <strong>0 credits</strong>.</p>
    
    <div style="background-color: #450a0a; border-left: 4px solid #ef4444; padding: 16px; border-radius: 6px; margin: 20px 0;">
        <div style="font-size: 13px; color: #fca5a5; font-weight: 600; text-transform: uppercase;">Status: Calls Disabled</div>
        <div style="font-size: 22px; font-weight: 800; color: #ffffff; margin-top: 4px;">0 Credits Available</div>
        <div style="font-size: 13px; color: #fecaca; margin-top: 6px;">New calls & scheduled campaigns are paused until recharged.</div>
    </div>

    <p>Please top up your CallingGen account credits to resume outbound AI calling functionality.</p>
    """
    return _wrap_email_layout("CallingGen Credits Exhausted", body, button_text="Top Up Credits Immediately", button_url="#/topup")

def get_password_changed_html(full_name: str, email: str, timestamp_str: str) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    time_str = html.escape(timestamp_str)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your password for CallingGen account <strong>{email_str}</strong> was successfully changed on <strong>{time_str}</strong>.</p>
    
    <div style="background-color: #1e293b; padding: 14px 16px; border-radius: 6px; margin: 20px 0; border: 1px solid #334155;">
        <span style="color: #94a3b8; font-size: 13px;">Security Notice:</span>
        <p style="margin: 4px 0 0 0; color: #e2e8f0; font-size: 13px;">If you did not initiate this change, please contact your account administrator or support immediately to secure your account.</p>
    </div>
    """
    return _wrap_email_layout("Password Changed Successfully", body)

def get_first_time_password_changed_html(full_name: str, email: str) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Congratulations! You have successfully updated your temporary password and completed account setup for <strong>{email_str}</strong>.</p>
    
    <div style="background-color: #064e3b; border-left: 4px solid #10b981; padding: 16px; border-radius: 6px; margin: 20px 0;">
        <div style="font-size: 14px; color: #a7f3d0; font-weight: 600;">Account Setup Completed</div>
        <p style="margin: 4px 0 0 0; color: #ecfdf5; font-size: 13px;">Your CallingGen client portal account is now fully active and ready for use.</p>
    </div>
    """
    return _wrap_email_layout("Account Setup Completed", body, button_text="Go to Dashboard", button_url="#/dashboard")

def get_password_reset_code_html(email: str, reset_code: str) -> str:
    email_str = html.escape(email)
    code_str = html.escape(reset_code)
    
    body = f"""
    <p>Hello,</p>
    <p>We received a password reset request for your CallingGen account (<strong>{email_str}</strong>).</p>
    <p>Use the following 6-digit verification code to reset your password:</p>
    
    <div style="text-align: center; margin: 24px 0;">
        <div style="display: inline-block; background-color: #1e293b; border: 2px dashed #3b82f6; color: #60a5fa; font-size: 32px; font-weight: 800; letter-spacing: 6px; padding: 14px 28px; border-radius: 8px;">
            {code_str}
        </div>
    </div>

    <p style="font-size: 13px; color: #94a3b8;">This code will expire in <strong>15 minutes</strong>. If you did not request a password reset, you can safely ignore this message.</p>
    """
    return _wrap_email_layout("Password Reset Request", body)

def get_password_reset_success_html(full_name: str, email: str) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Your password for <strong>{email_str}</strong> has been successfully reset.</p>
    <p>You can now log in to CallingGen using your new password.</p>
    """
    return _wrap_email_layout("Password Reset Confirmation", body, button_text="Log In to Account", button_url="#/login")

def get_welcome_account_html(full_name: str, email: str, temp_password: str, company_name: str | None = None, plan_name: str = "Starter", credits: int = 2000) -> str:
    name_str = html.escape(full_name or "User")
    email_str = html.escape(email)
    pass_str = html.escape(temp_password)
    comp_str = html.escape(company_name or "CallingGen")
    plan_str = html.escape(plan_name or "Starter")
    
    body = f"""
    <p>Hello <strong>{name_str}</strong>,</p>
    <p>Welcome to CallingGen! An administrator has created a new account for you under <strong>{comp_str}</strong>.</p>
    
    <div style="background-color: #1e293b; border: 1px solid #334155; padding: 18px; border-radius: 8px; margin: 20px 0;">
        <div style="font-size: 12px; color: #94a3b8; font-weight: 600; text-transform: uppercase;">Account & Credentials Summary</div>
        <div style="margin-top: 10px; font-size: 14px;"><strong>Email:</strong> <span style="color: #60a5fa;">{email_str}</span></div>
        <div style="margin-top: 6px; font-size: 14px;"><strong>Subscription Plan:</strong> <span style="color: #c084fc; font-weight: 600;">{plan_str}</span></div>
        <div style="margin-top: 6px; font-size: 14px;"><strong>Initial Credits Allocated:</strong> <span style="color: #34d399; font-weight: 600;">{credits} Credits</span></div>
        <div style="margin-top: 10px; font-size: 14px; border-top: 1px border-zinc-700; pt-2;"><strong>Temporary Password:</strong> <code style="background-color: #0f172a; color: #34d399; padding: 4px 8px; border-radius: 4px; font-family: monospace;">{pass_str}</code></div>
    </div>

    <p style="font-size: 13px; color: #94a3b8;">Upon your first login, you will be prompted to change your temporary password to a secure personal password.</p>
    """
    return _wrap_email_layout("Welcome to CallingGen", body, button_text="Log In & Complete Setup", button_url="#/login")

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


