from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.scrapers.bookmaker365_scraper import (
    Bookmaker365Scraper,
    _PLAYER_LEAGUES_URL,
    _PLAYER_LEAGUE_PREVIEW_URL,
    _REGULAR_LEAGUES_URL,
    _REGULAR_LEAGUE_PREVIEW_URL,
    _build_matchup_index,
    _extract_league_id,
    _parse_player_match,
    _parse_total_match,
    _GAME_TOTAL_LINES,
    _GAME_TOTAL_OT_LINES,
)

REGULAR_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bookmaker365_regular_league.json"
PLAYER_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bookmaker365_player_league.json"

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
