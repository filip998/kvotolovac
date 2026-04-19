from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.http_client import HttpClient
from app.scrapers.oktagonbet_scraper import (
    OktagonBetScraper,
    _parse_match,
    _parse_game_total_ot_match,
    _parse_match_detail,
    _parse_bulk_match,
    _parse_start_time,
    _is_player_market,
    _extract_league_id,
    _SPORT_SPECS,
)
from app.models.schemas import RawOddsData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oktagonbet_specials.json"
TOTALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oktagonbet_basketball_totals.json"


@pytest.fixture
def fixture_data() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def totals_fixture_data() -> dict:
    with open(TOTALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def player_matches(fixture_data) -> list[dict]:
    """Extract only player market matches from fixture (not duels/specials)."""
    return [m for m in fixture_data["esMatches"] if _is_player_market(m)]


# ── Unit tests for helpers ────────────────────────────────


def test_parse_start_time():
    result = _parse_start_time(1775829600000)
    assert result is not None
    assert "2026-04" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


def test_parse_start_time_zero():
    assert _parse_start_time(0) is None


def test_is_player_market_nba():
    match = {"leagueName": "Igrači ~ USA NBA", "leagueCategory": "PL"}
    assert _is_player_market(match) is True


def test_is_player_market_euroleague():
    match = {"leagueName": "Igrači ~ Euroleague", "leagueCategory": "PL"}
    assert _is_player_market(match) is True


def test_is_player_market_rejects_duels():
    match = {"leagueName": "Igrači Dueli ~ Euroleague", "leagueCategory": "DU"}
    assert _is_player_market(match) is False


def test_is_player_market_rejects_specials():
    match = {"leagueName": "Specijal ~ Euroleague", "leagueCategory": "SP"}
    assert _is_player_market(match) is False


def test_is_player_market_rejects_empty():
    assert _is_player_market({}) is False


def test_extract_league_id_nba():
    assert _extract_league_id("Igrači ~ USA NBA") == "nba"


def test_extract_league_id_euroleague():
    assert _extract_league_id("Igrači ~ Euroleague") == "euroleague"


def test_extract_league_id_aba():
    assert _extract_league_id("Igrači ~ ABA League") == "aba_liga"
    assert _extract_league_id("Igrači ~ AdmiralBet ABA liga - plej of") == "aba_liga"


def test_extract_league_id_live_basketball_variants():
    assert _extract_league_id("Argentina ~ Liga A") == "argentina_1"
    assert _extract_league_id("Puerto Rico ~ BSN") == "portoriko_1"
    assert _extract_league_id("New Zealand ~ NBL") == "new_zealand"
    assert _extract_league_id("South Korea ~ KBL") == "south_korea_play_offs"
    assert _extract_league_id("Uruguay ~ Liga Uruguaya") == "uruguay_winners_stage"


def test_extract_league_id_empty():
    assert _extract_league_id("") == "basketball"


# ── Parsing real fixture data ─────────────────────────────


def test_parse_match_returns_data(player_matches):
    results = _parse_match(player_matches[0])
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


def test_parse_match_has_player_names(player_matches):
    for m in player_matches:
        for r in _parse_match(m):
            assert r.player_name


def test_parse_match_has_thresholds(player_matches):
    for m in player_matches:
        for r in _parse_match(m):
            assert r.threshold > 0


def test_parse_match_has_odds(player_matches):
    all_results = []
    for m in player_matches:
        all_results.extend(_parse_match(m))
    with_both = [r for r in all_results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_match_bookmaker_id(player_matches):
    for m in player_matches:
        for r in _parse_match(m):
            assert r.bookmaker_id == "oktagonbet"


def test_parse_match_market_types(player_matches):
    valid_types = {
        "player_points",
        "player_points_milestones",
        "player_rebounds",
        "player_assists",
        "player_3points",
        "player_steals",
        "player_blocks",
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }
    all_types = set()
    for m in player_matches:
        for r in _parse_match(m):
            assert r.market_type in valid_types
            all_types.add(r.market_type)
    assert {
        "player_points",
        "player_3points",
        "player_steals",
        "player_blocks",
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }.issubset(all_types)


def test_parse_match_empty():
    assert _parse_match({}) == []


def test_parse_match_rejects_duels():
    match = {
        "home": "Player A",
        "away": "Player B",
        "leagueName": "Igrači Dueli ~ Euroleague",
        "leagueCategory": "DU",
        "kickOffTime": 1775829600000,
        "params": {"ouPlPoints": "15.5"},
        "odds": {"51679": 1.85, "51681": 1.85},
    }
    assert _parse_match(match) == []


def test_parse_match_rejects_specials():
    match = {
        "home": "Player A & Player B",
        "away": "postižu 45+ poena",
        "leagueName": "Specijal ~ Euroleague",
        "leagueCategory": "SP",
        "kickOffTime": 1775829600000,
        "params": {},
        "odds": {"50554": 6.0},
    }
    assert _parse_match(match) == []


def test_parse_match_multiple_markets():
    """Match with all supported bulk params produces all market entries."""
    match = {
        "home": "CJ McCollum",
        "away": "Atlanta Hawks",
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "kickOffTime": 1775862000000,
        "params": {
            "ouPlPoints": "17.5",
            "ouPlRebounds": "2.5",
            "ouPlAssists": "4.5",
            "ouPl3Points": "2.5",
            "ouPlSt": "0.5",
            "ouPlB": "0.5",
            "ouPlTPR": "20.5",
            "ouPlTPA": "22.5",
            "ouPlTRA": "7.5",
            "ouPlTPRA": "25.5",
        },
        "odds": {
            "51679": 1.85, "51681": 1.85,
            "51685": 1.55, "51687": 2.25,
            "51682": 1.87, "51684": 1.83,
            "51688": 1.9, "51690": 1.78,
            "55672": 1.45, "55674": 2.45,
            "55681": 2.05, "55683": 1.65,
            "55244": 1.85, "55246": 1.85,
            "55247": 1.9, "55249": 1.8,
            "55250": 1.85, "55252": 1.85,
            "55215": 1.9, "55217": 1.8,
        },
    }
    results = _parse_match(match)
    assert len(results) == 10
    types = {r.market_type for r in results}
    assert types == {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_3points",
        "player_steals",
        "player_blocks",
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }


def test_parse_match_uses_canonical_aba_league_id():
    match = {
        "home": "Player A",
        "away": "Team A",
        "leagueName": "Igrači ~ ABA League",
        "leagueCategory": "PL",
        "kickOffTime": 1775862000000,
        "params": {"ouPlPoints": "17.5"},
        "odds": {"51679": 1.85, "51681": 1.85},
    }

    results = _parse_match(match)
    assert len(results) == 1
    assert results[0].league_id == "aba_liga"


def test_parse_match_missing_threshold():
    """Match without any threshold params produces no results."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "kickOffTime": 1775829600000,
        "params": {},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match(match) == []


def test_parse_match_no_odds():
    """Match without over/under odds is skipped."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "kickOffTime": 1775829600000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {},
    }
    assert _parse_match(match) == []


def test_parse_match_malformed_threshold():
    """Match with non-numeric threshold is skipped."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "kickOffTime": 1775829600000,
        "params": {"ouPlPoints": "not_a_number"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match(match) == []


def test_parse_match_partial_odds():
    """Match with only over_odds (no under) still produces a result."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "kickOffTime": 1775829600000,
        "params": {"ouPlPoints": "15.5"},
        "odds": {"51679": 1.88},
    }
    results = _parse_match(match)
    assert len(results) == 1
    assert results[0].over_odds == 1.88
    assert results[0].under_odds is None


