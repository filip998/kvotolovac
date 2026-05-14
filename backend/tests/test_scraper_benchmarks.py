from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import close_db, init_db
from app.main import app
from app.models.schemas import (
    AutoResolutionRerunBatchCountsOut,
    AutoResolutionRerunBenchmarkOut,
    BenchmarkEventCoverageOut,
    BenchmarkSplitClusterOut,
    BenchmarkSplitDiagnosticsOut,
    BenchmarkSplitEventFragmentOut,
    BenchmarkSplitSportDiagnosticsOut,
    EventResolverBenchmarkOut,
    OpportunityDetailModeYieldOut,
    OpportunityAnalysisBenchmarkOut,
    OpportunityAnalysisRuleBenchmarkOut,
    OutcomeNormalizationBenchmarkOut,
    PersistenceBenchmarkOut,
    ScrapeRuntimeSettings,
)
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
    assert body["metadata"]["bookmaker_rate_limits"] == {}
    assert body["metadata"]["scrape_type_rate_limits"] == {}
    assert body["metadata"]["detail_modes"] == {
        "betole": settings.betole_detail_mode,
        "merkurxtip": settings.merkurxtip_detail_mode,
        "pinnbet": settings.pinnbet_detail_mode,
        "soccerbet": settings.soccerbet_detail_mode,
        "starbet": settings.starbet_detail_mode,
    }
    assert (
        body["metadata"]["max_middle_opportunities_per_market"]
        == settings.max_middle_opportunities_per_market
    )
    assert body["metadata"]["enable_fitted_middles"] is True
    assert body["metadata"]["min_fitted_middle_ev_percent"] == 0.0
    assert "scrape" in body["phase_durations_ms"]
    assert "normalize_threshold_odds" in body["phase_durations_ms"]
    assert "normalize_outcome_offers" in body["phase_durations_ms"]
    assert "persist_snapshot" in body["phase_durations_ms"]
    assert "resolve_events" in body["phase_durations_ms"]
    assert body["outcome_normalization"]["runs"] >= 1
    assert body["outcome_normalization"]["raw_outcome_offer_count"] >= 0
    assert body["event_resolver"]["candidate_count"] >= 0
    assert body["event_resolver"]["normalized_odds_rows_scanned"] >= 0
    assert body["event_resolver"]["normalized_outcome_offer_rows_scanned"] >= 0
    assert body["event_resolver"]["source_match_lookup_count"] >= 0
    assert body["event_resolver"]["source_match_scored_source_count"] >= 0
    assert body["event_resolver"]["source_match_index_candidate_count"] >= 0
    assert body["event_resolver"]["source_match_fallback_scan_count"] >= 0
    assert body["opportunity_analysis"]["loaded_offer_count"] >= 0
    assert body["opportunity_analysis"]["opportunity_count"] >= 0
    assert isinstance(body["opportunity_analysis"]["rules"], list)
    assert isinstance(body["opportunity_analysis"]["detail_mode_yield"], list)
    assert isinstance(body["event_coverage"], list)
    assert body["event_split_diagnostics"]["split_candidate_count"] >= 0
    assert body["event_split_diagnostics"]["overmerge_candidate_count"] >= 0
    assert isinstance(body["event_split_diagnostics"]["sports"], list)
    assert isinstance(body["sports"], list)
    assert isinstance(body["outcome_normalization"]["run_details"], list)
    assert isinstance(body["outcome_normalization"]["bookmakers"], list)
    assert isinstance(
        body["outcome_normalization"]["top_football_event_buckets"],
        list,
    )
    assert isinstance(body["event_resolver"]["top_source_match_slots"], list)
    assert body["auto_resolution_rerun"]["rerun_performed"] in {True, False}
    assert isinstance(body["auto_resolution_rerun"]["reasons"], list)
    assert body["auto_resolution_rerun"]["before"]["normalized_threshold_odds"] >= 0
    assert body["auto_resolution_rerun"]["after"]["normalized_threshold_odds"] >= 0
    assert body["persistence"]["wall_ms"] >= 0
    assert isinstance(body["persistence"]["row_counts"], dict)
    if body["event_coverage"]:
        coverage_row = body["event_coverage"][0]
        assert coverage_row["bookmaker_id"]
        assert coverage_row["sport"]
        assert coverage_row["normalized_events"] >= 0
        assert coverage_row["not_matched_events"] >= 0

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
        assert isinstance(entry["sports"], list)
        for sport_row in entry["sports"]:
            assert sport_row["sport"]
            assert sport_row["matches_after_normalization"] >= 0
            assert sport_row["not_matched_events"] >= 0
            assert 0.0 <= sport_row["match_rate"] <= 1.0

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
    assert "auto_resolution_rerun" in parsed
    assert "opportunity_analysis" in parsed
    assert "event_coverage" in parsed
    assert "event_split_diagnostics" in parsed
    assert "sports" in parsed


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
    monkeypatch.setattr(settings, "bookmaker_rate_limits", "betole:0.5")
    monkeypatch.setattr(
        settings,
        "scrape_type_rate_limits",
        "betole:outcome_offer:partial:0.25",
    )
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
        sport="football",
        lane="outcome_offer",
    )
    with scraper_benchmarks.recorder.scrape_request_context(
        bookmaker_id="betole",
        lane="outcome_offer",
        sport="football",
        league_id=None,
    ):
        scraper_benchmarks.recorder.record_http_request(
            method="GET",
            url="https://api.example.test/offer/GetFixtures?token=secret-token",
            elapsed_ms=120,
            attempts=2,
            rate_limit_wait_ms=35,
            network_ms=70,
            status_codes=[429, 200],
            error=False,
        )
        scraper_benchmarks.recorder.record_http_request(
            method="GET",
            url="https://api.example.test/Offer/GetEventMarkets?eventIds=1&token=secret-token",
            elapsed_ms=80,
            attempts=1,
            rate_limit_wait_ms=5,
            network_ms=60,
            status_codes=[200],
            error=False,
        )
    scraper_benchmarks.recorder.record_phase_durations(
        scrape_duration_ms=50,
        cycle_duration_ms=80,
    )
    scraper_benchmarks.recorder.record_opportunity_analysis(
        OpportunityAnalysisBenchmarkOut(
            canonical_offer_load_ms=7,
            primary_match_lookup_ms=2,
            grouping_ms=1,
            two_way_arbitrage_ms=3,
            loaded_offer_count=5,
            same_market_group_count=2,
            candidate_pair_count=4,
            publishable_candidate_count=1,
            opportunity_count=1,
            detail_mode_yield=[
                OpportunityDetailModeYieldOut(
                    bookmaker_id="betole",
                    detail_mode="partial",
                    opportunity_count=1,
                    opportunity_leg_count=2,
                    market_counts={"football_result_double_chance": 1},
                )
            ],
            rules=[
                OpportunityAnalysisRuleBenchmarkOut(
                    sport="football",
                    market_type="football_total_goals",
                    rule="same_line_arbitrage",
                    group_count=1,
                    offer_count=2,
                    candidate_pair_count=1,
                    publishable_candidate_count=1,
                    opportunity_count=1,
                )
            ],
        )
    )
    scraper_benchmarks.recorder.record_event_split_diagnostics(
        BenchmarkSplitDiagnosticsOut(
            split_candidate_count=1,
            events_in_split_candidates=2,
            members_in_split_candidates=5,
            sports=[
                BenchmarkSplitSportDiagnosticsOut(
                    sport="football",
                    split_candidate_count=1,
                    events_in_split_candidates=2,
                    members_in_split_candidates=5,
                )
            ],
            top_split_candidates=[
                BenchmarkSplitClusterOut(
                    sport="football",
                    reason_code="same_side_conflicting_opponent",
                    shared_side="home",
                    start_time="2026-05-06T12:00:00",
                    max_start_delta_minutes=0.0,
                    score=76.0,
                    events=[
                        BenchmarkSplitEventFragmentOut(
                            resolved_event_id="evt-left",
                            primary_match_id="match-a",
                            start_time="2026-05-06T12:00:00",
                            display_home_team="Team Alpha",
                            display_away_team="Team Beta",
                            display_league_name="League",
                            method="exact",
                            confidence=1.0,
                            member_count=2,
                            bookmaker_ids=["book-a", "book-b"],
                            match_ids=["match-a", "match-b"],
                        ),
                        BenchmarkSplitEventFragmentOut(
                            resolved_event_id="evt-right",
                            primary_match_id="match-c",
                            start_time="2026-05-06T12:00:00",
                            display_home_team="Team Alpha",
                            display_away_team="Team Gamma",
                            display_league_name="League",
                            method="exact",
                            confidence=1.0,
                            member_count=3,
                            bookmaker_ids=["book-c"],
                            match_ids=["match-c"],
                        ),
                    ],
                )
            ],
        )
    )
    scraper_benchmarks.recorder.record_auto_resolution_rerun(
        AutoResolutionRerunBenchmarkOut(
            rerun_performed=True,
            reasons=["auto_aliases"],
            team_review_cases_seen_count=2,
            auto_review_cases_approved_count=1,
            anchored_auto_review_count=1,
            aliases_requested_count=1,
            aliases_applied_count=1,
            before=AutoResolutionRerunBatchCountsOut(
                normalized_threshold_odds=10,
                normalized_outcome_offers=4,
                unresolved_diagnostics=3,
                team_review_cases=2,
            ),
            after=AutoResolutionRerunBatchCountsOut(
                normalized_threshold_odds=11,
                normalized_outcome_offers=4,
                unresolved_diagnostics=2,
                team_review_cases=1,
            ),
            delta=AutoResolutionRerunBatchCountsOut(
                normalized_threshold_odds=1,
                unresolved_diagnostics=-1,
                team_review_cases=-1,
            ),
        )
    )
    scraper_benchmarks.recorder.record_persistence(
        PersistenceBenchmarkOut(
            wall_ms=9,
            upsert_odds_ms=3,
            commit_ms=2,
            row_counts={"odds": 12, "outcome_offers": 0},
        )
    )

    snapshot = scraper_benchmarks.recorder.publish(
        matches_per_bookmaker={"betole": 3},
        odds_per_bookmaker={"betole": 12},
        total_unique_matches=3,
        matches_per_bookmaker_sport={("betole", "football"): 3},
        odds_per_bookmaker_sport={("betole", "football"): 12},
        event_coverage=[
            BenchmarkEventCoverageOut(
                bookmaker_id="betole",
                sport="football",
                normalized_events=3,
                matched_events=2,
                unmatched_events=1,
                not_matched_events=1,
                match_rate=0.6667,
            )
        ],
    )

    assert snapshot.metadata is not None
    assert snapshot.metadata.proxies_configured is True
    assert snapshot.metadata.proxy_count == 1
    assert snapshot.metadata.analysis_markets == ["football:football_result"]
    assert snapshot.metadata.bookmaker_rate_limits == {"betole": 0.5}
    assert snapshot.metadata.scrape_type_rate_limits == {
        "betole:outcome_offer:partial": 0.25
    }
    scraper_row = snapshot.scrapers[0]
    assert scraper_row.http.logical_requests == 2
    assert scraper_row.http.attempts == 3
    assert scraper_row.http.retries == 1
    assert scraper_row.http.total_rate_limit_wait_ms == 40
    assert scraper_row.http.total_network_ms == 130
    assert scraper_row.http.status_classes == {"2xx": 2, "4xx": 1}
    assert len(scraper_row.requests) == 2
    requests_by_endpoint = {row.endpoint: row for row in scraper_row.requests}
    fixtures_request = requests_by_endpoint["/offer/GetFixtures"]
    assert fixtures_request.lane == "outcome_offer"
    assert fixtures_request.sport == "football"
    assert fixtures_request.method == "GET"
    assert fixtures_request.logical_requests == 1
    assert requests_by_endpoint["/Offer/GetEventMarkets"].logical_requests == 1
    assert len(scraper_row.sports) == 1
    sport_row = scraper_row.sports[0]
    assert sport_row.sport == "football"
    assert sport_row.duration_ms == 50
    assert sport_row.raw_items == 12
    assert sport_row.matches_after_normalization == 3
    assert sport_row.matched_events == 2
    assert sport_row.unmatched_events == 1
    assert sport_row.not_matched_events == 1
    assert snapshot.event_coverage[0].bookmaker_id == "betole"
    assert snapshot.opportunity_analysis.loaded_offer_count == 5
    assert snapshot.opportunity_analysis.candidate_pair_count == 4
    assert snapshot.opportunity_analysis.detail_mode_yield[0].bookmaker_id == "betole"
    assert snapshot.opportunity_analysis.detail_mode_yield[0].detail_mode == "partial"
    assert snapshot.opportunity_analysis.detail_mode_yield[0].market_counts == {
        "football_result_double_chance": 1
    }
    assert snapshot.opportunity_analysis.rules[0].rule == "same_line_arbitrage"
    assert snapshot.event_split_diagnostics.split_candidate_count == 1
    assert snapshot.auto_resolution_rerun.rerun_performed is True
    assert snapshot.auto_resolution_rerun.reasons == ["auto_aliases"]
    assert (
        snapshot.auto_resolution_rerun.before.normalized_threshold_odds == 10
    )
    assert snapshot.auto_resolution_rerun.delta.unresolved_diagnostics == -1
    assert snapshot.event_split_diagnostics.sports[0].sport == "football"
    assert snapshot.persistence.wall_ms == 9
    assert snapshot.persistence.row_counts["odds"] == 12
    assert (
        snapshot.event_split_diagnostics.top_split_candidates[0].events[
            0
        ].resolved_event_id
        == "evt-left"
    )
    assert snapshot.sports[0].sport == "football"
    assert snapshot.sports[0].matched_events == 2

    out_dir = Path(settings.benchmark_dir)
    persisted = "\n".join(path.read_text() for path in out_dir.glob("*"))
    assert "secret-password" not in persisted
    assert secret_proxy not in persisted
    assert "secret-token" not in persisted
    assert '"opportunity_analysis"' in persisted
    assert '"persistence"' in persisted


