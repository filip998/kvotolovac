from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.admiralbet_scraper import (
    AdmiralBetScraper,
    _BET_TENNIS_MATCH_WINNER,
    _parse_event,
    _parse_event_name,
    _parse_start_time,
    _parse_over_under_bets,
    _parse_milestone_bets,
    _parse_game_total_ot_bets,
    _parse_game_total_ot_event,
    _parse_handicap_ot_bets,
    _parse_handicap_ot_event,
    _extract_league_id,
    _parse_football_outcome_event,
    _parse_tennis_outcome_event,
    _parse_total_line,
    _resolve_total_line,
    _FOOTBALL_OUTCOME_PARAMS,
    _TENNIS_OUTCOME_PARAMS,
    _TENNIS_PAGE_URL,
    _LIST_URL,
)
from app.models.schemas import RawOddsData, RawOutcomeOffer

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "admiralbet_specials.json"
TOTALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "admiralbet_basketball_totals.json"
FOOTBALL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "admiralbet_football.json"


@pytest.fixture
def fixture_data() -> list[dict]:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def totals_fixture_data() -> list[dict]:
    with open(TOTALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def football_fixture_data() -> list[dict]:
    with open(FOOTBALL_FIXTURE_PATH) as f:
        return json.load(f)


# ── Unit tests for helpers ────────────────────────────────


def test_parse_event_name_standard():
    player, team = _parse_event_name("Janari Joesaar - KK Bosna")
    assert player == "Janari Joesaar"
    assert team == "KK Bosna"


def test_parse_event_name_no_separator():
    player, team = _parse_event_name("SomePlayerNoTeam")
    assert player == "SomePlayerNoTeam"
    assert team == ""


def test_parse_event_name_multiple_dashes():
    player, team = _parse_event_name("Jean-Pierre Tokoto - KK Krka Novo mesto")
    assert player == "Jean-Pierre Tokoto"
    assert team == "KK Krka Novo mesto"


def test_parse_start_time_valid():
    # AdmiralBet's naive dateTime already aligns with the other bookmakers'
    # UTC timestamps for the same event, so keep the wall clock intact.
    result = _parse_start_time("2026-04-11T16:00:00")
    assert result is not None
    assert "2026-04-11T16:00:00" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


def test_parse_start_time_invalid():
    assert _parse_start_time("not-a-date") is None


# ── League ID extraction ─────────────────────────────────


def test_extract_league_id_nba():
    assert _extract_league_id("NBA") == "nba"
    assert _extract_league_id("USA NBA") == "nba"


def test_extract_league_id_competition_name():
    assert _extract_league_id("AdmiralBet ABA Liga") == "aba_liga"
    assert _extract_league_id("AdmiralBet ABA liga - plej of") == "aba_liga"
    assert _extract_league_id("Euroleague") == "euroleague"
    assert _extract_league_id("Španija 1") == "španija 1"


def test_extract_league_id_none():
    assert _extract_league_id(None) == "basketball"
    assert _extract_league_id("") == "basketball"


# ── Over/under parsing ──────────────────────────────────


def test_parse_over_under_basic():
    event = {
        "bets": [{
            "betTypeId": 1598,
            "sBV": "10.5",
            "isPlayable": True,
            "betOutcomes": [
                {"name": "manje", "odd": 1.92, "isPlayable": True},
                {"name": "vise", "odd": 1.92, "isPlayable": True},
            ],
        }],
    }
    results = _parse_over_under_bets(event, "Player1", "TeamA", "2026-04-11T16:00:00+00:00", "nba")
    assert len(results) == 1
    assert results[0].threshold == 10.5
    assert results[0].over_odds == 1.92
    assert results[0].under_odds == 1.92
    assert results[0].market_type == "player_points"
    assert results[0].bookmaker_id == "admiralbet"


def test_parse_over_under_multiple_thresholds():
    event = {
        "bets": [
            {"betTypeId": 1598, "sBV": "8.5", "isPlayable": True, "betOutcomes": [
                {"name": "manje", "odd": 2.40, "isPlayable": True},
                {"name": "vise", "odd": 1.48, "isPlayable": True},
            ]},
            {"betTypeId": 1598, "sBV": "10.5", "isPlayable": True, "betOutcomes": [
                {"name": "manje", "odd": 1.92, "isPlayable": True},
                {"name": "vise", "odd": 1.92, "isPlayable": True},
            ]},
            {"betTypeId": 1598, "sBV": "12.5", "isPlayable": True, "betOutcomes": [
                {"name": "manje", "odd": 1.45, "isPlayable": True},
                {"name": "vise", "odd": 2.50, "isPlayable": True},
            ]},
        ],
    }
    results = _parse_over_under_bets(event, "Player1", "TeamA", None, "aba")
    assert len(results) == 3
    assert sorted(r.threshold for r in results) == [8.5, 10.5, 12.5]


def test_parse_over_under_skips_unplayable():
    event = {
        "bets": [{
            "betTypeId": 1598,
            "sBV": "10.5",
            "isPlayable": False,
            "betOutcomes": [
                {"name": "manje", "odd": 1.92, "isPlayable": True},
                {"name": "vise", "odd": 1.92, "isPlayable": True},
            ],
        }],
    }
    results = _parse_over_under_bets(event, "Player1", "TeamA", None, "aba")
    assert len(results) == 0


def test_parse_over_under_skips_no_sbv():
    event = {
        "bets": [{
            "betTypeId": 1598,
            "sBV": None,
            "isPlayable": True,
            "betOutcomes": [
                {"name": "manje", "odd": 1.92, "isPlayable": True},
                {"name": "vise", "odd": 1.92, "isPlayable": True},
            ],
        }],
    }
    results = _parse_over_under_bets(event, "Player1", "TeamA", None, "aba")
    assert len(results) == 0


def test_parse_over_under_partial_odds():
    event = {
        "bets": [{
            "betTypeId": 1598,
            "sBV": "10.5",
            "isPlayable": True,
            "betOutcomes": [
                {"name": "vise", "odd": 1.92, "isPlayable": True},
            ],
        }],
    }
    results = _parse_over_under_bets(event, "Player1", "TeamA", None, "aba")
    assert len(results) == 1
    assert results[0].over_odds == 1.92
    assert results[0].under_odds is None


# ── Game total (+OT) parsing ──────────────────────────────


def test_parse_game_total_ot_bets_multiple_thresholds():
    event = {
        "bets": [
            {
                "betTypeId": 213,
                "betTypeName": "Ukupno (+OT)",
                "sBV": "167.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "Manje", "odd": 1.94, "isPlayable": True},
                    {"name": "Vise", "odd": 1.87, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 213,
                "betTypeName": "Ukupno (+OT)",
                "sBV": "168.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "Manje", "odd": 1.84, "isPlayable": True},
                    {"name": "Vise", "odd": 1.95, "isPlayable": True},
                ],
            },
        ]
    }

    results = _parse_game_total_ot_bets(event, "PAOK", "Aris", None, "grčka 1")

    assert sorted((r.threshold, r.over_odds, r.under_odds) for r in results) == [
        (167.5, 1.87, 1.94),
        (168.5, 1.95, 1.84),
    ]
    assert all(r.market_type == "game_total_ot" for r in results)
    assert all(r.player_name is None for r in results)


def test_parse_game_total_ot_bets_ignores_team_totals_and_handicaps():
    event = {
        "bets": [
            {
                "betTypeId": 728,
                "betTypeName": "Domacin ukupno (+OT)",
                "sBV": "85.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "Manje", "odd": 1.88, "isPlayable": True},
                    {"name": "Vise", "odd": 1.83, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 191,
                "betTypeName": "Hendikep (+OT)",
                "sBV": "3.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.81, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 213,
                "betTypeName": "Ukupno (+OT)",
                "sBV": "168.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "Manje", "odd": 1.84, "isPlayable": True},
                    {"name": "Vise", "odd": 1.95, "isPlayable": True},
                ],
            },
        ]
    }

    results = _parse_game_total_ot_bets(event, "PAOK", "Aris", None, "grčka 1")

    assert len(results) == 1
    assert results[0].threshold == 168.5


# ── Milestone parsing ────────────────────────────────────


def test_parse_milestones_basic():
    event = {
        "bets": [{
            "betTypeId": 1683,
            "isPlayable": True,
            "betOutcomes": [
                {"name": "5+", "odd": 1.15, "isPlayable": True},
                {"name": "10+", "odd": 2.25, "isPlayable": True},
                {"name": "15+", "odd": 6.90, "isPlayable": True},
                {"name": "20+", "odd": 17.0, "isPlayable": True},
                {"name": "25+", "odd": 33.0, "isPlayable": True},
            ],
        }],
    }
    results = _parse_milestone_bets(event, "Player1", "TeamA", None, "aba")
    assert len(results) == 5
    assert sorted(r.threshold for r in results) == [4.5, 9.5, 14.5, 19.5, 24.5]
    assert all(r.under_odds is None for r in results)
    assert all(r.market_type == "player_points_milestones" for r in results)
    assert results[0].over_odds == 1.15


def test_parse_milestones_skips_unplayable():
    event = {
        "bets": [{
            "betTypeId": 1683,
            "isPlayable": True,
            "betOutcomes": [
                {"name": "5+", "odd": 1.15, "isPlayable": False},
                {"name": "10+", "odd": 2.25, "isPlayable": True},
            ],
        }],
    }
    results = _parse_milestone_bets(event, "Player1", "TeamA", None, "aba")
    assert len(results) == 1
    assert results[0].threshold == 9.5


def test_parse_milestones_ignores_unknown_names():
    event = {
        "bets": [{
            "betTypeId": 1683,
            "isPlayable": True,
            "betOutcomes": [
                {"name": "100+", "odd": 999.0, "isPlayable": True},
            ],
        }],
    }
    results = _parse_milestone_bets(event, "Player1", "TeamA", None, "aba")
    assert len(results) == 0


# ── Full event parsing ───────────────────────────────────


def test_parse_event_combines_types():
    event = {
        "name": "Michael Young - KK Bosna",
        "dateTime": "2026-04-11T16:00:00",
        "competitionName": "AdmiralBet ABA Liga",
        "bets": [
            {"betTypeId": 1683, "isPlayable": True, "betOutcomes": [
                {"name": "5+", "odd": 1.15, "isPlayable": True},
            ]},
            {"betTypeId": 1598, "sBV": "10.5", "isPlayable": True, "betOutcomes": [
                {"name": "manje", "odd": 1.92, "isPlayable": True},
                {"name": "vise", "odd": 1.92, "isPlayable": True},
            ]},
        ],
    }
    results = _parse_event(event)
    assert len(results) == 2
    types = {r.market_type for r in results}
    assert types == {"player_points", "player_points_milestones"}
    assert {r.league_id for r in results} == {"aba_liga"}


def test_parse_event_no_team():
    event = {"name": "SomePlayerNoSeparator", "dateTime": "2026-04-11T16:00:00", "bets": []}
    assert _parse_event(event) == []


def test_parse_event_shared_platform_format():
    event = {
        "name": "Kevin Durant - Houston Rockets",
        "dateTime": "2026-04-11T01:30:00",
        "bets": [{"betTypeId": 1598, "sBV": "24.5", "isPlayable": True, "betOutcomes": [
            {"name": "manje", "odd": 1.9, "isPlayable": True},
            {"name": "vise", "odd": 1.9, "isPlayable": True},
        ]}],
    }
    results = _parse_event(event)
    assert len(results) == 1
    assert results[0].home_team == "Houston Rockets"
    assert results[0].away_team == "Kevin Durant"
    assert results[0].player_name == "Kevin Durant"


def test_parse_game_total_ot_event_fixture(totals_fixture_data):
    results = _parse_game_total_ot_event(totals_fixture_data[0])

    assert len(results) == 4
    assert {r.market_type for r in results} == {"game_total_ot"}
    assert {r.home_team for r in results} == {"PAOK"}
    assert {r.away_team for r in results} == {"Aris"}
    assert sorted(r.threshold for r in results) == [167.5, 168.5, 169.5, 170.5]


# ── Handicap (+OT) parsing ───────────────────────────────


def test_parse_handicap_ot_bets_team1_favourite_negative_sbv():
    """sBV is signed; negative means team1 (home) is favoured.

    Real fixture data: Orlando Magic - Detroit Pistons live response had
    handicap rows like ``sBV='-4.5', '1'=1.95, '2'=1.85`` meaning home is
    favoured by 4.5; analyzer should see ``threshold=+4.5`` (home expected
    margin > +4.5 → over, < +4.5 → under).
    """
    event = {
        "bets": [
            {
                "betTypeId": 191,
                "betTypeName": "Hendikep (+OT)",
                "sBV": "-4.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.95, "isPlayable": True},
                    {"name": "2", "odd": 1.85, "isPlayable": True},
                ],
            }
        ]
    }
    results = _parse_handicap_ot_bets(event, "Orlando", "Detroit", None, "nba")
    assert len(results) == 1
    row = results[0]
    assert row.market_type == "home_handicap_ot"
    assert row.threshold == 4.5
    assert row.over_odds == 1.95
    assert row.under_odds == 1.85
    assert row.player_name is None
    assert row.home_team == "Orlando"
    assert row.away_team == "Detroit"


