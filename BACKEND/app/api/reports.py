import os
import asyncio
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from openai import AsyncOpenAI

from app.database import get_db
from app.models.call import Call
from app.models.contact import Contact
from app.models.report import Report
from app.models.campaign import Campaign
from app.models.user import User
from app.core.security import get_current_user
from app.services.report_service import generate_campaign_report, ReportRequest, CampaignMetric

router = APIRouter()

@router.get("/reports/generate")
async def generate_report(
    start_date: str,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        # Check credits: 1 credit required per report generation
        user_credits = current_user.credits or 0
        if user_credits < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient credits. Generating an AI report requires 1 credit. Please recharge your credits to proceed."
            )

        if not end_date:
            end_date = start_date

        # Parse dates (frontend sends YYYY-MM-DD)
        start_dt = datetime.strptime(f"{start_date.strip()} 00:00:00", "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(f"{end_date.strip()} 23:59:59", "%Y-%m-%d %H:%M:%S")

        # Get calls with Campaign details within date range for current user
        calls_result = await db.execute(
            select(Call, Campaign)
            .join(Contact, Call.contact_id == Contact.id)
            .join(Campaign, Contact.campaign_id == Campaign.id)
            .where(
                and_(
                    Call.started_at >= start_dt,
                    Call.started_at <= end_dt,
                    or_(Campaign.user_id == current_user.id, Campaign.user_id.is_(None))
                )
            )
        )
        call_rows = calls_result.all()
        calls = [row[0] for row in call_rows]
        campaigns = [row[1] for row in call_rows if len(row) > 1 and row[1]]

        total_calls = len(calls)
        if total_calls == 0:
            return {
                "report": "No calls were recorded in the selected date range. Please try selecting a wider date range to generate a meaningful report.",
                "stats": {"total": 0},
                "credits_deducted": 0,
                "remaining_credits": user_credits,
            }

        completed = sum(1 for c in calls if c.status == "completed")
        failed = sum(1 for c in calls if c.status == "failed")
        hot_leads = sum(1 for c in calls if c.category == "HOT")
        warm_leads = sum(1 for c in calls if c.category == "WARM")
        cold_leads = sum(1 for c in calls if c.category == "COLD")
        
        total_duration = sum((c.duration or 0) for c in calls)
        avg_duration = total_duration / total_calls if total_calls > 0 else 0
        date_desc = start_date if start_date == end_date else f"{start_date} to {end_date}"

        # Distinct campaigns and agents
        campaign_names = sorted(list(set(cmp.campaign_name for cmp in campaigns if cmp and cmp.campaign_name)))
        agent_names = sorted(list(set(cmp.agent for cmp in campaigns if cmp and cmp.agent)))
        if not agent_names:
            agent_names = ["CallingGen Voice SDR"]

        # Aggregate metrics for each individual campaign
        campaign_map = {}
        for row in call_rows:
            c = row[0]
            cmp = row[1] if len(row) > 1 else None
            c_name = cmp.campaign_name if cmp and cmp.campaign_name else "General Campaign"
            c_agent = cmp.agent if cmp and cmp.agent else "CallingGen Voice SDR"
            
            if c_name not in campaign_map:
                campaign_map[c_name] = {
                    "name": c_name,
                    "agent": c_agent,
                    "total": 0,
                    "completed": 0,
                    "failed": 0,
                    "hot": 0,
                    "warm": 0,
                    "cold": 0,
                    "total_duration": 0,
                }
            
            cm = campaign_map[c_name]
            cm["total"] += 1
            if c.status == "completed":
                cm["completed"] += 1
            elif c.status == "failed":
                cm["failed"] += 1
            
            if c.category == "HOT":
                cm["hot"] += 1
            elif c.category == "WARM":
                cm["warm"] += 1
            elif c.category == "COLD":
                cm["cold"] += 1
            
            cm["total_duration"] += (c.duration or 0)

        campaign_breakdown = []
        for cm in campaign_map.values():
            avg_d = int(cm["total_duration"] / cm["total"]) if cm["total"] > 0 else 0
            comp_r = round((cm["completed"] / cm["total"]) * 100, 1) if cm["total"] > 0 else 0.0
            campaign_breakdown.append(
                CampaignMetric(
                    name=cm["name"],
                    agent=cm["agent"],
                    total=cm["total"],
                    completed=cm["completed"],
                    failed=cm["failed"],
                    hot=cm["hot"],
                    warm=cm["warm"],
                    cold=cm["cold"],
                    avg_duration=avg_d,
                    total_duration=cm["total_duration"],
                    comp_rate=comp_r
                )
            )

        # Credit calculation
        call_credits = round(total_duration / 60, 1)
        credits_consumed = round(call_credits + 1.0, 1)

        # Get current user record from db for remaining credits
        user_stmt = select(User).where(User.id == current_user.id)
        user_res = await db.execute(user_stmt)
        db_user = user_res.scalars().first()
        if db_user:
            db_user.credits = max(0, (db_user.credits or 0) - 1)
            remaining_credits = db_user.credits
        else:
            remaining_credits = max(0, (current_user.credits or 0) - 1)

        # Extract sample call summaries & notes
        call_summaries = [
            (c.summary or c.transcript or f"Lead {c.category or 'General'}: duration {c.duration}s with status {c.status}")
            for c in calls
            if (c.summary or c.transcript or (c.duration and c.duration > 0))
        ][:20]

        report_req = ReportRequest(
            start_date=start_date,
            end_date=end_date,
            total_calls=total_calls,
            completed_calls=completed,
            failed_calls=failed,
            avg_duration_seconds=int(avg_duration),
            total_duration_seconds=int(total_duration),
            hot_leads=hot_leads,
            warm_leads=warm_leads,
            cold_leads=cold_leads,
            campaign_names=campaign_names,
            agent_names=agent_names,
            campaign_breakdown=campaign_breakdown,
            credits_consumed=credits_consumed,
            remaining_credits=remaining_credits,
            call_summaries=call_summaries
        )

        report_text = await generate_campaign_report(report_req)

        stats_data = {
            "total": total_calls,
            "completed": completed,
            "failed": failed,
            "hot": hot_leads,
            "warm": warm_leads,
            "cold": cold_leads,
            "avg_duration": int(avg_duration),
            "total_duration": int(total_duration),
            "campaigns_count": len(campaign_names) or 1,
            "campaign_names": campaign_names,
            "agent_names": agent_names,
            "campaign_breakdown": [cb.model_dump() for cb in campaign_breakdown],
            "credits_consumed": credits_consumed,
            "remaining_credits": remaining_credits,
        }

        # Save the report to the database
        db_report = Report(
            user_id=current_user.id,
            title=f"AI Performance Report ({date_desc})",
            start_date=start_date,
            end_date=end_date,
            content=report_text,
            stats=stats_data,
            generated_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        db.add(db_report)
        await db.commit()
        await db.refresh(db_report)

        return {
            "report": report_text,
            "stats": stats_data,
            "id": db_report.id,
            "credits_deducted": 1,
            "remaining_credits": remaining_credits,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Report generation error: {e}")
        return {"report": f"An error occurred while generating the report: {str(e)}", "stats": None}

def format_dt_iso(dt: Any = None) -> str:
    if not dt:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).isoformat()
        return dt.isoformat()
    try:
        return str(dt)
    except Exception:
        return datetime.now(timezone.utc).isoformat()

@router.get("/reports")
async def get_reports(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(Report)
            .where(or_(Report.user_id == current_user.id, Report.user_id.is_(None)))
            .order_by(Report.generated_at.desc())
        )
        reports = result.scalars().all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "generated_at": format_dt_iso(r.generated_at),
            }
            for r in reports
        ]
    except Exception as e:
        print(f"Error fetching reports: {e}")
        return []

@router.get("/reports/{report_id}")
async def get_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(Report).where(Report.id == report_id)
        )
        report = result.scalars().first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        return {
            "id": report.id,
            "title": report.title,
            "start_date": report.start_date,
            "end_date": report.end_date,
            "content": report.content,
            "stats": report.stats,
            "generated_at": format_dt_iso(report.generated_at),
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching report {report_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = await db.execute(
            select(Report).where(Report.id == report_id)
        )
        report = result.scalars().first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        if report.user_id and report.user_id != current_user.id and not current_user.is_admin:
            raise HTTPException(status_code=403, detail="Not authorized to delete this report")
        
        await db.delete(report)
        await db.commit()
        return {"message": "Report deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting report {report_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

