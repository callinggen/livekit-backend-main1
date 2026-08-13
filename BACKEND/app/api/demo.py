from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db, AsyncSessionLocal
from app.models.demo_lead import DemoLead
from app.models.user import User
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.job import Job
from app.models.call import Call
from app.services.livekit_service import make_livekit_call
import asyncio
from datetime import datetime

router = APIRouter()

class DemoRequest(BaseModel):
    name: str
    company: str
    email: str
    phone: str
    industry: str

async def trigger_outbound_call_task_async(
    lead_id: int, 
    name: str, 
    company: str, 
    phone: str, 
    industry: str
):
    """
    Background task that bridges the DemoLead to the Call/Campaign architecture
    and initiates the LiveKit call.
    """
    try:
        async with AsyncSessionLocal() as db:
            print("="*50)
            print(f"DEMO CALL TASK STARTED for {name}")
            
            # 1. Get Admin User (fallback)
            res = await db.execute(select(User).where(User.email == "admin@example.com"))
            admin = res.scalars().first()
            if not admin:
                print("Admin user not found. Demo call failed.")
                return

            # 2. Determine Script and Persona based on Industry
            industry_lower = industry.lower()
            if "real estate" in industry_lower:
                agent_name = "Alex (Real Estate)"
                dynamic_prompt = (
                    f"You are Alex, an AI Sales Representative for CallingGen specializing in the Real Estate sector. "
                    f"You are speaking with {name} who just requested a demo on our website. "
                    f"They work at {company}. "
                    f"Greet them by name and briefly explain how CallingGen "
                    f"can automate property inquiries, schedule viewings, and follow up with leads 24/7."
                )
            elif "health" in industry_lower or "medical" in industry_lower:
                agent_name = "Dr. Sarah (Healthcare)"
                dynamic_prompt = (
                    f"You are Sarah, an AI Representative for CallingGen specializing in Healthcare. "
                    f"You are speaking with {name} who just requested a demo on our website. "
                    f"They work at {company}. "
                    f"Greet them by name and briefly explain how CallingGen "
                    f"can automate patient appointment scheduling, handle after-hours inquiries, and reduce no-shows."
                )
            elif "education" in industry_lower or "school" in industry_lower:
                agent_name = "Prof. James (Education)"
                dynamic_prompt = (
                    f"You are James, an AI Admissions Counselor for CallingGen. "
                    f"You are speaking with {name} who just requested a demo on our website. "
                    f"They work at {company}. "
                    f"Greet them by name and briefly explain how CallingGen "
                    f"can automate student inquiries, course enrollments, and fee reminders effortlessly."
                )
            else:
                agent_name = "Voice-E (Tech Sales)"
                dynamic_prompt = (
                    f"You are Voice-E, an AI Sales Representative for CallingGen. "
                    f"You are speaking with {name} who just requested a demo on our website. "
                    f"They work at {company} in the {industry} sector. "
                    f"Greet them by name, mention their industry, and briefly explain how CallingGen "
                    f"can automate their specific workflows using AI voice agents."
                )

            res = await db.execute(select(Campaign).where(Campaign.campaign_name == "Website Demo Requests"))
            campaign = res.scalars().first()

            if not campaign:
                now = datetime.utcnow()
                campaign = Campaign(
                    user_id=admin.id,
                    campaign_name="Website Demo Requests",
                    agent=agent_name,
                    script=dynamic_prompt,
                    schedule_date=now.strftime("%Y-%m-%d"),
                    schedule_time=now.strftime("%H:%M"),
                    status="running"
                )
                db.add(campaign)
                await db.commit()
                await db.refresh(campaign)
            else:
                campaign.script = dynamic_prompt
                campaign.agent = agent_name
                await db.commit()

            # 3. Create Contact
            contact = Contact(
                campaign_id=campaign.id,
                name=name,
                phone=phone,
                status="calling",
                metadata_fields={"industry": industry, "company": company, "email": email} if 'email' in locals() else {"industry": industry, "company": company}
            )
            db.add(contact)
            
            # 4. Create single-use Job
            job = Job(
                campaign_id=campaign.id,
                status="queued",
                total_contacts=1,
                completed_contacts=0,
                failed_contacts=0,
            )
            db.add(job)
            await db.commit()
            
            await db.refresh(contact)
            await db.refresh(job)

            # 5. Create Call
            call = Call(
                job_id=job.id,
                contact_id=contact.id,
                phone=contact.phone,
                status="dialing",
            )
            db.add(call)
            await db.commit()
            await db.refresh(call)

            room_name = f"call-{call.id}"
            call.room_name = room_name
            await db.commit()
            
            # Update DemoLead with call_id
            lead_res = await db.execute(select(DemoLead).where(DemoLead.id == lead_id))
            lead = lead_res.scalars().first()
            if lead:
                lead.call_id = call.id
                await db.commit()
            
            print(f"Executing LiveKit Call for room {room_name}")

            # 6. Trigger SIP Call
            result = await make_livekit_call(phone=phone, room_name=room_name)
            
            if result.get("success"):
                call.status = "in_progress"
                call.livekit_participant_id = result.get("participant_id")
                await db.commit()
                print(f"Demo Call {call.id} successfully dispatched to LiveKit.")
            else:
                call.status = "failed"
                await db.commit()
                print(f"SIP dial failed for demo call: {result.get('error')}")

    except Exception as e:
        print(f"Error in background demo call task: {e}")