def test_parse_handicap_ot_bets_home_underdog_positive_sbv():
    """Positive sBV means team1 (home) is the underdog.

    Real fixture: ``Orlando vs Detroit`` ladder included ``sBV='1.5'`` with
    "1"=2.26, "2"=1.64 — home is the slight underdog. ``threshold = -1.5``
    (home expected margin around -1.5).
    """
    event = {
        "bets": [
            {
                "betTypeId": 191,
                "betTypeName": "Hendikep (+OT)",
                "sBV": "1.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 2.26, "isPlayable": True},
                    {"name": "2", "odd": 1.64, "isPlayable": True},
                ],
            }
        ]
    }
    results = _parse_handicap_ot_bets(event, "Orlando", "Detroit", None, "nba")
    assert len(results) == 1
    row = results[0]
    assert row.threshold == -1.5
    assert row.over_odds == 2.26
    assert row.under_odds == 1.64


def test_parse_handicap_ot_bets_multiple_lines():
    """A handicap ladder produces one row per line, sign-preserved."""
    event = {
        "bets": [
            {
                "betTypeId": 191,
                "betTypeName": "Hendikep (+OT)",
                "sBV": sbv,
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.9, "isPlayable": True},
                ],
            }
            for sbv in ("-4.5", "-2.5", "0.5")
        ]
    }
    results = _parse_handicap_ot_bets(event, "Home", "Away", None, "nba")
    assert sorted(r.threshold for r in results) == [-0.5, 2.5, 4.5]
    assert all(r.market_type == "home_handicap_ot" for r in results)


