from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.scrapers.bookmaker365_scraper import (
    Bookmaker365Scraper,
    _FOOTBALL_BULK_URL,
    _FOOTBALL_LEAGUES_URL,
    _FOOTBALL_LEAGUE_PREVIEW_URL,
    _PLAYER_BULK_URL,
    _PLAYER_LEAGUES_URL,
    _PLAYER_LEAGUE_PREVIEW_URL,
    _REGULAR_BULK_URL,
    _REGULAR_LEAGUES_URL,
    _REGULAR_LEAGUE_PREVIEW_URL,
    _TENNIS_BULK_URL,
    _build_matchup_index,
    _extract_league_id,
    _parse_football_outcome_match,
    _parse_player_match,
    _parse_start_time,
    _parse_tennis_outcome_match,
    _tennis_skip_reason,
    _parse_total_match,
    _GAME_TOTAL_LINES,
    _GAME_TOTAL_OT_LINES,
    _HANDICAP_OT_LINES,
)
from app.models.schemas import RawOutcomeOffer

REGULAR_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bookmaker365_regular_league.json"
PLAYER_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bookmaker365_player_league.json"
FOOTBALL_CATEGORIES_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "bookmaker365_football_categories.json"
)
FOOTBALL_LEAGUE_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "bookmaker365_football_league.json"
)
TENNIS_NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


def _tennis_kickoff_ms(**delta_kwargs) -> int:
    return int((TENNIS_NOW + timedelta(**delta_kwargs)).timestamp() * 1000)

REGULAR_LEAGUES_RESPONSE = {
    "categories": [
        {
            "id": "2228013",
            "name": "NBA / Play Off",
            "type": "LEAGUE",
            "url": "B",
            "count": 8,
        },
        {
            "id": "2293537",
            "name": "Evroliga / Play In",
            "type": "LEAGUE",
            "url": "B",
            "count": 1,
        },
    ]
}
PLAYER_LEAGUES_RESPONSE = {
    "categories": [
        {
            "id": "2300414",
            "name": "NBA / Mućkalica Igrači",
            "type": "LEAGUE",
            "url": "SK",
            "count": 20,
        },
        {
            "id": "2281547",
            "name": "NBA / Play Off / Broj poena,skokova,asistencija",
            "type": "LEAGUE",
            "url": "SK",
            "count": 29,
        },
    ]
}


