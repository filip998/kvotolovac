from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.admiralbet_scraper import (
    AdmiralBetScraper,
    _parse_event,
    _parse_event_name,
    _parse_start_time,
    _parse_over_under_bets,
    _parse_milestone_bets,
    _parse_game_total_ot_bets,
    _parse_game_total_ot_event,
    _extract_league_id,
)
from app.models.schemas import RawOddsData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "admiralbet_specials.json"
TOTALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "admiralbet_basketball_totals.json"


@pytest.fixture
def fixture_data() -> list[dict]:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def totals_fixture_data() -> list[dict]:
    with open(TOTALS_FIXTURE_PATH) as f:
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
