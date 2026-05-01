from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.balkanbet_scraper import (
    BalkanBetScraper,
    _BASKETBALL_SPEC,
    _SPORT_SPECS,
    _extract_league_id,
    _format_filter_from,
    _normalize_start_time,
    _parse_football_outcome_list,
    _parse_game_handicap_ot_list,
    _parse_game_total_ot_list,
    _parse_handicap_outcome_label,
    _parse_player_name,
    _parse_player_props_list,
)
from app.models.schemas import RawOddsData, RawOutcomeOffer

PLAYER_LIST_FIXTURE = Path(__file__).parent / "fixtures" / "balkanbet_player_list.json"
GAME_TOTAL_OT_LIST_FIXTURE = (
    Path(__file__).parent / "fixtures" / "balkanbet_game_total_ot_list.json"
)


@pytest.fixture
def player_list_data() -> dict:
    with open(PLAYER_LIST_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def game_total_ot_list_data() -> dict:
    with open(GAME_TOTAL_OT_LIST_FIXTURE) as f:
        return json.load(f)


# ── _normalize_start_time ─────────────────────────────────


def test_normalize_start_time_z_suffix():
    assert _normalize_start_time("2026-04-11T16:00:00.000Z") == "2026-04-11T16:00:00+00:00"


def test_normalize_start_time_already_canonical():
    assert _normalize_start_time("2026-04-11T16:00:00+00:00") == "2026-04-11T16:00:00+00:00"


def test_normalize_start_time_none():
    assert _normalize_start_time(None) is None


def test_normalize_start_time_invalid():
    assert _normalize_start_time("not-a-date") == "not-a-date"


# ── _format_filter_from ───────────────────────────────────


def test_format_filter_from_uses_naive_belgrade_seconds():
    out = _format_filter_from()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", out)


# ── _parse_player_name ────────────────────────────────────


def test_parse_player_name_normal():
    assert _parse_player_name("A.Plummer (Bosna)") == ("A.Plummer", "Bosna")


def test_parse_player_name_with_spaces():
    assert _parse_player_name("  A.Plummer (Bosna)  ") == ("A.Plummer", "Bosna")


def test_parse_player_name_no_team():
    assert _parse_player_name("A.Plummer") == ("A.Plummer", None)


def test_parse_player_name_empty():
    assert _parse_player_name("") == ("", None)


def test_parse_player_name_nested_parens():
    name, team = _parse_player_name("Some (Team Name)")
    assert name == "Some"
    assert team == "Team Name"


def test_parse_player_name_trailing_dash():
    """NSoft sometimes appends ' -' after the team parenthesis."""
    name, team = _parse_player_name("N.Jokić (Denver) -")
    assert name == "N.Jokić"
    assert team == "Denver"


def test_parse_player_name_trailing_dash_with_spaces():
    name, team = _parse_player_name("J.Harden (Cleveland)  -  ")
    assert name == "J.Harden"
    assert team == "Cleveland"


# ── _extract_league_id ────────────────────────────────────


def test_extract_league_id_known_tournament():
    assert _extract_league_id(2334, 252, _BASKETBALL_SPEC.tournament_league_map) == "euroleague"
    assert _extract_league_id(2334, 29368, _BASKETBALL_SPEC.tournament_league_map) == "aba_liga"


def test_extract_league_id_falls_back_to_tournament_slug():
    assert (
        _extract_league_id(2334, 9999, _BASKETBALL_SPEC.tournament_league_map)
        == "balkanbet_tournament_9999"
    )


def test_extract_league_id_falls_back_to_category_slug():
    assert (
        _extract_league_id(7777, None, _BASKETBALL_SPEC.tournament_league_map)
        == "balkanbet_category_7777"
    )


def test_extract_league_id_default():
    assert _extract_league_id(None, None, _BASKETBALL_SPEC.tournament_league_map) == "basketball"


def test_extract_league_id_coerces_string_tournament_id():
    """Defensive: NSoft can return numeric IDs as strings under some dataFormat options."""
    assert (
        _extract_league_id("2334", "252", _BASKETBALL_SPEC.tournament_league_map)
        == "euroleague"
    )
    assert (
        _extract_league_id(2334, "9999", _BASKETBALL_SPEC.tournament_league_map)
        == "balkanbet_tournament_9999"
    )


def test_extract_league_id_uses_default_arg_for_unknown_sport():
    """Future sports must not silently emit 'basketball' as their league fallback."""
    assert _extract_league_id(None, None, {}, default="football") == "football"


# ── _parse_player_props_list ─────────────────────────────


def test_parse_player_props_list_from_live_fixture(player_list_data):
    """Live response from BalkanBet's WEB_OVERVIEW endpoint must parse cleanly."""
    results = _parse_player_props_list(player_list_data, _BASKETBALL_SPEC)
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert {r.bookmaker_id for r in results} == {"balkanbet"}
    assert "player_points" in {r.market_type for r in results}
    expected_types = {
        "player_points", "player_rebounds", "player_assists", "player_3points",
        "player_points_rebounds", "player_points_assists",
        "player_points_rebounds_assists", "player_points_milestones",
    }
    assert {r.market_type for r in results} <= expected_types
    assert len({r.market_type for r in results}) > 1, "Fixture should produce multiple market types"
    assert {r.sport for r in results} == {"basketball"}
    assert all(r.player_name for r in results)
    assert all(r.threshold is not None for r in results)
    assert all(
        r.over_odds is not None or r.under_odds is not None for r in results
    )


def test_parse_player_props_list_empty():
    assert _parse_player_props_list({}, _BASKETBALL_SPEC) == []
    assert _parse_player_props_list({"data": {}}, _BASKETBALL_SPEC) == []
    assert _parse_player_props_list({"data": {"events": []}}, _BASKETBALL_SPEC) == []


def test_parse_football_outcome_list_emits_mvp_markets():
    data = {
        "data": {
            "events": [
                {
                    "j": "Hatta SC - Al Urooba UAE",
                    "n": "2026-04-29T13:55:00.000Z",
                    "c": 633,
                    "f": 29749,
                    "o": {
                        "6": {
                            "b": 6,
                            "h": [
                                {"e": "1", "g": 2.4},
                                {"e": "X", "g": 3.2},
                                {"e": "2", "g": 2.55},
                            ],
                        },
                        "368": {
                            "b": 368,
                            "h": [
                                {"e": "1X", "g": 1.37},
                                {"e": "12", "g": 1.24},
                                {"e": "X2", "g": 1.42},
                            ],
                        },
                        "443": {
                            "b": 443,
                            "h": [
                                {"e": "0-2", "g": 1.78},
                                {"e": "2+", "g": 1.28},
                                {"e": "3+", "g": 1.82},
                                {"e": "4+", "g": 3.1},
                            ],
                        },
                    },
                }
            ]
        }
    }

    results = _parse_football_outcome_list(data)

    assert len(results) == 10
    assert all(isinstance(row, RawOutcomeOffer) for row in results)
    assert {row.sport for row in results} == {"football"}
    assert {row.start_time for row in results} == {"2026-04-29T13:55:00+00:00"}
    assert {(row.market_type, row.outcome_code, row.line, row.raw_label) for row in results} >= {
        ("football_result", "home", None, "1"),
        ("football_result", "draw", None, "X"),
        ("football_result", "away", None, "2"),
        ("football_double_chance", "home_or_draw", None, "1X"),
        ("football_double_chance", "home_or_away", None, "12"),
        ("football_double_chance", "draw_or_away", None, "X2"),
        ("football_total_goals", "under", 2.5, "0-2"),
        ("football_total_goals", "over", 1.5, "2+"),
        ("football_total_goals", "over", 2.5, "3+"),
        ("football_total_goals", "over", 3.5, "4+"),
    }


def test_parse_player_props_list_skips_unparseable_name():
    data = {
        "data": {
            "events": [
                {
                    "j": "",
                    "n": "2026-04-12T19:00:00.000Z",
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 2402,
                            "g": ["20.5"],
                            "h": [
                                {"e": "Više", "g": 1.7},
                                {"e": "Manje", "g": 2.1},
                            ],
                        }
                    },
                }
            ]
        }
    }
    assert _parse_player_props_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_props_list_skips_market_without_threshold():
    data = {
        "data": {
            "events": [
                {
                    "j": "J.Doe (TeamX)",
                    "n": "2026-04-12T19:00:00.000Z",
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 2402,
                            "g": [],
                            "h": [
                                {"e": "Više", "g": 1.7},
                                {"e": "Manje", "g": 2.1},
                            ],
                        }
                    },
                }
            ]
        }
    }
    assert _parse_player_props_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_props_list_skips_market_with_no_odds():
    data = {
        "data": {
            "events": [
                {
                    "j": "J.Doe (TeamX)",
                    "n": "2026-04-12T19:00:00.000Z",
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 2402,
                            "g": ["20.5"],
                            "h": [],
                        }
                    },
                }
            ]
        }
    }
    assert _parse_player_props_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_props_list_ignores_unrelated_markets():
    data = {
        "data": {
            "events": [
                {
                    "j": "J.Doe (TeamX)",
                    "n": "2026-04-12T19:00:00.000Z",
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 9999,
                            "g": ["20.5"],
                            "h": [{"e": "Više", "g": 1.7}, {"e": "Manje", "g": 2.1}],
                        }
                    },
                }
            ]
        }
    }
    assert _parse_player_props_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_props_list_handles_only_over():
    data = {
        "data": {
            "events": [
                {
                    "j": "J.Doe (TeamX)",
                    "n": "2026-04-12T19:00:00.000Z",
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 2402,
                            "g": ["20.5"],
                            "h": [{"e": "Više", "g": 1.7}],
                        }
                    },
                }
            ]
        }
    }
    results = _parse_player_props_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    assert results[0].over_odds == 1.7
    assert results[0].under_odds is None


