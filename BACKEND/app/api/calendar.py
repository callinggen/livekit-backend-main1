from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone
import os
import smtplib

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel

from app.database import get_db
from app.models.contact_form_user import ContactFormUser

router = APIRouter(prefix="/api/calendar", tags=["Calendar"])

class BookSlotRequest(BaseModel):
    name: str
    email: str
    phone: str
    company: str
    industry: str
    appointment_time: str # ISO format string


# Indian Standard Time (IST - UTC+05:30)
LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))


# -------------------------------------------------------------------
# REAL GOOGLE CALENDAR EVENT CREATION
# -------------------------------------------------------------------
async def create_google_calendar_event(booking: BookSlotRequest):
    """
    Creates a real Google Calendar Event using Service Account or OAuth credentials.
    """
    creds_file = os.path.join(os.path.dirname(__file__), "..", "..", "credentials.json")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")

    if not os.path.exists(creds_file) or not calendar_id:
        print(f"[GOOGLE CALENDAR API] Skipped: 'credentials.json' or GOOGLE_CALENDAR_ID missing. (Slot: {booking.name} at {booking.appointment_time})")
        return

    try:
        import json
        from googleapiclient.discovery import build

        SCOPES = ['https://www.googleapis.com/auth/calendar']
        
        with open(creds_file, 'r') as f:
            creds_data = json.load(f)

        if "type" in creds_data and creds_data["type"] == "service_account":
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        elif "web" in creds_data or "installed" in creds_data:
            from google.oauth2.credentials import Credentials as UserCredentials
            token_path = os.path.join(os.path.dirname(__file__), "..", "..", "token.json")
            creds = None
            if os.path.exists(token_path):
                creds = UserCredentials.from_authorized_user_file(token_path, SCOPES)
            if not creds or not creds.valid:
                print(f"[GOOGLE CALENDAR API] 'token.json' missing or expired for OAuth Client ID.")
                print(f"  To authorize Google Calendar once, run: python generate_token.py")
                return
        else:
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)

        service = build('calendar', 'v3', credentials=creds)

        start_dt = datetime.fromisoformat(booking.appointment_time)
        end_dt = start_dt + timedelta(hours=1)

        event_body = {
            'summary': f'CallingGen Demo - {booking.name}',
            'description': (
                f'CallingGen AI Platform Demo Consultation\n\n'
                f'Client: {booking.name}\n'
                f'Company: {booking.company}\n'
                f'Industry: {booking.industry}\n'
                f'Phone: {booking.phone}\n'
                f'Email: {booking.email}'
            ),
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'Asia/Kolkata',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'Asia/Kolkata',
            },
            'attendees': [
                {'email': booking.email},
            ],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 15},
                ],
            },
        }

        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            sendUpdates='all'
        ).execute()

        print(f"[GOOGLE CALENDAR API SUCCESS] Event Created! Link: {created_event.get('htmlLink')}")

    except Exception as e:
        print(f"[GOOGLE CALENDAR API ERROR] Failed to create Google Calendar event: {e}")


