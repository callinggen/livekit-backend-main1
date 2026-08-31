from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone
import os
from pydantic import BaseModel
from app.database import get_db
from app.models.contact_form_user import ContactFormUser
from app.models.blocked_slot import BlockedSlot
from app.services.email_service import email_service


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
                {'email': calendar_id},
            ],
            'conferenceData': {
                'createRequest': {
                    'requestId': f'callinggen-meet-{int(start_dt.timestamp())}-{abs(hash(booking.email))}',
                    'conferenceSolutionKey': {
                        'type': 'hangoutsMeet'
                    }
                }
            },
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
            sendUpdates='all',
            conferenceDataVersion=1
        ).execute()

        meet_link = created_event.get('hangoutLink') or created_event.get('htmlLink')
        print(f"[GOOGLE CALENDAR API SUCCESS] Event Created with Real Google Meet Link: {meet_link}")
        return meet_link

    except Exception as e:
        print(f"[GOOGLE CALENDAR API ERROR] Failed to create Google Calendar event: {e}")
        return None


# -------------------------------------------------------------------
# RESEND-POWERED HTML EMAIL TEMPLATES
# -------------------------------------------------------------------
async def send_email_notifications(booking: BookSlotRequest):
    """
    Sends premium HTML email confirmations to Customer & Admin via Resend / EmailService.
    """
    admin_email = os.getenv("ADMIN_EMAIL", "saisathwik@genxreality.in")
    meeting_link = os.getenv("MEETING_LINK", "https://meet.google.com/hfi-jick-ijc")
    support_email = os.getenv("SUPPORT_EMAIL", "support@callinggen.in")

    try:
        dt_obj = datetime.fromisoformat(booking.appointment_time)
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=LOCAL_TZ)
    except Exception:
        dt_obj = datetime.now(LOCAL_TZ)

    readable_date = dt_obj.strftime("%A, %B %d, %Y")
    readable_time = dt_obj.strftime("%I:%M %p IST")

    # 1. Customer Confirmation Email
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
                    <div style="font-size: 14px; font-weight: 600; color: #10B981; margin-top: 6px;">📹 <a href="{meeting_link}" target="_blank" style="color: #10B981; text-decoration: underline;">Join Google Meet Call</a></div>
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
                    <a href="{meeting_link}" target="_blank" style="display: inline-block; padding: 14px 32px; background: linear-gradient(135deg, #4F6BFF 0%, #6366F1 100%); color: #FFFFFF; text-decoration: none; font-size: 15px; font-weight: 700; border-radius: 10px; box-shadow: 0 4px 12px rgba(79, 107, 255, 0.25);">Join Demo Meeting →</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 24px 40px; background-color: #F1F5F9; border-top: 1px solid #E2E8F0; text-align: center; font-size: 13px; color: #64748B;">
              Need to reschedule? Reply directly to this email or contact support at <a href="mailto:{support_email}" style="color: #4F6BFF; text-decoration: none;">{support_email}</a>.<br/>
              <span style="display: inline-block; margin-top: 8px;">© {datetime.now().year} CallingGen AI. All rights reserved.</span>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """

    # 2. Admin Notification Email
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

              <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #EEF2FF; border: 1px solid #C7D2FE; border-radius: 12px; padding: 20px; margin-bottom: 20px;">
                <tr>
                  <td style="font-size: 13px; color: #4338CA; font-weight: 700; text-transform: uppercase;">Scheduled IST Time Slot</td>
                </tr>
                <tr>
                  <td style="font-size: 16px; font-weight: 800; color: #3730A3; margin-top: 4px;">📅 {readable_date}</td>
                </tr>
                <tr>
                  <td style="font-size: 15px; font-weight: 700; color: #4F6BFF;">⏰ {readable_time}</td>
                </tr>
                <tr>
                  <td style="font-size: 14px; font-weight: 600; color: #10B981; margin-top: 8px;">📹 <a href="{meeting_link}" target="_blank" style="color: #10B981; text-decoration: underline;">Open Google Meet Link</a></td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """

    # Send Customer Confirmation via Resend
    try:
        email_service._send_email(
            to_email=booking.email,
            subject=f"Confirmed: CallingGen Demo Session on {readable_date}",
            body=cust_html,
            is_html=True,
        )
        print(f"[CALENDAR EMAIL] Confirmation sent to customer: {booking.email}")
    except Exception as e:
        print(f"[CALENDAR EMAIL ERROR] Failed to send customer confirmation: {e}")

    # Send Admin Alert via Resend
    if admin_email:
        try:
            email_service._send_email(
                to_email=admin_email,
                subject=f"🔥 New Demo Booking: {booking.name} ({booking.company})",
                body=admin_html,
                is_html=True,
            )
            print(f"[CALENDAR EMAIL] Notification sent to admin: {admin_email}")
        except Exception as e:
            print(f"[CALENDAR EMAIL ERROR] Failed to send admin notification: {e}")



# -------------------------------------------------------------------
# ENDPOINTS
# -------------------------------------------------------------------
@router.get("/slots")
async def get_available_slots(db: AsyncSession = Depends(get_db)):
    """
    Returns available 1-hour slots (10:00 AM to 6:00 PM IST) for 7 upcoming days (excluding Sundays).
    Includes remaining immediate slots for today.
    Only counts days that have at least 1 available slot.
    """
    now = datetime.now(LOCAL_TZ)
    
    all_slots = []
    days_added = 0
    day_offset = 0

    while days_added < 7 and day_offset < 30:
        current_day = now + timedelta(days=day_offset)
        day_offset += 1

        # Skip ONLY Sunday (weekday == 6)
        if current_day.weekday() == 6:
            continue

        day_has_slots = False

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
            # Skip slots in the past
            if slot_time <= now:
                continue

            all_slots.append(slot_time)
            day_has_slots = True

        # Count this day towards our 7 days window ONLY if it has available slots!
        if day_has_slots:
            days_added += 1

    result = await db.execute(select(ContactFormUser.appointment_time))
    raw_booked_slots = [row[0] for row in result.fetchall()]

    # Fetch all admin-blocked dates and slots
    blocked_res = await db.execute(select(BlockedSlot))
    raw_blocked = blocked_res.scalars().all()
    
    blocked_days = set()   # "YYYY-MM-DD"
    blocked_slots = set()  # "YYYY-MM-DD HH:MM"
    
    for block in raw_blocked:
        if block.blocked_date:
            if not block.slot_time:
                blocked_days.add(block.blocked_date)
            else:
                blocked_slots.add(f"{block.blocked_date} {block.slot_time}")

    # Normalize booked slots to string representation for 100% reliable matching
    booked_keys = set()
    for b in raw_booked_slots:
        if not b:
            continue
        if isinstance(b, str):
            try:
                b = datetime.fromisoformat(b)
            except Exception:
                continue
        # Truncate to YYYY-MM-DD HH:MM
        booked_keys.add(b.strftime("%Y-%m-%d %H:%M"))

    available_slots = []
    for slot in all_slots:
        day_key = slot.strftime("%Y-%m-%d")
        slot_key = slot.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M")
        slot_time_only = slot.strftime("%H:%M")
        
        # Check if full day is blocked or specific slot is blocked or booked
        if day_key not in blocked_days and slot_key not in blocked_slots and f"{day_key} {slot_time_only}" not in blocked_slots and slot_key not in booked_keys:
            available_slots.append(slot.isoformat())

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
