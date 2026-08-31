from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.models.user import User
from app.models.agent import Agent
from app.models.campaign import Campaign
from app.models.call import Call
from app.models.agent import Agent
from app.models.user_phone_number import UserPhoneNumber
from app.models.contact_form_user import ContactFormUser
from app.models.blocked_slot import BlockedSlot

from app.schemas.auth import UserCreateRequest
from app.core.security import get_password_hash
from app.services.email_service import email_service
from app.services.notification_service import notification_service
import secrets
from typing import List, Optional
from pydantic import BaseModel
from app.schemas.agent import AgentCreate

class BookingStatusUpdateRequest(BaseModel):
    status: str # "upcoming", "completed" (demo given), "no_show", "cancelled", etc.
    admin_notes: str | None = None

class BlockSlotRequest(BaseModel):
    blocked_date: str # YYYY-MM-DD
    slot_time: str | None = None # HH:MM or None for entire day
    reason: str | None = None

router = APIRouter()

class UserUpdateRequest(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    credits: int | None = None
    subscription_plan: str | None = None
    company_name: str | None = None
    industry: str | None = None
    status: str | None = None
    is_active: bool | None = None
    agent_name: str | None = None
    agent_language: str | None = None
    agent_voice: str | None = None
    agent_script: str | None = None
    agents: Optional[List[AgentCreate]] = None


@router.get("/stats")
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Return aggregated platform metrics for the admin dashboard."""
    # 1. Total users
    total_users_res = await db.execute(select(func.count(User.id)))
    total_users = total_users_res.scalar() or 0

    # 2. Users list for aggregation
    users_res = await db.execute(select(User))
    all_users = users_res.scalars().all()

    demo_users = sum(1 for u in all_users if (u.credits or 0) <= 50 or u.subscription_plan == "Demo")
    paid_users = total_users - demo_users

    total_credits = sum(u.credits or 0 for u in all_users)

    # 3. Active campaigns
    active_camp_res = await db.execute(
        select(func.count(Campaign.id)).where(Campaign.status.in_(["pending", "running", "scheduled"]))
    )
    active_campaigns = active_camp_res.scalar() or 0

    # 4. Total calls
    total_calls_res = await db.execute(select(func.count(Call.id)))
    total_calls = total_calls_res.scalar() or 0

    # 5. Plan distribution
    plan_counts: dict[str, int] = {}
    credits_by_plan_map: dict[str, int] = {}
    for u in all_users:
        plan = u.subscription_plan or "Starter"
        plan_counts[plan] = plan_counts.get(plan, 0) + 1
        credits_by_plan_map[plan] = credits_by_plan_map.get(plan, 0) + (u.credits or 0)

    plan_distribution = [{"name": p, "value": c} for p, c in plan_counts.items()]
    credits_by_plan = [{"name": p, "credits": int(c / 1000)} for p, c in credits_by_plan_map.items()]

    # 6. User growth trend (grouped by month)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    user_growth_map: dict[str, int] = {m: 0 for m in months}
    for u in all_users:
        if u.created_at:
            m_name = months[u.created_at.month - 1]
            user_growth_map[m_name] = user_growth_map.get(m_name, 0) + 1

    # Cumulative growth
    cumulative = 0
    user_growth = []
    current_month_idx = datetime.now(timezone.utc).month
    for i in range(max(0, current_month_idx - 6), current_month_idx):
        m = months[i]
        cumulative += user_growth_map[m]
        user_growth.append({"name": m, "users": max(cumulative, 1)})

    # 7. Revenue estimation
    starter_price, standard_price, pro_price = 49, 149, 499
    current_monthly_rev = sum(
        starter_price if u.subscription_plan == "Starter"
        else standard_price if u.subscription_plan == "Standard"
        else pro_price if u.subscription_plan == "Pro"
        else 0
        for u in all_users
    )
    
    revenue_data = [
        {"name": "Jan", "revenue": Math_round(current_monthly_rev * 0.3)},
        {"name": "Feb", "revenue": Math_round(current_monthly_rev * 0.5)},
        {"name": "Mar", "revenue": Math_round(current_monthly_rev * 0.7)},
        {"name": "Apr", "revenue": Math_round(current_monthly_rev * 0.85)},
        {"name": "May", "revenue": Math_round(current_monthly_rev * 0.95)},
        {"name": "Jun", "revenue": current_monthly_rev},
    ]

    # 8. Recent users (top 10)
    recent_users_res = await db.execute(
        select(User).options(selectinload(User.agents)).order_by(User.created_at.desc()).limit(10)
    )
    recent_users_list = recent_users_res.scalars().all()

    recent_users = [
        {
            "id": f"USR-{u.id}",
            "raw_id": u.id,
            "name": u.full_name or u.email or f"User #{u.id}",
            "email": u.email or "N/A",
            "mobile": u.phone_number or "N/A",
            "phone": u.phone_number or "N/A",
            "organization": u.full_name or "Independent",
            "plan": u.subscription_plan or "Starter",
            "credits": u.credits or 0,
            "type": "Demo" if (u.credits or 0) <= 50 or u.subscription_plan == "Demo" else "Regular",
            "status": "Active",
            "createdAt": u.created_at.isoformat() if u.created_at else datetime.now(timezone.utc).isoformat(),
            "agents": [
                {
                    "id": f"AGT-{a.id}",
                    "name": a.name,
                    "language": a.language,
                    "voice": a.voice,
                    "script": a.script,
                    "status": "Active"
                }
                for a in u.agents
            ] if u.agents else (
                [
                    {
                        "id": f"AGT-{u.id}",
                        "name": u.agent_name,
                        "language": u.agent_language or "English",
                        "voice": u.agent_voice or "Female 1",
                        "script": u.agent_script or "",
                        "status": "Active"
                    }
                ] if u.agent_name else []
            )
        }
        for u in recent_users_list
    ]

    # 9. Recent Activity
    recent_activities = []
    for u in recent_users_list[:5]:
        recent_activities.append({
            "id": f"ACT-{u.id}",
            "title": f"New user account registered: {u.email or u.full_name or u.id}",
            "time": u.created_at.strftime("%b %d, %H:%M") if u.created_at else "Recently",
            "type": "success"
        })

    return {
        "total_users": total_users,
        "paid_users": paid_users,
        "demo_users": demo_users,
        "total_credits": total_credits,
        "active_campaigns": active_campaigns,
        "total_calls": total_calls,
        "plan_distribution": plan_distribution,
        "credits_by_plan": credits_by_plan,
        "user_growth": user_growth,
        "revenue_data": revenue_data,
        "recent_users": recent_users,
        "recent_activities": recent_activities,
    }

def Math_round(val: float) -> int:
    return round(val)

@router.get("/users")
async def get_all_users(db: AsyncSession = Depends(get_db)):
    """Return list of all registered users."""
    res = await db.execute(select(User).options(selectinload(User.agents)).order_by(User.id.desc()))
    users = res.scalars().all()

    return [
        {
            "id": f"USR-{u.id}",
            "raw_id": u.id,
            "name": u.full_name or u.email or f"User #{u.id}",
            "email": u.email or "",
            "mobile": u.phone_number or "",
            "phone": u.phone_number or "",
            "organization": u.full_name or "Independent",
            "plan": u.subscription_plan or "Starter",
            "credits": u.credits or 0,
            "type": "Demo" if (u.credits or 0) <= 50 or u.subscription_plan == "Demo" else "Regular",
            "status": "Active" if getattr(u, "is_active", True) else "Inactive",
            "is_admin": u.is_admin,
            "createdAt": u.created_at.isoformat() if u.created_at else datetime.now(timezone.utc).isoformat(),
            "agents": [
                {
                    "id": f"AGT-{a.id}",
                    "name": a.name,
                    "language": a.language,
                    "voice": a.voice,
                    "script": a.script,
                    "status": "Active"
                }
                for a in u.agents
            ] if u.agents else (
                [
                    {
                        "id": f"AGT-{u.id}",
                        "name": u.agent_name,
                        "language": u.agent_language or "English (US)",
                        "voice": u.agent_voice or "Nova (ElevenLabs)",
                        "script": u.agent_script or "",
                        "status": "Active"
                    }
                ] if u.agent_name else []
            )
        }
        for u in users
    ]


@router.get("/contact-users")
async def get_contact_form_users(db: AsyncSession = Depends(get_db)):
    """Return list of all landing page appointment bookings."""
    res = await db.execute(select(ContactFormUser).order_by(ContactFormUser.created_at.desc()))
    users = res.scalars().all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "company": u.company or "N/A",
            "industry": u.industry or "N/A",
            "appointment_time": u.appointment_time.isoformat() if u.appointment_time else None,
            "status": u.status or "booked",
            "admin_notes": getattr(u, "admin_notes", None) or "",
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.put("/contact-users/{booking_id}/status")
async def update_booking_status(
    booking_id: int,
    req: BookingStatusUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    """Update appointment status (e.g. 'completed' / Demo Given, 'no_show', 'cancelled') and admin notes."""
    stmt = select(ContactFormUser).where(ContactFormUser.id == booking_id)
    res = await db.execute(stmt)
    booking = res.scalars().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking.status = req.status
    if req.admin_notes is not None:
        booking.admin_notes = req.admin_notes

    await db.commit()
    await db.refresh(booking)

    return {
        "success": True,
        "message": f"Booking #{booking_id} status updated to '{booking.status}'",
        "booking": {
            "id": booking.id,
            "status": booking.status,
            "admin_notes": booking.admin_notes
        }
    }


@router.get("/blocked-slots")
async def get_blocked_slots(db: AsyncSession = Depends(get_db)):
    """Return all admin-blocked dates and slots."""
    res = await db.execute(select(BlockedSlot).order_by(BlockedSlot.blocked_date.asc(), BlockedSlot.slot_time.asc()))
    blocks = res.scalars().all()
    return [
        {
            "id": b.id,
            "blocked_date": b.blocked_date,
            "slot_time": b.slot_time,
            "reason": b.reason or "Unavailable",
            "created_at": b.created_at.isoformat() if b.created_at else None
        }
        for b in blocks
    ]


@router.post("/blocked-slots", status_code=status.HTTP_201_CREATED)
async def block_slot(req: BlockSlotRequest, db: AsyncSession = Depends(get_db)):
    """Block a date or specific time slot from public availability."""
    new_block = BlockedSlot(
        blocked_date=req.blocked_date,
        slot_time=req.slot_time,
        reason=req.reason or "Unavailable"
    )
    db.add(new_block)
    await db.commit()
    await db.refresh(new_block)

    return {
        "success": True,
        "message": f"Blocked date {req.blocked_date}" + (f" slot {req.slot_time}" if req.slot_time else " (entire day)"),
        "blocked_slot": {
            "id": new_block.id,
            "blocked_date": new_block.blocked_date,
            "slot_time": new_block.slot_time,
            "reason": new_block.reason
        }
    }


@router.delete("/blocked-slots/{block_id}")
async def unblock_slot(block_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a date/slot block from un-availability list."""
    stmt = select(BlockedSlot).where(BlockedSlot.id == block_id)
    res = await db.execute(stmt)
    block = res.scalars().first()
    if not block:
        raise HTTPException(status_code=404, detail="Block entry not found")

    await db.delete(block)
    await db.commit()
    return {"success": True, "message": f"Unblocked slot #{block_id}"}


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreateRequest,
    db: AsyncSession = Depends(get_db)
):
    if user_data.email:
        stmt = select(User).where(User.email == user_data.email)
        result = await db.execute(stmt)
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered"
            )

    if user_data.phone_number:
        stmt = select(User).where(User.phone_number == user_data.phone_number)
        result = await db.execute(stmt)
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Phone number is already registered"
            )

    if not user_data.email and not user_data.phone_number:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either email or phone number"
        )

    raw_password = user_data.password or secrets.token_urlsafe(12)

    new_user = User(
        full_name=user_data.full_name,
        email=user_data.email,
        phone_number=user_data.phone_number,
        hashed_password=get_password_hash(raw_password),
        is_first_login=True,  # Prompt user to change password on first login

        is_admin=False,
        is_active=True,
        credits=user_data.credits if user_data.credits is not None else 2000,
        subscription_plan=user_data.subscription_plan or "Starter",
        company_name=user_data.company_name,
        industry=user_data.industry,
        agent_name=user_data.agent_name,
        agent_language=user_data.agent_language,
        agent_voice=user_data.agent_voice,
        agent_script=user_data.agent_script,
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    if getattr(user_data, "agents", None):
        for agent_data in user_data.agents:
            ag = Agent(
                user_id=new_user.id,
                name=agent_data.name,
                language=agent_data.language,
                voice=agent_data.voice,
                script=agent_data.script
            )
            db.add(ag)
        await db.commit()
    elif user_data.agent_name:
        # Only insert once — skip if already inserted above
        ag = Agent(
            user_id=new_user.id,
            name=user_data.agent_name,
            language=user_data.agent_language or "English",
            voice=user_data.agent_voice or "Meera",
            script=user_data.agent_script or ""
        )
        db.add(ag)
        await db.commit()

    if getattr(user_data, "phones", None):
        for phone_data in user_data.phones:
            pn = UserPhoneNumber(
                user_id=new_user.id,
                region=phone_data.region,
                phone_number=phone_data.phone_number,
                number_type=phone_data.number_type,
                provider_name=phone_data.provider_name,
                provider_account_id=phone_data.provider_account_id,
                api_key_auth_token=phone_data.api_key_auth_token,
                sip_id=phone_data.sip_id,
                sip_username=phone_data.sip_username,
                sip_password=phone_data.sip_password,
                status=phone_data.status,
                is_default=phone_data.is_default,
                max_concurrent_calls=getattr(phone_data, "max_concurrent_calls", 3),
                is_active=True,
            )
            db.add(pn)
        await db.commit()
    
    if user_data.email:

        try:
            notification_service.notify_account_created(new_user, raw_password)
        except Exception as e:
            print(f"Failed to send welcome email to {user_data.email}: {e}")

    
    return {
        "message": "User created successfully",
        "user": {
            "id": f"USR-{new_user.id}",
            "raw_id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "company_name": getattr(new_user, "company_name", None),
            "industry": getattr(new_user, "industry", None),
            "agent_name": getattr(new_user, "agent_name", None),
            "agent_language": getattr(new_user, "agent_language", None),
            "agent_voice": getattr(new_user, "agent_voice", None),
            "credits": new_user.credits,
            "subscription_plan": new_user.subscription_plan
        }
    }

@router.put("/users/{user_id}")
async def update_user_by_admin(
    user_id: str,
    update_data: UserUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    # Strip USR- prefix if passed
    raw_id = int(user_id.replace("USR-", "")) if "USR-" in user_id else int(user_id)
    
    stmt = select(User).where(User.id == raw_id)
    res = await db.execute(stmt)
    user = res.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_credits = user.credits or 0
    credits_changed = False
    plan_changed = False
    status_changed_to_active = False
    status_changed_to_inactive = False

    if update_data.full_name is not None:
        user.full_name = update_data.full_name
    if update_data.email is not None:
        user.email = update_data.email
    if update_data.phone_number is not None:
        user.phone_number = update_data.phone_number
    if update_data.credits is not None and update_data.credits != user.credits:
        user.credits = update_data.credits
        credits_changed = True
    if update_data.subscription_plan is not None and update_data.subscription_plan != user.subscription_plan:
        user.subscription_plan = update_data.subscription_plan
        plan_changed = True
    if update_data.company_name is not None:
        user.company_name = update_data.company_name
    if update_data.industry is not None:
        user.industry = update_data.industry

    # Handle status toggles
    if update_data.is_active is not None:
        if update_data.is_active and not getattr(user, "is_active", True):
            user.is_active = True
            status_changed_to_active = True
        elif not update_data.is_active and getattr(user, "is_active", True):
            user.is_active = False
            status_changed_to_inactive = True

    if update_data.status is not None:
        new_status = update_data.status.strip().lower()
        if new_status == "active" and not getattr(user, "is_active", True):
            user.is_active = True
            status_changed_to_active = True
        elif new_status in ("inactive", "suspended", "deactivated") and getattr(user, "is_active", True):
            user.is_active = False
            status_changed_to_inactive = True

    # Handle agent list updates
    if update_data.agents is not None:
        existing_agents_res = await db.execute(select(Agent).where(Agent.user_id == user.id))
        existing_agents = existing_agents_res.scalars().all()
        for old_agent in existing_agents:
            await db.delete(old_agent)
        await db.commit()

        for agent_data in update_data.agents:
            new_ag = Agent(
                user_id=user.id,
                name=agent_data.name,
                language=agent_data.language or "English",
                voice=agent_data.voice or "Meera",
                script=agent_data.script or ""
            )
            db.add(new_ag)
        await db.commit()

        if update_data.agents:
            primary = update_data.agents[0]
            user.agent_name = primary.name
            user.agent_language = primary.language or "English"
            user.agent_voice = primary.voice or "Meera"
            user.agent_script = primary.script or ""

    await db.commit()
    await db.refresh(user)

    # Trigger notification events
    if credits_changed or plan_changed:
        # If credits were increased/topped up or plan updated, send confirmation email
        if plan_changed or (update_data.credits is not None and update_data.credits > old_credits):
            notification_service.notify_plan_credit_updated(user)
        try:
            await notification_service.check_and_trigger_credit_notifications(db, user)
        except Exception as e:
            print(f"Error checking credit notifications on admin update: {e}")

    if status_changed_to_active:
        notification_service.notify_account_activated(user)
    elif status_changed_to_inactive:
        notification_service.notify_account_deactivated(user)


    return {
        "message": "User updated successfully",
        "user": {
            "id": f"USR-{user.id}",
            "raw_id": user.id,
            "name": user.full_name,
            "email": user.email,
            "company_name": getattr(user, "company_name", "CallingGen Corp"),
            "industry": getattr(user, "industry", "Technology & Software"),
            "credits": user.credits,
            "plan": user.subscription_plan,
            "status": "Active" if getattr(user, "is_active", True) else "Inactive"
        }
    }


@router.delete("/users/{user_id}")
async def delete_user_by_admin(
    user_id: str,
    db: AsyncSession = Depends(get_db)
):
    raw_id = int(user_id.replace("USR-", "")) if "USR-" in user_id else int(user_id)
    
    stmt = select(User).where(User.id == raw_id)
    res = await db.execute(stmt)
    user = res.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.commit()

    return {"message": f"User {user_id} deleted successfully"}

@router.put("/users/{user_id}")
async def update_user_by_admin(
    user_id: str,
    update_data: UserUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    # Strip USR- prefix if passed
    raw_id = int(user_id.replace("USR-", "")) if "USR-" in user_id else int(user_id)
    
    stmt = select(User).where(User.id == raw_id)
    res = await db.execute(stmt)
    user = res.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    old_credits = user.credits or 0
    credits_changed = False
    plan_changed = False
    status_changed_to_active = False
    status_changed_to_inactive = False

    if update_data.full_name is not None:
        user.full_name = update_data.full_name
    if update_data.email is not None:
        user.email = update_data.email
    if update_data.phone_number is not None:
        user.phone_number = update_data.phone_number
    if update_data.credits is not None and update_data.credits != user.credits:
        user.credits = update_data.credits
        credits_changed = True
    if update_data.subscription_plan is not None and update_data.subscription_plan != user.subscription_plan:
        user.subscription_plan = update_data.subscription_plan
        plan_changed = True
    if update_data.company_name is not None:
        user.company_name = update_data.company_name
    if update_data.industry is not None:
        user.industry = update_data.industry

    # Handle status toggles
    if update_data.is_active is not None:
        if update_data.is_active and not getattr(user, "is_active", True):
            user.is_active = True
            status_changed_to_active = True
        elif not update_data.is_active and getattr(user, "is_active", True):
            user.is_active = False
            status_changed_to_inactive = True

    if update_data.status is not None:
        new_status = update_data.status.strip().lower()
        if new_status == "active" and not getattr(user, "is_active", True):
            user.is_active = True
            status_changed_to_active = True
        elif new_status in ("inactive", "suspended", "deactivated") and getattr(user, "is_active", True):
            user.is_active = False
            status_changed_to_inactive = True

    await db.commit()
    await db.refresh(user)

    # Trigger notification events
    if credits_changed or plan_changed:
        # If credits were increased/topped up or plan updated, send confirmation email
        if plan_changed or (update_data.credits is not None and update_data.credits > old_credits):
            notification_service.notify_plan_credit_updated(user)
        try:
            await notification_service.check_and_trigger_credit_notifications(db, user)
        except Exception as e:
            print(f"Error checking credit notifications on admin update: {e}")

    if status_changed_to_active:
        notification_service.notify_account_activated(user)
    elif status_changed_to_inactive:
        notification_service.notify_account_deactivated(user)


    return {
        "message": "User updated successfully",
        "user": {
            "id": f"USR-{user.id}",
            "raw_id": user.id,
            "name": user.full_name,
            "email": user.email,
            "company_name": getattr(user, "company_name", "CallingGen Corp"),
            "industry": getattr(user, "industry", "Technology & Software"),
            "credits": user.credits,
            "plan": user.subscription_plan,
            "status": "Active" if getattr(user, "is_active", True) else "Inactive"
        }
    }


@router.delete("/users/{user_id}")
async def delete_user_by_admin(
    user_id: str,
    db: AsyncSession = Depends(get_db)
):
    raw_id = int(user_id.replace("USR-", "")) if "USR-" in user_id else int(user_id)
    
    stmt = select(User).where(User.id == raw_id)
    res = await db.execute(stmt)
    user = res.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.commit()

    return {"message": f"User {user_id} deleted successfully"}


@router.get("/contact-users")
async def get_admin_contact_users(db: AsyncSession = Depends(get_db)):
    """Fetch all contact form submissions and booked appointments for admin."""
    from app.models.contact_form_user import ContactFormUser
    result = await db.execute(select(ContactFormUser).order_by(ContactFormUser.created_at.desc()))
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "company": u.company or "N/A",
            "industry": u.industry or "N/A",
            "appointment_time": u.appointment_time.isoformat() if u.appointment_time else None,
            "status": u.status or "booked",
            "created_at": u.created_at.isoformat() if u.created_at else datetime.now(timezone.utc).isoformat()
        }
        for u in users
    ]

from app.models.contact_form_user import ContactFormUser


@router.get('/contact-users')
async def get_contact_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ContactFormUser).order_by(ContactFormUser.created_at.desc()))
    return result.scalars().all()



