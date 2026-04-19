from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import close_db, init_db
from app.main import app
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
from app.services import scraper_benchmarks
from app.services.scheduler import Scheduler


@pytest.fixture(autouse=True)
async def setup_app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "benchmark_dir", str(tmp_path / "benchmarks"))
    # Reset the recorder's in-memory state per-test.
    scraper_benchmarks.recorder._latest = None
    scraper_benchmarks.recorder._reset()
    await init_db(settings.db_path)
    registry._scrapers.clear()
    for bm in ("mozzart", "meridian"):
        registry.register(MockScraper(bm))
    yield
    await close_db()


@pytest.fixture
async def client(setup_app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_benchmarks_404_before_first_cycle(client: AsyncClient):
    resp = await client.get("/api/v1/scraper-benchmarks")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_benchmarks_published_after_cycle(client: AsyncClient, tmp_path):
    scheduler = Scheduler(interval_minutes=60)
    await scheduler.run_cycle()

    resp = await client.get("/api/v1/scraper-benchmarks")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["cycle_started_at"] is not None
    assert body["cycle_finished_at"] is not None
    assert body["scrape_duration_ms"] >= 0
    assert body["cycle_duration_ms"] >= 0

    bm_ids = {s["bookmaker_id"] for s in body["scrapers"]}
    assert {"mozzart", "meridian"}.issubset(bm_ids)
    for entry in body["scrapers"]:
        assert entry["leagues_attempted"] >= 1
        assert entry["raw_items"] >= 0
        assert entry["matches_after_normalization"] >= 0
        assert entry["odds_count"] >= 0
        assert 0.0 <= entry["failure_rate"] <= 1.0

    # Files written
    out_dir = Path(settings.benchmark_dir)
    snapshots = sorted(out_dir.glob("cycle-*.json"))
    assert len(snapshots) == 1
    on_disk = json.loads(snapshots[0].read_text())
    assert on_disk["scrapers"], "snapshot file should contain per-scraper rows"

    ndjson = (out_dir / "cycles.ndjson").read_text().strip().splitlines()
    assert len(ndjson) == 1
    parsed = json.loads(ndjson[0])
    assert parsed["scrapers"]


@pytest.mark.asyncio
async def test_benchmarks_ndjson_appends_per_cycle(client: AsyncClient):
    scheduler = Scheduler(interval_minutes=60)
    await scheduler.run_cycle()
    await scheduler.run_cycle()

    ndjson_path = Path(settings.benchmark_dir) / "cycles.ndjson"
    lines = ndjson_path.read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # must be valid JSON


@pytest.mark.asyncio
async def test_failed_scraper_increments_failure_rate(monkeypatch, client: AsyncClient):
    # Replace meridian's scrape_odds with one that always raises
    async def boom(_self, _league_id):
        raise RuntimeError("simulated failure")

    meridian = registry.get("meridian")
    monkeypatch.setattr(MockScraper, "scrape_odds", boom)

    scheduler = Scheduler(interval_minutes=60)
    await scheduler.run_cycle()

    resp = await client.get("/api/v1/scraper-benchmarks")
    body = resp.json()
    by_bm = {s["bookmaker_id"]: s for s in body["scrapers"]}
    # Both bookmakers used the patched method; both fail.
    assert by_bm["meridian"]["failure_rate"] == 1.0
    assert by_bm["mozzart"]["failure_rate"] == 1.0
    assert by_bm["meridian"]["raw_items"] == 0