@pytest.fixture
def regular_preview_data() -> dict:
    with open(REGULAR_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def player_preview_data() -> dict:
    with open(PLAYER_FIXTURE_PATH) as f:
        return json.load(f)


def test_extract_league_id_strips_player_suffixes():
    assert _extract_league_id("NBA / Play Off") == "nba"
    assert _extract_league_id("NBA / Play Off / Broj poena,skokova,asistencija") == "nba"
    assert _extract_league_id("NBA / Mućkalica Igrači") == "nba"
    assert _extract_league_id("Evroliga") == "euroleague"
    assert _extract_league_id("Evroliga / Play Off") == "euroleague"


def test_parse_start_time_handles_numeric_strings_and_zero():
    assert _parse_start_time("1778407200000") == "2026-05-10T10:00:00+00:00"
    assert _parse_start_time(0) is None
    assert _parse_start_time("0") is None


def test_parse_total_match_returns_regular_and_ot_lines(regular_preview_data):
    regular_results = _parse_total_match(regular_preview_data["esMatches"][0], _GAME_TOTAL_LINES)
    ot_results = _parse_total_match(regular_preview_data["esMatches"][0], _GAME_TOTAL_OT_LINES)

    assert {(row.market_type, row.threshold, row.over_odds, row.under_odds) for row in regular_results} == {
        ("game_total", 217.5, 1.85, 1.9),
        ("game_total", 216.5, 1.8, 1.95),
    }
    assert {(row.market_type, row.threshold, row.over_odds, row.under_odds) for row in ot_results} == {
        ("game_total_ot", 218.5, 1.9, 1.85),
        ("game_total_ot", 217.5, 1.85, 1.95),
    }


# ── Handicap (+OT) parsing ──────────────────────────────────────────────


def _build_365_handicap_match() -> dict:
    """Build a list-mode match reproducing the live Bookmaker365 ladder
    observed for Orlando vs Detroit (handicapOvertime through 13).

    Source values are the home team's *signed* Asian-handicap line
    (negative = home favourite, positive = home underdog — same as
    Mozzart's ``Hendikep -X`` UI).  After parsing, the home expected
    margin is the negation of the source value.  Pair codes: even
    (50430, 50432, ..., 50442, 51624, ...) = home covers (over_odds);
    odd (50431, 50433, ..., 50443, 51625, ...) = away covers (under_odds).
    """
    return {
        "id": 12345,
        "leagueName": "NBA / Play Off",
        "home": "Orlando",
        "away": "Detroit",
        "kickOffTime": 1777470900000,
        "params": {
            "handicapOvertime":   "3.5",   # main: home is the underdog by 3.5
            "handicapOvertime2":  "2.5",
            "handicapOvertime3":  "4.5",
            "handicapOvertime4":  "1.5",
            "handicapOvertime5":  "5.5",
            "handicapOvertime6":  "-1.5",
            "handicapOvertime7":  "6.5",
            "handicapOvertime8":  "-2.5",
            "handicapOvertime9":  "7.5",
            "handicapOvertime10": "-3.5",
            "handicapOvertime11": "8.5",
            "handicapOvertime12": "-4.5",
            "handicapOvertime13": "9.5",
        },
        "odds": {
            # N=1 (source 3.5 → threshold -3.5, balanced)
            "50430": 1.9,  "50431": 1.9,
            # N=2 (2.5 → -2.5): home covers harder ⇒ over=2.0
            "50432": 2.0,  "50433": 1.77,
            # N=3 (4.5 → -4.5): home covers easier ⇒ over=1.78
            "50434": 1.78, "50435": 2.0,
            # N=4 (1.5 → -1.5)
            "50436": 2.15, "50437": 1.67,
            # N=5 (5.5 → -5.5)
            "50438": 1.68, "50439": 2.1,
            # N=6 (-1.5 → +1.5, line on the home-favourite side)
            "50440": 2.65, "50441": 1.45,
            # N=7 (6.5 → -6.5)
            "50442": 1.6,  "50443": 2.25,
            # N=8 (-2.5 → +2.5)
            "51624": 2.85, "51625": 1.4,
            # N=9 (7.5 → -7.5)
            "51626": 1.52, "51627": 2.45,
            # N=10 (-3.5 → +3.5)
            "51628": 3.05, "51629": 1.35,
            # N=11 (8.5 → -8.5)
            "51630": 1.45, "51631": 2.65,
            # N=12 (-4.5 → +4.5)
            "51632": 3.25, "51633": 1.30,
            # N=13 (9.5 → -9.5)
            "51634": 1.4,  "51635": 2.85,
        },
    }


def test_parse_total_match_handicap_ladder_full_13_lines():
    """All 13 ladder lines emit one row each.  365 is on the same Tipster
    platform as MerkurXTip / OktagonBet / BetOle / SoccerBet: the source
    value is the home-perspective signed line (negative = home favourite),
    and the parser negates it so positive threshold = home favoured."""
    match = _build_365_handicap_match()
    results = _parse_total_match(match, _HANDICAP_OT_LINES)
    assert len(results) == 13
    assert {r.market_type for r in results} == {"home_handicap_ot"}
    assert {r.home_team for r in results} == {"Orlando"}
    assert {r.away_team for r in results} == {"Detroit"}
    assert all(r.player_name is None for r in results)

    by_threshold = {r.threshold: (r.over_odds, r.under_odds) for r in results}
    # source +3.5 → threshold -3.5 (balanced, home is the underdog)
    assert by_threshold[-3.5] == (1.9, 1.9)
    # source +4.5 → threshold -4.5 (home covers easier here)
    assert by_threshold[-4.5] == (1.78, 2.0)
    # source -1.5 → threshold +1.5 (line on the home-favourite side)
    assert by_threshold[1.5] == (2.65, 1.45)
    # source +9.5 → threshold -9.5 (extreme home-underdog line)
    assert by_threshold[-9.5] == (1.4, 2.85)


def test_parse_total_match_handicap_partial_ladder():
    """A match with fewer ladder lines emits only the present ones."""
    match = {
        "id": 1,
        "leagueName": "NBA",
        "home": "A",
        "away": "B",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "0", "handicapOvertime2": "-1.5"},
        "odds": {"50430": 1.88, "50431": 1.92, "50432": 1.85, "50433": 1.95},
    }
    results = _parse_total_match(match, _HANDICAP_OT_LINES)
    # source 0 → 0; source -1.5 → +1.5
    assert sorted(r.threshold for r in results) == [0.0, 1.5]


