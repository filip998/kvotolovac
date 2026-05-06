from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import close_db, init_db
from app.main import app
from app.models.schemas import ScrapeRuntimeSettings
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
    assert body["metadata"]["scraper_mode"] == settings.scraper_mode
    assert set(body["metadata"]["enabled_bookmakers"]) == {"mozzart", "meridian"}
    assert body["metadata"]["proxy_count"] == 0
    assert body["metadata"]["proxies_configured"] is False
    assert body["metadata"]["detail_modes"] == {
        "betole": settings.betole_detail_mode,
        "merkurxtip": settings.merkurxtip_detail_mode,
        "pinnbet": settings.pinnbet_detail_mode,
        "soccerbet": settings.soccerbet_detail_mode,
    }
    assert "scrape" in body["phase_durations_ms"]
    assert "normalize_threshold_odds" in body["phase_durations_ms"]
    assert "normalize_outcome_offers" in body["phase_durations_ms"]
    assert "persist_snapshot" in body["phase_durations_ms"]
    assert "resolve_events" in body["phase_durations_ms"]
    assert body["outcome_normalization"]["runs"] >= 1
    assert body["outcome_normalization"]["raw_outcome_offer_count"] >= 0
    assert body["event_resolver"]["candidate_count"] >= 0

    bm_ids = {s["bookmaker_id"] for s in body["scrapers"]}
    assert {"mozzart", "meridian"}.issubset(bm_ids)
    for entry in body["scrapers"]:
        assert entry["leagues_attempted"] >= 1
        assert entry["raw_items"] >= 0
        assert entry["matches_after_normalization"] >= 0
        assert entry["odds_count"] >= 0
        assert 0.0 <= entry["failure_rate"] <= 1.0
        assert entry["http"]["logical_requests"] >= 0
        assert isinstance(entry["requests"], list)

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
    assert parsed["metadata"]["enabled_bookmakers"]
    assert parsed["phase_durations_ms"]
    assert "outcome_normalization" in parsed
    assert "event_resolver" in parsed


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
    # Meridian only has the threshold-odds lane, so the patched scrape_odds
    # makes it fail every attempted task. Mozzart also advertises football
    # outcome offers in mock mode; that list-only lane still succeeds.
    assert by_bm["meridian"]["failure_rate"] == 1.0
    assert by_bm["mozzart"]["failure_rate"] == 0.5
    assert by_bm["meridian"]["raw_items"] == 0
    assert by_bm["mozzart"]["raw_items"] > 0


def test_http_request_aggregates_and_metadata_are_persisted_without_secrets(
    monkeypatch,
    tmp_path,
):
    secret_proxy = "http://user:secret-password@example.test:8080"
    monkeypatch.setattr(settings, "proxy_list", secret_proxy)
    runtime_settings = ScrapeRuntimeSettings(
        enabled_bookmakers=["betole"],
        enabled_sports=["football"],
        scrape_market_scope="all",
        analysis_markets=["football:football_result"],
        scrape_lookahead_hours=36,
        scrape_interval_minutes=10,
        max_middle_opportunities_per_market=10,
        rate_limit_per_second=1.0,
        meridian_rate_limit_per_second=2.0,
        soccerbet_detail_mode="partial",
        merkurxtip_detail_mode="full",
        pinnbet_detail_mode="partial",
        betole_detail_mode="partial",
        notification_gap_threshold=1.5,
        persist_inapp_notifications=False,
    )

    scraper_benchmarks.recorder.begin_cycle(
        "2026-05-06T12:00:00",
        runtime_settings=runtime_settings,
    )
    scraper_benchmarks.recorder.record_scrape_task(
        bookmaker_id="betole",
        duration_ms=50,
        raw_items=12,
        failed=False,
    )
    with scraper_benchmarks.recorder.scrape_request_context(
        bookmaker_id="betole",
        lane="outcome_offer",
        sport="football",
        league_id=None,
    ):
        scraper_benchmarks.recorder.record_http_request(
            method="GET",
            elapsed_ms=120,
            attempts=2,
            rate_limit_wait_ms=35,
            network_ms=70,
            status_codes=[429, 200],
            error=False,
        )
    scraper_benchmarks.recorder.record_phase_durations(
        scrape_duration_ms=50,
        cycle_duration_ms=80,
    )

    snapshot = scraper_benchmarks.recorder.publish(
        matches_per_bookmaker={"betole": 3},
        odds_per_bookmaker={"betole": 12},
        total_unique_matches=3,
    )

    assert snapshot.metadata is not None
    assert snapshot.metadata.proxies_configured is True
    assert snapshot.metadata.proxy_count == 1
    assert snapshot.metadata.analysis_markets == ["football:football_result"]
    scraper_row = snapshot.scrapers[0]
    assert scraper_row.http.logical_requests == 1
    assert scraper_row.http.attempts == 2
    assert scraper_row.http.retries == 1
    assert scraper_row.http.total_rate_limit_wait_ms == 35
    assert scraper_row.http.total_network_ms == 70
    assert scraper_row.http.status_classes == {"2xx": 1, "4xx": 1}
    assert len(scraper_row.requests) == 1
    request_row = scraper_row.requests[0]
    assert request_row.lane == "outcome_offer"
    assert request_row.sport == "football"
    assert request_row.method == "GET"

    out_dir = Path(settings.benchmark_dir)
    persisted = "\n".join(path.read_text() for path in out_dir.glob("*"))
    assert "secret-password" not in persisted
    assert secret_proxy not in persisted
