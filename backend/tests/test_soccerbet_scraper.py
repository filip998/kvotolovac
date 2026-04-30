from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.scrapers.soccerbet_scraper import (
    SoccerBetScraper,
    _ALL_GAMES_URL,
    _ALL_PLAYERS_URL,
    _DETAIL_URL,
    _GROUPS_URL,
    _GROUP_LEAGUES_URL,
    _LEAGUE_PREVIEW_URL,
    _PLAYER_PREVIEW_URL,
    _build_matchup_index,
    _extract_league_id,
    _parse_player_match,
    _parse_regular_match,
)


def _entry(tt: int, odd: float, specifier: str = "NULL", status: str = "U") -> dict:
    return {
        "tt": tt,
        "ov": odd,
        "sv": specifier,
        "bc": tt,
        "bpc": tt,
        "s": status,
    }


def _group(tt: int, *entries: tuple[str, float]) -> dict[str, dict]:
    return {specifier: _entry(tt, odd, specifier) for specifier, odd in entries}


def _group_with_status(tt: int, *entries: tuple[str, float, str]) -> dict[str, dict]:
    return {
        specifier: _entry(tt, odd, specifier, status)
        for specifier, odd, status in entries
    }


KICKOFF_MS = int((datetime.now(tz=timezone.utc) + timedelta(hours=2)).timestamp() * 1000)

REGULAR_PREVIEW_MATCH = {
    "id": 514392889,
    "matchCode": 79148,
    "home": "Atlanta Hawks",
    "away": "New York Knicks",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "NBA Play off",
    "betMap": {
        "50445": _group(50445, ("total=211.5", 1.92)),
        "50444": _group(50444, ("total=211.5", 1.88)),
        "50979": _group(50979, ("total=108.5", 1.84)),
        "50980": _group(50980, ("total=108.5", 1.96)),
    },
}

REGULAR_DETAIL_MATCH = {
    **REGULAR_PREVIEW_MATCH,
    "betMap": {
        **REGULAR_PREVIEW_MATCH["betMap"],
        "227": _group(227, ("total=208.5", 1.83)),
        "228": _group(228, ("total=208.5", 1.97)),
        "224": _group(224, ("hcp=-3.5", 1.90)),
        "226": _group(226, ("hcp=-3.5", 1.90)),
    },
}

PLAYER_PREVIEW_MATCH = {
    "id": 514398866,
    "matchCode": 81538,
    "superCode": 79148,
    "home": "Jalen Brunson",
    "away": "New York Knicks",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "NBA Play off Igrači",
    "betMap": {
        "51679": _group(51679, ("total=28.5", 1.91)),
        "51681": _group(51681, ("total=28.5", 1.87)),
        "51682": _group(51682, ("total=6.5", 1.74)),
        "51684": _group(51684, ("total=6.5", 2.02)),
        "51685": _group(51685, ("total=3.5", 1.68)),
        "51687": _group(51687, ("total=3.5", 2.12)),
        "51688": _group(51688, ("total=2.5", 1.95)),
        "51690": _group(51690, ("total=2.5", 1.81)),
        "55244": _group(55244, ("total=31.5", 1.87)),
        "55246": _group(55246, ("total=31.5", 1.91)),
        "55247": _group(55247, ("total=34.5", 1.89)),
        "55249": _group(55249, ("total=34.5", 1.89)),
        "55250": _group(55250, ("total=10.5", 1.80)),
        "55252": _group(55252, ("total=10.5", 1.94)),
        "55215": _group(55215, ("total=38.5", 1.86)),
        "55217": _group(55217, ("total=38.5", 1.92)),
        "55831": _group(55831, ("NULL", 2.25)),
        "55832": _group(55832, ("NULL", 1.55)),
    },
}

EUROLEAGUE_REGULAR_PREVIEW_MATCH = {
    "id": 514392890,
    "matchCode": 79149,
    "home": "Monaco",
    "away": "Olympiacos",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "Evroliga Play off",
    "betMap": {
        "50445": _group(50445, ("total=164.5", 1.90)),
        "50444": _group(50444, ("total=164.5", 1.90)),
    },
}

