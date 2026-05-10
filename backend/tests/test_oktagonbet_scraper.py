from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.http_client import HttpClient
from app.scrapers.oktagonbet_scraper import (
    OktagonBetScraper,
    _parse_match,
    _parse_game_total_ot_match,
    _parse_handicap_ot_match,
    _parse_match_detail,
    _parse_bulk_match,
    _parse_football_outcome_match,
    _parse_football_double_chance_bulk_match,
    _parse_tennis_outcome_match,
    _parse_start_time,
    _tennis_skip_reason,
    _is_player_market,
    _extract_league_id,
    _extract_plain_league_id,
    _SPORT_SPECS,
)
from app.models.schemas import RawOddsData, RawOutcomeOffer

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oktagonbet_specials.json"
TOTALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oktagonbet_basketball_totals.json"
FOOTBALL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oktagonbet_football_offer.json"
FOOTBALL_BULK_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oktagonbet_football_bulk.json"
TENNIS_NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


def _tennis_kickoff_ms(*, seconds: int = 0, minutes: int = 0) -> int:
    kickoff = TENNIS_NOW + timedelta(seconds=seconds, minutes=minutes)
    return int(kickoff.timestamp() * 1000)


@pytest.fixture
def fixture_data() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def totals_fixture_data() -> dict:
    with open(TOTALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def football_data() -> dict:
    with open(FOOTBALL_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def football_bulk_data() -> dict:
    with open(FOOTBALL_BULK_FIXTURE_PATH) as f:
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


def test_extract_plain_league_id_for_tennis():
    assert _extract_plain_league_id(
        "ITF M25 ~ Loule (Portugal)",
        "tennis",
    ) == "itf_m25_loule_(portugal)"
    assert _extract_plain_league_id("", "tennis") == "tennis"


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


# ── Handicap (+OT) parsing ──────────────────────────────────────────────


def test_parse_handicap_ot_match_positive_line_means_team1_favoured():
    """OktagonBet's ``handicapOvertime`` is the home team's signed
    Asian-handicap line (negative = home favourite, positive = home
    underdog — same convention as Mozzart's ``Hendikep -X`` UI).
    The parser negates the source value so positive threshold = home
    favoured (analyzer convention).  Pair codes: 50430 = home covers
    (over_odds), 50431 = away covers (under_odds).

    Live sample: Orlando vs Detroit (Detroit is favourite) returned
    ``handicapOvertime=3.5`` → threshold = -3.5.
    """
    match = {
        "id": 1,
        "home": "Orlando",
        "away": "Detroit",
        "leagueName": "USA NBA",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "3.5"},
        "odds": {"50430": 1.9, "50431": 1.9},
    }
    results = _parse_handicap_ot_match(match)
    assert len(results) == 1
    row = results[0]
    assert row.market_type == "home_handicap_ot"
    assert row.threshold == -3.5
    assert row.over_odds == 1.9
    assert row.under_odds == 1.9
    assert row.bookmaker_id == "oktagonbet"
    assert row.home_team == "Orlando"
    assert row.away_team == "Detroit"
    assert row.player_name is None


def test_parse_handicap_ot_match_negative_line_means_team1_underdog():
    """Houston vs LA Lakers (Houston home favourite) returned
    ``handicapOvertime=-3.5``; source is negated so threshold = +3.5.
    """
    match = {
        "id": 2,
        "home": "Houston",
        "away": "LA Lakers",
        "leagueName": "USA NBA",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-3.5"},
        "odds": {"50430": 1.9, "50431": 1.9},
    }
    results = _parse_handicap_ot_match(match)
    assert len(results) == 1
    assert results[0].threshold == 3.5


def test_parse_handicap_ot_match_pickem_zero_line_emits_row():
    match = {
        "id": 3,
        "home": "A",
        "away": "B",
        "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "0"},
        "odds": {"50430": 1.88, "50431": 1.92},
    }
    results = _parse_handicap_ot_match(match)
    assert len(results) == 1
    assert results[0].threshold == 0.0


def test_parse_handicap_ot_match_skips_player_market():
    match = {
        "id": 4,
        "home": "Jokic",
        "away": "Denver",
        "leagueName": "Igrači ~ USA NBA",
        "leagueCategory": "PL",
        "params": {"handicapOvertime": "-3.5"},
        "odds": {"50431": 1.9, "50430": 1.9},
    }
    assert _parse_handicap_ot_match(match) == []


def test_parse_handicap_ot_match_skips_unparseable_or_missing():
    bad = {
        "id": 5, "home": "A", "away": "B", "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "garbage"},
        "odds": {"50431": 1.9, "50430": 1.9},
    }
    no_odds = {
        "id": 6, "home": "A", "away": "B", "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-3.5"},
        "odds": {},
    }
    assert _parse_handicap_ot_match(bad) == []
    assert _parse_handicap_ot_match(no_odds) == []