def test_parse_game_total_ot_match_from_list_fixture(totals_fixture_data):
    results = _parse_game_total_ot_match(totals_fixture_data["list"]["esMatches"][0])

    assert len(results) == 1
    assert results[0].market_type == "game_total_ot"
    assert results[0].league_id == "argentina_1"
    assert (results[0].threshold, results[0].over_odds, results[0].under_odds) == (
        157.5,
        1.85,
        1.85,
    )


def test_parse_game_total_ot_match_from_detail_fixture(totals_fixture_data):
    results = _parse_game_total_ot_match(totals_fixture_data["detail"])

    assert len(results) == 9
    assert all(r.market_type == "game_total_ot" for r in results)
    assert sorted((r.threshold, r.over_odds, r.under_odds) for r in results) == [
        (153.5, 1.55, 2.25),
        (154.5, 1.62, 2.1),
        (155.5, 1.7, 2.0),
        (156.5, 1.75, 1.95),
        (157.5, 1.85, 1.85),
        (158.5, 1.93, 1.77),
        (159.5, 2.0, 1.7),
        (160.5, 2.15, 1.6),
        (161.5, 2.3, 1.53),
    ]


def test_parse_game_total_ot_match_excludes_combo_only_match(totals_fixture_data):
    assert _parse_game_total_ot_match(totals_fixture_data["list"]["esMatches"][1]) == []


