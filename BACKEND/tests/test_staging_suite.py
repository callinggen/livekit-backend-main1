import pytest
from httpx import AsyncClient

STAGING_BASE_URL = "https://stagingg.callinggen.in"


@pytest.mark.asyncio
async def test_staging_root_endpoint():
    """Verify Staging root landing page returns HTTP 200 OK."""
    async with AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(f"{STAGING_BASE_URL}/")
        assert resp.status_code == 200
        assert "CallingGen" in resp.text
        assert "AI Voice" in resp.text


@pytest.mark.asyncio
async def test_staging_login_page():
    """Verify Staging login page returns HTTP 200 OK."""
    async with AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(f"{STAGING_BASE_URL}/login")
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()


@pytest.mark.asyncio
async def test_staging_campaign_page():
    """Verify Staging campaign page returns HTTP 200 OK."""
    async with AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(f"{STAGING_BASE_URL}/campaign")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_staging_call_manager_page():
    """Verify Staging call-manager page returns HTTP 200 OK."""
    async with AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(f"{STAGING_BASE_URL}/call-manager")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_staging_pricing_page():
    """Verify Staging pricing page returns HTTP 200 OK."""
    async with AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(f"{STAGING_BASE_URL}/pricing")
        assert resp.status_code == 200
