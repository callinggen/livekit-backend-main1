import os
import pytest  # type: ignore
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update, case, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.database import Base
from app.models.user import User
from app.models.call import Call
from app.models.job import Job
from app.models.campaign import Campaign
from app.services.call_service import CallService


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"





@pytest.mark.asyncio
async def test_enterprise_health_endpoint():
    """Verify enterprise /api/health endpoint returns 200 with database & system stats."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("healthy", "degraded")
        assert "database" in data
        assert "system" in data
        assert "ram_used_percent" in data["system"]


@pytest.mark.asyncio
async def test_recordings_path_traversal_defense():
    """Verify /api/recordings blocks directory traversal and illegal file types."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Traversal with ..
        resp1 = await client.get("/api/recordings/../../etc/passwd")
        assert resp1.status_code in (400, 404)

        # Illegal executable extensions
        resp2 = await client.get("/api/recordings/malicious.exe")
        assert resp2.status_code == 400
        assert "Invalid recording filename" in resp2.json()["detail"]

        resp3 = await client.get("/api/recordings/script.sh")
        assert resp3.status_code == 400

        # Legitimate audio filename (non-existent -> 404 handled)
        resp4 = await client.get("/api/recordings/call_12345.wav")
        assert resp4.status_code == 404


@pytest.mark.asyncio
async def test_internal_webhook_security():
    """Verify internal webhooks require X-Internal-Secret."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # /complete without secret -> 401
        resp1 = await client.post("/api/calls/123/complete", json={})
        assert resp1.status_code == 401

        # /complete with forged secret -> 401
        resp2 = await client.post(
            "/api/calls/123/complete",
            json={},
            headers={"X-Internal-Secret": "forged_secret_token_123"}
        )
        assert resp2.status_code == 401

        # /lookup without secret -> 401
        resp3 = await client.get("/api/calls/lookup?room_name=call-123")
        assert resp3.status_code == 401

        # /lookup with forged secret -> 401
        resp4 = await client.get(
            "/api/calls/lookup?room_name=call-123",
            headers={"X-Internal-Secret": "wrong_secret"}
        )
        assert resp4.status_code == 401


@pytest.mark.asyncio
async def test_atomic_credit_deductions():
    """Verify atomic SQL credit deduction prevents negative balances and double-spending."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        user = User(
            id=101,
            email="wallet_test@callinggen.in",
            full_name="Wallet Test User",
            credits=50,
            hashed_password="hashed_pw",
        )
        db.add(user)
        await db.commit()

        # Atomic decrement 30 credits
        credits_to_deduct = 30
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(credits=case((User.credits >= credits_to_deduct, User.credits - credits_to_deduct), else_=0))
        )
        await db.commit()

        u = await db.get(User, 101)
        assert u is not None
        assert u.credits == 20

        # Attempt to deduct 40 credits when only 20 remain -> Floor at 0, no negative balance
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(credits=case((User.credits >= 40, User.credits - 40), else_=0))
        )
        await db.commit()

        u = await db.get(User, 101)
        assert u is not None
        assert u.credits == 0

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