def test_opportunity_analysis_benchmark_defaults_and_serialization():
    metrics = OpportunityAnalysisBenchmarkOut()

    payload = metrics.model_dump()

    assert payload["loaded_offer_count"] == 0
    assert payload["candidate_pair_count"] == 0
    assert payload["rules"] == []
    assert payload["detail_mode_yield"] == []


def test_event_resolver_extraction_benchmark_defaults_and_serialization():
    metrics = EventResolverBenchmarkOut()

    payload = metrics.model_dump()

    assert payload["extract_raw_odds_sources_ms"] == 0
    assert payload["extract_raw_outcome_sources_ms"] == 0
    assert payload["extract_normalized_odds_candidates_ms"] == 0
    assert payload["extract_normalized_outcome_candidates_ms"] == 0
    assert payload["extract_source_match_ms"] == 0
    assert payload["raw_odds_rows_scanned"] == 0
    assert payload["normalized_odds_candidates_emitted"] == 0
    assert payload["normalized_outcome_candidates_emitted"] == 0
    assert payload["source_match_lookup_count"] == 0
    assert payload["source_match_source_count"] == 0
    assert payload["source_match_scored_source_count"] == 0
    assert payload["source_match_index_candidate_count"] == 0
    assert payload["source_match_exact_url_hit_count"] == 0
    assert payload["source_match_listed_pair_hit_count"] == 0
    assert payload["source_match_unordered_pair_hit_count"] == 0
    assert payload["source_match_fallback_scan_count"] == 0
    assert payload["source_match_max_sources_per_lookup"] == 0
    assert payload["source_match_truncated_slot_count"] == 0
    assert payload["top_source_match_slots"] == []
    assert payload["football_raw_candidate_count"] == 0


