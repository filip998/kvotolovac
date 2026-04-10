from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.http_client import HttpClient
from app.scrapers.oktagonbet_scraper import (
    OktagonBetScraper,
    _get_detail_fetch_concurrency,
    _get_player_match_ids,
    _parse_match,
    _parse_match_detail,
    _parse_start_time,
    _is_player_market,
    _extract_league_id,
)
from app.models.schemas import RawOddsData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oktagonbet_specials.json"


@pytest.fixture
def fixture_data() -> dict:
    with open(FIXTURE_PATH) as f:
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


def test_extract_league_id_empty():
    assert _extract_league_id("") == "basketball"


# ── Parsing real fixture data ─────────────────────────────


def test_get_player_match_ids(fixture_data, player_matches):
    ids = _get_player_match_ids(fixture_data["esMatches"])
    assert ids == [match["id"] for match in player_matches]


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


def test_get_detail_fetch_concurrency_uses_http_rate_limit():
    http_client = HttpClient(rate_limit_per_second=4.0)
    assert _get_detail_fetch_concurrency(http_client, 10) == 4


def test_get_detail_fetch_concurrency_unlimited_rate_limit():
    http_client = HttpClient(rate_limit_per_second=0)
    assert _get_detail_fetch_concurrency(http_client, 12) == 10


# ── Integration: OktagonBetScraper with mocked HTTP ──────────


@pytest.mark.asyncio
async def test_scraper_returns_data(fixture_data):
    scraper = OktagonBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fixture_data
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "oktagonbet" for r in results)


@pytest.mark.asyncio
async def test_scraper_fetches_detail_ladders(player_matches):
    scraper = OktagonBetScraper()
    detail_matches = {
        match["id"]: {
            **match,
            "odds": {**match["odds"], "54096": 1.18, "54101": 1.65},
        }
        for match in player_matches
    }

    async def mock_get(url, **kwargs):
        if "/sport/SK/mob" in url:
            return {"esMatches": player_matches}
        for match_id, detail in detail_matches.items():
            if f"/match/{match_id}" in url:
                return detail
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get) as mock_get:
        results = await scraper.scrape_odds("basketball")

    ladder_results = [
        result for result in results
        if result.market_type == "player_points_milestones"
        and result.under_odds is None
        and result.threshold in {4.5, 9.5}
    ]
    assert len(ladder_results) == len(player_matches) * 2
    detail_calls = [
        call.args[0]
        for call in mock_get.await_args_list
        if "/match/" in call.args[0]
    ]
    assert detail_calls == [
        f"https://www.oktagonbet.com/restapi/offer/sr/match/{match['id']}"
        for match in player_matches
    ]


@pytest.mark.asyncio
async def test_scraper_limits_detail_fetch_concurrency():
    matches = [
        {
            "id": 1000 + idx,
            "home": f"Player {idx}",
            "away": "Team A",
            "leagueName": "Igrači ~ USA NBA",
            "leagueCategory": "PL",
            "kickOffTime": 1775829600000,
            "params": {"ouPlPoints": "15.5"},
            "odds": {"51679": 1.88, "51681": 1.92},
        }
        for idx in range(4)
    ]
    scraper = OktagonBetScraper(HttpClient(rate_limit_per_second=3.0))
    peak_active = 0
    active = 0

    async def mock_get(url, **kwargs):
        nonlocal peak_active, active
        if "/sport/SK/mob" in url:
            return {"esMatches": matches}

        active += 1
        peak_active = max(peak_active, active)
        try:
            await asyncio.sleep(0.01)
        finally:
            active -= 1

        match_id = int(url.rsplit("/", 1)[-1])
        match = next(match for match in matches if match["id"] == match_id)
        return {**match, "odds": {**match["odds"], "54096": 1.5}}

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert peak_active > 1
    assert peak_active <= 3
    ladder_results = [
        result for result in results
        if result.market_type == "player_points_milestones"
        and result.under_odds is None
        and result.threshold == 4.5
    ]
    assert len(ladder_results) == len(matches)


@pytest.mark.asyncio
async def test_scraper_filters_non_player_markets(fixture_data):
    """Duels and specials from fixture should be filtered out."""
    scraper = OktagonBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fixture_data
        results = await scraper.scrape_odds("basketball")

    # Only "Igrači ~" (non-duel) matches should produce results
    player_names = {r.player_name for r in results}
    # Duels have two players in home — they should be filtered
    for name in player_names:
        assert "&" not in name  # Specials have "Player A & Player B"


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