EUROLEAGUE_PLAYER_PREVIEW_MATCH = {
    "id": 514398867,
    "matchCode": 81539,
    "superCode": 79149,
    "home": "Mike James",
    "away": "Monaco",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "Evroliga Play off Igrači",
    "betMap": {
        "51679": _group(51679, ("total=16.5", 1.86)),
        "51681": _group(51681, ("total=16.5", 1.92)),
        "55215": _group(55215, ("total=24.5", 1.88)),
        "55217": _group(55217, ("total=24.5", 1.88)),
    },
}

PLAYER_DETAIL_MATCH = {
    **PLAYER_PREVIEW_MATCH,
    "betMap": {
        **PLAYER_PREVIEW_MATCH["betMap"],
        "55672": _group(55672, ("total=1.5", 2.10)),
        "55674": _group(55674, ("total=1.5", 1.67)),
        "55681": _group(55681, ("total=0.5", 1.84)),
        "55683": _group(55683, ("total=0.5", 1.92)),
        "56169": _group(56169, ("total=3.5", 1.88)),
        "56171": _group(56171, ("total=3.5", 1.86)),
        "54106": _group(54106, ("NULL", 1.28)),
        "54111": _group(54111, ("NULL", 2.05)),
        "55692": _group(55692, ("total=7.5", 1.93)),
        "55694": _group(55694, ("total=7.5", 1.83)),
        "55833": _group(55833, ("NULL", 9.50)),
    },
}

GROUPS_RESPONSE = {"categories": [{"id": "2495"}]}
GROUP_LEAGUES_RESPONSE = {
    "categories": [
        {"id": "2516034", "name": "NBA Play off", "pmCount": 1, "playersCount": 1}
    ]
}


def test_extract_league_id_strips_players_suffix():
    assert _extract_league_id("NBA Play off Igrači") == "nba"


def test_build_matchup_index_uses_regular_match_code():
    matchups = _build_matchup_index([REGULAR_PREVIEW_MATCH])
    assert matchups[79148].league_id == "nba"
    assert matchups[79148].home_team == "Atlanta Hawks"
    assert matchups[79148].away_team == "New York Knicks"


def test_parse_regular_match_preview_only_returns_ot_total():
    results = _parse_regular_match(REGULAR_PREVIEW_MATCH)

    assert {row.market_type for row in results} == {"game_total_ot"}
    assert {row.threshold for row in results} == {211.5}


def test_parse_regular_match_detail_returns_both_total_types():
    results = _parse_regular_match(REGULAR_DETAIL_MATCH)

    assert {row.market_type for row in results} == {"game_total", "game_total_ot"}
    assert {row.threshold for row in results if row.market_type == "game_total"} == {208.5}


def test_parse_player_match_preview_uses_underlying_matchup():
    matchup_by_super_code = _build_matchup_index([REGULAR_PREVIEW_MATCH])

    results = _parse_player_match(PLAYER_PREVIEW_MATCH, matchup_by_super_code)

    assert {row.market_type for row in results} == {
        "player_points",
        "player_assists",
        "player_rebounds",
        "player_3points",
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }
    assert {row.home_team for row in results} == {"Atlanta Hawks"}
    assert {row.away_team for row in results} == {"New York Knicks"}
    assert {row.league_id for row in results} == {"nba"}
    assert all(row.player_name == "Jalen Brunson" for row in results)


def test_parse_player_match_skips_locked_player_picks():
    matchup_by_super_code = _build_matchup_index([REGULAR_PREVIEW_MATCH])
    player_match = {
        **PLAYER_PREVIEW_MATCH,
        "betMap": {
            "51685": _group_with_status(
                51685,
                ("total=1.5", 1.85, "U"),
                ("total=2.5", 1.85, "L"),
            ),
            "51687": _group_with_status(
                51687,
                ("total=1.5", 1.85, "U"),
                ("total=2.5", 1.85, "L"),
            ),
        },
    }

    results = _parse_player_match(player_match, matchup_by_super_code)

    rebounds = [row for row in results if row.market_type == "player_rebounds"]
    assert [(row.threshold, row.over_odds, row.under_odds) for row in rebounds] == [
        (1.5, 1.85, 1.85)
    ]


def test_parse_player_match_detail_adds_detail_only_supported_markets():
    matchup_by_super_code = _build_matchup_index([REGULAR_PREVIEW_MATCH])

    results = _parse_player_match(PLAYER_DETAIL_MATCH, matchup_by_super_code)
    market_types = {row.market_type for row in results}

    assert "player_steals" in market_types
    assert "player_blocks" in market_types
    assert "player_turnovers" in market_types
    assert "player_points_milestones" in market_types
    assert "player_points_q1" not in market_types


