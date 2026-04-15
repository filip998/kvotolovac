from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import close_db, get_db, init_db
from app.main import app
from app.models.schemas import (
    MatchingReviewDiagnostic,
    NormalizedOdds,
    RawOddsData,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from app.scrapers.base import BaseScraper
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
from app.services.league_registry import resolve_league
from app.services.scheduler import scheduler
from app.services.normalizer import normalize_odds_with_diagnostics, normalize_team_name
from app.store import odds_store


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
    assert "available_bookmakers" in matches[0]
    assert len(matches[0]["available_bookmakers"]) > 0


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
    resp = await client.get("/api/v1/discrepancies?market_type=player_points&min_gap=1.0&bookmaker_ids=meridian")
    assert resp.status_code == 200
    for row in resp.json():
        assert "meridian" in {row["bookmaker_a_id"], row["bookmaker_b_id"]}


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


@pytest.mark.asyncio
async def test_list_unresolved_odds(client: AsyncClient):
    batch_scraped_at = "2026-04-13T16:36:09.440629"
    await odds_store.upsert_bookmaker("admiralbet", "AdmiralBet")
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="admiralbet",
            raw_league_id="AdmiralBet ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="P. Nikolic",
            raw_team_name="Borac Cacak",
            normalized_team_name="Borac Cacak",
            start_time="2026-04-13T16:00:00+00:00",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
            available_matchups_same_slot=["Dubai vs Buducnost"],
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    resp = await client.get("/api/v1/unresolved-odds?bookmaker_ids=admiralbet")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["bookmaker_name"] == "AdmiralBet"
    assert data[0]["reason_code"] == "no_canonical_matchup_for_team_at_slot"


@pytest.mark.asyncio
async def test_list_matches_can_filter_by_bookmaker(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")

    resp = await client.get("/api/v1/matches?bookmaker_ids=meridian")

    assert resp.status_code == 200
    for match in resp.json():
        assert any(book["id"] == "meridian" for book in match["available_bookmakers"])


@pytest.mark.asyncio
async def test_matching_review_summary_and_cases(client: AsyncClient, league_registry_file):
    batch_scraped_at = "2026-04-16T17:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")
    await odds_store.upsert_match(
        "match-bulgaria",
        "bulgaria_nbl",
        "Rilski Sportist",
        "Levski Sofia",
        start_time=batch_scraped_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="match-bulgaria",
            bookmaker_id="mozzart",
            league_id="bulgaria_nbl",
            home_team="Rilski Sportist",
            away_team="Levski Sofia",
            market_type="game_total",
            threshold=161.5,
            over_odds=1.85,
            under_odds=1.95,
            start_time=batch_scraped_at,
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.insert_matching_review_case(
        MatchingReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            suggested_league_id="bulgaria_nbl",
            match_id="match-bulgaria",
            home_team="Rilski Sportist",
            away_team="Levski Sofia",
            start_time=batch_scraped_at,
            reason_code="league_inferred_from_event_context",
            confidence="high",
            evidence=["League votes: Bulgaria NBL x1, NBL x1"],
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    summary_resp = await client.get("/api/v1/matching-review/summary")
    cases_resp = await client.get("/api/v1/matching-review/cases")

    assert summary_resp.status_code == 200
    assert summary_resp.json()["pending_reviews"] == 1
    assert any(
        row["league_id"] == "bulgaria_nbl"
        for row in summary_resp.json()["leagues"]
    )

    assert cases_resp.status_code == 200
    assert len(cases_resp.json()) == 1
    assert cases_resp.json()[0]["suggested_league_name"] == "Bulgaria NBL"


@pytest.mark.asyncio
async def test_approve_matching_review_case_saves_bookmaker_alias(
    client: AsyncClient,
    league_registry_file,
):
    batch_scraped_at = "2026-04-16T17:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")
    case_id = await odds_store.insert_matching_review_case(
        MatchingReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            suggested_league_id="bulgaria_nbl",
            match_id="match-bulgaria",
            home_team="Rilski Sportist",
            away_team="Levski Sofia",
            start_time=batch_scraped_at,
            reason_code="league_inferred_from_event_context",
            confidence="high",
            evidence=["League votes: Bulgaria NBL x1, NBL x1"],
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/matching-review/cases/{case_id}/approve")
    cases_resp = await client.get("/api/v1/matching-review/cases?status=approved")

    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_league_id"] == "bulgaria_nbl"
    assert resolve_league("NBL", bookmaker_id="meridian").league_id == "bulgaria_nbl"
    assert cases_resp.status_code == 200
    assert cases_resp.json()[0]["status"] == "approved"

    normalized, unresolved, reviews, team_reviews = normalize_odds_with_diagnostics(
        [
            RawOddsData(
                bookmaker_id="mozzart",
                league_id="Bulgarian NBL",
                home_team="Rilski Sportist",
                away_team="Levski Sofia",
                market_type="game_total",
                threshold=161.5,
                over_odds=1.85,
                under_odds=1.95,
                start_time=batch_scraped_at,
            ),
            RawOddsData(
                bookmaker_id="meridian",
                league_id="NBL",
                home_team="Rilski Sportist",
                away_team="Levski Sofia",
                market_type="game_total",
                threshold=162.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time=batch_scraped_at,
            ),
        ]
    )

    assert unresolved == []
    assert len({offer.match_id for offer in normalized}) == 1
    assert reviews == []
    assert team_reviews == []


@pytest.mark.asyncio
async def test_approve_matching_review_case_marks_sibling_cases_approved(
    client: AsyncClient,
    league_registry_file,
):
    batch_scraped_at = "2026-04-16T18:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")

    first_case_id = await odds_store.insert_matching_review_case(
        MatchingReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            suggested_league_id="bulgaria_nbl",
            match_id="match-bulgaria-1",
            home_team="Rilski Sportist",
            away_team="Levski Sofia",
            start_time=batch_scraped_at,
            reason_code="league_inferred_from_event_context",
            confidence="high",
            evidence=["League votes: Bulgaria NBL x2"],
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.insert_matching_review_case(
        MatchingReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            suggested_league_id="bulgaria_nbl",
            match_id="match-bulgaria-2",
            home_team="Beroe",
            away_team="Shumen",
            start_time=batch_scraped_at,
            reason_code="league_inferred_from_event_context",
            confidence="high",
            evidence=["League votes: Bulgaria NBL x2"],
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(
        f"/api/v1/matching-review/cases/{first_case_id}/approve"
    )
    approved_resp = await client.get("/api/v1/matching-review/cases?status=approved")
    pending_resp = await client.get("/api/v1/matching-review/cases?status=pending")

    assert approve_resp.status_code == 200
    assert approved_resp.status_code == 200
    assert len(approved_resp.json()) == 2
    assert {case["status"] for case in approved_resp.json()} == {"approved"}
    assert pending_resp.status_code == 200
    assert pending_resp.json() == []


@pytest.mark.asyncio
async def test_matching_review_summary_falls_back_to_latest_review_snapshot(
    client: AsyncClient,
):
    batch_scraped_at = "2026-04-16T19:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")
    await odds_store.upsert_match(
        "match-bulgaria",
        "bulgaria_nbl",
        "Rilski Sportist",
        "Levski Sofia",
        batch_scraped_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="match-bulgaria",
            bookmaker_id="meridian",
            league_id="bulgaria_nbl",
            home_team="Rilski Sportist",
            away_team="Levski Sofia",
            market_type="game_total",
            threshold=161.5,
            over_odds=1.85,
            under_odds=1.95,
            start_time=batch_scraped_at,
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.insert_matching_review_case(
        MatchingReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            suggested_league_id="bulgaria_nbl",
            match_id="match-bulgaria",
            home_team="Rilski Sportist",
            away_team="Levski Sofia",
            start_time=batch_scraped_at,
            reason_code="league_inferred_from_event_context",
            confidence="high",
            evidence=["League votes: Bulgaria NBL x2"],
        ),
        scraped_at=batch_scraped_at,
    )

    summary_resp = await client.get("/api/v1/matching-review/summary")
    cases_resp = await client.get("/api/v1/matching-review/cases")

    assert summary_resp.status_code == 200
    assert summary_resp.json()["total_matches"] == 1
    assert summary_resp.json()["pending_reviews"] == 1
    assert cases_resp.status_code == 200
    assert len(cases_resp.json()) == 1


@pytest.mark.asyncio
async def test_team_review_cases_and_approval(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T20:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "scope_league_id": "bulgaria_nbl",
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 92,
                "evidence": ["Exact start time: 2026-04-16T20:00:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    cases_resp = await client.get("/api/v1/team-review/cases")
    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    approved_resp = await client.get("/api/v1/team-review/cases?status=approved")

    assert cases_resp.status_code == 200
    assert len(cases_resp.json()) == 1
    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_name"] == "Rilski Sportist"
    assert normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian") == "Rilski Sportist"
    assert approved_resp.status_code == 200
    assert approved_resp.json()[0]["status"] == "approved"


@pytest.mark.asyncio
async def test_team_review_case_can_be_declined(client: AsyncClient):
    batch_scraped_at = "2026-04-16T21:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "scope_league_id": None,
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "medium",
                "similarity_score": 82,
                "evidence": ["Candidate team: Rilski Sportist"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    decline_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/decline")
    pending_resp = await client.get("/api/v1/team-review/cases?status=pending")

    assert decline_resp.status_code == 200
    assert decline_resp.json()["status"] == "declined"
    assert pending_resp.status_code == 200
    assert pending_resp.json() == []


@pytest.mark.asyncio
async def test_team_review_approval_rejects_unscoped_alias(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T21:30:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "scope_league_id": None,
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "medium",
                "similarity_score": 82,
                "evidence": ["Candidate team: Rilski Sportist"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    pending_resp = await client.get("/api/v1/team-review/cases?status=pending")

    assert approve_resp.status_code == 409
    assert "resolved competition scope" in approve_resp.json()["detail"]
    assert normalize_team_name("Rilski Sport.", None, "meridian") == "Rilski Sport."
    assert pending_resp.status_code == 200
    assert [row["id"] for row in pending_resp.json()] == [case_id]


@pytest.mark.asyncio
async def test_team_review_approval_only_updates_clicked_case(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T22:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")

    shared_payload = {
        "bookmaker_id": "meridian",
        "raw_league_id": "NBL",
        "normalized_raw_league_id": "nbl",
        "scope_league_id": "bulgaria_nbl",
        "raw_team_name": "Rilski Sport.",
        "normalized_raw_team_name": "Rilski Sport.",
        "suggested_team_name": "Rilski Sportist",
        "reason_code": "candidate_team_match_same_start_time",
        "confidence": "high",
        "similarity_score": 92,
        "evidence": ["Exact start time: 2026-04-16T22:00:00+00:00"],
        "status": "pending",
    }
    first_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                **shared_payload,
                "start_time": "2026-04-16T22:00:00+00:00",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    second_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                **shared_payload,
                "start_time": "2026-04-16T23:00:00+00:00",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{first_case_id}/approve")
    approved_resp = await client.get("/api/v1/team-review/cases?status=approved")
    pending_resp = await client.get("/api/v1/team-review/cases?status=pending")

    assert approve_resp.status_code == 200
    assert approved_resp.status_code == 200
    assert [row["id"] for row in approved_resp.json()] == [first_case_id]
    assert pending_resp.status_code == 200
    assert [row["id"] for row in pending_resp.json()] == [second_case_id]


@pytest.mark.asyncio
async def test_team_review_approval_handles_null_scraped_at(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T23:30:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "scope_league_id": "bulgaria_nbl",
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 92,
                "evidence": ["Exact start time: 2026-04-16T23:30:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    db = await get_db()
    await db.execute("UPDATE team_review_cases SET scraped_at = NULL WHERE id = ?", (case_id,))
    await db.commit()
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    approved_case = await odds_store.get_team_review_case(case_id)

    assert approve_resp.status_code == 200
    assert approved_case is not None
    assert approved_case.status == "approved"
