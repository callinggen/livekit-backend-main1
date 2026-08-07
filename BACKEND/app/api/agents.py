from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models.agent import Agent
from app.models.user import User
from app.schemas.agent import AgentResponse
from app.core.security import get_current_user

router = APIRouter()

@router.get("/", response_model=List[AgentResponse])
async def get_agents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Agent).where(Agent.user_id == current_user.id)
    result = await db.execute(stmt)
    agents = result.scalars().all()
    return agents