def test_parse_game_total_ot_match_does_not_emit_handicap_after_change():
    match = {
        "id": 7,
        "home": "A",
        "away": "B",
        "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-3.5"},
        "odds": {"50431": 1.9, "50430": 1.9},
    }
    assert _parse_game_total_ot_match(match) == []


def test_parse_football_outcome_match_emits_result_and_totals(football_data):
    results = _parse_football_outcome_match(football_data["esMatches"][0])

    assert len(results) == 5
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert {r.bookmaker_id for r in results} == {"oktagonbet"}
    assert {r.sport for r in results} == {"football"}
    assert {r.league_id for r in results} == {"austria_3_east"}
    assert {r.home_team for r in results} == {"Wiener Sport-Club"}
    assert {r.away_team for r in results} == {"Parndorf"}
    assert {r.start_time for r in results} == {"2026-05-05T17:30:00+00:00"}
    assert {
        (r.market_type, r.outcome_code, r.line, r.raw_label, r.odds)
        for r in results
    } == {
        ("football_result", "home", None, "1", 2.80),
        ("football_result", "draw", None, "X", 3.50),
        ("football_result", "away", None, "2", 2.12),
        ("football_total_goals", "under", 2.5, "0-2", 2.22),
        ("football_total_goals", "over", 2.5, "3+", 1.54),
    }


def test_parse_football_double_chance_bulk_match_emits_double_chance(football_bulk_data):
    match = football_bulk_data["42312714"]
    results = _parse_football_double_chance_bulk_match(match)

    assert len(results) == 3
    assert {
        (r.market_type, r.outcome_code, r.raw_label, r.odds)
        for r in results
    } == {
        ("football_double_chance", "home_or_draw", "1X", 1.58),
        ("football_double_chance", "home_or_away", "12", 1.23),
        ("football_double_chance", "draw_or_away", "X2", 1.34),
    }


def test_parse_football_outcome_match_skips_invalid_rows():
    match = {
        "home": "Home",
        "away": "Away",
        "leagueName": "Test League",
        "kickOffTime": 1778002200000,
        "odds": {
            "1": 0,
            "2": -1,
            "3": "bad",
            "22": 2.05,
        },
    }

    results = _parse_football_outcome_match(match)

    assert len(results) == 1
    assert results[0].market_type == "football_total_goals"
    assert results[0].outcome_code == "under"


def test_parse_football_outcome_match_requires_teams_and_odds_map():
    assert _parse_football_outcome_match({"away": "Away", "odds": {"1": 1.9}}) == []
    assert _parse_football_outcome_match({"home": "Home", "odds": {"1": 1.9}}) == []
    assert _parse_football_outcome_match({"home": "Home", "away": "Away", "odds": []}) == []


def test_parse_football_double_chance_bulk_match_dedupes_duplicate_groups():
    match = {
        "home": "Home",
        "away": "Away",
        "leagueName": "Test League",
        "kickOffTime": 1778002200000,
        "odBetPickGroups": [
            {"tipTypes": [{"tipTypeId": 7, "value": 1.50}]},
            {"tipTypes": [{"tipTypeId": 7, "value": 9.99}, {"tipTypeId": 8, "value": 1.25}]},
        ],
    }

    results = _parse_football_double_chance_bulk_match(match)

    assert [(r.outcome_code, r.odds) for r in results] == [
        ("home_or_draw", 1.50),
        ("home_or_away", 1.25),
    ]


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
async def test_scraper_list_requests_use_configured_lookahead_hours(monkeypatch):
    scraper = OktagonBetScraper()
    captured_params: list[dict] = []
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 36)

    async def mock_get(url, **kwargs):
        captured_params.append(kwargs.get("params", {}))
        return {"esMatches": []}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert captured_params
    assert all(params["hours"] == "36" for params in captured_params)


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


# ── Tennis outcome offers ────────────────────────────────


