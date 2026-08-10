from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models.agent import Agent
from app.models.user import User
from app.schemas.agent import AgentResponse, AgentCreate
from app.core.security import get_current_user

router = APIRouter()

DEFAULT_AGENTS = [
    {
        "name": "Voice-E (Tax Agent)",
        "language": "English",
        "voice": "Female 1",
        "script": """You are a professional and courteous Tax Verification Officer.

You are calling {{customer_name}} regarding a routine verification of their tax records.

Your objectives are:
1. Greet the customer politely by name.
2. Confirm you are speaking with the correct person.
3. Inform them that this is a routine tax verification call.
4. Ask whether all outstanding taxes for the current assessment period have already been paid.
5. If the customer confirms payment:
   - Thank them.
   - Ask if they have the payment reference or approximate payment date for verification.
   - Inform them that no further action may be required after verification.
6. If the customer says taxes have not yet been paid:
   - Politely remind them that payment may still be pending.
   - Ask whether they need assistance or information regarding the payment process.
7. If the customer is unsure:
   - Ask whether they would like to verify their records before making any statements.
8. Never threaten, pressure, or provide legal advice.
9. Remain calm, professional, and patient throughout the call."""
    },
    {
        "name": "Meera (Morning Tax)",
        "language": "English",
        "voice": "Female 1",
        "script": """AGENT IDENTITY:
You are Meera, a friendly and professional tax consultant calling on behalf of Morning Tax.
Speak at a moderate pace, never interrupt the customer, keep responses under two to three sentences whenever possible, and focus on helping rather than selling.

STEP 1 — GREETING & PERMISSION:
Greet the customer: "Hi, may I speak with {{customer_name}}?"
Wait for their response.
Then say: "Hi {{customer_name}}, this is Meera calling from Morning Tax. I know tax season has already passed, so I'll keep this brief. We're reaching out to technology professionals, stock compensation employees, and people with international income because many still qualify for tax savings or even refunds after filing. Do you have about a minute?"

PRIMARY GOAL: Book a fifteen-minute consultation with a Senior Tax Strategist."""
    },
    {
        "name": "Raj (Morning Tax)",
        "language": "English",
        "voice": "Male 1",
        "script": """AGENT IDENTITY:
You are Raj, a friendly and professional tax consultant calling on behalf of Morning Tax.
Speak at a moderate pace, never interrupt the customer, keep responses under two to three sentences whenever possible, and focus on helping rather than selling.

STEP 1 — GREETING & PERMISSION:
Greet the customer: "Hi, may I speak with {{customer_name}}?"
Wait for their response.
Then say: "Hi {{customer_name}}, this is Raj calling from Morning Tax. I know tax season has already passed, so I'll keep this brief. We're reaching out to technology professionals, stock compensation employees, and people with international income because many still qualify for tax savings or even refunds after filing. Do you have about a minute?"

PRIMARY GOAL: Book a fifteen-minute consultation with a Senior Tax Strategist."""
    }
]

@router.get("/", response_model=List[AgentResponse])
async def get_agents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Agent).where(Agent.user_id == current_user.id)
    result = await db.execute(stmt)
    agents = result.scalars().all()
    
    if not agents:
        # Prioritize custom agent created during user registration / admin setup
        if current_user.agent_name:
            custom_agent = Agent(
                user_id=current_user.id,
                name=current_user.agent_name,
                language=current_user.agent_language or "English (US)",
                voice=current_user.agent_voice or "Nova (ElevenLabs)",
                script=current_user.agent_script or ""
            )
            db.add(custom_agent)
            await db.commit()
            await db.refresh(custom_agent)
            return [custom_agent]

        new_agents = []
        for da in DEFAULT_AGENTS:
            ag = Agent(
                user_id=current_user.id,
                name=da["name"],
                language=da["language"],
                voice=da["voice"],
                script=da["script"]
            )
            db.add(ag)
            new_agents.append(ag)
        await db.commit()
        for ag in new_agents:
            await db.refresh(ag)
        agents = new_agents
    else:
        # If user has a custom agent configured on their User profile (e.g. from Admin creation),
        # ensure that custom agent exists in the agents table and filter out default template agents!
        if current_user.agent_name:
            has_custom = any(a.name == current_user.agent_name for a in agents)
            default_names = [da["name"] for da in DEFAULT_AGENTS]
            if not has_custom:
                custom_agent = Agent(
                    user_id=current_user.id,
                    name=current_user.agent_name,
                    language=current_user.agent_language or "English (US)",
                    voice=current_user.agent_voice or "Nova (ElevenLabs)",
                    script=current_user.agent_script or ""
                )
                db.add(custom_agent)
                await db.commit()
                await db.refresh(custom_agent)
                user_custom_agents = [a for a in agents if a.name not in default_names]
                agents = [custom_agent] + user_custom_agents
            else:
                user_custom_agents = [a for a in agents if a.name not in default_names]
                if user_custom_agents:
                    agents = user_custom_agents

    return agents

@router.post("/", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    agent_data: AgentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    new_agent = Agent(
        user_id=current_user.id,
        name=agent_data.name,
        language=agent_data.language,
        voice=agent_data.voice,
        script=agent_data.script
    )
    db.add(new_agent)
    await db.commit()
    await db.refresh(new_agent)
    return new_agent