def test_parse_total_match_does_not_mix_handicap_with_totals():
    """Regression: parsing for game_total_ot must ignore handicap codes/params."""
    match = _build_365_handicap_match()
    totals = _parse_total_match(match, _GAME_TOTAL_OT_LINES)
    # The fixture has no overUnderOvertime[N] keys, so totals parser yields nothing
    assert totals == []


def test_parse_player_match_uses_super_code_matchup(player_preview_data, regular_preview_data):
    matchup_index = _build_matchup_index(regular_preview_data["esMatches"])

    results = _parse_player_match(player_preview_data["esMatches"][0], matchup_index)

    assert {row.market_type for row in results} == {
        "player_3points",
        "player_assists",
        "player_blocks",
        "player_points",
        "player_points_assists",
        "player_points_rebounds",
        "player_points_rebounds_assists",
        "player_rebounds",
        "player_rebounds_assists",
        "player_steals",
        "player_turnovers",
    }
    assert {row.home_team for row in results} == {"Detroit"}
    assert {row.away_team for row in results} == {"Orlando"}
    assert {row.player_name for row in results} == {"Cade Cunningham"}
    assert all(row.league_id == "nba" for row in results)


def test_parse_player_match_falls_back_to_team_and_kickoff(player_preview_data, regular_preview_data):
    preview_match = {
        key: value for key, value in player_preview_data["esMatches"][0].items() if key != "superCode"
    }
    matchup_index = _build_matchup_index(regular_preview_data["esMatches"])

    results = _parse_player_match(preview_match, matchup_index)

    assert {row.player_name for row in results} == {"Cade Cunningham"}
    assert {row.home_team for row in results} == {"Detroit"}
    assert {row.away_team for row in results} == {"Orlando"}


@pytest.mark.asyncio
async def test_scrape_odds_prefers_bulk_feeds_when_coverage_matches(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
    player_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )
    regular_bulk_data = deepcopy(regular_preview_data)
    regular_bulk_data["esMatches"][0]["params"].update({"handicapOvertime": "3.5"})
    regular_bulk_data["esMatches"][0]["odds"].update({"50430": 1.9, "50431": 1.9})

    async def fake_get_json(url: str, *, params=None, headers=None):
        del headers
        assert params["hours"]
        if url == _REGULAR_BULK_URL:
            return regular_bulk_data
        if url == _PLAYER_BULK_URL:
            return player_preview_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} >= {
        "game_total",
        "game_total_ot",
        "home_handicap_ot",
        "player_assists",
        "player_points",
        "player_rebounds",
    }
    requested_urls = [call.args[0] for call in http_client.get_json.call_args_list]
    assert requested_urls == [_REGULAR_BULK_URL, _PLAYER_BULK_URL]


@pytest.mark.asyncio
async def test_scrape_odds_uses_matched_regular_and_player_leagues(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
    player_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _REGULAR_LEAGUES_URL:
            return REGULAR_LEAGUES_RESPONSE
        if url == _PLAYER_LEAGUES_URL:
            return PLAYER_LEAGUES_RESPONSE
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2228013"):
            return regular_preview_data
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2293537"):
            return {"esMatches": []}
        if url == _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2281547"):
            return player_preview_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} >= {
        "game_total",
        "game_total_ot",
        "player_assists",
        "player_points",
        "player_rebounds",
    }
    assert {row.player_name for row in results if row.player_name} == {
        "Ausar Thompson",
        "Cade Cunningham",
    }
    assert {row.home_team for row in results if row.player_name} == {"Detroit"}
    assert {row.away_team for row in results if row.player_name} == {"Orlando"}

    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2300414") not in requested_urls
    assert _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2293537") in requested_urls