def test_parse_handicap_ot_bets_skips_unplayable_or_unparseable():
    event = {
        "bets": [
            # unplayable bet
            {
                "betTypeId": 191,
                "sBV": "3.5",
                "isPlayable": False,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.9, "isPlayable": True},
                ],
            },
            # missing sBV
            {
                "betTypeId": 191,
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.9, "isPlayable": True},
                ],
            },
            # both outcomes unplayable
            {
                "betTypeId": 191,
                "sBV": "5.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": False},
                    {"name": "2", "odd": 1.9, "isPlayable": False},
                ],
            },
        ]
    }
    assert _parse_handicap_ot_bets(event, "H", "A", None, "nba") == []


def test_parse_handicap_ot_event_uses_event_metadata():
    event = {
        "name": "PAOK - Aris",
        "dateTime": "2026-04-11T19:00:00",
        "competitionName": "AdmiralBet ABA Liga",
        "bets": [
            {
                "betTypeId": 191,
                "betTypeName": "Hendikep (+OT)",
                "sBV": "3.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.81, "isPlayable": True},
                ],
            }
        ],
    }
    results = _parse_handicap_ot_event(event)
    assert len(results) == 1
    row = results[0]
    assert row.home_team == "PAOK"
    assert row.away_team == "Aris"
    assert row.league_id == "aba_liga"
    assert row.threshold == -3.5  # PAOK is the underdog (sBV +3.5 → threshold -3.5)


