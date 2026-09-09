from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone
import os
import smtplib
from dotenv import load_dotenv
load_dotenv()

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pydantic import BaseModel

from app.database import get_db
from app.models.contact_form_user import ContactFormUser
from app.models.blocked_slot import BlockedSlot

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
        import importlib
        build = importlib.import_module("googleapiclient.discovery").build
        service_account = importlib.import_module("google.oauth2.service_account")
        Credentials = getattr(service_account, "Credentials")

        SCOPES = ['https://www.googleapis.com/auth/calendar']
        
        with open(creds_file, 'r') as f:
            creds_data = json.load(f)

        if "type" in creds_data and creds_data["type"] == "service_account":
            creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        elif "web" in creds_data or "installed" in creds_data:
            user_creds_mod = importlib.import_module("google.oauth2.credentials")
            UserCredentials = getattr(user_creds_mod, "Credentials")
            token_path = os.path.join(os.path.dirname(__file__), "..", "..", "token.json")
            creds = None
            if os.path.exists(token_path):
                creds = UserCredentials.from_authorized_user_file(token_path, SCOPES)
            if not creds or not creds.valid:
                print(f"[GOOGLE CALENDAR API] 'token.json' missing or expired for OAuth Client ID.")
                print(f"  To authorize Google Calendar once, run: python generate_token.py")
                return
        else:
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
# RESEND-STYLE BEAUTIFUL HTML EMAIL TEMPLATES
# -------------------------------------------------------------------
def generate_ics_invite(
    summary: str,
    description: str,
    start_dt: datetime,
    end_dt: datetime,
    organizer_email: str,
    attendee_email: str,
    attendee_name: str,
    admin_email: str,
    meeting_link: str = "https://meet.google.com/hfi-jick-ijc"
) -> str:
    """Generates standard RFC 5545 iCalendar (.ics) format string for Google Calendar, Apple Calendar, and Outlook auto-invite."""
    fmt = "%Y%m%dT%H%M%SZ"
    start_utc = start_dt.astimezone(timezone.utc).strftime(fmt)
    end_utc = end_dt.astimezone(timezone.utc).strftime(fmt)
    now_utc = datetime.now(timezone.utc).strftime(fmt)
    uid = f"callinggen-{int(start_dt.timestamp())}-{abs(hash(attendee_email))}@callinggen.in"

    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//CallingGen AI//Calendar Booking//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:REQUEST\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{now_utc}\r\n"
        f"DTSTART:{start_utc}\r\n"
        f"DTEND:{end_utc}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{description}\\nMeeting Link: {meeting_link}\r\n"
        f"LOCATION:Google Meet ({meeting_link})\r\n"
        f"ORGANIZER;CN=CallingGen AI:mailto:{organizer_email}\r\n"
        f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN={attendee_name}:mailto:{attendee_email}\r\n"
        f"ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=CallingGen Admin:mailto:{admin_email}\r\n"
        "STATUS:CONFIRMED\r\n"
        "SEQUENCE:0\r\n"
        "TRANSP:OPAQUE\r\n"
        "BEGIN:VALARM\r\n"
        "TRIGGER:-PT15M\r\n"
        "ACTION:DISPLAY\r\n"
        "DESCRIPTION:CallingGen Demo Reminder (in 15 min)\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