@pytest.mark.asyncio
async def test_scrape_odds_falls_back_when_bulk_player_matchups_are_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
    player_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )
    regular_bulk_data = deepcopy(regular_preview_data)
    regular_bulk_data["esMatches"][0]["params"].update({"handicapOvertime": "3.5"})
    regular_bulk_data["esMatches"][0]["odds"].update({"50430": 1.9, "50431": 1.9})
    unmatched_player_bulk = deepcopy(player_preview_data)
    for match in unmatched_player_bulk["esMatches"]:
        match["superCode"] = 999999
        match["away"] = "Unmatched Team"

    async def fake_get_json(url: str, *, params=None, headers=None):
        del headers
        if url in {_REGULAR_BULK_URL, _PLAYER_BULK_URL}:
            assert params["hours"]
        if url == _REGULAR_BULK_URL:
            return regular_bulk_data
        if url == _PLAYER_BULK_URL:
            return unmatched_player_bulk
        if url == _REGULAR_LEAGUES_URL:
            return REGULAR_LEAGUES_RESPONSE
        if url == _PLAYER_LEAGUES_URL:
            return PLAYER_LEAGUES_RESPONSE
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2228013"):
            return regular_preview_data
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2293537"):
            return {"esMatches": []}
        if url == _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2300414"):
            return {"esMatches": []}
        if url == _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2281547"):
            return player_preview_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert any(row.market_type.startswith("player_") for row in results)
    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert _REGULAR_BULK_URL in requested_urls
    assert _PLAYER_BULK_URL in requested_urls
    assert _REGULAR_LEAGUES_URL in requested_urls
    assert _PLAYER_LEAGUES_URL in requested_urls


@pytest.fixture
def football_categories_data() -> dict:
    return json.loads(FOOTBALL_CATEGORIES_FIXTURE_PATH.read_text())


@pytest.fixture
def football_league_data() -> dict:
    return json.loads(FOOTBALL_LEAGUE_FIXTURE_PATH.read_text())


def test_parse_football_outcome_match_emits_all_three_markets(football_league_data):
    match = football_league_data["esMatches"][0]

    results = _parse_football_outcome_match(match)

    by_outcome = {(r.market_type, r.outcome_code): r for r in results}
    assert (("football_result", "home")) in by_outcome
    assert by_outcome[("football_result", "home")].odds == pytest.approx(1.88)
    assert by_outcome[("football_result", "draw")].raw_label == "X"
    assert by_outcome[("football_result", "away")].odds == pytest.approx(3.62)
    assert by_outcome[("football_double_chance", "home_or_draw")].odds == pytest.approx(1.29)
    assert by_outcome[("football_double_chance", "home_or_away")].raw_label == "12"
    assert by_outcome[("football_double_chance", "draw_or_away")].odds == pytest.approx(1.93)
    assert by_outcome[("football_total_goals", "under")].line == 2.5
    assert by_outcome[("football_total_goals", "under")].odds == pytest.approx(2.91)
    assert by_outcome[("football_total_goals", "over")].line == 2.5
    assert by_outcome[("football_total_goals", "over")].odds == pytest.approx(1.41)

    sample = next(iter(results))
    assert sample.bookmaker_id == "365"
    assert sample.sport == "football"
    assert sample.home_team == "Liverpool"
    assert sample.away_team == "Chelsea"
    assert sample.start_time == "2026-05-09T11:30:00+00:00"


def test_parse_football_outcome_match_skips_zero_or_missing_odds():
    match = {
        "home": "Home",
        "away": "Away",
        "leagueName": "X",
        "kickOffTime": 1778326200000,
        "odds": {"1": 1.5, "2": 0, "3": None, "22": -1, "24": "x"},
    }

    results = _parse_football_outcome_match(match)

    assert {(r.market_type, r.outcome_code) for r in results} == {("football_result", "home")}


def test_parse_football_outcome_match_returns_empty_without_teams():
    match = {
        "home": "",
        "away": "Chelsea",
        "kickOffTime": 1778326200000,
        "odds": {"1": 1.5, "2": 3.0, "3": 2.0},
    }

    assert _parse_football_outcome_match(match) == []


def test_parse_football_outcome_match_recovers_missing_away_from_match_label():
    match = {
        "home": "Liverpool",
        "away": "",
        "name": "Liverpool - Chelsea",
        "leagueName": "Engleska 1",
        "kickOffTime": 1778326200000,
        "odds": {"1": 1.5, "2": 3.0, "3": 2.0},
    }

    results = _parse_football_outcome_match(match)

    assert results
    assert {row.home_team for row in results} == {"Liverpool"}
    assert {row.away_team for row in results} == {"Chelsea"}


def test_parse_football_outcome_match_falls_back_to_default_league():
    # When the league name is missing the parser must fall back to the
    # generic ``football`` league id, NOT 365's basketball default.
    match = {
        "home": "A",
        "away": "B",
        "leagueName": None,
        "kickOffTime": 1778326200000,
        "odds": {"1": 1.5, "2": 3.0, "3": 2.0},
    }

    results = _parse_football_outcome_match(match)

    assert {r.league_id for r in results} == {"football"}