def test_parse_match_detail_fixed_thresholds():
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "kickOffTime": 1775829600000,
        "odds": {"54096": 1.18, "54101": 1.65, "57454": 25.0},
    }
    results = _parse_match_detail(match)
    assert [r.threshold for r in results] == [4.5, 9.5, 59.5]
    assert all(r.market_type == "player_points_milestones" for r in results)
    assert all(r.under_odds is None for r in results)


# ── Integration: OktagonBetScraper with mocked HTTP ──────────


def _build_bulk_match_player(match_id: int, player: str, team: str, league: str, kickoff_ms: int, *, points_thr: float | None = None, points_over: float | None = None, points_under: float | None = None, milestones: dict | None = None) -> dict:
    """Build a bulk-PUT-shape match for a player (SK) market."""
    groups = []
    if points_thr is not None:
        groups.append({
            "id": 6020,
            "name": "Poeni Igraca",
            "handicapParamValue": points_thr,
            "tipTypes": [
                {"tipTypeId": 51679, "value": points_over or 0},
                {"tipTypeId": 51681, "value": points_under or 0},
            ],
        })
    if milestones:
        # milestones: {tip_type_id_str: odd}
        groups.append({
            "id": 2278620,
            "name": "Milestones",
            "handicapParamValue": None,
            "tipTypes": [
                {"tipTypeId": int(code), "value": odd} for code, odd in milestones.items()
            ],
        })
    return {
        "id": match_id,
        "home": player,
        "away": team,
        "kickOffTime": kickoff_ms,
        "leagueName": league,
        "leagueCategory": "PL",
        "sport": "SK",
        "odBetPickGroups": groups,
    }


def _build_bulk_match_game_ot(match_id: int, home: str, away: str, league: str, kickoff_ms: int, ot_thresholds: dict[float, tuple[float | None, float | None]]) -> dict:
    """Build a bulk-PUT-shape match for a basketball (B) game total OT market.

    ``ot_thresholds`` maps threshold → (over_odd, under_odd). Each entry is
    placed in its own group (matching how OktagonBet returns alt-OT lines).
    """
    # Allocate distinct (over, under) tip-type IDs from _GAME_TOTAL_OT_LINES
    from app.scrapers.oktagonbet_scraper import _GAME_TOTAL_OT_LINES
    groups = []
    for idx, (thr, (over, under)) in enumerate(ot_thresholds.items()):
        over_code, under_code, _ = _GAME_TOTAL_OT_LINES[idx % len(_GAME_TOTAL_OT_LINES)]
        groups.append({
            "id": 4204 + idx,
            "name": f"Konacan ishod ukljucujuci OT - {thr}",
            "handicapParamValue": thr,
            "tipTypes": [
                {"tipTypeId": int(over_code), "value": over or 0},
                {"tipTypeId": int(under_code), "value": under or 0},
            ],
        })
    return {
        "id": match_id,
        "home": home,
        "away": away,
        "kickOffTime": kickoff_ms,
        "leagueName": league,
        "sport": "B",
        "odBetPickGroups": groups,
    }


@pytest.mark.asyncio
async def test_scraper_returns_data(fixture_data):
    scraper = OktagonBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get, \
         patch.object(scraper._http, "put_json", new_callable=AsyncMock) as mock_put:
        mock_get.return_value = fixture_data
        mock_put.return_value = {}
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "oktagonbet" for r in results)