# ── _parse_game_total_ot_list ─────────────────────────────


def test_parse_game_total_ot_list_from_fixture(game_total_ot_list_data):
    results = _parse_game_total_ot_list(game_total_ot_list_data, _BASKETBALL_SPEC)
    assert len(results) == 3
    assert all(isinstance(r, RawOddsData) for r in results)
    assert {r.market_type for r in results} == {"game_total_ot"}
    assert all(r.home_team and r.away_team for r in results)
    assert all(r.threshold is not None for r in results)


def test_parse_game_total_ot_list_skips_invalid_match_name():
    data = {
        "data": {
            "events": [
                {
                    "a": 1,
                    "j": "no-separator",
                    "n": "2026-04-12T19:00:00.000Z",
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 530,
                            "g": ["210.5"],
                            "h": [{"e": "Više", "g": 1.9}, {"e": "Manje", "g": 1.9}],
                        }
                    },
                }
            ]
        }
    }
    assert _parse_game_total_ot_list(data, _BASKETBALL_SPEC) == []


# ── _parse_handicap_outcome_label ─────────────────────────


def test_parse_handicap_outcome_label_h1_positive():
    assert _parse_handicap_outcome_label("H1 9.5") == ("H1", 9.5)


def test_parse_handicap_outcome_label_h2_negative():
    assert _parse_handicap_outcome_label("H2 -9.5") == ("H2", -9.5)