@router.get("/users/{user_id}/activity")
async def get_user_activity(user_id: str, db: AsyncSession = Depends(get_db)):
    """Fetch high-level activity stats for a specific user."""
    import zoneinfo
    
    raw_id = int(user_id.replace("USR-", "")) if "USR-" in user_id else int(user_id)
    
    # Verify user exists
    user_exists = await db.execute(select(User.id).where(User.id == raw_id))
    if not user_exists.scalars().first():
        raise HTTPException(status_code=404, detail="User not found")
        
    # Total campaigns
    camp_res = await db.execute(select(func.count(Campaign.id)).where(Campaign.user_id == raw_id))
    total_campaigns = camp_res.scalar() or 0
    
    # Calculate start of today in IST
    try:
        ist_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    except Exception:
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        
    now_ist = datetime.now(ist_tz)
    start_of_day_ist = datetime(now_ist.year, now_ist.month, now_ist.day, tzinfo=ist_tz)
    # Convert IST start-of-day to naive UTC because Call.started_at is naive UTC
    start_of_day_utc = start_of_day_ist.astimezone(timezone.utc).replace(tzinfo=None)
    
    # Today's Calls Base Query
    base_calls_query = (
        select(func.count(Call.id))
        .join(Campaign, Call.campaign_id == Campaign.id)
        .where(Campaign.user_id == raw_id)
        .where(Call.started_at >= start_of_day_utc)
    )
    
    # Total calls today
    calls_res = await db.execute(base_calls_query)
    total_calls_today = calls_res.scalar() or 0
    
    # Successful calls today
    succ_res = await db.execute(base_calls_query.where(Call.status == "completed"))
    successful_calls = succ_res.scalar() or 0
    
    # Failed calls today
    fail_res = await db.execute(base_calls_query.where(Call.status.in_(["failed", "error"])))
    failed_calls = fail_res.scalar() or 0
    
    return {
        "user_id": user_id,
        "total_campaigns": total_campaigns,
        "today": {
            "calls": total_calls_today,
            "successful": successful_calls,
            "failed": failed_calls
        }
    }