def test_parse_handicap_ot_event_fixture(totals_fixture_data):
    """The first basketball event fixture has one Hendikep (+OT) line at sBV=3.5."""
    results = _parse_handicap_ot_event(totals_fixture_data[0])
    assert len(results) == 1
    row = results[0]
    assert row.market_type == "home_handicap_ot"
    assert row.threshold == -3.5
    assert row.over_odds == 1.9
    assert row.under_odds == 1.81
    assert row.home_team == "PAOK"
    assert row.away_team == "Aris"


def test_parse_game_total_ot_event_does_not_emit_handicap(totals_fixture_data):
    """Regression: the totals parser must not pick up handicap rows now that
    the handicap parser exists alongside it."""
    results = _parse_game_total_ot_event(totals_fixture_data[0])
    assert all(r.market_type == "game_total_ot" for r in results)


# ── Fixture integration ──────────────────────────────────


def test_fixture_parse_all_events(fixture_data):
    all_results = []
    for event in fixture_data:
        all_results.extend(_parse_event(event))
    assert len(all_results) > 0
    assert all(isinstance(r, RawOddsData) for r in all_results)
    assert all(r.bookmaker_id == "admiralbet" for r in all_results)


def test_fixture_has_both_market_types(fixture_data):
    types = set()
    for event in fixture_data:
        for r in _parse_event(event):
            types.add(r.market_type)
    assert "player_points" in types
    assert "player_points_milestones" in types


def test_fixture_all_have_player_names(fixture_data):
    for event in fixture_data:
        for r in _parse_event(event):
            assert r.player_name


def test_fixture_all_have_positive_thresholds(fixture_data):
    for event in fixture_data:
        for r in _parse_event(event):
            assert r.threshold > 0


def test_fixture_over_under_have_odds(fixture_data):
    ou_results = []
    for event in fixture_data:
        for r in _parse_event(event):
            if r.market_type == "player_points":
                ou_results.append(r)
    assert len(ou_results) > 0
    with_both = [r for r in ou_results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_over_under_bets_all_market_types():
    """All 8 player over/under betTypeIds must map to the correct market_type."""
    bet_map = {
        1598: "player_points",
        1599: "player_assists",
        1600: "player_rebounds",
        300: "player_3points",
        1601: "player_points_assists",
        1602: "player_points_rebounds",
        1603: "player_rebounds_assists",
        1604: "player_points_rebounds_assists",
    }
    for bet_type_id, expected_market in bet_map.items():
        event = {
            "name": "Test Player - Test Team",
            "dateTime": "2026-04-15T20:00:00",
            "bets": [{"betTypeId": bet_type_id, "sBV": "5.5", "isPlayable": True, "betOutcomes": [
                {"name": "vise", "odd": 1.8, "isPlayable": True},
                {"name": "manje", "odd": 1.9, "isPlayable": True},
            ]}],
        }
        results = _parse_event(event)
        assert len(results) == 1, f"betTypeId {bet_type_id} should produce 1 result"
        assert results[0].market_type == expected_market, (
            f"betTypeId {bet_type_id}: expected {expected_market}, got {results[0].market_type}"
        )


def test_parse_over_under_bets_ignores_unknown_bet_type():
    """Unknown betTypeIds must not produce over/under results."""
    event = {
        "name": "Test Player - Test Team",
        "dateTime": "2026-04-15T20:00:00",
        "bets": [{"betTypeId": 9999, "sBV": "5.5", "isPlayable": True, "betOutcomes": [
            {"name": "vise", "odd": 1.8, "isPlayable": True},
            {"name": "manje", "odd": 1.9, "isPlayable": True},
        ]}],
    }
    assert _parse_event(event) == []


# ── Scraper integration ──────────────────────────────────


@pytest.mark.asyncio
async def test_scraper_returns_data(fixture_data):
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fixture_data
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "admiralbet" for r in results)


