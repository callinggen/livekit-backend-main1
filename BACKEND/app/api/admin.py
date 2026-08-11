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
from app.schemas.auth import UserCreateRequest
from app.core.security import get_password_hash
from app.services.email_service import email_service
from app.services.notification_service import notification_service
import secrets
from pydantic import BaseModel

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
            ]
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
        is_first_login=True,
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

    # Insert custom agent into the agents table to bypass default agent seeding
    if user_data.agent_name:
        custom_agent = Agent(
            user_id=new_user.id,
            name=user_data.agent_name,
            language=user_data.agent_language or "English",
            voice=user_data.agent_voice or "Meera",
            script=user_data.agent_script or ""
        )
        db.add(custom_agent)
        await db.commit()
    
    # Create associated Agent record for this specific account
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
        ag = Agent(
            user_id=new_user.id,
            name=user_data.agent_name,
            language=user_data.agent_language or "English (US)",
            voice=user_data.agent_voice or "Nova (ElevenLabs)",
            script=user_data.agent_script or ""
        )
        db.add(ag)
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