@pytest.mark.asyncio
async def test_scraper_fetches_ot_detail_ladder_via_bulk():
    """Bulk PUT response with extended OT thresholds should produce all ladder entries."""
    scraper = OktagonBetScraper()
    list_match = {
        "id": 42182971,
        "home": "Obras Sanitarias",
        "away": "Boca Juniors",
        "kickOffTime": 1776722400000,
        "leagueName": "Argentina ~ Liga A",
        "sport": "B",
    }
    bulk_match = _build_bulk_match_game_ot(
        42182971, "Obras Sanitarias", "Boca Juniors",
        "Argentina ~ Liga A", 1776722400000,
        {
            153.5: (1.55, 2.25),
            154.5: (1.62, 2.10),
            155.5: (1.70, 2.00),
            156.5: (1.75, 1.95),
            157.5: (1.85, 1.85),
            158.5: (1.93, 1.77),
            159.5: (2.00, 1.70),
            160.5: (2.15, 1.60),
            161.5: (2.30, 1.53),
        },
    )

    async def mock_get(url, **kwargs):
        if "/sport/SK/mob" in url:
            return {"esMatches": []}
        if "/sport/B/mob" in url:
            return {"esMatches": [list_match]}
        raise AssertionError(f"Unexpected GET URL: {url}")

    async def mock_put(url, **kwargs):
        assert "prematchesByIds.html" in url
        return {42182971: bulk_match}

    with patch.object(scraper._http, "get_json", side_effect=mock_get), \
         patch.object(scraper._http, "put_json", side_effect=mock_put):
        results = await scraper.scrape_odds("basketball")

    ot_results = [r for r in results if r.market_type == "game_total_ot"]
    assert len(ot_results) == 9
    assert sorted(r.threshold for r in ot_results) == [
        153.5, 154.5, 155.5, 156.5, 157.5, 158.5, 159.5, 160.5, 161.5,
    ]
    base_line = next(r for r in ot_results if r.threshold == 157.5)
    assert (base_line.over_odds, base_line.under_odds) == (1.85, 1.85)


@pytest.mark.asyncio
async def test_scraper_uses_list_kickoff_when_bulk_kickoff_differs():
    """List metadata wins for kickoff time even if bulk response has a different value."""
    scraper = OktagonBetScraper()
    list_kickoff = 1776722400000
    list_match = {
        "id": 42182971,
        "home": "Obras Sanitarias",
        "away": "Boca Juniors",
        "kickOffTime": list_kickoff,
        "leagueName": "Argentina ~ Liga A",
        "sport": "B",
    }
    bulk_match = _build_bulk_match_game_ot(
        42182971, "Obras Sanitarias", "Boca Juniors",
        "Argentina ~ Liga A", list_kickoff + 300000,
        {157.5: (1.85, 1.85)},
    )

    async def mock_get(url, **kwargs):
        if "/sport/SK/mob" in url:
            return {"esMatches": []}
        if "/sport/B/mob" in url:
            return {"esMatches": [list_match]}
        raise AssertionError(f"Unexpected GET URL: {url}")

    async def mock_put(url, **kwargs):
        return {42182971: bulk_match}

    with patch.object(scraper._http, "get_json", side_effect=mock_get), \
         patch.object(scraper._http, "put_json", side_effect=mock_put):
        results = await scraper.scrape_odds("basketball")

    base_line = next(r for r in results if r.market_type == "game_total_ot" and r.threshold == 157.5)
    assert base_line.start_time == "2026-04-20T22:00:00+00:00"


@pytest.mark.asyncio
async def test_scraper_fetches_milestone_ladders_via_bulk(player_matches):
    """Bulk PUT response with the Milestones group should produce milestone entries per player."""
    scraper = OktagonBetScraper()

    # Build slim bulk-shape matches with milestones for each player.
    bulk_payload = {}
    for m in player_matches:
        bulk_payload[m["id"]] = _build_bulk_match_player(
            m["id"], m["home"], m["away"], m.get("leagueName", "Igrači ~ USA NBA"),
            m.get("kickOffTime", 1775829600000),
            milestones={"54096": 1.18, "54101": 1.65},
        )

    async def mock_get(url, **kwargs):
        if "/sport/SK/mob" in url:
            return {"esMatches": player_matches}
        if "/sport/B/mob" in url:
            return {"esMatches": []}
        raise AssertionError(f"Unexpected GET URL: {url}")

    async def mock_put(url, **kwargs):
        return bulk_payload

    with patch.object(scraper._http, "get_json", side_effect=mock_get), \
         patch.object(scraper._http, "put_json", side_effect=mock_put) as put_mock:
        results = await scraper.scrape_odds("basketball")

    ladder_results = [
        result for result in results
        if result.market_type == "player_points_milestones"
        and result.under_odds is None
        and result.threshold in {4.5, 9.5}
    ]
    assert len(ladder_results) == len(player_matches) * 2
    # Confirms we made exactly one bulk PUT (vs one GET per match).
    assert put_mock.await_count == 1


