import os
from typing import List, Optional, Tuple
from pydantic import BaseModel
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

class CampaignMetric(BaseModel):
    name: str
    agent: str = "Voice AI SDR"
    total: int = 0
    completed: int = 0
    failed: int = 0
    hot: int = 0
    warm: int = 0
    cold: int = 0
    avg_duration: int = 0
    total_duration: int = 0
    comp_rate: float = 0.0

class ReportRequest(BaseModel):
    start_date: str
    end_date: str
    total_calls: int
    completed_calls: int
    failed_calls: int
    avg_duration_seconds: int
    total_duration_seconds: int = 0
    hot_leads: int
    warm_leads: int
    cold_leads: int
    campaign_names: List[str] = []
    agent_names: List[str] = []
    campaign_breakdown: List[CampaignMetric] = []
    credits_consumed: float = 0.0
    remaining_credits: int = 0
    appointments_booked: int = 0
    call_summaries: List[str] = []

SYSTEM_PROMPT = """You are an executive-level Voice AI Campaign Analyst and Performance Strategist.
Your task is to analyze outbound voice AI call performance data and generate a clear, highly analytical, and actionable campaign performance report.

Your output must be strictly formatted in clean Markdown with the following exact section headers:

## Executive Summary
A concise paragraph summarizing the campaign period, active campaigns and AI agents, total dials, connection rate, conversion quality (% hot/warm leads), credit efficiency, and primary bottleneck.

## Campaign & Agent Overview
Bullet points covering:
- **Active Campaigns**: List of campaign names analyzed and total campaign count.
- **AI Agent Personas**: Active agent names and target conversation objectives.
- **Credits & Financials**: Total credits consumed, credits per connected call, and remaining credit balance.

## Campaign-by-Campaign Performance Breakdown
For each individual campaign, provide a dedicated sub-heading (### Campaign: [Name]) with its specific performance metrics:
- **Volume & Connection**: Total dials, connected calls with connection %, failed/unanswered count.
- **Talk Time**: Average call duration and total talk time.
- **Lead Classification**: Hot, Warm, Cold lead counts and qualification %.
- **Campaign Insight**: Concise 1-sentence analytical observation for this campaign.

## Call Volume & Telephony Analysis
Bullet points covering:
- **Total Dials**: Total calls dialed and average dials per day.
- **Completion Rate**: Connected/completed calls vs. failed/unanswered calls with exact percentages.
- **Connectivity & Routing**: Unanswered, busy, or failed call analysis with telecom timing implications.
- **Duration Insight**: Average call length and total conversation talk time across the period.

## Lead Classification & Conversion Breakdown
Bullet points covering:
- **Hot Leads**: Count, % of total dials, and % of completed calls.
- **Warm Leads**: Count, % of total dials, and % of completed calls.
- **Cold Leads / Opt-Outs**: Count, % of total dials, and % of completed calls.
- **Conversion Efficiency**: Ratio of dials per qualified lead (Hot + Warm) and Cost Per Qualified Lead (CPQL) in credits.

## Recommendations & Action Items
Provide 4 to 5 prioritized, numbered, actionable recommendations:
1. Refine Opening Script Hook (Urgent)
2. Optimize Calling Windows & Timing
3. Accelerate Qualification Speed
4. List Cleansing & Volume Scaling
5. Monitor Duration vs. Lead Quality

Rules:
1. Be data-driven and analytical — use exact numbers, ratios, and percentages.
2. Never invent raw numbers; base all insights strictly on provided metrics and call summaries.
3. Keep the tone professional, objective, and executive-ready.
4. Output markdown directly without markdown code fence wrappers."""

def get_ai_client() -> Tuple[Optional[AsyncOpenAI], Optional[str]]:
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    
    if deepseek_key:
        return AsyncOpenAI(
            api_key=deepseek_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ), "deepseek-chat"
    elif openai_key:
        return AsyncOpenAI(
            api_key=openai_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        ), "gpt-4o-mini"
    return None, None

