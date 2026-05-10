from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.merkurxtip_scraper import (
    MerkurXTipScraper,
    _parse_match_detail,
    _parse_game_total_ot_match,
    _parse_handicap_ot_match,
    _parse_football_outcome_match,
    _parse_tennis_outcome_match,
    _tennis_skip_reason,
    _get_player_matches,
    _get_total_match_ids,
    _parse_start_time,
    _extract_league_id,
    _extract_plain_league_id,
)
from app.models.schemas import RawOddsData, RawOutcomeOffer

LEAGUE_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "merkurxtip_league.json"
MATCH_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "merkurxtip_match.json"
TOTALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "merkurxtip_game_total_ot.json"
FOOTBALL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "merkurxtip_football_offer.json"
TENNIS_NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


def _tennis_kickoff_ms(**delta_kwargs) -> int:
    return int((TENNIS_NOW + timedelta(**delta_kwargs)).timestamp() * 1000)


@pytest.fixture
def league_data() -> dict:
    with open(LEAGUE_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def match_data() -> dict:
    with open(MATCH_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def totals_data() -> dict:
    with open(TOTALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def football_data() -> dict:
    with open(FOOTBALL_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def player_matches(league_data) -> list[dict]:
    """Extract only player matches from league fixture."""
    return [
        m for m in league_data["esMatches"]
        if "igrači" in m.get("leagueName", "").lower()
        and m.get("params", {}).get("ouPlPoints")
    ]


# ── Unit tests for helpers ────────────────────────────────


def test_parse_start_time():
    result = _parse_start_time(1775923200000)
    assert result is not None
    assert "2026" in result


def test_parse_start_time_numeric_string():
    assert _parse_start_time("1778407200000") == "2026-05-10T10:00:00+00:00"


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


def test_parse_start_time_zero():
    assert _parse_start_time(0) is None
    assert _parse_start_time("0") is None


# ── _extract_league_id ────────────────────────────────────


def test_extract_league_id_acb():
    assert _extract_league_id("ACB Igrači") == "acb"


def test_extract_league_id_nba():
    assert _extract_league_id("NBA Igrači") == "nba"


def test_extract_league_id_euroleague():
    assert _extract_league_id("Euroleague Igrači") == "euroleague"


def test_extract_league_id_only_igraci():
    assert _extract_league_id("Igrači") == "basketball"


def test_extract_league_id_no_igraci():
    assert _extract_league_id("ACB Liga") == "acb liga"


def test_extract_league_id_empty():
    assert _extract_league_id("") == "basketball"


def test_extract_league_id_case_insensitive():
    assert _extract_league_id("acb igrači") == "acb"
    assert _extract_league_id("ACB IGRAČI") == "acb"


def test_extract_plain_league_id_for_tennis():
    assert _extract_plain_league_id("Rome Masters", "tennis") == "rome_masters"
    assert _extract_plain_league_id(" ", "tennis") == "tennis"


# ── _get_player_matches ─────────────────────────────────


def test_get_player_matches(league_data):
    matches = _get_player_matches(league_data["esMatches"])
    assert [m["id"] for m in matches] == [132935920, 132935921]


def test_get_player_matches_filters_non_player_and_empty_odds():
    matches = [
        {
            "id": 1,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
        {
            "id": 2,
            "leagueName": "ACB Liga",
            "params": {"ouPlPoints": "18.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
        {
            "id": 3,
            "leagueName": "ACB Igrači",
            "params": {"unrelated": "1.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
        {
            "id": 4,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
            "odds": {},
        },
    ]
    assert [m["id"] for m in _get_player_matches(matches)] == [1]


def test_get_player_matches_empty():
    assert _get_player_matches([]) == []


def test_get_player_matches_does_not_require_id():
    matches = [
        {
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
    ]
    assert _get_player_matches(matches) == matches


def test_get_player_matches_includes_non_points_markets():
    matches = [
        {
            "id": 1,
            "leagueName": "ACB Igrači",
            "params": {"ouPlTPRA": "45.5"},
            "odds": {"55215": 1.9, "55217": 1.9},
        },
        {
            "id": 2,
            "leagueName": "ACB Igrači",
            "params": {"ouPlRebounds": "5.5"},
            "odds": {"51685": 1.9, "51687": 1.9},
        },
    ]
    assert [m["id"] for m in _get_player_matches(matches)] == [1, 2]


def test_get_player_matches_skips_no_odds_params(league_data):
    """List-only player parsing cannot recover rows that are missing inline odds."""
    ids = [m["id"] for m in _get_player_matches(league_data["esMatches"])]
    assert 132935923 not in ids


def test_get_total_match_ids(totals_data):
    assert _get_total_match_ids(totals_data["list"]["esMatches"]) == [132948727]


# ── _parse_match_detail ───────────────────────────────────


def test_parse_match_detail_returns_data(match_data):
    results = _parse_match_detail(match_data)
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


def test_parse_match_detail_bookmaker_id(match_data):
    for r in _parse_match_detail(match_data):
        assert r.bookmaker_id == "merkurxtip"


def test_parse_match_detail_has_player_names(match_data):
    for r in _parse_match_detail(match_data):
        assert r.player_name == "J.Batemon"


def test_parse_match_detail_has_thresholds(match_data):
    for r in _parse_match_detail(match_data):
        assert r.threshold > 0


def test_parse_match_detail_has_odds(match_data):
    results = _parse_match_detail(match_data)
    with_both = [r for r in results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_match_detail_league_id(match_data):
    results = _parse_match_detail(match_data)
    assert all(r.league_id == "acb" for r in results)


def test_parse_match_detail_market_types(match_data):
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
    for r in _parse_match_detail(match_data):
        assert r.market_type in valid_types


def test_parse_match_detail_empty():
    assert _parse_match_detail({}) == []


def test_parse_match_detail_with_alt_thresholds():
    """Match with alt thresholds produces multiple player_points entries."""
    match = {
        "home": "LeBron James",
        "away": "LA Lakers",
        "leagueName": "NBA Igrači",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "26.5", "ouPlP2": "24.5", "ouPlP3": "28.5"},
        "odds": {
            "51679": 1.94, "51681": 1.86,
            "55253": 1.6, "55255": 2.15,
            "55256": 2.3, "55258": 1.53,
        },
    }
    results = _parse_match_detail(match)
    assert len(results) == 3
    thresholds = sorted([r.threshold for r in results])
    assert thresholds == [24.5, 26.5, 28.5]
    assert all(r.market_type == "player_points" for r in results)


def test_parse_match_detail_all_markets_and_ladders():
    """Full match with all threshold lines + fixed ladders."""
    match = {
        "home": "Nikola Jokic",
        "away": "Denver Nuggets",
        "leagueName": "NBA Igrači",
        "kickOffTime": 1775923200000,
        "params": {
            "ouPlPoints": "28.5",
            "ouPl3Points": "1.5",
            "ouPlSt": "1.5",
            "ouPlB": "0.5",
            "ouPlTPR": "40.5",
            "ouPlTPA": "36.5",
            "ouPlTRA": "12.5",
            "ouPlTPRA": "46.5",
        },
        "odds": {
            "51679": 1.91, "51681": 1.89,
            "51688": 1.7, "51690": 2.0,
            "55672": 1.8, "55674": 1.95,
            "55681": 2.1, "55683": 1.65,
            "55244": 1.85, "55246": 1.91,
            "55247": 1.78, "55249": 1.98,
            "55250": 1.82, "55252": 1.92,
            "55215": 1.76, "55217": 2.04,
            "54096": 1.05,
            "54111": 1.32,
            "54141": 8.5,
        },
    }

    results = _parse_match_detail(match)
    markets = {(r.market_type, r.threshold) for r in results}

    assert ("player_points", 28.5) in markets
    assert ("player_3points", 1.5) in markets
    assert ("player_steals", 1.5) in markets
    assert ("player_blocks", 0.5) in markets
    assert ("player_points_rebounds", 40.5) in markets
    assert ("player_points_assists", 36.5) in markets
    assert ("player_rebounds_assists", 12.5) in markets
    assert ("player_points_rebounds_assists", 46.5) in markets

    ladder_results = [
        r for r in results
        if r.market_type == "player_points_milestones" and r.under_odds is None
    ]
    assert {(r.threshold, r.over_odds) for r in ladder_results} == {
        (4.5, 1.05),
        (19.5, 1.32),
        (49.5, 8.5),
    }


def test_parse_match_detail_fixture_all_ladders(match_data):
    """The match fixture has all 11 fixed ladder entries."""
    results = _parse_match_detail(match_data)
    ladder = [r for r in results if r.market_type == "player_points_milestones"]
    assert len(ladder) == 11
    thresholds = sorted([r.threshold for r in ladder])
    assert thresholds == [4.5, 9.5, 14.5, 19.5, 24.5, 29.5, 34.5, 39.5, 44.5, 49.5, 59.5]


def test_parse_match_detail_fixture_all_threshold_lines(match_data):
    """The match fixture has all 12 threshold lines (3 points + 9 other markets)."""
    results = _parse_match_detail(match_data)
    threshold_results = [r for r in results if r.market_type != "player_points_milestones"]
    assert len(threshold_results) == 12


def test_parse_list_player_match_documents_fast_coverage():
    match = {
        "home": "Jokic N.",
        "away": "Denver Nuggets",
        "leagueName": "NBA Igrači",
        "kickOffTime": 1775923200000,
        "params": {
            "ouPlPoints": "28.5",
            "ouPlRebounds": "12.5",
            "ouPlAssists": "8.5",
            "ouPl3Points": "1.5",
            "ouPlTPRA": "49.5",
        },
        "odds": {
            "51679": 1.91,
            "51681": 1.89,
            "51685": 1.82,
            "51687": 1.98,
            "51682": 1.77,
            "51684": 2.05,
            "51688": 1.7,
            "51690": 2.0,
            "55215": 1.76,
            "55217": 2.04,
        },
    }

    results = _parse_match_detail(match)

    assert {(r.market_type, r.threshold) for r in results} == {
        ("player_points", 28.5),
        ("player_rebounds", 12.5),
        ("player_assists", 8.5),
        ("player_3points", 1.5),
        ("player_points_rebounds_assists", 49.5),
    }


def test_parse_match_detail_missing_threshold():
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "ACB Igrači",
        "kickOffTime": 1775923200000,
        "params": {},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_non_player_league():
    match = {
        "home": "Real Madrid",
        "away": "Barcelona",
        "leagueName": "ACB Liga",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_no_odds():
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "ACB Igrači",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_malformed_threshold():
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "ACB Igrači",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "not_a_number"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_parse_game_total_ot_match_from_list_fixture(totals_data):
    results = _parse_game_total_ot_match(totals_data["list"]["esMatches"][0])

    assert len(results) == 1
    assert results[0].market_type == "game_total_ot"
    assert results[0].league_id == "nba"
    assert results[0].player_name is None
    assert (results[0].threshold, results[0].over_odds, results[0].under_odds) == (
        222.5,
        1.9,
        1.9,
    )


def test_parse_game_total_ot_match_from_detail_fixture(totals_data):
    results = _parse_game_total_ot_match(totals_data["detail"])

    assert len(results) == 9
    assert all(r.market_type == "game_total_ot" for r in results)
    assert sorted((r.threshold, r.over_odds, r.under_odds) for r in results) == [
        (218.5, 1.6, 2.28),
        (219.5, 1.7, 2.18),
        (220.5, 1.75, 2.1),
        (221.5, 1.8, 2.0),
        (222.5, 1.9, 1.9),
        (223.5, 2.0, 1.8),
        (224.5, 2.1, 1.75),
        (225.5, 2.2, 1.67),
        (226.5, 2.28, 1.6),
    ]


def test_parse_game_total_ot_match_skips_player_market():
    match = {
        "id": 1,
        "home": "Jokic N.",
        "away": "Denver",
        "leagueName": "NBA Igrači",
        "params": {"overUnderOvertime": "222.5"},
        "odds": {"50444": 1.9, "50445": 1.9},
    }
    assert _parse_game_total_ot_match(match) == []


# ── Handicap (+OT) parsing ──────────────────────────────────────────────


def test_parse_handicap_ot_match_positive_line_means_team1_favoured():
    """MerkurXTip's ``handicapOvertime`` is the home team's signed
    Asian-handicap line (negative = home favourite, positive = home
    underdog — same as Mozzart's ``Hendikep -X`` UI).  The parser
    negates the source so positive threshold = home favoured.

    Real live shape: Orlando vs Detroit (Detroit is favourite) returned
    ``handicapOvertime='4.5'`` with code 50430 = home covers, code
    50431 = away covers.  Source +4.5 → threshold = -4.5 (Orlando is
    the underdog by 4.5).
    """
    match = {
        "id": 1,
        "home": "Orlando",
        "away": "Detroit",
        "leagueName": "USA NBA",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "4.5"},
        "odds": {"50430": 1.85, "50431": 1.95},
    }
    results = _parse_handicap_ot_match(match)
    assert len(results) == 1
    row = results[0]
    assert row.market_type == "home_handicap_ot"
    assert row.threshold == -4.5  # source +4.5 negated to home-margin convention
    assert row.over_odds == 1.85   # 50430 = home covers
    assert row.under_odds == 1.95  # 50431 = away covers
    assert row.home_team == "Orlando"
    assert row.away_team == "Detroit"
    assert row.player_name is None


def test_parse_handicap_ot_match_negative_line_means_team1_underdog():
    """Houston vs LA Lakers (Houston home favourite) returned
    ``handicapOvertime='-4.5'``; source is negated so threshold = +4.5
    (positive = home favourite by 4.5)."""
    match = {
        "id": 2,
        "home": "Houston",
        "away": "LA Lakers",
        "leagueName": "USA NBA",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-4.5"},
        "odds": {"50430": 2.0, "50431": 1.8},
    }
    results = _parse_handicap_ot_match(match)
    assert len(results) == 1
    assert results[0].threshold == 4.5
    assert results[0].over_odds == 2.0   # 50430 = home covers
    assert results[0].under_odds == 1.8  # 50431 = away covers


def test_parse_handicap_ot_match_pickem_zero_line_emits_row():
    """A zero-handicap pick'em is a legitimate line and must emit a row."""
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
        "home": "Jokic N.",
        "away": "Denver",
        "leagueName": "NBA Igrači",
        "params": {"handicapOvertime": "-3.5"},
        "odds": {"50430": 1.9, "50431": 1.9},
    }
    assert _parse_handicap_ot_match(match) == []


def test_parse_handicap_ot_match_skips_unparseable_line_or_no_odds():
    bad_line = {
        "id": 5, "home": "A", "away": "B", "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "garbage"},
        "odds": {"50430": 1.9, "50431": 1.9},
    }
    no_odds = {
        "id": 6, "home": "A", "away": "B", "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-3.5"},
        "odds": {},
    }
    assert _parse_handicap_ot_match(bad_line) == []
    assert _parse_handicap_ot_match(no_odds) == []


def test_parse_game_total_ot_match_does_not_emit_handicap_after_change():
    """Regression: totals parser must not pick up handicapOvertime/codes."""
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


# ── Football outcome offers ────────────────────────────────


def test_parse_football_outcome_match_emits_mvp_markets(football_data):
    results = _parse_football_outcome_match(football_data["esMatches"][0])

    assert len(results) == 8
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert {r.bookmaker_id for r in results} == {"merkurxtip"}
    assert {r.sport for r in results} == {"football"}
    assert {r.league_id for r in results} == {"eredivisie"}
    assert {r.home_team for r in results} == {"Waalwijk"}
    assert {r.away_team for r in results} == {"Willem II"}
    assert {r.start_time for r in results} == {"2026-05-05T16:45:00+00:00"}
    assert {
        (r.market_type, r.outcome_code, r.line, r.raw_label, r.odds)
        for r in results
    } == {
        ("football_result", "home", None, "1", 2.27),
        ("football_result", "draw", None, "X", 3.35),
        ("football_result", "away", None, "2", 3.05),
        ("football_double_chance", "home_or_draw", None, "1X", 1.35),
        ("football_double_chance", "home_or_away", None, "12", 1.30),
        ("football_double_chance", "draw_or_away", None, "X2", 1.60),
        ("football_total_goals", "under", 2.5, "0-2", 2.03),
        ("football_total_goals", "over", 2.5, "3+", 1.73),
    }


def test_parse_football_outcome_match_skips_invalid_rows():
    base_match = {
        "home": "Home",
        "away": "Away",
        "leagueName": "Test League",
        "kickOffTime": 1777999500000,
        "odds": {
            "1": 0,
            "2": -1,
            "3": "bad",
            "7": 1.55,
        },
    }

    results = _parse_football_outcome_match(base_match)

    assert len(results) == 1
    assert results[0].market_type == "football_double_chance"
    assert results[0].outcome_code == "home_or_draw"


def test_parse_football_outcome_match_requires_teams_and_odds_map():
    assert _parse_football_outcome_match({"away": "Away", "odds": {"1": 1.9}}) == []
    assert _parse_football_outcome_match({"home": "Home", "odds": {"1": 1.9}}) == []
    assert _parse_football_outcome_match({"home": "Home", "away": "Away", "odds": []}) == []


# ── Tennis outcome offers ────────────────────────────────


def test_parse_tennis_outcome_match_emits_match_winner_offers():
    match = {
        "id": 133125569,
        "home": "C. Ruud",
        "away": "J. Lehecka",
        "leagueName": "Rome Masters",
        "kickOffTime": 1778403600000,
        "live": False,
        "blocked": False,
        "odds": {"1": 1.55, "3": 2.3, "251": 1.82},
    }

    results = _parse_tennis_outcome_match(match)

    assert len(results) == 2
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert {r.bookmaker_id for r in results} == {"merkurxtip"}
    assert {r.sport for r in results} == {"tennis"}
    assert {r.league_id for r in results} == {"rome_masters"}
    assert {r.home_team for r in results} == {"C. Ruud"}
    assert {r.away_team for r in results} == {"J. Lehecka"}
    assert {r.market_type for r in results} == {"tennis_match_winner"}
    assert {r.source_url for r in results} == {
        "https://www.merkurxtip.rs/sr/sportsko-kladjenje/tenis/T"
    }
    assert {
        (r.outcome_code, r.raw_label, r.odds, r.line, r.start_time)
        for r in results
    } == {
        ("home", "1", 1.55, None, "2026-05-10T09:00:00+00:00"),
        ("away", "2", 2.3, None, "2026-05-10T09:00:00+00:00"),
    }


def test_parse_tennis_outcome_match_allows_future_live_flagged_prematch():
    results = _parse_tennis_outcome_match(
        {
            "id": 133125569,
            "home": "C. Ruud",
            "away": "J. Lehecka",
            "leagueName": "Rome Masters",
            "kickOffTime": str(_tennis_kickoff_ms(minutes=10)),
            "live": True,
            "blocked": False,
            "odds": {"1": 1.55, "3": 2.3, "251": 1.82},
        },
        now=TENNIS_NOW,
    )

    assert len(results) == 2
    assert {row.outcome_code for row in results} == {"home", "away"}
    assert {row.start_time for row in results} == {"2026-05-10T12:10:00+00:00"}


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
        "home": "C. Ruud",
        "away": "J. Lehecka",
        "leagueName": "Rome Masters",
        "kickOffTime": _tennis_kickoff_ms(**delta_kwargs),
        "live": True,
        "blocked": False,
        "odds": {"1": 1.55, "3": 2.3},
    }

    assert _parse_tennis_outcome_match(match, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(match, now=TENNIS_NOW) == expected_reason


def test_parse_tennis_outcome_match_skips_live_rows_with_bad_start_time():
    missing_start = {
        "home": "C. Ruud",
        "away": "J. Lehecka",
        "leagueName": "Rome Masters",
        "live": True,
        "blocked": False,
        "odds": {"1": 1.55, "3": 2.3},
    }
    invalid_start = {**missing_start, "kickOffTime": "not-an-epoch"}

    assert _parse_tennis_outcome_match(missing_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(missing_start, now=TENNIS_NOW) == "missing_start_time"
    assert _parse_tennis_outcome_match(invalid_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(invalid_start, now=TENNIS_NOW) == "invalid_start_time"


def test_parse_tennis_outcome_match_applies_skip_precedence():
    base_match = {
        "home": "C. Ruud",
        "away": "J. Lehecka",
        "leagueName": "Rome",
        "kickOffTime": _tennis_kickoff_ms(minutes=10),
        "live": False,
        "blocked": False,
        "odds": {"1": 1.55, "3": 2.3},
    }

    assert _parse_tennis_outcome_match({**base_match, "live": True}, now=TENNIS_NOW) != []
    assert _parse_tennis_outcome_match({**base_match, "blocked": True}) == []
    assert _tennis_skip_reason({**base_match, "blocked": True}, now=TENNIS_NOW) == "blocked"
    assert _parse_tennis_outcome_match({**base_match, "leagueName": "Rome Doubles"}) == []
    assert _tennis_skip_reason({**base_match, "leagueName": "Rome Doubles"}, now=TENNIS_NOW) == "doubles"
    assert _parse_tennis_outcome_match({**base_match, "home": "A. Player/B. Player"}) == []
    assert _tennis_skip_reason({**base_match, "home": "A. Player/B. Player"}, now=TENNIS_NOW) == "doubles"
    assert _parse_tennis_outcome_match({**base_match, "away": "A. Player/B. Player"}) == []
    assert _tennis_skip_reason({**base_match, "away": "A. Player/B. Player"}, now=TENNIS_NOW) == "doubles"


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


# ── Integration: MerkurXTipScraper with mocked HTTP ──────


@pytest.mark.asyncio
async def test_scraper_returns_data_from_bulk_listing(match_data, league_data):
    scraper = MerkurXTipScraper()
    match_detail_calls: list[str] = []

    async def mock_get(url, **kwargs):
        if url.endswith("/league-group/166/mob"):
            return league_data
        if url.endswith("/sport/B/mob"):
            return {"esMatches": []}
        if "/league/" in url:
            pytest.fail("legacy league fallback should not run when bulk listing has player matches")
        if "/match/" in url:
            match_detail_calls.append(url)
            pytest.fail("player detail calls should not run for list-parseable player props")
        return match_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 3
    assert match_detail_calls == []
    assert all(isinstance(r, RawOddsData) for r in results)


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = MerkurXTipScraper()
    results = await scraper.scrape_odds("euroleague")
    assert results == []


@pytest.mark.asyncio
async def test_list_requests_use_configured_lookahead_hours(monkeypatch):
    scraper = MerkurXTipScraper()
    captured_params: list[dict] = []
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 36)

    async def mock_get(url, **kwargs):
        captured_params.append(kwargs.get("params", {}))
        return {"esMatches": []}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        await scraper._fetch_bulk_player_matches()
        await scraper._fetch_total_matches()

    assert len(captured_params) == 2
    assert all(params["hours"] == "36" for params in captured_params)


@pytest.mark.asyncio
async def test_scraper_empty_league():
    scraper = MerkurXTipScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"esMatches": []}
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = MerkurXTipScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = MerkurXTipScraper()
    assert scraper.get_bookmaker_id() == "merkurxtip"
    assert scraper.get_bookmaker_name() == "MERKUR X TIP"
    assert "basketball" in scraper.get_supported_leagues()
    assert scraper.get_supported_outcome_sports() == ["football", "tennis"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_uses_sport_listing(football_data):
    scraper = MerkurXTipScraper()
    calls: list[tuple[str, dict]] = []

    async def mock_get(url, **kwargs):
        calls.append((url, kwargs.get("params", {})))
        if url.endswith("/sport/S/mob"):
            return football_data
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("football")

    assert len(results) == 8
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert len(calls) == 1
    assert calls[0][0].endswith("/sport/S/mob")
    assert calls[0][1]["hours"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_non_football_returns_empty():
    scraper = MerkurXTipScraper()

    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        results = await scraper.scrape_outcome_offers("basketball")

    assert results == []
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_outcome_offers_tennis_uses_one_sport_listing(caplog):
    scraper = MerkurXTipScraper()
    calls: list[tuple[str, dict]] = []
    caplog.set_level(logging.INFO)
    tennis_data = {
        "esMatches": [
            {
                "id": 133125569,
                "home": "C. Ruud",
                "away": "J. Lehecka",
                "leagueName": "Rome",
                "kickOffTime": 1778403600000,
                "live": False,
                "blocked": False,
                "odds": {"1": 1.55, "3": 2.3},
            },
            {
                "id": 133125570,
                "home": "Future H.",
                "away": "Future A.",
                "leagueName": "Rome",
                "kickOffTime": _tennis_kickoff_ms(minutes=10),
                "live": True,
                "blocked": False,
                "odds": {"1": 1.8, "3": 1.95},
            },
            {
                "id": 133125571,
                "home": "Soon H.",
                "away": "Soon A.",
                "leagueName": "Rome",
                "kickOffTime": _tennis_kickoff_ms(minutes=5),
                "live": True,
                "blocked": False,
                "odds": {"1": 1.8, "3": 1.95},
            },
            {
                "id": 133125545,
                "home": "Cadenasso G./Vasami J.",
                "away": "Granollers M./Zeballos H.",
                "leagueName": "Rome Doubles",
                "kickOffTime": 1778403600000,
                "live": False,
                "blocked": False,
                "odds": {"1": 7.4, "3": 1.05},
            },
        ]
    }

    async def mock_get(url, **kwargs):
        calls.append((url, kwargs.get("params", {})))
        if url.endswith("/sport/T/mob"):
            return tennis_data
        if "/match/" in url:
            pytest.fail("tennis match winner import should not fetch per-match details")
        raise AssertionError(f"Unexpected URL: {url}")

    with (
        patch("app.scrapers.merkurxtip_scraper.current_utc_time", return_value=TENNIS_NOW),
        patch.object(scraper._http, "get_json", side_effect=mock_get),
    ):
        results = await scraper.scrape_outcome_offers("tennis")

    assert len(results) == 4
    assert all(r.sport == "tennis" for r in results)
    assert len(calls) == 1
    assert calls[0][0].endswith("/sport/T/mob")
    assert calls[0][1]["hours"]
    assert "skipped={'live_near_or_past_start': 1, 'doubles': 1}" in caplog.text


@pytest.mark.asyncio
async def test_scraper_falls_back_to_legacy_leagues_when_bulk_listing_empty():
    scraper = MerkurXTipScraper()
    calls: list[str] = []
    league_matches = [
        {
            "id": 201,
            "home": "Player One",
            "away": "Team One",
            "leagueName": "ACB Igrači",
            "kickOffTime": 1775923200000,
            "params": {"ouPlPoints": "26.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
    ]

    async def mock_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/league-group/166/mob"):
            return {"esMatches": []}
        if url.endswith("/sport/B/mob"):
            return {"esMatches": []}
        if "/league/" in url:
            return {"esMatches": league_matches}
        if "/match/" in url:
            pytest.fail("legacy player fallback should parse list odds without detail calls")
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 1
    assert any("/league/" in call for call in calls)


@pytest.mark.asyncio
async def test_scraper_parses_player_list_without_detail_calls():
    league_matches = [
        {
            "id": 201,
            "home": "Player One",
            "away": "Team One",
            "leagueName": "ACB Igrači",
            "kickOffTime": 1775923200000,
            "params": {"ouPlPoints": "26.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
        {
            "id": 202,
            "home": "Player Two",
            "away": "Team Two",
            "leagueName": "ACB Igrači",
            "kickOffTime": 1775923200000,
            "params": {"ouPlPoints": "18.5"},
            "odds": {"51679": 1.8, "51681": 2.0},
        },
        {
            "id": 203,
            "home": "Player Three",
            "away": "Team Three",
            "leagueName": "ACB Igrači",
            "kickOffTime": 1775923200000,
            "params": {"ouPlTPRA": "34.5"},
            "odds": {"55215": 1.87, "55217": 1.93},
        },
    ]

    class StubHttpClient:
        def __init__(self) -> None:
            self.rate_limit_per_second = 4.0
            self.match_detail_calls: list[str] = []

        async def get_json(self, url: str, **kwargs):
            if url.endswith("/league-group/166/mob"):
                return {"esMatches": league_matches}
            if url.endswith("/sport/B/mob"):
                return {"esMatches": []}
            if "/match/" in url:
                self.match_detail_calls.append(url)
                raise Exception("player detail should not be fetched")
            raise AssertionError(f"Unexpected URL: {url}")

    http_client = StubHttpClient()
    scraper = MerkurXTipScraper(http_client=http_client)

    results = await scraper.scrape_odds("basketball")

    assert http_client.match_detail_calls == []
    assert {(r.player_name, r.market_type) for r in results} == {
        ("Player One", "player_points"),
        ("Player Two", "player_points"),
        ("Player Three", "player_points_rebounds_assists"),
    }


@pytest.mark.asyncio
async def test_scraper_player_list_missing_inline_odds_does_not_fetch_detail():
    """If player list rows lack inline odds, skip them instead of fetching detail."""
    league_matches = [
        {
            "id": 301,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
        },
    ]
    detail_calls: list[str] = []

    async def mock_get(url, **kwargs):
        if url.endswith("/league-group/166/mob"):
            return {"esMatches": league_matches}
        if url.endswith("/sport/B/mob"):
            return {"esMatches": []}
        if "/league/" in url:
            return {"esMatches": []}
        if "/match/" in url:
            detail_calls.append(url)
            pytest.fail("player detail should not be fetched when inline odds are missing")
        raise Exception("detail failed")

    scraper = MerkurXTipScraper()
    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert detail_calls == []


@pytest.mark.asyncio
async def test_scraper_partial_mode_returns_list_ot_total_only(totals_data):
    scraper = MerkurXTipScraper()

    async def mock_get(url, **kwargs):
        if url.endswith("/league-group/166/mob"):
            return {"esMatches": []}
        if url.endswith("/sport/B/mob"):
            return totals_data["list"]
        if "/league/" in url:
            return {"esMatches": []}
        if "/match/132948727" in url:
            pytest.fail("partial mode should not fetch total detail")
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 1
    assert {r.market_type for r in results} == {"game_total_ot"}
    assert [(r.threshold, r.over_odds, r.under_odds) for r in results] == [
        (222.5, 1.9, 1.9)
    ]


@pytest.mark.asyncio
async def test_scraper_full_mode_returns_detail_ot_totals(totals_data):
    scraper = MerkurXTipScraper(detail_mode="full")

    async def mock_get(url, **kwargs):
        if url.endswith("/league-group/166/mob"):
            return {"esMatches": []}
        if url.endswith("/sport/B/mob"):
            return totals_data["list"]
        if "/league/" in url:
            return {"esMatches": []}
        if "/match/132948727" in url:
            return totals_data["detail"]
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 9
    assert {r.market_type for r in results} == {"game_total_ot"}
    assert sorted(r.threshold for r in results) == [
        218.5,
        219.5,
        220.5,
        221.5,
        222.5,
        223.5,
        224.5,
        225.5,
        226.5,
    ]


@pytest.mark.asyncio
async def test_scraper_detail_replaces_list_total_line_when_same_threshold(totals_data):
    scraper = MerkurXTipScraper(detail_mode="full")
    list_data = json.loads(json.dumps(totals_data["list"]))
    detail_data = json.loads(json.dumps(totals_data["detail"]))
    list_data["esMatches"][0]["odds"]["50444"] = 1.83
    list_data["esMatches"][0]["odds"]["50445"] = 1.87
    detail_data["kickOffTime"] = list_data["esMatches"][0]["kickOffTime"] + 300000

    async def mock_get(url, **kwargs):
        if url.endswith("/league-group/166/mob"):
            return {"esMatches": []}
        if url.endswith("/sport/B/mob"):
            return list_data
        if "/league/" in url:
            return {"esMatches": []}
        if "/match/132948727" in url:
            return detail_data
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 9
    base_line = next(result for result in results if result.threshold == 222.5)
    assert (base_line.over_odds, base_line.under_odds) == (1.9, 1.9)
    assert base_line.start_time == "2026-04-15T23:35:00+00:00"