async def send_email_notifications(booking: BookSlotRequest, meet_link_override: str | None = None):
    """
    Sends premium Resend HTML email confirmations to Customer (booking.email) & Admin (calliggen0@gmail.com)
    with Google Meet video link and iCalendar (.ics) Google Calendar attachments.
    """
    admin_email = os.getenv("ADMIN_EMAIL", "saisathwik@genxreality.in")
    support_email = os.getenv("SUPPORT_EMAIL", "callinggen0@gmail.com")
    from_email = os.getenv("RESEND_FROM_EMAIL", "CallingGen <noreply@callinggen.in>")
    meeting_link = meet_link_override or os.getenv("MEETING_LINK", "https://meet.google.com/hfi-jick-ijc")

    try:
        dt_obj = datetime.fromisoformat(booking.appointment_time)
        readable_date = dt_obj.strftime("%A, %B %d, %Y")
        readable_time = dt_obj.strftime("%I:%M %p IST")
    except Exception:
        readable_date = booking.appointment_time
        readable_time = ""
        dt_obj = datetime.now(LOCAL_TZ)

    end_dt = dt_obj + timedelta(hours=1)

    # Generate 1-Click Calendar Web URLs
    import urllib.parse
    import base64

    fmt_gcal = "%Y%m%dT%H%M%SZ"
    start_utc_str = dt_obj.astimezone(timezone.utc).strftime(fmt_gcal)
    end_utc_str = end_dt.astimezone(timezone.utc).strftime(fmt_gcal)

    gcal_params = {
        "action": "TEMPLATE",
        "text": f"CallingGen Demo - {booking.name}",
        "details": f"1-on-1 Personalized CallingGen Voice AI Demo Consultation\n\nClient: {booking.name}\nCompany: {booking.company}\nPhone: {booking.phone}\n\nJoin Google Meet: {meeting_link}",
        "location": meeting_link,
        "dates": f"{start_utc_str}/{end_utc_str}",
    }
    google_cal_url = f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(gcal_params)}"

    fmt_outlook = "%Y-%m-%dT%H:%M:%SZ"
    outlook_start = dt_obj.astimezone(timezone.utc).strftime(fmt_outlook)
    outlook_end = end_dt.astimezone(timezone.utc).strftime(fmt_outlook)
    outlook_params = {
        "path": "/calendar/action/compose",
        "rru": "addevent",
        "startdt": outlook_start,
        "enddt": outlook_end,
        "subject": f"CallingGen Demo - {booking.name}",
        "body": f"CallingGen Voice AI Demo Consultation\nJoin Google Meet: {meeting_link}",
        "location": meeting_link,
    }
    outlook_cal_url = f"https://outlook.live.com/calendar/0/deeplink/compose?{urllib.parse.urlencode(outlook_params)}"

    # Generate standard iCalendar (.ics) attachment with RFC 5545 compliance
    ics_content = generate_ics_invite(
        summary=f"CallingGen Demo - {booking.name}",
        description=f"CallingGen Voice AI Demo Consultation\\nClient: {booking.name}\\nCompany: {booking.company}\\nPhone: {booking.phone}\\nMeeting Link: {meeting_link}",
        start_dt=dt_obj,
        end_dt=end_dt,
        organizer_email="noreply@callinggen.in",
        attendee_email=booking.email,
        attendee_name=booking.name,
        admin_email=admin_email,
        meeting_link=meeting_link,
    )

    ics_b64 = base64.b64encode(ics_content.encode("utf-8")).decode("utf-8")
    ics_attachment = {
        "filename": "invite.ics",
        "content": ics_b64,
        "content_type": "text/calendar; method=REQUEST; charset=UTF-8",
    }

    # 1. Customer Confirmation Email
    cust_html = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #FAFAFA; margin: 0; padding: 0; color: #1E293B; }}
          .card {{ max-width: 580px; margin: 32px auto; background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.04); }}
          .header {{ padding: 32px 32px 20px; border-bottom: 1px solid #F1F5F9; background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%); color: #FFFFFF; }}
          .content {{ padding: 32px; font-size: 15px; line-height: 1.6; color: #334155; }}
          .badge {{ display: inline-block; padding: 4px 12px; font-size: 11px; font-weight: 700; background: rgba(255,255,255,0.2); color: #FFFFFF; border-radius: 9999px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
          .slot-box {{ background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 14px; padding: 20px; margin: 24px 0; }}
          .btn-primary {{ display: inline-block; background-color: #4F46E5; color: #FFFFFF !important; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 28px; border-radius: 10px; box-shadow: 0 4px 10px rgba(79, 70, 229, 0.25); }}
          .btn-gcal {{ display: inline-block; background-color: #0F172A; color: #FFFFFF !important; font-size: 13px; font-weight: 600; text-decoration: none; padding: 10px 18px; border-radius: 8px; margin: 4px; }}
          .btn-outlook {{ display: inline-block; background-color: #0284C7; color: #FFFFFF !important; font-size: 13px; font-weight: 600; text-decoration: none; padding: 10px 18px; border-radius: 8px; margin: 4px; }}
          .footer {{ padding: 24px 32px; background: #F8FAFC; border-top: 1px solid #F1F5F9; font-size: 13px; color: #94A3B8; text-align: center; }}
        </style>
      </head>
      <body>
        <div class="card">
          <div class="header">
            <div class="badge">Appointment Confirmed</div>
            <h1 style="margin: 0; font-size: 22px; font-weight: 800; color: #FFFFFF;">Your 1-on-1 CallingGen Demo is Scheduled</h1>
          </div>
          <div class="content">
            <p>Hi <b>{booking.name}</b>,</p>
            <p>Thank you for scheduling a consultation with CallingGen. We look forward to demonstrating how our voice AI agents can automate your customer calling operations.</p>
            
            <div class="slot-box">
              <div style="font-size: 11px; color: #64748B; font-weight: 700; text-transform: uppercase;">Scheduled Time (IST)</div>
              <div style="font-size: 18px; font-weight: 800; color: #0F172A; margin: 6px 0 2px;">📅 {readable_date}</div>
              <div style="font-size: 16px; font-weight: 700; color: #4F46E5;">⏰ {readable_time}</div>
            </div>

            <div style="text-align: center; margin: 28px 0 20px;">
              <a href="{meeting_link}" class="btn-primary" target="_blank">🎥 Join Google Meet Video Call</a>
            </div>

            <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px; text-align: center; margin-bottom: 24px;">
              <p style="margin: 0 0 10px; font-size: 13px; font-weight: 700; color: #1E293B;">Add this meeting to your Calendar:</p>
              <div>
                <a href="{google_cal_url}" class="btn-gcal" target="_blank">📅 Add to Google Calendar</a>
                <a href="{outlook_cal_url}" class="btn-outlook" target="_blank">📅 Add to Outlook Calendar</a>
              </div>
            </div>

            <p style="font-size: 12px; color: #64748B; line-height: 1.5;">
              💡 <b>Auto-Sync / Calendar Attachment:</b> An interactive <code>invite.ics</code> file is attached. Opening it will automatically pin this event and video call directly to your calendar app.
            </p>
          </div>
          <div class="footer">
            © CallingGen AI • Autonomous Telephony Voice Agents<br/>
            Need to reschedule? Reply directly or contact <b>{support_email}</b>
          </div>
        </div>
      </body>
    </html>
    """

    # 2. Admin Alert Email (Sent to saisathwik@genxreality.in)
    admin_html = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
      </head>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F8FAFC; margin: 0; padding: 24px;">
        <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 580px; background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.03);">
          
          <tr>
            <td style="padding: 24px 32px; background: #0F172A; color: #FFFFFF; font-size: 17px; font-weight: 800;">
              ⚡ New Demo Lead Scheduled
            </td>
          </tr>

          <tr>
            <td style="padding: 32px;">
              <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; padding: 20px; margin-bottom: 20px;">
                <tr>
                  <td style="padding-bottom: 8px; font-size: 11px; color: #64748B; font-weight: 700; text-transform: uppercase;">Prospect Information</td>
                </tr>
                <tr>
                  <td style="font-size: 18px; font-weight: 800; color: #0F172A;">{booking.name}</td>
                </tr>
                <tr>
                  <td style="padding-top: 10px; font-size: 14px; color: #334155; line-height: 1.6;">
                    📧 Email: <a href="mailto:{booking.email}" style="color: #4F46E5; font-weight: 600;">{booking.email}</a><br/>
                    📞 Phone: <b>{booking.phone}</b><br/>
                    🏢 Company: <b>{booking.company}</b><br/>
                    💼 Industry: <b>{booking.industry}</b>
                  </td>
                </tr>
              </table>

              <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #EEF2FF; border: 1px solid #C7D2FE; border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                <tr>
                  <td style="font-size: 11px; color: #4338CA; font-weight: 700; text-transform: uppercase;">Scheduled IST Slot</td>
                </tr>
                <tr>
                  <td style="font-size: 16px; font-weight: 800; color: #3730A3; padding-top: 4px;">📅 {readable_date}</td>
                </tr>
                <tr>
                  <td style="font-size: 15px; font-weight: 700; color: #4F6BFF;">⏰ {readable_time}</td>
                </tr>
                <tr>
                  <td style="padding-top: 14px;">
                    <a href="{meeting_link}" target="_blank" style="display: inline-block; background-color: #10B981; color: #FFFFFF; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-weight: 700; font-size: 13px;">
                      🎥 Open Google Meet Call
                    </a>
                  </td>
                </tr>
              </table>

              <div style="text-align: center;">
                <a href="{google_cal_url}" target="_blank" style="display: inline-block; background-color: #0F172A; color: #FFFFFF; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-size: 13px; font-weight: 600;">
                  📅 Add to Admin Google Calendar
                </a>
              </div>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """

    from app.services.email_service import email_service

    # Send Confirmation to Customer via Resend
    try:
        email_service._send_email(
            to_email=booking.email,
            subject=f"Confirmed: Your CallingGen Voice AI Demo on {readable_date}",
            body=cust_html,
            is_html=True,
            from_override=from_email,
            reply_to=support_email,
            attachments=[ics_attachment],
            headers={
                "Content-Class": "urn:content-classes:calendarmessage",
            },
        )
        print(f"[CALENDAR EMAIL] Confirmation sent to customer: {booking.email} via Resend")
    except Exception as e:
        print(f"[CALENDAR EMAIL ERROR] Failed to send customer confirmation via Resend: {e}")

    # Send Notification to Admin via Resend
    if admin_email:
        try:
            email_service._send_email(
                to_email=admin_email,
                subject=f"🔥 New Demo Booking: {booking.name} ({booking.company})",
                body=admin_html,
                is_html=True,
                from_override="CallingGen Alerts <noreply@callinggen.in>",
                reply_to=booking.email,
                attachments=[ics_attachment],
                headers={
                    "Content-Class": "urn:content-classes:calendarmessage",
                },
            )
            print(f"[CALENDAR EMAIL] Notification sent to admin: {admin_email} via Resend")
        except Exception as e:
            print(f"[CALENDAR EMAIL ERROR] Failed to send admin alert via Resend: {e}")


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
