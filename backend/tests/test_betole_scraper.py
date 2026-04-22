from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.scrapers.betole_scraper import (
    BetOleScraper,
    _PLAYER_LEAGUES_URL,
    _PLAYER_LEAGUE_PREVIEW_URL,
    _REGULAR_LEAGUES_URL,
    _REGULAR_LEAGUE_PREVIEW_URL,
    _build_matchup_index,
    _extract_league_id,
    _parse_player_match,
    _parse_regular_match,
)

REGULAR_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "betole_regular_league.json"
PLAYER_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "betole_players_league.json"

REGULAR_LEAGUES_RESPONSE = {
    "categories": [
        {
            "id": "2257173",
            "name": "USA, NBA - Play Offs",
            "type": "LEAGUE",
            "url": "B",
            "count": 8,
        }
    ]
}
PLAYER_LEAGUES_RESPONSE = {
    "categories": [
        {
            "id": "2300270",
            "name": "USA, NBA - Play Offs,Players Duel",
            "type": "LEAGUE",
            "url": "SK",
            "count": 5,
        },
        {
            "id": "2266084",
            "name": "USA, NBA - Play Offs,Players",
            "type": "LEAGUE",
            "url": "SK",
            "count": 29,
        },
    ]
}

EXTRA_REGULAR_LEAGUE = {
    "id": "2265038",
    "name": "Brazil, NBB - Play Offs",
    "type": "LEAGUE",
    "url": "B",
    "count": 7,
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
    assert _extract_league_id("USA, NBA - Play Offs") == "nba"
    assert _extract_league_id("USA, NBA - Play Offs,Players") == "nba"
    assert _extract_league_id("USA, NBA - Play Offs,Players Duel") == "nba"


def test_parse_regular_match_returns_ot_total_with_source_url(regular_preview_data):
    results = _parse_regular_match(regular_preview_data["esMatches"][0])

    assert len(results) == 1
    assert results[0].market_type == "game_total_ot"
    assert (results[0].threshold, results[0].over_odds, results[0].under_odds) == (
        219.5,
        1.9,
        1.85,
    )
    assert results[0].source_url == "https://www.betole.com/match-special/90241113"


def test_parse_player_match_uses_super_code_matchup(player_preview_data, regular_preview_data):
    matchup_index = _build_matchup_index(regular_preview_data["esMatches"])

    results = _parse_player_match(player_preview_data["esMatches"][0], matchup_index)

    assert {row.market_type for row in results} == {
        "player_assists",
        "player_points",
        "player_rebounds",
    }
    assert {row.home_team for row in results} == {"Detroit Pistons"}
    assert {row.away_team for row in results} == {"Orlando Magic"}
    assert {row.player_name for row in results} == {"Ausar Thompson"}
    assert all(row.league_id == "nba" for row in results)
    assert all(row.source_url == "https://www.betole.com/match-special/90241113" for row in results)


def test_parse_player_match_falls_back_to_team_and_kickoff(player_preview_data, regular_preview_data):
    preview_match = {
        key: value for key, value in player_preview_data["esMatches"][0].items() if key != "superCode"
    }
    matchup_index = _build_matchup_index(regular_preview_data["esMatches"])

    results = _parse_player_match(preview_match, matchup_index)

    assert {row.player_name for row in results} == {"Ausar Thompson"}
    assert {row.home_team for row in results} == {"Detroit Pistons"}
    assert {row.away_team for row in results} == {"Orlando Magic"}


@pytest.mark.asyncio
async def test_scrape_odds_keeps_all_regular_leagues_while_matching_player_props(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
    player_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    extra_regular_preview = {
        "esMatches": [
            {
                **regular_preview_data["esMatches"][0],
                "id": 90249999,
                "matchCode": 7777,
                "leagueName": EXTRA_REGULAR_LEAGUE["name"],
                "home": "Franca",
                "away": "Botafogo",
            }
        ]
    }
    regular_leagues_response = {
        "categories": [*REGULAR_LEAGUES_RESPONSE["categories"], EXTRA_REGULAR_LEAGUE]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _REGULAR_LEAGUES_URL:
            return regular_leagues_response
        if url == _PLAYER_LEAGUES_URL:
            return PLAYER_LEAGUES_RESPONSE
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2257173"):
            return regular_preview_data
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id=EXTRA_REGULAR_LEAGUE["id"]):
            return extra_regular_preview
        if url == _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2266084"):
            return player_preview_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} == {
        "game_total_ot",
        "player_assists",
        "player_points",
        "player_rebounds",
    }
    assert {row.player_name for row in results if row.player_name} == {
        "Ausar Thompson",
        "Cade Cunningham",
    }
    assert {row.home_team for row in results if row.player_name} == {"Detroit Pistons"}
    assert {row.away_team for row in results if row.player_name} == {"Orlando Magic"}

    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert _REGULAR_LEAGUE_PREVIEW_URL.format(league_id=EXTRA_REGULAR_LEAGUE["id"]) in requested_urls
    assert _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2300270") not in requested_urls

    regular_matchups = {
        (row.home_team, row.away_team, row.league_id)
        for row in results
        if row.player_name is None
    }
    assert regular_matchups == {
        ("Detroit Pistons", "Orlando Magic", "nba"),
        ("Franca", "Botafogo", "brazil_nbb"),
    }


@pytest.mark.asyncio
async def test_scrape_odds_returns_regular_results_when_player_leagues_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _REGULAR_LEAGUES_URL:
            return REGULAR_LEAGUES_RESPONSE
        if url == _PLAYER_LEAGUES_URL:
            return {"categories": []}
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2257173"):
            return regular_preview_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} == {"game_total_ot"}
    assert all(row.player_name is None for row in results)

    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2266084") not in requested_urls


@pytest.mark.asyncio
async def test_scrape_odds_builds_player_matchups_from_matched_regular_leagues(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
    player_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    colliding_regular_preview = {
        "esMatches": [
            {
                **regular_preview_data["esMatches"][0],
                "id": 90248888,
                "matchCode": 8888,
                "leagueName": EXTRA_REGULAR_LEAGUE["name"],
                "home": "Franca",
                "away": "Detroit Pistons",
            }
        ]
    }
    regular_leagues_response = {
        "categories": [*REGULAR_LEAGUES_RESPONSE["categories"], EXTRA_REGULAR_LEAGUE]
    }
    player_preview_without_super_code = {
        "esMatches": [
            {
                key: value
                for key, value in player_preview_data["esMatches"][0].items()
                if key != "superCode"
            }
        ]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _REGULAR_LEAGUES_URL:
            return regular_leagues_response
        if url == _PLAYER_LEAGUES_URL:
            return PLAYER_LEAGUES_RESPONSE
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id="2257173"):
            return regular_preview_data
        if url == _REGULAR_LEAGUE_PREVIEW_URL.format(league_id=EXTRA_REGULAR_LEAGUE["id"]):
            return colliding_regular_preview
        if url == _PLAYER_LEAGUE_PREVIEW_URL.format(league_id="2266084"):
            return player_preview_without_super_code
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    player_rows = [row for row in results if row.player_name == "Ausar Thompson"]
    assert player_rows
    assert {(row.home_team, row.away_team, row.league_id) for row in player_rows} == {
        ("Detroit Pistons", "Orlando Magic", "nba")
    }
