from __future__ import annotations

import asyncio

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import close_db, get_db, init_db
from app.main import app
from app.models.schemas import (
    NormalizedOdds,
    RawOddsData,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from app.scrapers.base import BaseScraper
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
from app.services.scheduler import scheduler
from app.services.normalizer import normalize_team_name
from app.services.team_registry import create_canonical_team, remember_team_alias
from app.store import odds_store


@pytest.fixture(autouse=True)
async def setup_app():
    """Set up fresh DB and register scrapers before each test."""
    await init_db(settings.db_path)
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
async def test_list_canonical_teams_filters_by_sport(
    client: AsyncClient,
    team_registry_file,
):
    basketball_team = create_canonical_team(
        display_name="Partizan",
        sport="basketball",
    )
    football_team = create_canonical_team(
        display_name="Partizan",
        sport="football",
    )

    basketball_resp = await client.get("/api/v1/canonical-teams?sport=basketball&search=Partizan")
    football_resp = await client.get("/api/v1/canonical-teams?sport=football&search=Partizan")

    assert basketball_resp.status_code == 200
    assert [row["id"] for row in basketball_resp.json()] == [basketball_team.team_id]
    assert football_resp.status_code == 200
    assert [row["id"] for row in football_resp.json()] == [football_team.team_id]


@pytest.mark.asyncio
async def test_merge_canonical_teams_reassigns_aliases(
    client: AsyncClient,
    team_registry_file,
):
    source = create_canonical_team(display_name="QA Merge Source")
    target = create_canonical_team(display_name="QA Merge Target")
    remember_team_alias(
        bookmaker_id="maxbet",
        raw_team_name="QA Merge Alias",
        team_name="QA Merge Source",
    )
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="QA Merge Target Alias",
        team_name="QA Merge Target",
    )

    list_resp = await client.get("/api/v1/canonical-teams?search=QA%20Merge")
    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    merged_resp = await client.get("/api/v1/canonical-teams?search=QA%20Merge")

    assert list_resp.status_code == 200
    assert {team["display_name"] for team in list_resp.json()} == {
        "QA Merge Source",
        "QA Merge Target",
    }
    assert merge_resp.status_code == 200
    assert merge_resp.json()["merged_team_name"] == "QA Merge Target"
    assert normalize_team_name("QA Merge Alias", None, "maxbet") == "QA Merge Target"
    assert normalize_team_name("QA Merge Source", None, "maxbet") == "QA Merge Target"
    assert merged_resp.status_code == 200
    assert [team["display_name"] for team in merged_resp.json()] == ["QA Merge Target"]
    assert "QA Merge Source" in merged_resp.json()[0]["aliases"]


@pytest.mark.asyncio
async def test_merge_canonical_teams_rewrites_pending_team_review_cases(
    client: AsyncClient,
    team_registry_file,
):
    await close_db()
    await init_db(settings.db_path)
    batch_scraped_at = "2026-04-16T19:45:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")

    source = create_canonical_team(display_name="QA Pending Merge Source")
    target = create_canonical_team(display_name="QA Pending Merge Target")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Euroleague",
                "normalized_raw_league_id": "euroleague",
                "scope_league_id": "euroleague",
                "raw_team_name": "QA Pending Raw Alias",
                "normalized_raw_team_name": "QA Pending Raw Alias",
                "suggested_team_id": source.team_id,
                "suggested_team_name": source.team_name,
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 95,
                "candidate_teams": [
                    {
                        "team_id": source.team_id,
                        "team_name": source.team_name,
                        "score": 95,
                    },
                    {
                        "team_id": target.team_id,
                        "team_name": target.team_name,
                        "score": 88,
                    },
                ],
                "canonical_home_team": source.team_name,
                "canonical_away_team": "Olympiacos",
                "evidence": ["Exact start time: 2026-04-16T19:45:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    approve_resp = await client.post(
        f"/api/v1/team-review/cases/{case_id}/approve",
        json={"team_id": source.team_id},
    )
    updated_case = await odds_store.get_team_review_case(case_id)

    assert merge_resp.status_code == 200
    assert updated_case is not None
    assert updated_case.suggested_team_id == target.team_id
    assert updated_case.suggested_team_name == target.team_name
    assert updated_case.canonical_home_team == target.team_name
    assert all(candidate.team_id != source.team_id for candidate in updated_case.candidate_teams)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_id"] == target.team_id
    assert normalize_team_name("QA Pending Raw Alias", None, "meridian") == target.team_name