@pytest.mark.asyncio
async def test_scraper_returns_player_props_and_ot_totals(fixture_data, totals_fixture_data):
    scraper = AdmiralBetScraper()

    async def mock_get(url, **kwargs):
        params = kwargs.get("params", {})
        if params.get("sportId") == "123":
            return fixture_data
        if params.get("sportId") == "2":
            return totals_fixture_data
        raise AssertionError(f"Unexpected params: {params}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert {"player_points", "player_points_milestones", "game_total_ot"} <= {
        result.market_type for result in results
    }
    game_totals = [result for result in results if result.market_type == "game_total_ot"]
    assert sorted(result.threshold for result in game_totals) == [167.5, 168.5, 169.5, 170.5]


@pytest.mark.asyncio
async def test_scraper_list_requests_use_24h_window(monkeypatch):
    scraper = AdmiralBetScraper()
    captured_params: list[dict] = []
    fixed_now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 24)
    monkeypatch.setattr("app.scrapers.admiralbet_scraper.current_utc_time", lambda: fixed_now)

    async def mock_get(url, **kwargs):
        captured_params.append(kwargs.get("params", {}))
        return []

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert len(captured_params) == 2
    assert all(params["dateFrom"] == "2030-01-01T12:00:00" for params in captured_params)
    assert all(params["dateTo"] == "2030-01-02T12:00:00" for params in captured_params)


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = AdmiralBetScraper()
    results = await scraper.scrape_odds("euroleague")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_response():
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = []
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_non_list_response():
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"error": "bad request"}
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = AdmiralBetScraper()
    assert scraper.get_bookmaker_id() == "admiralbet"
    assert scraper.get_bookmaker_name() == "AdmiralBet"
    assert "basketball" in scraper.get_supported_leagues()


# ── Football outcome lane ─────────────────────────────────


def test_parse_football_outcome_event_happy_path(football_fixture_data):
    event = football_fixture_data[0]
    offers = _parse_football_outcome_event(event)

    by_market: dict[str, list[RawOutcomeOffer]] = {}
    for o in offers:
        by_market.setdefault(o.market_type, []).append(o)

    assert sorted(by_market) == [
        "football_double_chance",
        "football_result",
        "football_total_goals",
    ]
    assert len(by_market["football_result"]) == 3
    assert len(by_market["football_double_chance"]) == 3
    # totals: 1.5 and 3.5 are skipped, only 2.5 over+under remain
    assert len(by_market["football_total_goals"]) == 2

    result_codes = {o.outcome_code for o in by_market["football_result"]}
    assert result_codes == {"home", "draw", "away"}
    dc_codes = {o.outcome_code for o in by_market["football_double_chance"]}
    assert dc_codes == {"home_or_draw", "home_or_away", "draw_or_away"}

    totals = by_market["football_total_goals"]
    assert {t.outcome_code for t in totals} == {"over", "under"}
    assert all(t.line == 2.5 for t in totals)
    assert {t.raw_label for t in totals} == {"0-2", "3+"}

    sample = by_market["football_result"][0]
    assert sample.bookmaker_id == "admiralbet"
    assert sample.sport == "football"
    assert sample.home_team == "Deportivo Cuenca"
    assert sample.away_team == "San Lorenzo"
    # Naive AdmiralBet datetimes are treated as UTC and emitted with explicit offset
    assert sample.start_time == "2026-05-06T02:00:00+00:00"


def test_parse_football_outcome_event_normalizes_outcome_names(football_fixture_data):
    event = football_fixture_data[0]
    # Mutate one Konacan ishod outcome to add stray whitespace + lowercase
    for bet in event["bets"]:
        if bet.get("betTypeId") == 135:
            bet["betOutcomes"][0]["name"] = " 1 "  # was "1"
            bet["betOutcomes"][1]["name"] = "x"  # was "X"
            break

    offers = _parse_football_outcome_event(event)
    result_codes = {
        o.outcome_code for o in offers if o.market_type == "football_result"
    }
    assert result_codes == {"home", "draw", "away"}