@router.post("/trigger-call")
async def trigger_demo_call(
    req: DemoRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    try:
        # 1. Save the lead in the database
        new_lead = DemoLead(
            name=req.name,
            company=req.company,
            email=req.email,
            phone=req.phone,
            industry=req.industry,
            status="calling"
        )
        db.add(new_lead)
        await db.commit()
        await db.refresh(new_lead)

        # 2. Trigger the call in the background so API responds instantly
        background_tasks.add_task(
            trigger_outbound_call_task_async, 
            new_lead.id,
            req.name, 
            req.company, 
            req.phone, 
            req.industry
        )

        return {"success": True, "lead_id": new_lead.id, "message": "Call initiated successfully."}
    except Exception as e:
        print(f"Error triggering demo call: {e}")
        raise HTTPException(status_code=500, detail="Failed to initiate demo call")

@router.get("/leads")
async def get_demo_leads(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(DemoLead).order_by(DemoLead.created_at.desc()))
        leads = result.scalars().all()
        return leads
    except Exception as e:
        print(f"Error fetching demo leads: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch leads")

from app.api.calls import _to_ist, _fmt_duration, _parse_transcript

@router.get("/call/{call_id}")
async def get_demo_call(call_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch call details for the demo admin popup."""
    try:
        result = await db.execute(
            select(Call, Contact, Campaign)
            .join(Contact, Call.contact_id == Contact.id)
            .join(Campaign, Contact.campaign_id == Campaign.id)
            .where(Call.id == call_id)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Call not found")
            
        call, contact, campaign = row
        
        is_active = call.status in ("dialing", "in_progress")
        response_display = contact.response or "—"
        if is_active:
            response_display = "In Progress"
            
        return {
            "id": str(call.id),
            "name": contact.customer_name or contact.name,
            "phone": contact.phone,
            "status": call.status.capitalize(),
            "response": response_display,
            "datetime": _to_ist(call.started_at),
            "campaign": campaign.campaign_name,
            "duration": _fmt_duration(call.duration or 0) if not is_active else "—",
            "transcript": _parse_transcript(call.transcript),
            "summary": call.summary or "",
            "category": call.category or "UNCATEGORIZED",
            "appointment_date": contact.appointment_date or "",
            "appointment_time": contact.appointment_time or "",
            "recording_url": call.recording_url or "",
        }
    except Exception as e:
        print(f"Error fetching demo call {call_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch call details")