def test_persistence_benchmark_defaults_and_serialization():
    metrics = PersistenceBenchmarkOut()

    payload = metrics.model_dump()

    assert payload["wall_ms"] == 0
    assert payload["begin_transaction_ms"] == 0
    assert payload["upsert_snapshot_persisting_ms"] == 0
    assert payload["upsert_leagues_ms"] == 0
    assert payload["upsert_matches_ms"] == 0
    assert payload["upsert_snapshot_matches_ms"] == 0
    assert payload["upsert_sources_ms"] == 0
    assert payload["upsert_odds_ms"] == 0
    assert payload["insert_odds_history_ms"] == 0
    assert payload["upsert_outcome_offers_ms"] == 0
    assert payload["insert_unresolved_odds_ms"] == 0
    assert payload["insert_team_review_cases_ms"] == 0
    assert payload["insert_auto_approved_team_reviews_ms"] == 0
    assert payload["update_auto_approved_reviews_ms"] == 0
    assert payload["upsert_snapshot_persisted_ms"] == 0
    assert payload["commit_ms"] == 0
    assert payload["row_counts"] == {}


def test_outcome_normalization_benchmark_defaults_and_serialization():
    metrics = OutcomeNormalizationBenchmarkOut()

    payload = metrics.model_dump()

    assert payload["runs"] == 0
    assert payload["team_review_proxy_rows"] == 0
    assert payload["football_event_canonical_conflict_skip_count"] == 0
    assert (
        payload["football_event_canonical_conflict_fuzzy_score_avoided_count"] == 0
    )
    assert payload["team_review_proxy_slot_resolution_ms"] == 0
    assert payload["team_review_proxy_case_build_ms"] == 0
    assert payload["team_review_proxy_resolve_team_cache_hits"] == 0
    assert payload["team_review_proxy_slot_candidate_search_count"] == 0
    assert payload["team_review_proxy_global_candidate_cache_hits"] == 0
    assert payload["row_iteration_ms"] == 0
    assert payload["event_resolution_offer_count"] == 0
    assert payload["football_event_time_slot_count"] == 0
    assert payload["run_details"] == []
    assert payload["bookmakers"] == []
    assert payload["top_football_event_buckets"] == []


def test_auto_resolution_rerun_benchmark_defaults_and_serialization():
    metrics = AutoResolutionRerunBenchmarkOut()

    payload = metrics.model_dump()

    assert payload["rerun_performed"] is False
    assert payload["rerun_skipped"] is False
    assert payload["decision"] == "not_needed"
    assert payload["decision_reason"] == ""
    assert payload["estimated_affected_row_count"] == 0
    assert payload["affected_row_rerun_threshold"] == 0
    assert payload["merge_affected_row_count"] == 0
    assert payload["merge_affected_row_rerun_threshold"] == 0
    assert payload["reasons"] == []
    assert payload["team_review_cases_seen_count"] == 0
    assert payload["aliases_requested_count"] == 0
    assert payload["aliases_applied_count"] == 0
    assert payload["pending_merge_count"] == 0
    assert payload["applied_merge_count"] == 0
    assert payload["before"]["normalized_threshold_odds"] == 0
    assert payload["after"]["normalized_outcome_offers"] == 0
    assert payload["delta"]["unresolved_diagnostics"] == 0
