import os
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from openai import AsyncOpenAI

from app.database import get_db
from app.models.call import Call
from app.models.contact import Contact
from app.models.report import Report

router = APIRouter()

@router.get("/reports/generate")
async def generate_report(start_date: str, end_date: str, db: AsyncSession = Depends(get_db)):
    try:
        # Parse dates (frontend sends YYYY-MM-DD)
        start_dt = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")

        # Get calls within date range
        calls_result = await db.execute(
            select(Call).where(and_(Call.started_at >= start_dt, Call.started_at <= end_dt))
        )
        calls = calls_result.scalars().all()

        total_calls = len(calls)
        if total_calls == 0:
            return {
                "report": "No calls were recorded in the selected date range. Please try selecting a wider date range to generate a meaningful report.",
                "stats": {"total": 0}
            }

        completed = sum(1 for c in calls if c.status == "completed")
        failed = sum(1 for c in calls if c.status == "failed")
        hot_leads = sum(1 for c in calls if c.category == "HOT")
        warm_leads = sum(1 for c in calls if c.category == "WARM")
        cold_leads = sum(1 for c in calls if c.category == "COLD")
        
        avg_duration = sum((c.duration or 0) for c in calls) / total_calls if total_calls > 0 else 0
        avg_duration_str = f"{int(avg_duration // 60)}m {int(avg_duration % 60)}s"

        # Generate prompt for AI
        prompt = (
            f"You are an AI data analyst for CallingGen. Generate a highly professional performance report based on the following aggregate call data from {start_date} to {end_date}.\n\n"
            f"Data:\n"
            f"- Total Calls Made: {total_calls}\n"
            f"- Completed Calls: {completed}\n"
            f"- Failed/Unanswered: {failed}\n"
            f"- Hot Leads: {hot_leads}\n"
            f"- Warm Leads: {warm_leads}\n"
            f"- Cold Leads/Opt-Outs: {cold_leads}\n"
            f"- Average Call Duration: {avg_duration_str}\n\n"
            "CRITICAL RULES:\n"
            "1. You MUST NOT hallucinate, invent, or assume ANY data, numbers, metrics, or trends that are not explicitly provided in the Data section above.\n"
            "2. Your analysis MUST be strictly derived ONLY from the numbers provided.\n"
            "3. If you make recommendations, they must be logical deductions based strictly on the provided data.\n\n"
            "Format the report using Markdown with exactly the following headers:\n"
            "### Executive Summary\n"
            "### Call Volume Analysis\n"
            "### Lead Classification Breakdown\n"
            "### Recommendations & Action Items\n\n"
            "Make it concise, insightful, and professional. Do not add introductory or concluding remarks outside these headers."
        )

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if deepseek_key:
            client = AsyncOpenAI(
                api_key=deepseek_key,
                base_url="https://api.deepseek.com/v1"
            )
            response = await client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.1
            )
            report_text = response.choices[0].message.content or "Failed to generate report."
        else:
            report_text = (
                "### Executive Summary\n"
                "AI generation requires DEEPSEEK_API_KEY in the environment. Here is the raw data summary.\n\n"
                "### Call Volume Analysis\n"
                f"Total Calls: {total_calls}. Completed: {completed}. Failed: {failed}.\n\n"
                "### Lead Classification Breakdown\n"
                f"Hot: {hot_leads}, Warm: {warm_leads}, Cold: {cold_leads}.\n\n"
                "### Recommendations & Action Items\n"
                "Configure DEEPSEEK_API_KEY for full AI insights."
            )

        stats_data = {
            "total": total_calls,
            "completed": completed,
            "failed": failed,
            "hot": hot_leads,
            "warm": warm_leads,
            "cold": cold_leads
        }

        # Save the report to the database
        title = f"CallingGen Report ({start_date} to {end_date})"
        db_report = Report(
            title=title,
            start_date=start_date,
            end_date=end_date,
            content=report_text,
            stats=stats_data,
            generated_at=datetime.utcnow()
        )
        db.add(db_report)
        await db.commit()
        await db.refresh(db_report)

        return {
            "report": report_text,
            "stats": stats_data,
            "id": db_report.id
        }
    except Exception as e:
        print(f"Report generation error: {e}")
        return {"report": f"An error occurred while generating the report: {str(e)}", "stats": None}

@router.get("/reports")
async def get_reports(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(
            select(Report).order_by(Report.generated_at.desc())
        )
        reports = result.scalars().all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "generated_at": r.generated_at.isoformat(),
            }
            for r in reports
        ]
    except Exception as e:
        print(f"Error fetching reports: {e}")
        return []

@router.get("/reports/{report_id}")
async def get_report(report_id: int, db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(
            select(Report).where(Report.id == report_id)
        )
        report = result.scalars().first()
        if not report:
            return {"error": "Report not found"}
        
        return {
            "id": report.id,
            "title": report.title,
            "start_date": report.start_date,
            "end_date": report.end_date,
            "content": report.content,
            "stats": report.stats,
            "generated_at": report.generated_at.isoformat(),
        }
    except Exception as e:
        print(f"Error fetching report {report_id}: {e}")
        return {"error": str(e)}