def test_parse_tennis_outcome_match_emits_match_winner_offers():
    match = {
        "id": 42345455,
        "home": "Tiago Pereira",
        "away": "Joao Domingues",
        "leagueName": "ITF M25 ~ Loule (Portugal)",
        "kickOffTime": 1778407200000,
        "live": False,
        "blocked": False,
        "odds": {"1": 1.4, "3": 2.8, "50538": 1.83},
    }

    results = _parse_tennis_outcome_match(match)

    assert len(results) == 2
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert {r.bookmaker_id for r in results} == {"oktagonbet"}
    assert {r.sport for r in results} == {"tennis"}
    assert {r.league_id for r in results} == {"itf_m25_loule_(portugal)"}
    assert {r.home_team for r in results} == {"Tiago Pereira"}
    assert {r.away_team for r in results} == {"Joao Domingues"}
    assert {r.market_type for r in results} == {"tennis_match_winner"}
    assert {r.source_url for r in results} == {
        "https://www.oktagonbet.com/sr/sportsko-kladjenje/tenis/T"
    }
    assert {
        (r.outcome_code, r.raw_label, r.odds, r.line, r.start_time)
        for r in results
    } == {
        ("home", "1", 1.4, None, _parse_start_time(1778407200000)),
        ("away", "2", 2.8, None, _parse_start_time(1778407200000)),
    }


def test_parse_tennis_outcome_match_allows_future_live_flagged_prematch():
    results = _parse_tennis_outcome_match(
        {
            "home": "Tiago Pereira",
            "away": "Joao Domingues",
            "leagueName": "ITF M25 ~ Loule (Portugal)",
            "kickOffTime": _tennis_kickoff_ms(minutes=10),
            "live": True,
            "blocked": False,
            "odds": {"1": 1.4, "3": 2.8},
        },
        now=TENNIS_NOW,
    )

    assert len(results) == 2
    assert {row.outcome_code for row in results} == {"home", "away"}
    assert {row.start_time for row in results} == {"2026-05-10T12:10:00+00:00"}


