from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import init_db, close_db
from app.main import app
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
from app.services.scheduler import scheduler


@pytest.fixture(autouse=True)
async def setup_app():
    """Set up fresh DB and register scrapers before each test."""
    await init_db(":memory:")
    # Clear and re-register scrapers
    registry._scrapers.clear()
    for bm in ("mozzart", "meridian", "maxbet"):
        registry.register(MockScraper(bm))
    yield
    await close_db()


@pytest.fixture
async def client(setup_app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "KvotoLovac"


@pytest.mark.asyncio
async def test_status_endpoint(client: AsyncClient):
    resp = await client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_trigger_scrape(client: AsyncClient):
    resp = await client.post("/api/v1/scrape/trigger")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matches_scraped"] > 0
    assert data["odds_scraped"] > 0
    assert data["discrepancies_found"] > 0


@pytest.mark.asyncio
async def test_list_matches_after_scrape(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/matches")
    assert resp.status_code == 200
    matches = resp.json()
    assert len(matches) >= 4


@pytest.mark.asyncio
async def test_get_match_detail(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    matches_resp = await client.get("/api/v1/matches")
    match_id = matches_resp.json()[0]["id"]

    resp = await client.get(f"/api/v1/matches/{match_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == match_id


@pytest.mark.asyncio
async def test_get_match_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/matches/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_match_odds(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    matches_resp = await client.get("/api/v1/matches")
    match_id = matches_resp.json()[0]["id"]

    resp = await client.get(f"/api/v1/matches/{match_id}/odds")
    assert resp.status_code == 200
    assert len(resp.json()) > 0


@pytest.mark.asyncio
async def test_match_history(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    matches_resp = await client.get("/api/v1/matches")
    match_id = matches_resp.json()[0]["id"]

    resp = await client.get(f"/api/v1/matches/{match_id}/history")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_discrepancies(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/discrepancies")
    assert resp.status_code == 200
    discs = resp.json()
    assert len(discs) > 0


@pytest.mark.asyncio
async def test_discrepancy_filters(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/discrepancies?market_type=player_points&min_gap=1.0")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_discrepancy_detail(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    discs_resp = await client.get("/api/v1/discrepancies")
    disc_id = discs_resp.json()[0]["id"]

    resp = await client.get(f"/api/v1/discrepancies/{disc_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == disc_id


@pytest.mark.asyncio
async def test_discrepancy_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/discrepancies/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_leagues(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/leagues")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_bookmakers(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/bookmakers")
    assert resp.status_code == 200
    bms = resp.json()
    assert len(bms) == 3