def test_parse_handicap_outcome_label_normalises_p_to_h():
    assert _parse_handicap_outcome_label("P1 -6.5") == ("H1", -6.5)
    assert _parse_handicap_outcome_label("P2 +6.5") == ("H2", 6.5)


def test_parse_handicap_outcome_label_zero_pickem():
    assert _parse_handicap_outcome_label("H1 0") == ("H1", 0.0)
    assert _parse_handicap_outcome_label("H2 0") == ("H2", 0.0)


def test_parse_handicap_outcome_label_comma_decimal():
    assert _parse_handicap_outcome_label("H1 1,5") == ("H1", 1.5)


def test_parse_handicap_outcome_label_rejects_non_handicap():
    assert _parse_handicap_outcome_label("Više od 210.5") is None
    assert _parse_handicap_outcome_label("Domaćin Manje od 103.5") is None
    assert _parse_handicap_outcome_label("") is None
    assert _parse_handicap_outcome_label("H3 1.5") is None
    assert _parse_handicap_outcome_label("H1") is None  # missing line
    assert _parse_handicap_outcome_label("H1 abc") is None


# ── _parse_game_handicap_ot_list ──────────────────────────


def _build_handicap_event(
    event_id: int,
    name: str,
    h1_label: str,
    h1_odds: float | None,
    h2_label: str,
    h2_odds: float | None,
    *,
    market_id: int = 524,
    g: list[str] | None = None,
    extra_outcomes: list[dict] | None = None,
) -> dict:
    """Build a minimal NSoft event JSON containing one handicap market.

    Keeps the test fixtures explicit and self-contained so the regression
    intent of each test case is obvious from the call site.
    """
    outcomes: list[dict] = []
    if h1_odds is not None or h1_label:
        outcomes.append({"a": 1, "e": h1_label, "g": h1_odds})
    if h2_odds is not None or h2_label:
        outcomes.append({"a": 2, "e": h2_label, "g": h2_odds})
    if extra_outcomes:
        outcomes.extend(extra_outcomes)
    return {
        "a": event_id,
        "j": name,
        "n": "2026-04-12T19:00:00.000Z",
        "c": 999,
        "f": 999,
        "o": {
            "1": {
                "a": event_id * 10,
                "b": market_id,
                "g": g if g is not None else [],
                "h": outcomes,
            }
        },
    }