# -------------------------------------------------------------------
# RESEND-STYLE BEAUTIFUL HTML EMAIL TEMPLATES
# -------------------------------------------------------------------
async def send_email_notifications(booking: BookSlotRequest):
    """
    Sends premium Resend-styled HTML email confirmations to Customer & Admin.
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    admin_email = os.getenv("ADMIN_EMAIL", smtp_user)

    if not smtp_user or not smtp_pass:
        print(f"[EMAIL NOTIFICATION] Skipped: SMTP_USER or SMTP_PASSWORD not configured in .env.")
        return

    dt_obj = datetime.fromisoformat(booking.appointment_time)
    readable_date = dt_obj.strftime("%A, %B %d, %Y")
    readable_time = dt_obj.strftime("%I:%M %p IST")

    # 1. Customer Confirmation Email (Resend Style)
    cust_msg = MIMEMultipart("alternative")
    cust_msg["Subject"] = f"Confirmed: CallingGen Demo Session on {readable_date}"
    cust_msg["From"] = f"CallingGen <{smtp_user}>"
    cust_msg["To"] = booking.email

    cust_html = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Appointment Confirmed</title>
      </head>
      <body style="margin: 0; padding: 0; background-color: #F8FAFC; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1E293B;">
        <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin: 40px auto; background-color: #FFFFFF; border-radius: 16px; border: 1px solid #E2E8F0; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.05);">
          
          <!-- Header -->
          <tr>
            <td style="padding: 40px 40px 32px 40px; background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%); text-align: left;">
              <table border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td>
                    <span style="font-size: 22px; font-weight: 800; color: #FFFFFF; letter-spacing: -0.5px;">Calling<span style="color: #6366F1;">Gen</span></span>
                  </td>
                  <td align="right">
                    <span style="display: inline-block; padding: 6px 14px; background: rgba(99, 102, 241, 0.2); border: 1px solid rgba(99, 102, 241, 0.4); border-radius: 20px; color: #818CF8; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Confirmed</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Body Content -->
          <tr>
            <td style="padding: 40px;">
              <h1 style="margin: 0 0 16px 0; font-size: 26px; font-weight: 800; color: #0F172A; tracking-tight: -0.5px;">You're booked! 🎉</h1>
              <p style="margin: 0 0 28px 0; font-size: 15px; line-height: 24px; color: #475569;">
                Hi <b>{booking.name}</b>, your 1-on-1 personalized demo with CallingGen has been scheduled. Here are your booking details:
              </p>

              <!-- Session Details Card -->
              <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #F8FAFC; border-radius: 12px; border: 1px solid #E2E8F0; padding: 24px; margin-bottom: 28px;">
                <tr>
                  <td style="padding-bottom: 14px; border-bottom: 1px border #E2E8F0;">
                    <span style="font-size: 12px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">Date & Time (Indian Standard Time)</span>
                    <div style="font-size: 17px; font-weight: 700; color: #0F172A; margin-top: 4px;">📅 {readable_date}</div>
                    <div style="font-size: 15px; font-weight: 600; color: #4F6BFF; margin-top: 2px;">⏰ {readable_time}</div>
                  </td>
                </tr>
                <tr>
                  <td style="padding-top: 14px;">
                    <table border="0" cellpadding="0" cellspacing="0" width="100%">
                      <tr>
                        <td width="50%">
                          <span style="font-size: 12px; font-weight: 600; color: #64748B;">Company</span>
                          <div style="font-size: 14px; font-weight: 600; color: #1E293B; margin-top: 2px;">{booking.company}</div>
                        </td>
                        <td width="50%">
                          <span style="font-size: 12px; font-weight: 600; color: #64748B;">Industry</span>
                          <div style="font-size: 14px; font-weight: 600; color: #1E293B; margin-top: 2px;">{booking.industry}</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- What to Expect -->
              <div style="margin-bottom: 28px;">
                <h3 style="margin: 0 0 12px 0; font-size: 16px; font-weight: 700; color: #0F172A;">What we'll cover during your demo:</h3>
                <ul style="margin: 0; padding-left: 20px; font-size: 14px; line-height: 22px; color: #475569;">
                  <li style="margin-bottom: 6px;">Live demonstration of AI agent voice calling & transcriptions</li>
                  <li style="margin-bottom: 6px;">Customizing agent voice personas for Indian regional languages</li>
                  <li style="margin-bottom: 6px;">Integrating leads directly into your CRM & WhatsApp</li>
                </ul>
              </div>

              <!-- CTA Button -->
              <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 24px;">
                <tr>
                  <td align="center">
                    <a href="https://callinggen.in" target="_blank" style="display: inline-block; padding: 14px 32px; background: linear-gradient(135deg, #4F6BFF 0%, #6366F1 100%); color: #FFFFFF; text-decoration: none; font-size: 15px; font-weight: 700; border-radius: 10px; box-shadow: 0 4px 12px rgba(79, 107, 255, 0.25);">Visit CallingGen Platform →</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 24px 40px; background-color: #F1F5F9; border-top: 1px solid #E2E8F0; text-align: center; font-size: 13px; color: #64748B;">
              Need to reschedule? Reply directly to this email or contact support at <a href="mailto:{smtp_user}" style="color: #4F6BFF; text-decoration: none;">{smtp_user}</a>.<br/>
              <span style="display: inline-block; margin-top: 8px;">© {datetime.now().year} CallingGen AI. All rights reserved.</span>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """
    cust_msg.attach(MIMEText(cust_html, "html"))

    # 2. Admin Notification Email (Resend Dashboard Alert Style)
    admin_msg = MIMEMultipart("alternative")
    admin_msg["Subject"] = f"🔥 New Demo Booking: {booking.name} ({booking.company})"
    admin_msg["From"] = f"CallingGen Alerts <{smtp_user}>"
    admin_msg["To"] = admin_email

    admin_html = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
      </head>
      <body style="margin: 0; padding: 0; background-color: #F8FAFC; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1E293B;">
        <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin: 40px auto; background-color: #FFFFFF; border-radius: 16px; border: 1px solid #E2E8F0; overflow: hidden;">
          
          <tr>
            <td style="padding: 24px 32px; background: #0F172A; color: #FFFFFF; font-size: 16px; font-weight: 700;">
              ⚡ New Demo Appointment Scheduled
            </td>
          </tr>

          <tr>
            <td style="padding: 32px;">
              <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #F1F5F9; border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                <tr>
                  <td style="padding-bottom: 10px; font-size: 13px; color: #64748B; font-weight: 700; text-transform: uppercase;">Client Information</td>
                </tr>
                <tr>
                  <td style="font-size: 18px; font-weight: 800; color: #0F172A;">{booking.name}</td>
                </tr>
                <tr>
                  <td style="padding-top: 8px; font-size: 14px; color: #334155;">
                    📧 Email: <b>{booking.email}</b><br/>
                    📞 Phone: <b>{booking.phone}</b><br/>
                    🏢 Company: <b>{booking.company}</b><br/>
                    💼 Industry: <b>{booking.industry}</b>
                  </td>
                </tr>
              </table>

              <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #EEF2FF; border: 1px solid #C7D2FE; border-radius: 12px; padding: 20px;">
                <tr>
                  <td style="font-size: 13px; color: #4338CA; font-weight: 700; text-transform: uppercase;">Scheduled IST Time Slot</td>
                </tr>
                <tr>
                  <td style="font-size: 16px; font-weight: 800; color: #3730A3; margin-top: 4px;">📅 {readable_date}</td>
                </tr>
                <tr>
                  <td style="font-size: 15px; font-weight: 700; color: #4F6BFF;">⏰ {readable_time}</td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """
    admin_msg.attach(MIMEText(admin_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=5.0) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, booking.email, cust_msg.as_string())
            if admin_email:
                server.sendmail(smtp_user, admin_email, admin_msg.as_string())

        print(f"[EMAIL SUCCESS] Resend-style confirmation email sent to {booking.email} and admin notification to {admin_email}")

    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send email notifications: {e}")


# -------------------------------------------------------------------
# ENDPOINTS
# -------------------------------------------------------------------
@router.get("/slots")
async def get_available_slots(db: AsyncSession = Depends(get_db)):
    """
    Returns available 1-hour slots (10:00 AM to 6:00 PM IST) for the next 7 days.
    """
    today = datetime.now(LOCAL_TZ)
    
    all_slots = []
    for day_offset in range(1, 8):
        current_day = today + timedelta(days=day_offset)
        if current_day.weekday() >= 5: # Skip weekends
            continue
        
        # 10 AM to 6 PM IST (10:00 to 18:00) in 1-hour slots
        for hour in range(10, 19):
            slot_time = datetime(
                year=current_day.year, 
                month=current_day.month, 
                day=current_day.day, 
                hour=hour, 
                minute=0, 
                tzinfo=LOCAL_TZ
            )
            all_slots.append(slot_time)

    result = await db.execute(select(ContactFormUser.appointment_time))
    booked_slots = [row[0] for row in result.fetchall()]

    available_slots = [
        slot.isoformat() 
        for slot in all_slots 
        if slot.replace(tzinfo=None) not in [b.replace(tzinfo=None) for b in booked_slots if b]
    ]

    return {"available_slots": available_slots}


@router.post("/book")
async def book_appointment(request: BookSlotRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    try:
        appt_time = datetime.fromisoformat(request.appointment_time)
        
        # 1. Check if slot is already booked in DB
        result = await db.execute(select(ContactFormUser).where(ContactFormUser.appointment_time == appt_time))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="This slot has already been booked.")

        # 2. Save to Database
        new_user = ContactFormUser(
            name=request.name,
            email=request.email,
            phone=request.phone,
            company=request.company,
            industry=request.industry,
            appointment_time=appt_time,
            status="booked"
        )
        db.add(new_user)
        await db.commit()

        # 3. Trigger Real Google Calendar API & Email notifications in background
        background_tasks.add_task(create_google_calendar_event, request)
        background_tasks.add_task(send_email_notifications, request)

        return {"status": "success", "message": "Appointment booked successfully"}

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
