from __future__ import annotations

import json
import re
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
    _parse_game_total_ot_list,
    _parse_player_name,
    _parse_player_points_list,
)
from app.models.schemas import RawOddsData

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


# ── _parse_player_points_list ─────────────────────────────


def test_parse_player_points_list_from_live_fixture(player_list_data):
    """Live response from BalkanBet's WEB_OVERVIEW endpoint must parse cleanly."""
    results = _parse_player_points_list(player_list_data, _BASKETBALL_SPEC)
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert {r.bookmaker_id for r in results} == {"balkanbet"}
    assert {r.market_type for r in results} == {"player_points"}
    assert {r.sport for r in results} == {"basketball"}
    assert all(r.player_name for r in results)
    assert all(r.threshold is not None for r in results)
    assert all(
        r.over_odds is not None or r.under_odds is not None for r in results
    )
    # Every event in the fixture is a player-prop carrier; the parser must
    # produce at least one row per event with markets.
    assert len(results) >= len(player_list_data["data"]["events"])


def test_parse_player_points_list_empty():
    assert _parse_player_points_list({}, _BASKETBALL_SPEC) == []
    assert _parse_player_points_list({"data": {}}, _BASKETBALL_SPEC) == []
    assert _parse_player_points_list({"data": {"events": []}}, _BASKETBALL_SPEC) == []


def test_parse_player_points_list_skips_unparseable_name():
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
    assert _parse_player_points_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_points_list_skips_market_without_threshold():
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
    assert _parse_player_points_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_points_list_skips_market_with_no_odds():
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
    assert _parse_player_points_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_points_list_ignores_unrelated_markets():
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
    assert _parse_player_points_list(data, _BASKETBALL_SPEC) == []


def test_parse_player_points_list_handles_only_over():
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
    results = _parse_player_points_list(data, _BASKETBALL_SPEC)
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
    assert {"player_points", "game_total_ot"} <= {r.market_type for r in results}


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

    assert len(results) == 3
    assert {r.market_type for r in results} == {"game_total_ot"}


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
    assert {r.market_type for r in results} == {"player_points"}


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

    assert len(results) == 3
    assert {r.market_type for r in results} == {"game_total_ot"}


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


# ── SportSpec extensibility ──────────────────────────────


def test_parse_player_points_list_supports_long_key_format():
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
    results = _parse_player_points_list(data, _BASKETBALL_SPEC)
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
    assert 2402 in _BASKETBALL_SPEC.player_points_market_ids
    assert 530 in _BASKETBALL_SPEC.game_total_ot_market_ids