@router.get("/users/{user_id}/campaigns")
async def get_user_campaigns(user_id: str, db: AsyncSession = Depends(get_db)):
    """Fetch aggregated campaign stats for a specific user."""
    from app.models.contact import Contact
    
    raw_id = int(user_id.replace("USR-", "")) if "USR-" in user_id else int(user_id)
    
    # Verify user exists
    user_exists = await db.execute(select(User.id).where(User.id == raw_id))
    if not user_exists.scalars().first():
        raise HTTPException(status_code=404, detail="User not found")
        
    subq_contacts = (
        select(func.count(Contact.id))
        .where(Contact.campaign_id == Campaign.id)
        .scalar_subquery()
    )
    
    subq_calls = (
        select(func.count(Call.id))
        .where(Call.campaign_id == Campaign.id)
        .scalar_subquery()
    )
    
    subq_succ = (
        select(func.count(Call.id))
        .where(Call.campaign_id == Campaign.id)
        .where(Call.status == "completed")
        .scalar_subquery()
    )
    
    subq_fail = (
        select(func.count(Call.id))
        .where(Call.campaign_id == Campaign.id)
        .where(Call.status.in_(["failed", "error"]))
        .scalar_subquery()
    )
    
    stmt = (
        select(
            Campaign,
            subq_contacts.label("total_contacts"),
            subq_calls.label("calls_made"),
            subq_succ.label("successful_calls"),
            subq_fail.label("failed_calls")
        )
        .where(Campaign.user_id == raw_id)
        .order_by(Campaign.created_at.desc())
    )
    
    res = await db.execute(stmt)
    rows = res.all()
    
    result = []
    for row in rows:
        c = row.Campaign
        result.append({
            "id": c.id,
            "campaign_name": c.campaign_name,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "status": c.status,
            "total_contacts": row.total_contacts or 0,
            "calls_made": row.calls_made or 0,
            "successful_calls": row.successful_calls or 0,
            "failed_calls": row.failed_calls or 0
        })
        
    return result