def test_parse_tennis_outcome_match_skips_live_rows_near_start_or_past():
    base_match = {
        "home": "Tiago Pereira",
        "away": "Joao Domingues",
        "leagueName": "ITF M25 ~ Loule (Portugal)",
        "kickOffTime": _tennis_kickoff_ms(seconds=35),
        "live": False,
        "blocked": False,
        "odds": {"1": 1.4, "3": 2.8},
    }

    near_start = {**base_match, "live": True}
    past_start = {
        **base_match,
        "live": True,
        "kickOffTime": _tennis_kickoff_ms(minutes=-1),
    }

    assert _parse_tennis_outcome_match(near_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(near_start, now=TENNIS_NOW) == "live_near_or_past_start"
    assert _parse_tennis_outcome_match(past_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(past_start, now=TENNIS_NOW) == "live_near_or_past_start"


def test_parse_tennis_outcome_match_skips_live_rows_with_bad_start_time():
    missing_start = {
        "home": "Tiago Pereira",
        "away": "Joao Domingues",
        "leagueName": "ITF M25 ~ Loule (Portugal)",
        "live": True,
        "blocked": False,
        "odds": {"1": 1.4, "3": 2.8},
    }
    invalid_start = {**missing_start, "kickOffTime": "not-an-epoch"}

    assert _parse_tennis_outcome_match(missing_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(missing_start, now=TENNIS_NOW) == "missing_start_time"
    assert _parse_tennis_outcome_match(invalid_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(invalid_start, now=TENNIS_NOW) == "invalid_start_time"


def test_parse_tennis_outcome_match_skip_precedence_for_blocked_and_doubles():
    future_live_match = {
        "home": "Tiago Pereira",
        "away": "Joao Domingues",
        "leagueName": "ITF M25 ~ Loule (Portugal)",
        "kickOffTime": _tennis_kickoff_ms(minutes=10),
        "live": True,
        "blocked": False,
        "odds": {"1": 1.4, "3": 2.8},
    }

    blocked = {**future_live_match, "blocked": True}
    doubles_league = {**future_live_match, "leagueName": "ATP Doubles ~ Rome"}
    doubles_home = {**future_live_match, "home": "A. Player/B. Player"}
    missing_home = {**future_live_match, "home": ""}
    invalid_odds = {**future_live_match, "odds": []}

    assert _parse_tennis_outcome_match(blocked, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(blocked, now=TENNIS_NOW) == "blocked"
    assert _parse_tennis_outcome_match(doubles_league, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(doubles_league, now=TENNIS_NOW) == "doubles"
    assert _parse_tennis_outcome_match(doubles_home, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(doubles_home, now=TENNIS_NOW) == "doubles"
    assert _parse_tennis_outcome_match(missing_home, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(missing_home, now=TENNIS_NOW) == "missing_competitor"
    assert _parse_tennis_outcome_match(invalid_odds, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(invalid_odds, now=TENNIS_NOW) == "invalid_odds_map"


def test_parse_tennis_outcome_match_skips_invalid_rows():
    assert _parse_tennis_outcome_match({"away": "Away", "odds": {"1": 1.9}}) == []
    assert _parse_tennis_outcome_match({"home": "Home", "odds": {"1": 1.9}}) == []
    assert _parse_tennis_outcome_match({"home": "Home", "away": "Away", "odds": []}) == []
    assert _parse_tennis_outcome_match(
        {"home": "Home", "away": "Away", "odds": {"1": 0, "3": "bad"}}
    ) == []


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
    assert scraper.get_supported_outcome_sports() == ["football", "tennis"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_uses_list_and_bulk(football_data, football_bulk_data):
    scraper = OktagonBetScraper()
    calls: list[tuple[str, dict]] = []

    async def mock_get(url, **kwargs):
        calls.append((url, kwargs.get("params", {})))
        if "/sport/S/mob" in url:
            return football_data
        raise AssertionError(f"Unexpected GET URL: {url}")

    async def mock_put(url, **kwargs):
        assert "prematchesByIds.html" in url
        assert kwargs["json_body"] == [42312714]
        return football_bulk_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get), \
         patch.object(scraper._http, "put_json", side_effect=mock_put) as put_mock:
        results = await scraper.scrape_outcome_offers("football")

    assert len(results) == 8
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert len(calls) == 1
    assert calls[0][0].endswith("/sport/S/mob")
    assert calls[0][1]["hours"]
    assert put_mock.await_count == 1


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_returns_list_offers_when_bulk_missing(football_data):
    scraper = OktagonBetScraper()

    async def mock_get(url, **kwargs):
        if "/sport/S/mob" in url:
            return football_data
        raise AssertionError(f"Unexpected GET URL: {url}")

    async def mock_put(url, **kwargs):
        return {}

    with patch.object(scraper._http, "get_json", side_effect=mock_get), \
         patch.object(scraper._http, "put_json", side_effect=mock_put):
        results = await scraper.scrape_outcome_offers("football")

    assert len(results) == 5
    assert {r.market_type for r in results} == {"football_result", "football_total_goals"}


@pytest.mark.asyncio
async def test_scrape_outcome_offers_unsupported_sport_returns_empty():
    scraper = OktagonBetScraper()

    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get, \
         patch.object(scraper._http, "put_json", new_callable=AsyncMock) as mock_put:
        results = await scraper.scrape_outcome_offers("basketball")

    assert results == []
    mock_get.assert_not_called()
    mock_put.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_outcome_offers_tennis_uses_one_list_call_without_bulk():
    scraper = OktagonBetScraper()
    calls: list[tuple[str, dict]] = []
    tennis_data = {
        "esMatches": [
            {
                "id": 42345455,
                "home": "Tiago Pereira",
                "away": "Joao Domingues",
                "leagueName": "ITF M25 ~ Loule (Portugal)",
                "kickOffTime": 1778407200000,
                "live": False,
                "blocked": False,
                "odds": {"1": 1.4, "3": 2.8},
            },
            {
                "id": 42340299,
                "home": "Cadenasso G./Vasami J.",
                "away": "Granollers M./Zeballos H.",
                "leagueName": "ATP Doubles ~ Rome (Italy)-STB",
                "kickOffTime": 1778407200000,
                "live": False,
                "blocked": False,
                "odds": {"1": 7.4, "3": 1.05},
            },
            {
                "id": 42340298,
                "home": "Taisei Ichikawa",
                "away": "Uisung Park",
                "leagueName": "ITF M15 ~ Wuning (China)",
                "kickOffTime": int(
                    (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).timestamp()
                    * 1000
                ),
                "live": True,
                "blocked": False,
                "odds": {"1": 2.8, "3": 1.4},
            },
        ]
    }

    async def mock_get(url, **kwargs):
        calls.append((url, kwargs.get("params", {})))
        if "/sport/T/mob" in url:
            return tennis_data
        raise AssertionError(f"Unexpected GET URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get), \
         patch.object(scraper._http, "put_json", new_callable=AsyncMock) as mock_put:
        results = await scraper.scrape_outcome_offers("tennis")

    assert len(results) == 4
    assert all(r.sport == "tennis" for r in results)
    assert len(calls) == 1
    assert calls[0][0].endswith("/sport/T/mob")
    assert calls[0][1]["hours"]
    mock_put.assert_not_called()
