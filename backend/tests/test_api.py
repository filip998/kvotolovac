from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import init_db, close_db
from app.main import app
from app.models.schemas import RawOddsData
from app.scrapers.base import BaseScraper
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
    assert data["scan"]["in_progress"] is False
    assert data["scan"]["phase"] == "idle"


@pytest.mark.asyncio
async def test_trigger_scrape(client: AsyncClient):
    resp = await client.post("/api/v1/scrape/trigger")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matches_scraped"] > 0
    assert data["odds_scraped"] > 0
    assert data["discrepancies_found"] > 0


@pytest.mark.asyncio
async def test_trigger_scrape_rejects_when_cycle_is_already_running(client: AsyncClient):
    class SlowScraper(BaseScraper):
        def get_bookmaker_id(self) -> str:
            return "slow"

        def get_bookmaker_name(self) -> str:
            return "Slow"

        def get_supported_leagues(self) -> list[str]:
            return ["euroleague"]

        async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
            await asyncio.sleep(0.05)
            return [
                RawOddsData(
                    bookmaker_id="slow",
                    league_id=league_id,
                    home_team="Olympiacos",
                    away_team="Real Madrid",
                    market_type="player_points",
                    player_name="Sasha Vezenkov",
                    threshold=18.5,
                    over_odds=1.9,
                    under_odds=1.9,
                    start_time="2030-01-01T20:00:00+00:00",
                )
            ]

    registry._scrapers.clear()
    registry.register(SlowScraper())

    cycle_task = asyncio.create_task(scheduler.run_cycle())
    for _ in range(10):
        if scheduler.is_cycle_in_progress:
            break
        await asyncio.sleep(0.01)

    assert scheduler.is_cycle_in_progress is True

    resp = await client.post("/api/v1/scrape/trigger")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Scrape already in progress"

    await cycle_task


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
    assert "middle_profit_margin" in discs[0]


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
    assert "middle_profit_margin" in resp.json()


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
