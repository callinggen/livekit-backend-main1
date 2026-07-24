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
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.4
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

        return {
            "report": report_text,
            "stats": {
                "total": total_calls,
                "completed": completed,
                "failed": failed,
                "hot": hot_leads,
                "warm": warm_leads,
                "cold": cold_leads
            }
        }
    except Exception as e:
        print(f"Report generation error: {e}")
        return {"report": f"An error occurred while generating the report: {str(e)}", "stats": None}