def test_parse_football_outcome_event_classifies_totals_by_diacritic_insensitive_name(
    football_fixture_data,
):
    event = football_fixture_data[0]
    # Stress: "Više" instead of "Vise" must still classify as over.
    for bet in event["bets"]:
        if bet.get("betTypeId") == 137 and bet.get("sBV") == "2.5":
            for outcome in bet["betOutcomes"]:
                if outcome["name"] == "Vise":
                    outcome["name"] = "Više"
                elif outcome["name"] == "Manje":
                    outcome["name"] = "Manje  "  # whitespace + accent mix
            break

    offers = _parse_football_outcome_event(event)
    totals = [o for o in offers if o.market_type == "football_total_goals"]
    assert {t.outcome_code for t in totals} == {"over", "under"}


def test_parse_football_outcome_event_skips_non_2_5_lines(football_fixture_data):
    event = football_fixture_data[0]
    offers = _parse_football_outcome_event(event)
    totals = [o for o in offers if o.market_type == "football_total_goals"]
    assert all(t.line == 2.5 for t in totals)
    assert len(totals) == 2


def test_parse_football_outcome_event_skips_non_playable_bets(football_fixture_data):
    event = football_fixture_data[0]
    for bet in event["bets"]:
        bet["isPlayable"] = False

    assert _parse_football_outcome_event(event) == []


def test_parse_football_outcome_event_skips_non_playable_outcomes(football_fixture_data):
    event = football_fixture_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 135:
            for outcome in bet["betOutcomes"]:
                outcome["isPlayable"] = False
            break

    offers = _parse_football_outcome_event(event)
    result_offers = [o for o in offers if o.market_type == "football_result"]
    assert result_offers == []
    # Other bets (DC, totals) are unaffected
    assert any(o.market_type == "football_double_chance" for o in offers)


def test_parse_football_outcome_event_skips_zero_or_negative_odds(football_fixture_data):
    event = football_fixture_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 135:
            for outcome in bet["betOutcomes"]:
                outcome["odd"] = 0
            break

    offers = _parse_football_outcome_event(event)
    assert [o for o in offers if o.market_type == "football_result"] == []


def test_parse_football_outcome_event_skips_unknown_outcome_codes(football_fixture_data):
    event = football_fixture_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 152:
            for outcome in bet["betOutcomes"]:
                outcome["name"] = "WAT"  # unknown code
            break

    offers = _parse_football_outcome_event(event)
    assert [o for o in offers if o.market_type == "football_double_chance"] == []


def test_parse_football_outcome_event_drops_offer_when_event_name_unparseable(
    football_fixture_data,
):
    event = football_fixture_data[0]
    event["name"] = "NoSeparator"
    assert _parse_football_outcome_event(event) == []


def test_parse_total_line_tolerates_string_variants():
    assert _parse_total_line("2.5") == 2.5
    assert _parse_total_line("2.50") == 2.5
    assert _parse_total_line(" 2.5 ") == 2.5
    assert _parse_total_line(2.5) == 2.5
    assert _parse_total_line(None) is None
    assert _parse_total_line("") is None
    assert _parse_total_line("not-a-number") is None


def test_resolve_total_line_prefers_bet_level_and_falls_back_to_outcome():
    bet = {"sBV": "2.5"}
    outcome = {"sBV": None}
    assert _resolve_total_line(bet, outcome) == 2.5

    bet = {"sBV": None}
    outcome = {"sBV": "2.5"}
    assert _resolve_total_line(bet, outcome) == 2.5

    bet = {"sBV": "2.5"}
    outcome = {"sBV": "2.5"}
    assert _resolve_total_line(bet, outcome) == 2.5

    bet = {"sBV": "2.5"}
    outcome = {"sBV": "3.5"}
    assert _resolve_total_line(bet, outcome) is None

    bet = {"sBV": None}
    outcome = {"sBV": None}
    assert _resolve_total_line(bet, outcome) is None


def test_parse_football_outcome_event_uses_outcome_level_sbv_when_bet_level_missing(
    football_fixture_data,
):
    event = football_fixture_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 137 and bet.get("sBV") == "2.5":
            bet["sBV"] = None  # drop bet-level line, outcome-level "2.5" remains
            break

    offers = _parse_football_outcome_event(event)
    totals = [o for o in offers if o.market_type == "football_total_goals"]
    assert {t.outcome_code for t in totals} == {"over", "under"}
    assert all(t.line == 2.5 for t in totals)


def test_parse_football_outcome_event_falls_back_to_default_league(football_fixture_data):
    event = football_fixture_data[0]
    event["competitionName"] = None
    offers = _parse_football_outcome_event(event)
    assert offers
    assert all(o.league_id == "football" for o in offers)


def test_parse_football_outcome_event_falls_back_for_degenerate_competition(
    football_fixture_data,
):
    event = football_fixture_data[0]
    event["competitionName"] = "---"
    offers = _parse_football_outcome_event(event)
    assert offers
    assert all(o.league_id == "football" for o in offers)


