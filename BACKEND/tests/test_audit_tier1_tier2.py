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
async def test_internal_webhook_security(client: AsyncClient):
    """Verify internal webhooks require X-Internal-Secret."""
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

        c_res = await db.execute(select(User.credits).where(User.id == 101))
        assert c_res.scalar_one() == 20

        # Attempt to deduct 40 credits when only 20 remain -> Floor at 0, no negative balance
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(credits=case((User.credits >= 40, User.credits - 40), else_=0))
        )
        await db.commit()

        c_res2 = await db.execute(select(User.credits).where(User.id == 101))
        assert c_res2.scalar_one() == 0

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_enterprise_security_headers_and_request_id(client: AsyncClient):
    """Verify enterprise security headers, request tracing ID, and response timing are attached."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200

    # OWASP Security Headers
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert resp.headers.get("X-XSS-Protection") == "1; mode=block"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    # Distributed Tracing & APM Headers
    assert "X-Request-ID" in resp.headers
    assert resp.headers["X-Request-ID"].startswith("req_")
    assert "X-Response-Time" in resp.headers
    assert resp.headers["X-Response-Time"].endswith("ms")


@pytest.mark.asyncio
async def test_rate_limiter_protection(client: AsyncClient):
    """Verify rate limiter blocks automated brute-force attempts on sensitive endpoints."""
    # /api/auth/login threshold is 15 requests/min
    responses = []
    for _ in range(18):
        r = await client.post("/api/auth/login", json={"identifier": "attacker@example.com", "password": "wrong"})
        responses.append(r)

    # The later requests in the burst must be throttled with HTTP 429
    status_codes = [r.status_code for r in responses]
    assert 429 in status_codes

    rate_limited_resp = next(r for r in responses if r.status_code == 429)
    assert "Retry-After" in rate_limited_resp.headers
    assert rate_limited_resp.headers.get("RateLimit-Remaining") == "0"


@pytest.mark.asyncio
async def test_atomic_whatsapp_credit_service(db_session: AsyncSession):
    """Verify WhatsAppCreditService applies atomic decrements and protects wallet balances."""
    from app.services.whatsapp_credit_service import WhatsAppCreditService

    test_user = User(
        id=202,
        email="wa_wallet@callinggen.in",
        full_name="WA Wallet User",
        credits=40,
        hashed_password="hashed_pw",
    )
    db_session.add(test_user)
    await db_session.commit()

    # Deduct 15 credits atomically
    new_bal = await WhatsAppCreditService.deduct_credits(db_session, test_user, 15)
    assert new_bal == 25

    bal_check = await db_session.execute(select(User.credits).where(User.id == 202))
    assert bal_check.scalar_one() == 25

    # Deduct 30 credits (exceeds balance 25) -> floor at 0
    floored_bal = await WhatsAppCreditService.deduct_credits(db_session, test_user, 30)
    assert floored_bal == 0

    bal_check2 = await db_session.execute(select(User.credits).where(User.id == 202))
    assert bal_check2.scalar_one() == 0


@pytest.mark.asyncio
async def test_multitenancy_campaign_authorization(client: AsyncClient, db_session: AsyncSession):
    """Verify tenant isolation: User 1 cannot access, launch, or modify User 2's campaigns."""
    other_campaign = Campaign(
        id=8888,
        user_id=2,  # Belongs to User 2
        campaign_name="Tenant 2 Confidential Campaign",
        agent="Sales SDR",
        script="Secret Script",
        schedule_date="2026-10-01",
        schedule_time="10:00:00",
        status="scheduled"
    )
    db_session.add(other_campaign)
    await db_session.commit()

    # Client is authenticated as User 1
    # 1. View campaign -> 403 Forbidden
    resp_get = await client.get("/api/campaigns/8888")
    assert resp_get.status_code == 403
    assert "Not authorized" in resp_get.json()["detail"]

    # 2. Launch campaign -> 403 Forbidden
    resp_launch = await client.post("/api/campaigns/8888/launch")
    assert resp_launch.status_code == 403

    # 3. Pause campaign -> 403 Forbidden
    resp_pause = await client.post("/api/campaigns/8888/pause")
    assert resp_pause.status_code == 403


@pytest.mark.asyncio
async def test_multitenancy_report_authorization(client: AsyncClient, db_session: AsyncSession):
    """Verify IDOR protection: User 1 cannot access User 2's AI campaign report."""
    from app.models.report import Report

    other_report = Report(
        id=7777,
        user_id=2,  # Belongs to User 2
        title="Tenant 2 Q3 Strategy Report",
        start_date="2026-09-01",
        end_date="2026-09-20",
        content="Confidential financial report details",
        stats={"total": 500}
    )
    db_session.add(other_report)
    await db_session.commit()

    # Client is authenticated as User 1
    # View report -> 403 Forbidden
    resp_get = await client.get("/api/reports/7777")
    assert resp_get.status_code == 403
    assert "Not authorized" in resp_get.json()["detail"]