def test_parse_game_handicap_ot_list_signed_positive_line():
    """Live shape: ``H1 +9.5`` / ``H2 -9.5`` ⇒ home is the underdog (+9.5)
    so home expected margin = -9.5 ⇒ stored threshold is -9.5."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    1, "Detroit Pistons - Orlando Magic",
                    "H1 9.5", 1.35, "H2 -9.5", 2.85,
                    g=["9.5"],
                )
            ]
        }
    }
    results = _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    r = results[0]
    assert r.market_type == "home_handicap_ot"
    assert r.home_team == "Detroit Pistons"
    assert r.away_team == "Orlando Magic"
    assert r.threshold == -9.5
    assert r.over_odds == 1.35   # home covers
    assert r.under_odds == 2.85  # away covers


def test_parse_game_handicap_ot_list_signed_negative_line():
    """``H1 -3.5`` / ``H2 +3.5`` ⇒ home favoured by 3.5 ⇒ threshold +3.5."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    2, "Orlando Magic - Detroit Pistons",
                    "H1 -3.5", 2.8, "H2 3.5", 1.36,
                    g=["-3.5"],
                )
            ]
        }
    }
    results = _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    assert results[0].threshold == 3.5
    assert results[0].over_odds == 2.8
    assert results[0].under_odds == 1.36


def test_parse_game_handicap_ot_list_legacy_p1_p2_label_shape():
    """Older NSoft responses (and the existing fixture) use ``P1 / P2``
    instead of ``H1 / H2``; the parser must accept both."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    3, "ASVEL Lyon-Villeurbanne - Fenerbahce Istanbul",
                    "P1 -6.5", 1.85, "P2 +6.5", 1.85,
                    g=["6.5"],
                )
            ]
        }
    }
    results = _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    r = results[0]
    # P1 -6.5 means team1 (home) -6.5 ⇒ home favoured by 6.5 ⇒ threshold +6.5
    assert r.threshold == 6.5
    assert r.over_odds == 1.85
    assert r.under_odds == 1.85


def test_parse_game_handicap_ot_list_zero_pickem():
    """Pick'em (line = 0) is a valid handicap; threshold must be 0."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    4, "Team A - Team B",
                    "H1 0", 1.9, "H2 0", 1.9,
                    g=["0"],
                )
            ]
        }
    }
    results = _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    assert results[0].threshold == 0.0
    assert results[0].over_odds == 1.9
    assert results[0].under_odds == 1.9


def test_parse_game_handicap_ot_list_skips_when_h1_line_missing():
    """If the H1 outcome label is missing or unparseable we cannot recover
    the signed line, so the row must be skipped rather than persisting an
    incorrect threshold."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    5, "Team A - Team B",
                    "Garbage label", 1.9, "H2 -3.5", 1.9,
                    g=["3.5"],
                )
            ]
        }
    }
    assert _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC) == []


def test_parse_game_handicap_ot_list_skips_when_no_odds():
    """If both H1 and H2 odds are missing the market is unplayable."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    6, "Team A - Team B",
                    "H1 1.5", None, "H2 -1.5", None,
                    g=["1.5"],
                )
            ]
        }
    }
    assert _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC) == []