def _generate_deterministic_report(data: ReportRequest, duration_str: str, total_duration_str: str) -> str:
    comp_rate = round((data.completed_calls / data.total_calls) * 100, 1) if data.total_calls > 0 else 0
    hot_pct = round((data.hot_leads / data.total_calls) * 100, 1) if data.total_calls > 0 else 0
    warm_pct = round((data.warm_leads / data.total_calls) * 100, 1) if data.total_calls > 0 else 0
    cold_pct = round((data.cold_leads / data.total_calls) * 100, 1) if data.total_calls > 0 else 0
    qualified_leads = data.hot_leads + data.warm_leads
    conv_ratio = round(data.completed_calls / max(1, qualified_leads), 1) if qualified_leads > 0 else "N/A"
    cpql = round(data.credits_consumed / max(1, qualified_leads), 1) if qualified_leads > 0 else "N/A"
    campaigns_str = ", ".join(data.campaign_names) if data.campaign_names else "All Active Campaigns"
    agents_str = ", ".join(data.agent_names) if data.agent_names else "Voice AI Assistant"
    
    # Generate Campaign breakdown text
    campaign_sections = []
    if data.campaign_breakdown:
        for cb in data.campaign_breakdown:
            cb_rate = cb.comp_rate
            cb_hot_pct = round((cb.hot / cb.total) * 100, 1) if cb.total > 0 else 0
            cb_warm_pct = round((cb.warm / cb.total) * 100, 1) if cb.total > 0 else 0
            cb_cold_pct = round((cb.cold / cb.total) * 100, 1) if cb.total > 0 else 0
            tot_mins = cb.total_duration // 60
            tot_secs = cb.total_duration % 60
            avg_mins = cb.avg_duration // 60
            avg_secs = cb.avg_duration % 60
            
            campaign_sections.append(f"""### Campaign: {cb.name}
- **Agent Assigned**: {cb.agent}
- **Volume & Connection**: {cb.total} total dials | {cb.completed} connected ({cb_rate}%) | {cb.failed} failed/busy
- **Talk Time**: Avg {avg_mins}m {avg_secs}s ({cb.avg_duration}s) | Total talk time {tot_mins}m {tot_secs}s
- **Lead Classification**: {cb.hot} Hot Leads ({cb_hot_pct}%) | {cb.warm} Warm Leads ({cb_warm_pct}%) | {cb.cold} Cold ({cb_cold_pct}%)""")
    
    campaign_breakdown_text = "\n\n".join(campaign_sections) if campaign_sections else f"""### Campaign: {campaigns_str}
- **Agent Assigned**: {agents_str}
- **Volume & Connection**: {data.total_calls} total dials | {data.completed_calls} connected ({comp_rate}%) | {data.failed_calls} failed/busy
- **Talk Time**: Avg {duration_str} | Total talk time {total_duration_str}
- **Lead Classification**: {data.hot_leads} Hot Leads ({hot_pct}%) | {data.warm_leads} Warm Leads ({warm_pct}%) | {data.cold_leads} Cold ({cold_pct}%)"""

    return f"""## Executive Summary
During the reporting period from {data.start_date} to {data.end_date}, a total of {data.total_calls} calls were dialed across {len(data.campaign_names) or 1} campaign(s) powered by {agents_str}. The campaign achieved a {comp_rate}% connection rate ({data.completed_calls} completed calls) with {total_duration_str} of total conversational talk time. Lead qualification generated {data.hot_leads} Hot Leads ({hot_pct}%) and {data.warm_leads} Warm Leads ({warm_pct}%), consuming approximately {data.credits_consumed} credits with high conversion efficiency.

## Campaign & Agent Overview
- **Active Campaigns**: {campaigns_str} ({len(data.campaign_names) or 1} total campaign batch).
- **AI Agent Personas**: {agents_str} handling discovery, qualification, and appointment scheduling.
- **Credits & Financials**: {data.credits_consumed} total credits consumed ({round(data.credits_consumed / max(1, data.completed_calls), 1) if data.completed_calls > 0 else 1.0} credits per completed call), with {data.remaining_credits} credits currently available.

## Campaign-by-Campaign Performance Breakdown
{campaign_breakdown_text}

## Call Volume & Telephony Analysis
- **Total Dials**: {data.total_calls} total attempts logged during the reporting timeframe.
- **Completion Rate**: {comp_rate}% connection rate ({data.completed_calls} connected) vs {round(100 - comp_rate, 1)}% unanswered/busy ({data.failed_calls} attempts).
- **Connectivity & Routing**: {data.failed_calls} failed or busy attempts identify opportunities to optimize call scheduling windows.
- **Duration Insight**: Average call length of {duration_str} and cumulative talk time of {total_duration_str} demonstrates sufficient conversational depth.

## Lead Classification & Conversion Breakdown
- **Hot Leads**: {data.hot_leads} ({hot_pct}% of total dials, {round((data.hot_leads / data.completed_calls) * 100, 1) if data.completed_calls > 0 else 0}% of completed calls).
- **Warm Leads**: {data.warm_leads} ({warm_pct}% of total dials, {round((data.warm_leads / data.completed_calls) * 100, 1) if data.completed_calls > 0 else 0}% of completed calls).
- **Cold Leads / Opt-Outs**: {data.cold_leads} ({cold_pct}% of total dials).
- **Conversion Efficiency**: 1 qualified lead generated for every {conv_ratio} connected calls, at a Cost Per Qualified Lead of {cpql} credits.

## Recommendations & Action Items
1. **Refine Opening Script Hook (Urgent)**: Tighten the value proposition within the initial 10-15 seconds to maximize engagement.
2. **Optimize Calling Windows & Timing**: Target calling hours between 10:00 AM – 1:00 PM and 4:00 PM – 6:00 PM for peak connection rates.
3. **Accelerate Qualification Speed**: Position key qualifying questions earlier in the dialogue tree.
4. **List Cleansing & Volume Scaling**: Remove recurring unreachable numbers and scale daily dial batches.
5. **Monitor Duration vs. Lead Quality**: Track correlation between 60s+ calls and high-intent sales conversions."""