@pytest.mark.asyncio
async def test_scrape_odds_partial_mode_uses_broad_preview_feeds_only():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _ALL_GAMES_URL:
            return {
                "esMatches": [
                    REGULAR_PREVIEW_MATCH,
                    EUROLEAGUE_REGULAR_PREVIEW_MATCH,
                ]
            }
        if url == _ALL_PLAYERS_URL:
            return {
                "esMatches": [
                    PLAYER_PREVIEW_MATCH,
                    EUROLEAGUE_PLAYER_PREVIEW_MATCH,
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="partial")
    results = await scraper.scrape_odds("basketball")

    market_types = {row.market_type for row in results}
    assert "game_total_ot" in market_types
    assert "game_total" not in market_types
    assert "player_turnovers" not in market_types
    assert "player_steals" not in market_types
    assert any(row.player_name == "Jalen Brunson" for row in results)
    assert any(row.player_name == "Mike James" for row in results)
    assert {row.home_team for row in results if row.player_name == "Mike James"} == {"Monaco"}
    assert {row.away_team for row in results if row.player_name == "Mike James"} == {"Olympiacos"}
    called_urls = [call.args[0] for call in http_client.get_json.call_args_list]
    assert set(called_urls) == {_ALL_GAMES_URL, _ALL_PLAYERS_URL}
    assert len(called_urls) == 2


@pytest.mark.asyncio
async def test_scrape_odds_full_mode_uses_detail_enrichment():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _GROUPS_URL:
            return GROUPS_RESPONSE
        if url == _GROUP_LEAGUES_URL.format(group_id="2495"):
            return GROUP_LEAGUES_RESPONSE
        if url == _LEAGUE_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [REGULAR_PREVIEW_MATCH]}
        if url == _PLAYER_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [PLAYER_PREVIEW_MATCH]}
        if url == _DETAIL_URL.format(match_code=79148):
            return REGULAR_DETAIL_MATCH
        if url == _DETAIL_URL.format(match_code=81538):
            return PLAYER_DETAIL_MATCH
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="full")
    results = await scraper.scrape_odds("basketball")

    market_types = {row.market_type for row in results}
    assert "game_total" in market_types
    assert "game_total_ot" in market_types
    assert "player_turnovers" in market_types
    assert "player_steals" in market_types
    assert "player_blocks" in market_types
    assert "player_points_milestones" in market_types


@pytest.mark.asyncio
async def test_scrape_odds_full_mode_uses_preview_super_code_when_detail_omits_it():
    player_detail_without_super_code = {
        key: value for key, value in PLAYER_DETAIL_MATCH.items() if key != "superCode"
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _GROUPS_URL:
            return GROUPS_RESPONSE
        if url == _GROUP_LEAGUES_URL.format(group_id="2495"):
            return GROUP_LEAGUES_RESPONSE
        if url == _LEAGUE_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [REGULAR_PREVIEW_MATCH]}
        if url == _PLAYER_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [PLAYER_PREVIEW_MATCH]}
        if url == _DETAIL_URL.format(match_code=79148):
            return REGULAR_DETAIL_MATCH
        if url == _DETAIL_URL.format(match_code=81538):
            return player_detail_without_super_code
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="full")
    results = await scraper.scrape_odds("basketball")

    assert any(row.player_name == "Jalen Brunson" for row in results)
    assert "player_turnovers" in {row.market_type for row in results}


@pytest.mark.asyncio
async def test_scrape_odds_full_mode_drops_player_rows_when_detail_fails():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _GROUPS_URL:
            return GROUPS_RESPONSE
        if url == _GROUP_LEAGUES_URL.format(group_id="2495"):
            return GROUP_LEAGUES_RESPONSE
        if url == _LEAGUE_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [REGULAR_PREVIEW_MATCH]}
        if url == _PLAYER_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [PLAYER_PREVIEW_MATCH]}
        if url == _DETAIL_URL.format(match_code=79148):
            return REGULAR_DETAIL_MATCH
        if url == _DETAIL_URL.format(match_code=81538):
            raise RuntimeError("player detail failed")
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="full")
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} == {"game_total", "game_total_ot"}
    assert all(row.player_name is None for row in results)