def test_parse_tennis_outcome_match_emits_match_winner_offers():
    match = {
        "id": 47608215,
        "leagueName": "WTA Rome",
        "home": "Pegula J.",
        "away": "Masarova R.",
        "kickOffTime": 1778410800000,
        "live": False,
        "blocked": False,
        "odds": {"1": 1.15, "3": 5.4},
    }

    results = _parse_tennis_outcome_match(match)

    assert len(results) == 2
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert {r.bookmaker_id for r in results} == {"365"}
    assert {r.sport for r in results} == {"tennis"}
    assert {r.league_id for r in results} == {"wta_rome"}
    assert {r.home_team for r in results} == {"Pegula J."}
    assert {r.away_team for r in results} == {"Masarova R."}
    assert {r.market_type for r in results} == {"tennis_match_winner"}
    assert {r.source_url for r in results} == {
        "https://www.365.rs/sr/sportsko-kladjenje/tenis/T"
    }
    assert {
        (r.outcome_code, r.raw_label, r.odds, r.line, r.start_time)
        for r in results
    } == {
        ("home", "1", 1.15, None, _parse_start_time(1778410800000)),
        ("away", "2", 5.4, None, _parse_start_time(1778410800000)),
    }


def test_parse_tennis_outcome_match_allows_future_live_flagged_prematch():
    results = _parse_tennis_outcome_match(
        {
            "id": 47608215,
            "leagueName": "WTA Rome",
            "home": "Pegula J.",
            "away": "Masarova R.",
            "kickOffTime": str(_tennis_kickoff_ms(minutes=10)),
            "live": True,
            "blocked": False,
            "odds": {"1": 1.15, "3": 5.4},
        },
        now=TENNIS_NOW,
    )

    assert len(results) == 2
    assert {row.outcome_code for row in results} == {"home", "away"}
    assert {row.start_time for row in results} == {"2026-05-10T12:10:00+00:00"}


def test_parse_tennis_outcome_match_allows_future_live_after_matchup_recovery():
    results = _parse_tennis_outcome_match(
        {
            "id": 47608215,
            "leagueName": "WTA Rome",
            "home": "",
            "away": "",
            "name": "Pegula J. - Masarova R.",
            "kickOffTime": _tennis_kickoff_ms(minutes=10),
            "live": True,
            "blocked": False,
            "odds": {"1": 1.15, "3": 5.4},
        },
        now=TENNIS_NOW,
    )

    assert len(results) == 2
    assert {row.home_team for row in results} == {"Pegula J."}
    assert {row.away_team for row in results} == {"Masarova R."}