@pytest.mark.asyncio
async def test_merge_canonical_teams_rewrites_legacy_name_only_team_review_cases(
    client: AsyncClient,
    team_registry_file,
):
    await close_db()
    await init_db(settings.db_path)
    batch_scraped_at = "2026-04-16T19:50:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")

    source = create_canonical_team(display_name="QA Legacy Merge Source")
    target = create_canonical_team(display_name="QA Legacy Merge Target")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Euroleague",
                "normalized_raw_league_id": "euroleague",
                "scope_league_id": "euroleague",
                "raw_team_name": "QA Legacy Raw Alias",
                "normalized_raw_team_name": "QA Legacy Raw Alias",
                "suggested_team_name": source.team_name,
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 94,
                "evidence": ["Exact start time: 2026-04-16T19:50:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    updated_case = await odds_store.get_team_review_case(case_id)

    assert merge_resp.status_code == 200
    assert updated_case is not None
    assert updated_case.suggested_team_id == target.team_id
    assert updated_case.suggested_team_name == target.team_name
    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_id"] == target.team_id
    assert normalize_team_name("QA Legacy Raw Alias", None, "meridian") == target.team_name


@pytest.mark.asyncio
async def test_merge_canonical_teams_rejects_same_team(
    client: AsyncClient,
    team_registry_file,
):
    team = create_canonical_team(display_name="QA Merge Same Team")

    resp = await client.post(
        f"/api/v1/canonical-teams/{team.team_id}/merge",
        json={"target_team_id": team.team_id},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Cannot merge a canonical team into itself"


@pytest.mark.asyncio
async def test_merge_canonical_teams_rejects_missing_target(
    client: AsyncClient,
    team_registry_file,
):
    team = create_canonical_team(display_name="QA Merge Missing Target")

    resp = await client.post(
        f"/api/v1/canonical-teams/{team.team_id}/merge",
        json={"target_team_id": 99999},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Both canonical teams must exist before merging"


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
    assert approve_resp.json()["resolved_team_name"] is None
    assert normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian") == "Rilski Sportist"
    assert approved_resp.status_code == 200
    assert approved_resp.json()[0]["status"] == "approved"


@pytest.mark.asyncio
async def test_team_review_approval_flattens_to_final_canonical_team(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T20:30:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("brazil_nbb", "Brazil NBB", "basketball", "Brazil")
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Uniao Corinthians",
        team_name="EC Uniao Corinthians",
        competition_id="brazil_nbb",
    )
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Brazil NBB",
                "normalized_raw_league_id": "brazil nbb",
                "scope_league_id": "brazil_nbb",
                "raw_team_name": "U.Corinthians",
                "normalized_raw_team_name": "U.Corinthians",
                "suggested_team_name": "Uniao Corinthians",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 93,
                "evidence": ["Exact start time: 2026-04-16T20:30:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")

    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_name"] == "Uniao Corinthians"
    assert approve_resp.json()["resolved_team_name"] == "EC Uniao Corinthians"
    assert normalize_team_name("Uniao Corinthians", "brazil_nbb", "meridian") == "EC Uniao Corinthians"
    assert normalize_team_name("U.Corinthians", "brazil_nbb", "meridian") == "EC Uniao Corinthians"


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
async def test_team_review_approval_accepts_unscoped_alias(
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

    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_name"] == "Rilski Sportist"
    assert normalize_team_name("Rilski Sport.", None, "meridian") == "Rilski Sportist"
    assert pending_resp.status_code == 200
    assert pending_resp.json() == []


@pytest.mark.asyncio
async def test_team_review_approval_rejects_circular_alias(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T21:45:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Baskonia Gatez",
        team_name="Baskonia",
        competition_id="euroleague",
    )
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Euroleague",
                "normalized_raw_league_id": "euroleague",
                "scope_league_id": "euroleague",
                "raw_team_name": "Baskonia",
                "normalized_raw_team_name": "Baskonia",
                "suggested_team_name": "Baskonia Gatez",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 91,
                "evidence": ["Exact start time: 2026-04-16T21:45:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")

    assert approve_resp.status_code == 409
    assert "Circular alias" in approve_resp.json()["detail"]


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


@pytest.mark.asyncio
async def test_init_db_migrates_team_review_cases_to_nullable_suggested_name(tmp_path):
    await close_db()
    db_path = tmp_path / "legacy-team-review.db"

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE team_review_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bookmaker_id TEXT NOT NULL,
                raw_league_id TEXT NOT NULL,
                normalized_raw_league_id TEXT NOT NULL,
                scope_league_id TEXT NOT NULL,
                raw_team_name TEXT NOT NULL,
                normalized_raw_team_name TEXT NOT NULL,
                suggested_team_name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                confidence TEXT NOT NULL,
                evidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                scraped_at TEXT,
                approved_at TEXT
            );
            """
        )
        await db.commit()

    await init_db(str(db_path))
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
                "suggested_team_name": None,
                "start_time": "2026-04-16T23:45:00+00:00",
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "medium",
                "similarity_score": 82,
                "evidence": ["Candidate team: Rilski Sportist"],
                "status": "pending",
            }
        ),
        scraped_at="2026-04-16T23:45:00+00:00",
    )

    inserted_case = await odds_store.get_team_review_case(case_id)

    assert inserted_case is not None
    assert inserted_case.suggested_team_name is None


@pytest.mark.asyncio
async def test_init_db_enables_foreign_keys_for_canonical_team_refs():
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    db = await get_db()
    pragma_row = await (await db.execute("PRAGMA foreign_keys")).fetchone()

    assert pragma_row is not None
    assert pragma_row[0] == 1

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO team_review_cases (
                bookmaker_id,
                raw_league_id,
                normalized_raw_league_id,
                sport,
                scope_league_id,
                raw_team_name,
                normalized_raw_team_name,
                suggested_team_id,
                start_time,
                reason_code,
                evidence,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "meridian",
                "Euroleague",
                "euroleague",
                "basketball",
                "euroleague",
                "Invalid FK Team",
                "invalid fk team",
                999999,
                "2026-04-17T00:00:00+00:00",
                "candidate_team_match_same_start_time",
                "[]",
                "pending",
            ),
        )


@pytest.mark.asyncio
async def test_init_db_rebuilds_legacy_tables_with_canonical_team_foreign_keys(tmp_path):
    await close_db()
    db_path = tmp_path / "legacy-canonical-fk.db"

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE leagues (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sport TEXT NOT NULL,
                country TEXT,
                is_active BOOLEAN DEFAULT TRUE
            );
            CREATE TABLE bookmakers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                website_url TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE matches (
                id TEXT PRIMARY KEY,
                league_id TEXT REFERENCES leagues(id),
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                start_time TIMESTAMP,
                status TEXT DEFAULT 'upcoming',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE team_review_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bookmaker_id TEXT REFERENCES bookmakers(id),
                raw_league_id TEXT NOT NULL,
                normalized_raw_league_id TEXT NOT NULL,
                scope_league_id TEXT,
                raw_team_name TEXT NOT NULL,
                normalized_raw_team_name TEXT NOT NULL,
                suggested_team_name TEXT,
                start_time TIMESTAMP,
                reason_code TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'medium',
                evidence TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                declined_at TIMESTAMP
            );
            """
        )
        await db.commit()

    await init_db(str(db_path))
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")

    db = await get_db()
    match_fks = await db.execute_fetchall("PRAGMA foreign_key_list(matches)")
    team_review_fks = await db.execute_fetchall("PRAGMA foreign_key_list(team_review_cases)")

    assert any(row[2] == "canonical_teams" and row[3] == "home_team_id" for row in match_fks)
    assert any(row[2] == "canonical_teams" and row[3] == "away_team_id" for row in match_fks)
    assert any(
        row[2] == "canonical_teams" and row[3] == "suggested_team_id" for row in team_review_fks
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO matches (
                id,
                league_id,
                sport,
                home_team_id,
                away_team_id,
                home_team,
                away_team
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-fk-match",
                "euroleague",
                "basketball",
                999999,
                999998,
                "Legacy Home",
                "Legacy Away",
            ),
        )
        await db.commit()
    await db.rollback()

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO team_review_cases (
                bookmaker_id,
                raw_league_id,
                normalized_raw_league_id,
                sport,
                scope_league_id,
                raw_team_name,
                normalized_raw_team_name,
                suggested_team_id,
                start_time,
                reason_code,
                evidence,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "meridian",
                "Euroleague",
                "euroleague",
                "basketball",
                "euroleague",
                "Legacy FK Team",
                "legacy fk team",
                999999,
                "2026-04-17T00:30:00+00:00",
                "candidate_team_match_same_start_time",
                "[]",
                "pending",
            ),
        )
        await db.commit()
    await db.rollback()