async def generate_campaign_report(data: ReportRequest) -> str:
    avg_mins = data.avg_duration_seconds // 60
    avg_secs = data.avg_duration_seconds % 60
    duration_str = f"{avg_mins}m {avg_secs}s ({data.avg_duration_seconds}s)"

    tot_hours = data.total_duration_seconds // 3600
    tot_mins = (data.total_duration_seconds % 3600) // 60
    tot_secs = data.total_duration_seconds % 60
    total_duration_str = f"{tot_hours}h {tot_mins}m {tot_secs}s" if tot_hours > 0 else f"{tot_mins}m {tot_secs}s"

    campaigns_str = ", ".join(data.campaign_names) if data.campaign_names else "General Outreach"
    agents_str = ", ".join(data.agent_names) if data.agent_names else "Voice AI SDR"

    # Format campaign breakdown for prompt
    campaign_breakdown_prompt = []
    for cb in data.campaign_breakdown:
        campaign_breakdown_prompt.append(
            f"- Campaign '{cb.name}' (Agent: {cb.agent}): {cb.total} dials, {cb.completed} connected ({cb.comp_rate}%), {cb.failed} failed, avg {cb.avg_duration}s, Leads: {cb.hot} Hot, {cb.warm} Warm, {cb.cold} Cold"
        )
    campaign_breakdown_text = "\n".join(campaign_breakdown_prompt) if campaign_breakdown_prompt else "Single aggregated campaign batch."

    summaries_text = "\n".join([f"- {s}" for s in data.call_summaries[:15]]) if data.call_summaries else "No detailed call transcripts available."

    user_prompt = f"""Generate a Performance Report for the following voice AI campaign data:

REPORTING WINDOW:
- Period: {data.start_date} to {data.end_date}

OVERALL METRICS:
- Total Calls Dialed: {data.total_calls}
- Completed / Connected Calls: {data.completed_calls}
- Failed / Unanswered / Busy: {data.failed_calls}
- Average Call Duration: {duration_str}
- Total Cumulative Talk Time: {total_duration_str}
- Total Credits Consumed: {data.credits_consumed} credits
- Remaining Balance: {data.remaining_credits} credits

INDIVIDUAL CAMPAIGN BREAKDOWNS:
{campaign_breakdown_text}

OVERALL LEAD CLASSIFICATION & PIPELINE:
- Hot Leads: {data.hot_leads}
- Warm Leads: {data.warm_leads}
- Cold Leads / Opt-Outs: {data.cold_leads}

SAMPLE CALL SUMMARIES & TRANSCRIPTS:
{summaries_text}

Analyze the data thoroughly, compute percentages/ratios, evaluate each campaign under '## Campaign-by-Campaign Performance Breakdown', and output the structured report."""

    client, model_name = get_ai_client()
    if not client or not model_name:
        return _generate_deterministic_report(data, duration_str, total_duration_str)

    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=2500
        )

        content = response.choices[0].message.content or ""
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        return content if content.strip() else _generate_deterministic_report(data, duration_str, total_duration_str)
    except Exception as e:
        print(f"[ReportService] AI LLM call error: {e}. Using structured analytical fallback.")
        return _generate_deterministic_report(data, duration_str, total_duration_str)