def test_parse_football_outcome_event_emits_source_url_when_provided(football_fixture_data):
    event = football_fixture_data[0]
    offers = _parse_football_outcome_event(event, source_url="https://example/event/1")
    assert offers
    assert all(o.source_url == "https://example/event/1" for o in offers)


def test_extract_league_id_default_kwarg_falls_back_for_empty_inputs():
    # The football helper passes default="football" so degenerate league names
    # don't silently land under the basketball default.
    assert _extract_league_id(None, default="football") == "football"
    assert _extract_league_id("", default="football") == "football"
    assert _extract_league_id("   ", default="football") == "football"
    assert _extract_league_id("---", default="football") == "football"
    # Existing basketball callers (no kwarg) keep the old default.
    assert _extract_league_id(None) == "basketball"


def _tennis_event(**overrides) -> dict:
    event = {
        "id": 6314006,
        "name": "Yasmine Mansouri - Victoria Milovanova",
        "competitionId": 37210,
        "regionId": 185,
        "sportId": 3,
        "systemStatus": 1,
        "feedStatus": 1,
        "isInOffer": True,
        "isPlayable": True,
        "dateTime": "2026-05-10T09:17:00",
        "bets": [
            {
                "betTypeId": 214,
                "betTypeName": "1.set - Pobednik",
                "isPlayable": True,
                "isInOffer": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.8, "isPlayable": True, "isInOffer": True},
                    {"name": "2", "odd": 1.9, "isPlayable": True, "isInOffer": True},
                ],
            },
            {
                "betTypeId": _BET_TENNIS_MATCH_WINNER,
                "betTypeName": "Pobednik",
                "isPlayable": True,
                "isInOffer": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.77, "isPlayable": True, "isInOffer": True},
                    {"name": "2", "odd": 1.98, "isPlayable": True, "isInOffer": True},
                    {"name": "X", "odd": 99.0, "isPlayable": True, "isInOffer": True},
                ],
            },
        ],
        "shortName": "Yasmine M-Victoria ",
        "isLive": False,
        "competitionName": "Monastir Ž",
        "regionName": "ITF Žene",
        "sportName": "Tenis",
    }
    event.update(overrides)
    return event


def test_parse_tennis_outcome_event_emits_match_winner_offers():
    offers = _parse_tennis_outcome_event(_tennis_event())

    assert len(offers) == 2
    assert all(isinstance(offer, RawOutcomeOffer) for offer in offers)
    assert {offer.bookmaker_id for offer in offers} == {"admiralbet"}
    assert {offer.sport for offer in offers} == {"tennis"}
    assert {offer.league_id for offer in offers} == {"monastir ž"}
    assert {offer.home_team for offer in offers} == {"Yasmine Mansouri"}
    assert {offer.away_team for offer in offers} == {"Victoria Milovanova"}
    assert {offer.market_type for offer in offers} == {"tennis_match_winner"}
    assert {offer.source_url for offer in offers} == {_TENNIS_PAGE_URL}
    assert {
        (offer.outcome_code, offer.raw_label, offer.odds, offer.line, offer.start_time)
        for offer in offers
    } == {
        ("home", "1", 1.77, None, "2026-05-10T09:17:00+00:00"),
        ("away", "2", 1.98, None, "2026-05-10T09:17:00+00:00"),
    }
    assert 99.0 not in {offer.odds for offer in offers}
    assert 1.8 not in {offer.odds for offer in offers}


def test_parse_tennis_outcome_event_skips_event_level_exclusions():
    assert _parse_tennis_outcome_event(_tennis_event(isLive=True)) == []
    assert _parse_tennis_outcome_event(_tennis_event(isPlayable=False)) == []
    assert _parse_tennis_outcome_event(_tennis_event(isInOffer=False)) == []
    assert _parse_tennis_outcome_event(
        _tennis_event(name="Player One/Player Two - Player Three/Player Four")
    ) == []
    assert _parse_tennis_outcome_event(
        _tennis_event(name="Player One - Player Two - Player Three")
    ) == []
    assert _parse_tennis_outcome_event(_tennis_event(name="NoSeparator")) == []


