from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import close_db, init_db
from app.main import app
from app.models.schemas import NormalizedOdds
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
from app.services.team_registry import create_canonical_team
from app.store import odds_store


SCRAPED_AT = "2030-01-01T19:00:00+00:00"
START_TIME = "2030-01-01T20:00:00+00:00"


@pytest.fixture(autouse=True)
async def setup_app():
    await init_db(settings.db_path)
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


async def _seed_two_matches(*, same_start: bool = True):
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.set_current_snapshot(SCRAPED_AT)

    home_target = create_canonical_team(display_name="Partizan Belgrade")
    away_target = create_canonical_team(display_name="Crvena Zvezda")
    home_source = create_canonical_team(display_name="KK Partizan")
    away_source = create_canonical_team(display_name="Red Star Belgrade")

    await odds_store.upsert_match(
        id="target-match",
        league_id="euroleague",
        home_team="Partizan Belgrade",
        away_team="Crvena Zvezda",
        home_team_id=home_target.team_id,
        away_team_id=away_target.team_id,
        start_time=START_TIME,
    )
    other_start = START_TIME if same_start else "2030-01-02T20:00:00+00:00"
    await odds_store.upsert_match(
        id="source-match",
        league_id="euroleague",
        home_team="KK Partizan",
        away_team="Red Star Belgrade",
        home_team_id=home_source.team_id,
        away_team_id=away_source.team_id,
        start_time=other_start,
    )

    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="target-match",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan Belgrade",
            away_team="Crvena Zvezda",
            market_type="game_total",
            threshold=160.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at=SCRAPED_AT,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="source-match",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="KK Partizan",
            away_team="Red Star Belgrade",
            market_type="game_total",
            threshold=161.5,
            over_odds=1.90,
            under_odds=1.92,
        ),
        scraped_at=SCRAPED_AT,
    )

    return {
        "home_target_id": home_target.team_id,
        "away_target_id": away_target.team_id,
        "home_source_id": home_source.team_id,
        "away_source_id": away_source.team_id,
    }