def test_parse_game_handicap_ot_list_keeps_partial_one_sided_odds():
    """If only the H1 (or H2) side is priced, still emit the row so the
    discrepancy analyzer can pair it with another bookmaker's same-line
    offering."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    7, "Team A - Team B",
                    "H1 3.5", 2.0, "H2 -3.5", None,
                    g=["3.5"],
                )
            ]
        }
    }
    results = _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    assert results[0].threshold == -3.5
    assert results[0].over_odds == 2.0
    assert results[0].under_odds is None


def test_parse_game_handicap_ot_list_skips_invalid_match_name():
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    8, "no-separator",
                    "H1 1.5", 1.9, "H2 -1.5", 1.9,
                    g=["1.5"],
                )
            ]
        }
    }
    assert _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC) == []


def test_parse_game_handicap_ot_list_ignores_unrelated_markets():
    """Market ids not in ``game_handicap_ot_market_ids`` (e.g. totals
    market 530) must be skipped even when their outcome labels happen to
    look numeric."""
    data = {
        "data": {
            "events": [
                {
                    "a": 9,
                    "j": "Team A - Team B",
                    "n": "2026-04-12T19:00:00.000Z",
                    "o": {
                        "1": {
                            "a": 90,
                            "b": 530,  # totals market, NOT handicap
                            "g": ["207.5"],
                            "h": [
                                {"e": "Više od 207.5", "g": 1.85},
                                {"e": "Manje od 207.5", "g": 1.85},
                            ],
                        }
                    },
                }
            ]
        }
    }
    assert _parse_game_handicap_ot_list(data, _BASKETBALL_SPEC) == []


def test_parse_game_total_ot_list_ignores_handicap_market():
    """The totals parser must not accidentally pick up handicap market 524
    rows (regression for a generic ``g[0]`` consumer)."""
    data = {
        "data": {
            "events": [
                _build_handicap_event(
                    10, "Team A - Team B",
                    "H1 5.5", 1.85, "H2 -5.5", 1.85,
                    g=["5.5"],
                )
            ]
        }
    }
    assert _parse_game_total_ot_list(data, _BASKETBALL_SPEC) == []


def test_parse_game_handicap_ot_list_from_fixture(game_total_ot_list_data):
    """The list-fixture handicap entries (using the legacy P1/P2 label
    shape) must round-trip through the parser and produce the right
    direction."""
    results = _parse_game_handicap_ot_list(
        game_total_ot_list_data, _BASKETBALL_SPEC
    )
    assert len(results) == 1
    r = results[0]
    assert r.market_type == "home_handicap_ot"
    assert r.home_team == "ASVEL Lyon-Villeurbanne"
    assert r.away_team == "Fenerbahce Istanbul"
    # P1 -6.5 → team1 favoured ⇒ threshold = +6.5
    assert r.threshold == 6.5
    assert r.over_odds == 1.85
    assert r.under_odds == 1.85


# ── Scraper integration ──────────────────────────────────


@pytest.mark.asyncio
async def test_scraper_returns_data(player_list_data, game_total_ot_list_data):
    scraper = BalkanBetScraper()

    async def mock_get(url, **kwargs):
        if "/events/" in url:
            pytest.fail("List-only refactor must not issue per-event detail calls")
        sport_id = kwargs.get("params", {}).get("filter[sportId]")
        if sport_id == "273":
            return player_list_data
        if sport_id == "36":
            return game_total_ot_list_data
        return {"data": {"events": []}}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "balkanbet" for r in results)
    assert "player_points" in {r.market_type for r in results}
    assert "game_total_ot" in {r.market_type for r in results}
    assert "home_handicap_ot" in {r.market_type for r in results}


@pytest.mark.asyncio
async def test_scraper_returns_ot_totals_from_basketball_list(game_total_ot_list_data):
    scraper = BalkanBetScraper()

    async def mock_get(url, **kwargs):
        if "/events/" in url:
            pytest.fail("OT totals must be parsed from the list response (no detail calls)")
        if kwargs.get("params", {}).get("filter[sportId]") == "36":
            return game_total_ot_list_data
        return {"data": {"events": []}}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    # Same single fetch produces totals AND handicap rows: 3 totals + 1 handicap.
    market_types = {r.market_type for r in results}
    assert market_types == {"game_total_ot", "home_handicap_ot"}
    assert sum(1 for r in results if r.market_type == "game_total_ot") == 3
    assert sum(1 for r in results if r.market_type == "home_handicap_ot") == 1


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = BalkanBetScraper()
    results = await scraper.scrape_odds("football")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_response():
    scraper = BalkanBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"data": {"events": []}}
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = BalkanBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_keeps_player_points_when_ot_list_fails(player_list_data):
    scraper = BalkanBetScraper()

    async def mock_get(url, **kwargs):
        if "/events/" in url:
            pytest.fail("List-only refactor must not issue per-event detail calls")
        sport_id = kwargs.get("params", {}).get("filter[sportId]")
        if sport_id == "36":
            raise Exception("OT list failed")
        if sport_id == "273":
            return player_list_data
        return {"data": {"events": []}}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert "game_total_ot" not in {r.market_type for r in results}
    assert "player_points" in {r.market_type for r in results}


@pytest.mark.asyncio
async def test_scraper_keeps_ot_totals_when_player_list_fails(game_total_ot_list_data):
    scraper = BalkanBetScraper()

    async def mock_get(url, **kwargs):
        if "/events/" in url:
            pytest.fail("List-only refactor must not issue per-event detail calls")
        if kwargs.get("params", {}).get("filter[sportId]") == "273":
            raise Exception("player list failed")
        return game_total_ot_list_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 4
    market_types = {r.market_type for r in results}
    assert market_types == {"game_total_ot", "home_handicap_ot"}
    assert sum(1 for r in results if r.market_type == "game_total_ot") == 3
    assert sum(1 for r in results if r.market_type == "home_handicap_ot") == 1


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = BalkanBetScraper()
    assert scraper.get_bookmaker_id() == "balkanbet"
    assert scraper.get_bookmaker_name() == "BalkanBet"
    assert "basketball" in scraper.get_supported_leagues()


@pytest.mark.asyncio
async def test_scraper_issues_only_two_requests():
    """List-only refactor: exactly one call per sport (player + totals), no detail fetches."""
    scraper = BalkanBetScraper()
    captured_urls: list[str] = []
    captured_params: list[dict] = []

    async def mock_get(url, **kwargs):
        captured_urls.append(url)
        captured_params.append(kwargs.get("params", {}))
        return {"data": {"events": []}}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        await scraper.scrape_odds("basketball")

    assert len(captured_urls) == 2
    assert all("/events/" not in url.replace("/api/v1/events", "") for url in captured_urls)
    assert {p.get("filter[sportId]") for p in captured_params} == {"273", "36"}


@pytest.mark.asyncio
async def test_scraper_list_request_uses_live_accepted_filter_from_format():
    scraper = BalkanBetScraper()
    captured_params: list[dict] = []

    async def mock_get(url, **kwargs):
        captured_params.append(kwargs.get("params", {}))
        return {"data": {"events": []}}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert captured_params
    assert {p["filter[sportId]"] for p in captured_params} == {"273", "36"}
    assert all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", p["filter[from]"])
        for p in captured_params
    )


@pytest.mark.asyncio
async def test_scraper_list_request_uses_24h_filter_to(monkeypatch):
    scraper = BalkanBetScraper()
    captured_params: list[dict] = []
    fixed_now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 24)
    monkeypatch.setattr("app.scrapers.balkanbet_scraper.current_utc_time", lambda: fixed_now)

    async def mock_get(url, **kwargs):
        captured_params.append(kwargs.get("params", {}))
        return {"data": {"events": []}}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert captured_params
    expected_from = _format_filter_from(fixed_now)
    expected_to = _format_filter_from(fixed_now + timedelta(hours=24))
    assert all(p["filter[from]"] == expected_from for p in captured_params)
    assert all(p["filter[to]"] == expected_to for p in captured_params)


# ── SportSpec extensibility ──────────────────────────────


def test_parse_player_props_list_supports_long_key_format():
    """Defensive: if NSoft stops honoring shortProps=1, parsers must still work."""
    data = {
        "data": {
            "events": [
                {
                    "name": "J.Doe (TeamX)",
                    "startsAt": "2026-04-12T19:00:00.000Z",
                    "categoryId": 2334,
                    "tournamentId": 252,
                    "markets": [
                        {
                            "marketId": 2402,
                            "specialValues": ["20.5"],
                            "outcomes": [
                                {"name": "Više", "odd": 1.7},
                                {"name": "Manje", "odd": 2.1},
                            ],
                        }
                    ],
                }
            ]
        }
    }
    results = _parse_player_props_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    assert results[0].player_name == "J.Doe"
    assert results[0].threshold == 20.5
    assert results[0].over_odds == 1.7
    assert results[0].under_odds == 2.1
    assert results[0].league_id == "euroleague"


def test_parse_game_total_ot_list_supports_long_key_format():
    data = {
        "data": {
            "events": [
                {
                    "name": "Home - Away",
                    "startsAt": "2026-04-12T19:00:00.000Z",
                    "categoryId": 2334,
                    "tournamentId": 29368,
                    "markets": [
                        {
                            "marketId": 530,
                            "specialValues": ["210.5"],
                            "outcomes": [
                                {"name": "Više", "odd": 1.9},
                                {"name": "Manje", "odd": 1.9},
                            ],
                        }
                    ],
                }
            ]
        }
    }
    results = _parse_game_total_ot_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    assert results[0].home_team == "Home"
    assert results[0].away_team == "Away"
    assert results[0].threshold == 210.5
    assert results[0].league_id == "aba_liga"


def test_sport_specs_registry_has_basketball():
    assert "basketball" in _SPORT_SPECS
    assert _SPORT_SPECS["basketball"] is _BASKETBALL_SPEC
    assert _BASKETBALL_SPEC.player_sport_id == "273"
    assert _BASKETBALL_SPEC.totals_sport_id == "36"
    assert 2402 in _BASKETBALL_SPEC.player_market_map
    assert _BASKETBALL_SPEC.player_market_map[2402] == "player_points"
    assert 530 in _BASKETBALL_SPEC.game_total_ot_market_ids


def test_parse_player_props_list_coerces_string_market_id():
    """NSoft may return marketId as a string; parser must coerce to int for map lookup."""
    data = {
        "data": {
            "events": [
                {
                    "j": "J.Doe (TeamX)",
                    "n": "2026-04-12T19:00:00.000Z",
                    "c": 2334,
                    "f": 252,
                    "o": {
                        "1": {
                            "a": 1,
                            "b": "2403",
                            "g": ["8.5"],
                            "h": [{"e": "Više", "g": 1.8}, {"e": "Manje", "g": 1.9}],
                        }
                    },
                }
            ]
        }
    }
    results = _parse_player_props_list(data, _BASKETBALL_SPEC)
    assert len(results) == 1
    assert results[0].market_type == "player_rebounds"


def test_parse_player_props_list_skips_da_ne_markets():
    """Markets 3132/3135 use DA/NE (yes/no) outcomes, not Više/Manje — they must produce no rows."""
    data = {
        "data": {
            "events": [
                {
                    "j": "J.Doe (TeamX)",
                    "n": "2026-04-12T19:00:00.000Z",
                    "c": 2334,
                    "f": 252,
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 3132,
                            "g": [],
                            "h": [{"e": "DA", "g": 4.2}, {"e": "NE", "g": 1.2}],
                        },
                        "2": {
                            "a": 2,
                            "b": 3135,
                            "g": [],
                            "h": [{"e": "DA", "g": 9.0}],
                        },
                    },
                }
            ]
        }
    }
    results = _parse_player_props_list(data, _BASKETBALL_SPEC)
    assert results == []


def test_parse_player_props_multiple_market_types():
    """Parser must emit correct market_type for each market ID."""
    data = {
        "data": {
            "events": [
                {
                    "j": "J.Doe (TeamX)",
                    "n": "2026-04-12T19:00:00.000Z",
                    "c": 2334,
                    "f": 252,
                    "o": {
                        "1": {
                            "a": 1,
                            "b": 2402,
                            "g": ["20.5"],
                            "h": [{"e": "Više", "g": 1.7}, {"e": "Manje", "g": 2.1}],
                        },
                        "2": {
                            "a": 2,
                            "b": 2406,
                            "g": ["5.5"],
                            "h": [{"e": "Više", "g": 1.8}, {"e": "Manje", "g": 1.9}],
                        },
                        "3": {
                            "a": 3,
                            "b": 3087,
                            "g": ["2.5"],
                            "h": [{"e": "Više", "g": 2.0}, {"e": "Manje", "g": 1.7}],
                        },
                    },
                }
            ]
        }
    }
    results = _parse_player_props_list(data, _BASKETBALL_SPEC)
    types = {r.market_type for r in results}
    assert types == {"player_points", "player_assists", "player_3points"}