@pytest.mark.asyncio
async def test_scraper_makes_single_bulk_put_for_many_matches():
    """Verify the bulk endpoint is called once per chunk, not per match."""
    matches = [
        {
            "id": 1000 + idx,
            "home": f"Player {idx}",
            "away": "Team A",
            "leagueName": "Igrači ~ USA NBA",
            "leagueCategory": "PL",
            "kickOffTime": 1775829600000,
            "sport": "SK",
        }
        for idx in range(4)
    ]
    bulk_payload = {
        m["id"]: _build_bulk_match_player(
            m["id"], m["home"], m["away"], m["leagueName"], m["kickOffTime"],
            milestones={"54096": 1.5},
        )
        for m in matches
    }
    scraper = OktagonBetScraper()

    async def mock_get(url, **kwargs):
        if "/sport/SK/mob" in url:
            return {"esMatches": matches}
        if "/sport/B/mob" in url:
            return {"esMatches": []}
        raise AssertionError(f"Unexpected GET URL: {url}")

    async def mock_put(url, **kwargs):
        return bulk_payload

    with patch.object(scraper._http, "get_json", side_effect=mock_get), \
         patch.object(scraper._http, "put_json", side_effect=mock_put) as put_mock:
        results = await scraper.scrape_odds("basketball")

    assert put_mock.await_count == 1  # single bulk call regardless of match count
    ladder_results = [
        r for r in results
        if r.market_type == "player_points_milestones"
        and r.under_odds is None
        and r.threshold == 4.5
    ]
    assert len(ladder_results) == len(matches)


@pytest.mark.asyncio
async def test_scraper_filters_non_player_markets(fixture_data):
    """Duels and specials from fixture should be filtered out."""
    scraper = OktagonBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get, \
         patch.object(scraper._http, "put_json", new_callable=AsyncMock) as mock_put:
        mock_get.return_value = fixture_data
        mock_put.return_value = {}
        results = await scraper.scrape_odds("basketball")

    # Only "Igrači ~" (non-duel) matches should produce results
    player_names = {r.player_name for r in results}
    # Duels have two players in home — they should be filtered
    for name in player_names:
        if name:
            assert "&" not in name  # Specials have "Player A & Player B"


def test_dedupe_merges_over_under_from_different_sources():
    """Legacy parser emits over-only, bulk emits under-only — merged row should have both."""
    over_only = RawOddsData(
        bookmaker_id="oktagonbet", league_id="nba", sport="basketball",
        home_team="Hawks", away_team="Player1", market_type="player_points",
        player_name="Player1", threshold=15.5,
        over_odds=1.85, under_odds=None, start_time="2026-04-13T23:00:00+00:00",
    )
    under_only = RawOddsData(
        bookmaker_id="oktagonbet", league_id="nba", sport="basketball",
        home_team="Hawks", away_team="Player1", market_type="player_points",
        player_name="Player1", threshold=15.5,
        over_odds=None, under_odds=1.92, start_time="2026-04-13T23:00:00+00:00",
    )
    merged = OktagonBetScraper._dedupe_raw_odds([over_only, under_only])
    assert len(merged) == 1
    assert merged[0].over_odds == 1.85
    assert merged[0].under_odds == 1.92


def test_parse_bulk_match_first_non_null_wins_for_duplicate_tip_types():
    """If the same tipTypeId appears in two groups, keep the first non-null odd."""
    spec = _SPORT_SPECS["basketball"]
    match = {
        "id": 1,
        "home": "Player1",
        "away": "Hawks",
        "kickOffTime": 1775829600000,
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "sport": "SK",
        "odBetPickGroups": [
            {"id": 6020, "name": "Poeni Igraca", "handicapParamValue": 15.5,
             "tipTypes": [{"tipTypeId": 51679, "value": 1.85}, {"tipTypeId": 51681, "value": 1.95}]},
            {"id": 6020, "name": "Poeni Igraca (dup)", "handicapParamValue": 15.5,
             "tipTypes": [{"tipTypeId": 51679, "value": 9.99}, {"tipTypeId": 51681, "value": 9.99}]},
        ],
    }
    results = _parse_bulk_match(match, spec)
    assert len(results) == 1
    assert results[0].over_odds == 1.85
    assert results[0].under_odds == 1.95


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = OktagonBetScraper()
    results = await scraper.scrape_odds("euroleague")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_response():
    scraper = OktagonBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"esMatches": []}
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = OktagonBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = OktagonBetScraper()
    assert scraper.get_bookmaker_id() == "oktagonbet"
    assert scraper.get_bookmaker_name() == "OktagonBet"
    assert "basketball" in scraper.get_supported_leagues()