def test_parse_tennis_outcome_event_skips_bet_and_outcome_level_exclusions():
    assert _parse_tennis_outcome_event(
        _tennis_event(
            bets=[
                {
                    "betTypeId": _BET_TENNIS_MATCH_WINNER,
                    "betTypeName": "Pobednik",
                    "isPlayable": False,
                    "isInOffer": True,
                    "betOutcomes": [{"name": "1", "odd": 1.7, "isPlayable": True}],
                }
            ]
        )
    ) == []
    assert _parse_tennis_outcome_event(
        _tennis_event(
            bets=[
                {
                    "betTypeId": _BET_TENNIS_MATCH_WINNER,
                    "betTypeName": "Pobednik",
                    "isPlayable": True,
                    "isInOffer": False,
                    "betOutcomes": [{"name": "1", "odd": 1.7, "isPlayable": True}],
                }
            ]
        )
    ) == []
    assert _parse_tennis_outcome_event(
        _tennis_event(
            bets=[
                {
                    "betTypeId": _BET_TENNIS_MATCH_WINNER,
                    "betTypeName": "1.set - Pobednik",
                    "isPlayable": True,
                    "isInOffer": True,
                    "betOutcomes": [{"name": "1", "odd": 1.7, "isPlayable": True}],
                },
                {
                    "betTypeId": _BET_TENNIS_MATCH_WINNER,
                    "betTypeName": "Pobednik",
                    "isPlayable": True,
                    "isInOffer": True,
                    "betOutcomes": [
                        {"name": "1", "odd": 1.5, "isPlayable": False, "isInOffer": True},
                        {"name": "2", "odd": 1.9, "isPlayable": True, "isInOffer": False},
                        {"name": "1", "odd": 0, "isPlayable": True, "isInOffer": True},
                        {"name": "2", "odd": "bad", "isPlayable": True, "isInOffer": True},
                    ],
                },
            ]
        )
    ) == []


def test_parse_tennis_outcome_event_keeps_same_matchup_different_start_times():
    first = _tennis_event(dateTime="2026-05-10T09:17:00")
    second = _tennis_event(id=6314007, dateTime="2026-05-10T11:17:00")

    offers = [
        *_parse_tennis_outcome_event(first),
        *_parse_tennis_outcome_event(second),
    ]

    assert len(offers) == 4
    assert sorted({offer.start_time for offer in offers}) == [
        "2026-05-10T09:17:00+00:00",
        "2026-05-10T11:17:00+00:00",
    ]


def test_get_supported_outcome_sports_isolates_football_from_basketball_capability():
    scraper = AdmiralBetScraper()
    # threshold-odds lane: basketball only — football MUST NOT leak here,
    # otherwise the unified pipeline would call scrape_odds("football") every cycle.
    assert scraper.get_supported_leagues() == ["basketball"]
    # outcome-offer lane: football + tennis
    assert scraper.get_supported_outcome_sports() == ["football", "tennis"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_returns_empty_for_unsupported_sport_without_http():
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        results = await scraper.scrape_outcome_offers("basketball")
    assert results == []
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_outcome_offers_uses_football_params_and_24h_window(
    monkeypatch, football_fixture_data
):
    scraper = AdmiralBetScraper()
    captured: dict = {}
    fixed_now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 24)
    monkeypatch.setattr("app.scrapers.admiralbet_scraper.current_utc_time", lambda: fixed_now)

    async def mock_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return football_fixture_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("football")

    assert captured["url"] == _LIST_URL
    params = captured["params"]
    assert params["pageId"] == _FOOTBALL_OUTCOME_PARAMS["pageId"] == "14"
    assert params["sportId"] == _FOOTBALL_OUTCOME_PARAMS["sportId"] == "1"
    assert params["isLive"] == "false"
    assert params["dateFrom"] == "2030-01-01T12:00:00"
    assert params["dateTo"] == "2030-01-02T12:00:00"
    assert params["eventMappingTypes"] == ["1", "2", "3", "4", "5"]

    # Result shape sanity: at least one offer per target market.
    market_types = {o.market_type for o in results}
    assert market_types == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }
    # End-to-end UTC-naive pin: AdmiralBet treats naive datetimes as UTC.
    assert all(o.start_time == "2026-05-06T02:00:00+00:00" for o in results)


@pytest.mark.asyncio
async def test_scrape_outcome_offers_tennis_uses_one_list_request(monkeypatch):
    scraper = AdmiralBetScraper()
    captured: dict = {}
    fixed_now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 24)
    monkeypatch.setattr("app.scrapers.admiralbet_scraper.current_utc_time", lambda: fixed_now)

    async def mock_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return [_tennis_event()]

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("tennis")

    assert len(results) == 2
    assert captured["url"] == _LIST_URL
    params = captured["params"]
    assert params["pageId"] == _TENNIS_OUTCOME_PARAMS["pageId"] == "3"
    assert params["sportId"] == _TENNIS_OUTCOME_PARAMS["sportId"] == "3"
    assert params["isLive"] == "false"
    assert params["dateFrom"] == "2030-01-01T12:00:00"
    assert params["dateTo"] == "2030-01-02T12:00:00"
    assert params["eventMappingTypes"] == ["1", "2", "3", "4", "5"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_tennis_handles_non_list_response():
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"error": "bad request"}
        results = await scraper.scrape_outcome_offers("tennis")
    assert results == []


@pytest.mark.asyncio
async def test_scrape_outcome_offers_handles_http_failure():
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("boom")
        results = await scraper.scrape_outcome_offers("football")
    assert results == []


@pytest.mark.asyncio
async def test_scrape_outcome_offers_handles_non_list_response():
    scraper = AdmiralBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"error": "bad request"}
        results = await scraper.scrape_outcome_offers("football")
    assert results == []