@pytest.mark.parametrize(
    ("delta_kwargs", "expected_reason"),
    [
        ({"minutes": 5}, "live_near_or_past_start"),
        ({"seconds": 35}, "live_near_or_past_start"),
        ({"minutes": -1}, "live_near_or_past_start"),
    ],
)
def test_parse_tennis_outcome_match_skips_live_rows_near_start_or_past(
    delta_kwargs,
    expected_reason,
):
    match = {
        "leagueName": "WTA Rome",
        "home": "Pegula J.",
        "away": "Masarova R.",
        "kickOffTime": _tennis_kickoff_ms(**delta_kwargs),
        "live": True,
        "blocked": False,
        "odds": {"1": 1.15, "3": 5.4},
    }

    assert _parse_tennis_outcome_match(match, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(match, now=TENNIS_NOW) == expected_reason


def test_parse_tennis_outcome_match_skips_live_rows_with_bad_start_time():
    missing_start = {
        "leagueName": "WTA Rome",
        "home": "Pegula J.",
        "away": "Masarova R.",
        "live": True,
        "blocked": False,
        "odds": {"1": 1.15, "3": 5.4},
    }
    invalid_start = {**missing_start, "kickOffTime": "not-an-epoch"}

    assert _parse_tennis_outcome_match(missing_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(missing_start, now=TENNIS_NOW) == "missing_start_time"
    assert _parse_tennis_outcome_match(invalid_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(invalid_start, now=TENNIS_NOW) == "invalid_start_time"


def test_parse_tennis_outcome_match_applies_skip_precedence():
    base_match = {
        "leagueName": "WTA Rome",
        "home": "Pegula J.",
        "away": "Masarova R.",
        "kickOffTime": _tennis_kickoff_ms(minutes=10),
        "live": False,
        "blocked": False,
        "odds": {"1": 1.15, "3": 5.4},
    }

    assert _parse_tennis_outcome_match({**base_match, "live": True}, now=TENNIS_NOW) != []
    assert _parse_tennis_outcome_match({**base_match, "blocked": True}) == []
    assert _tennis_skip_reason({**base_match, "blocked": True}, now=TENNIS_NOW) == "blocked"
    assert _parse_tennis_outcome_match({**base_match, "leagueName": "ATP Doubles Rome"}) == []
    assert _tennis_skip_reason({**base_match, "leagueName": "ATP Doubles Rome"}, now=TENNIS_NOW) == "doubles"
    assert _parse_tennis_outcome_match({**base_match, "home": "Player A./Player B."}) == []
    assert _tennis_skip_reason({**base_match, "home": "Player A./Player B."}, now=TENNIS_NOW) == "doubles"
    assert _parse_tennis_outcome_match({**base_match, "away": "Player A./Player B."}) == []
    assert _tennis_skip_reason({**base_match, "away": "Player A./Player B."}, now=TENNIS_NOW) == "doubles"


def test_parse_tennis_outcome_match_skips_invalid_rows():
    assert _parse_tennis_outcome_match({"away": "Away", "odds": {"1": 1.9}}) == []
    assert _tennis_skip_reason({"away": "Away", "odds": {"1": 1.9}}) == "missing_competitor"
    assert _parse_tennis_outcome_match({"home": "Home", "odds": {"1": 1.9}}) == []
    assert _tennis_skip_reason({"home": "Home", "odds": {"1": 1.9}}) == "missing_competitor"
    assert _parse_tennis_outcome_match({"home": "Home", "away": "Away", "odds": []}) == []
    assert _tennis_skip_reason({"home": "Home", "away": "Away", "odds": []}) == "invalid_odds_map"
    assert _parse_tennis_outcome_match(
        {"home": "Home", "away": "Away", "odds": {"1": 0, "3": "bad"}}
    ) == []


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_prefers_bulk_feed(
    football_league_data,
    monkeypatch,
):
    fixture_start = datetime(2026, 5, 9, 11, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    async def fake_get_json(url: str, *, params=None, headers=None):
        del headers
        assert params["hours"]
        if url == _FOOTBALL_BULK_URL:
            return football_league_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    assert {r.market_type for r in results} == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }
    requested_urls = [call.args[0] for call in http_client.get_json.call_args_list]
    assert requested_urls == [_FOOTBALL_BULK_URL]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_falls_back_when_bulk_incomplete(
    football_categories_data,
    football_league_data,
    monkeypatch,
):
    fixture_start = datetime(2026, 5, 9, 11, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )
    incomplete_bulk = {
        "esMatches": [
            {
                "id": 1,
                "sport": "S",
                "home": "Liverpool",
                "away": "Chelsea",
                "leagueName": "Premijer liga",
                "kickOffTime": int(fixture_start.timestamp() * 1000),
                "odds": {"1": 1.88, "2": 3.4, "3": 3.62},
            }
        ]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _FOOTBALL_BULK_URL:
            return incomplete_bulk
        if url == _FOOTBALL_LEAGUES_URL:
            return football_categories_data
        if url == _FOOTBALL_LEAGUE_PREVIEW_URL.format(league_id="2222588"):
            return football_league_data
        if url == _FOOTBALL_LEAGUE_PREVIEW_URL.format(league_id="2222600"):
            return {"esMatches": []}
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    assert "football_double_chance" in {r.market_type for r in results}
    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert _FOOTBALL_BULK_URL in requested_urls
    assert _FOOTBALL_LEAGUES_URL in requested_urls
    assert _FOOTBALL_LEAGUE_PREVIEW_URL.format(league_id="2222588") in requested_urls


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_fans_out_per_league(
    football_categories_data, football_league_data, monkeypatch
):
    fixture_start = datetime(2026, 5, 9, 11, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    second_league = {
        "esMatches": [
            {
                "id": 99,
                "sport": "S",
                "home": "Real Madrid",
                "away": "Barcelona",
                "leagueName": "Liga Šampiona - Play Off",
                "kickOffTime": int(fixture_start.timestamp() * 1000),
                "odds": {"1": 2.1, "2": 3.5, "3": 3.2, "22": 2.0, "24": 1.8},
            }
        ]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _FOOTBALL_LEAGUES_URL:
            return football_categories_data
        if url == _FOOTBALL_LEAGUE_PREVIEW_URL.format(league_id="2222600"):
            return second_league
        if url == _FOOTBALL_LEAGUE_PREVIEW_URL.format(league_id="2222588"):
            return football_league_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    by_market = {r.market_type for r in results}
    assert by_market == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }
    assert {r.bookmaker_id for r in results} == {"365"}
    assert {r.home_team for r in results} == {"Liverpool", "Real Madrid"}

    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert _FOOTBALL_LEAGUES_URL in requested_urls
    assert _FOOTBALL_LEAGUE_PREVIEW_URL.format(league_id="2222600") in requested_urls
    assert _FOOTBALL_LEAGUE_PREVIEW_URL.format(league_id="2222588") in requested_urls


@pytest.mark.asyncio
async def test_scrape_outcome_offers_non_football_returns_empty():
    http_client = AsyncMock()
    http_client.rate_limit_per_second = 1.0

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("basketball")

    assert results == []
    http_client.get_json.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_outcome_offers_tennis_uses_one_bulk_call_without_fallback(
    monkeypatch,
    caplog,
):
    fixture_start = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    scrape_now = fixture_start - timedelta(hours=1)
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: scrape_now,
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )
    tennis_data = {
        "esMatches": [
            {
                "id": 47608215,
                "leagueName": "WTA Rome",
                "home": "Pegula J.",
                "away": "Masarova R.",
                "kickOffTime": int(fixture_start.timestamp() * 1000),
                "live": False,
                "blocked": False,
                "odds": {"1": 1.15, "3": 5.4},
            },
            {
                "id": 47600001,
                "leagueName": "ATP Doubles Rome",
                "home": "Player A./Player B.",
                "away": "Player C./Player D.",
                "kickOffTime": int(fixture_start.timestamp() * 1000),
                "live": False,
                "blocked": False,
                "odds": {"1": 1.5, "3": 2.4},
            },
            {
                "id": 47608760,
                "leagueName": "ITF Changwon (Žene)",
                "home": "Chang H.",
                "away": "Koike E.",
                "kickOffTime": int(fixture_start.timestamp() * 1000),
                "live": True,
                "blocked": False,
                "odds": {"1": 1.64, "3": 2.1},
            },
            {
                "id": 47608761,
                "leagueName": "ITF Changwon (Žene)",
                "home": "Soon H.",
                "away": "Soon E.",
                "kickOffTime": int((scrape_now + timedelta(minutes=5)).timestamp() * 1000),
                "live": True,
                "blocked": False,
                "odds": {"1": 1.64, "3": 2.1},
            },
        ]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del headers
        assert params["hours"]
        if url == _TENNIS_BULK_URL:
            return tennis_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("tennis")

    assert len(results) == 4
    assert all(row.sport == "tennis" for row in results)
    requested_urls = [call.args[0] for call in http_client.get_json.call_args_list]
    assert requested_urls == [_TENNIS_BULK_URL]
    assert "skipped={'doubles': 1, 'live_near_or_past_start': 1}" in caplog.text


@pytest.mark.asyncio
async def test_scrape_outcome_offers_tennis_has_no_fallback_when_bulk_fails(monkeypatch):
    fixture_start = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start,
    )

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _TENNIS_BULK_URL:
            raise RuntimeError("bulk unavailable")
        raise AssertionError(f"Unexpected fallback URL: {url}")

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("tennis")

    assert results == []
    requested_urls = [call.args[0] for call in http_client.get_json.call_args_list]
    assert requested_urls == [_TENNIS_BULK_URL]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_drops_matches_after_lookahead(
    football_categories_data, monkeypatch
):
    # cutoff = now + 1h; the kickoff is 24h out, so the match must be
    # filtered before it ever reaches the parser.
    fixture_start = datetime(2026, 5, 9, 11, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=24),
    )
    monkeypatch.setattr(
        "app.scrapers.bookmaker365_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=1),
    )

    far_future_league = {
        "esMatches": [
            {
                "id": 1,
                "sport": "S",
                "home": "X",
                "away": "Y",
                "leagueName": "Liga Šampiona - Play Off",
                "kickOffTime": int(fixture_start.timestamp() * 1000),
                "odds": {"1": 1.9},
            }
        ]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _FOOTBALL_LEAGUES_URL:
            return football_categories_data
        return far_future_league

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = Bookmaker365Scraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    assert results == []