@pytest.mark.asyncio
async def test_merge_matches_happy_path(client: AsyncClient, team_registry_file):
    teams = await _seed_two_matches()

    resp = await client.post(
        "/api/v1/matches/merge",
        json={
            "target_match_id": "target-match",
            "source_match_ids": ["source-match"],
            "team_pairings": [
                {"source_team_id": teams["home_source_id"], "target_team_id": teams["home_target_id"]},
                {"source_team_id": teams["away_source_id"], "target_team_id": teams["away_target_id"]},
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_match_id"] == "target-match"
    assert body["merged_source_match_ids"] == ["source-match"]
    assert body["reassigned_odds"] == 1
    assert body["deleted_source_matches"] == 1

    # Source match is gone
    assert await odds_store.get_match("source-match") is None
    # Target now has both bookmakers
    target = await odds_store.get_match("target-match")
    assert target is not None
    target_odds = await odds_store.get_odds_for_match("target-match")
    bookmakers = {o.bookmaker_id for o in target_odds}
    assert bookmakers == {"mozzart", "meridian"}

    # Team aliases are merged - source teams now resolve to target teams
    teams_resp = await client.get(
        f"/api/v1/canonical-teams?search=Partizan",
    )
    display_names = [team["display_name"] for team in teams_resp.json()]
    # Source display name disappears (merged into target); target remains.
    assert "Partizan Belgrade" in display_names
    assert "KK Partizan" not in display_names


@pytest.mark.asyncio
async def test_merge_matches_rejects_different_start_time(client: AsyncClient, team_registry_file):
    teams = await _seed_two_matches(same_start=False)

    resp = await client.post(
        "/api/v1/matches/merge",
        json={
            "target_match_id": "target-match",
            "source_match_ids": ["source-match"],
            "team_pairings": [],
        },
    )
    assert resp.status_code == 400
    assert "start_time" in resp.json()["detail"]
    # Both matches still exist
    assert await odds_store.get_match("source-match") is not None
    assert await odds_store.get_match("target-match") is not None


@pytest.mark.asyncio
async def test_merge_matches_404_on_missing_target(client: AsyncClient, team_registry_file):
    resp = await client.post(
        "/api/v1/matches/merge",
        json={
            "target_match_id": "does-not-exist",
            "source_match_ids": ["also-not"],
            "team_pairings": [],
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_merge_matches_rejects_target_in_sources(client: AsyncClient, team_registry_file):
    await _seed_two_matches()
    resp = await client.post(
        "/api/v1/matches/merge",
        json={
            "target_match_id": "target-match",
            "source_match_ids": ["target-match"],
            "team_pairings": [],
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_merge_matches_dedupes_colliding_odds(client: AsyncClient, team_registry_file):
    """When source and target have an odds row with the same bookmaker/market/player/threshold,
    only one row should survive after the merge (the highest id)."""
    teams = await _seed_two_matches()

    # Insert a colliding pair: same bookmaker_id+market+threshold on both matches.
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="target-match",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan Belgrade",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Some Player",
            threshold=12.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at=SCRAPED_AT,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="source-match",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="KK Partizan",
            away_team="Red Star Belgrade",
            market_type="player_points",
            player_name="Some Player",
            threshold=12.5,
            over_odds=1.7,
            under_odds=2.1,
        ),
        scraped_at=SCRAPED_AT,
    )

    resp = await client.post(
        "/api/v1/matches/merge",
        json={
            "target_match_id": "target-match",
            "source_match_ids": ["source-match"],
            "team_pairings": [
                {"source_team_id": teams["home_source_id"], "target_team_id": teams["home_target_id"]},
                {"source_team_id": teams["away_source_id"], "target_team_id": teams["away_target_id"]},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    # Only one row should remain for the colliding key
    odds = await odds_store.get_odds_for_match("target-match")
    player_rows = [o for o in odds if o.player_name == "Some Player"]
    assert len(player_rows) == 1
    # Newer (source) row had over_odds=1.7 - it has the highest id, so it wins
    assert player_rows[0].over_odds == 1.7


@pytest.mark.asyncio
async def test_merge_matches_dedupes_inter_source_collision(client: AsyncClient, team_registry_file):
    """Two source matches share an odds row with the same unique key but the
    target has none. The merge must dedupe across sources (otherwise the
    UPDATE trips the UNIQUE constraint)."""
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.set_current_snapshot(SCRAPED_AT)

    home_target = create_canonical_team(display_name="Target Home")
    away_target = create_canonical_team(display_name="Target Away")
    home_a = create_canonical_team(display_name="A Home")
    away_a = create_canonical_team(display_name="A Away")
    home_b = create_canonical_team(display_name="B Home")
    away_b = create_canonical_team(display_name="B Away")

    await odds_store.upsert_match(
        id="target-match",
        league_id="euroleague",
        home_team="Target Home",
        away_team="Target Away",
        home_team_id=home_target.team_id,
        away_team_id=away_target.team_id,
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        id="source-a",
        league_id="euroleague",
        home_team="A Home",
        away_team="A Away",
        home_team_id=home_a.team_id,
        away_team_id=away_a.team_id,
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        id="source-b",
        league_id="euroleague",
        home_team="B Home",
        away_team="B Away",
        home_team_id=home_b.team_id,
        away_team_id=away_b.team_id,
        start_time=START_TIME,
    )

    # Same bookmaker/market/threshold on both sources; nothing on target.
    for mid, over in (("source-a", 1.85), ("source-b", 1.92)):
        await odds_store.upsert_odds(
            NormalizedOdds(
                match_id=mid,
                bookmaker_id="mozzart",
                league_id="euroleague",
                home_team="X",
                away_team="Y",
                market_type="game_total",
                threshold=160.5,
                over_odds=over,
                under_odds=2.0,
            ),
            scraped_at=SCRAPED_AT,
        )

    resp = await client.post(
        "/api/v1/matches/merge",
        json={
            "target_match_id": "target-match",
            "source_match_ids": ["source-a", "source-b"],
            "team_pairings": [
                {"source_team_id": home_a.team_id, "target_team_id": home_target.team_id},
                {"source_team_id": away_a.team_id, "target_team_id": away_target.team_id},
                {"source_team_id": home_b.team_id, "target_team_id": home_target.team_id},
                {"source_team_id": away_b.team_id, "target_team_id": away_target.team_id},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    odds = await odds_store.get_odds_for_match("target-match")
    rows = [o for o in odds if o.market_type == "game_total"]
    assert len(rows) == 1, [r.over_odds for r in rows]
    # The newer (source-b) row had over_odds=1.92 and the highest id, so it wins.
    assert rows[0].over_odds == 1.92
