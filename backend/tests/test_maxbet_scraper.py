from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.maxbet_scraper import (
    MaxBetScraper,
    _parse_match_detail,
    _get_player_match_ids,
    _parse_start_time,
)
from app.models.schemas import RawOddsData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "maxbet_specials.json"


@pytest.fixture
def fixture_data() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def player_matches(fixture_data) -> list[dict]:
    """Extract only player points matches from fixture."""
    return [m for m in fixture_data["esMatches"]
            if "poeni igrača" in m.get("leagueName", "").lower()
            and m.get("params", {}).get("ouPlPoints")]


# ── Unit tests for helpers ────────────────────────────────


def test_parse_start_time():
    result = _parse_start_time(1775775600000)
    assert result is not None
    assert "2026-04" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


# ── Parsing real fixture data ─────────────────────────────


def test_get_player_match_ids(fixture_data):
    ids = _get_player_match_ids(fixture_data["esMatches"])
    assert len(ids) > 0
    assert all(isinstance(i, int) for i in ids)


def test_parse_match_detail_returns_data(player_matches):
    results = _parse_match_detail(player_matches[0])
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


def test_parse_match_detail_has_player_names(player_matches):
    for m in player_matches:
        for r in _parse_match_detail(m):
            assert r.player_name


def test_parse_match_detail_has_thresholds(player_matches):
    for m in player_matches:
        for r in _parse_match_detail(m):
            assert r.threshold > 0


def test_parse_match_detail_has_odds(player_matches):
    all_results = []
    for m in player_matches:
        all_results.extend(_parse_match_detail(m))
    with_both = [r for r in all_results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_match_detail_bookmaker_id(player_matches):
    for m in player_matches:
        for r in _parse_match_detail(m):
            assert r.bookmaker_id == "maxbet"


def test_parse_match_detail_market_type(player_matches):
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
    for m in player_matches:
        for r in _parse_match_detail(m):
            assert r.market_type in valid_types


def test_parse_match_detail_empty():
    assert _parse_match_detail({}) == []


def test_parse_match_detail_with_alt_thresholds():
    """Match with alt thresholds produces multiple RawOddsData per player."""
    match = {
        "home": "LeBron James",
        "away": "LA Lakers",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775779200000,
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


def test_parse_match_detail_includes_expanded_markets_and_fixed_ladders():
    match = {
        "home": "Nikola Jokic",
        "away": "Denver Nuggets",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775779200000,
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
            "51679": 1.91,
            "51681": 1.89,
            "51688": 1.7,
            "51690": 2.0,
            "55672": 1.8,
            "55674": 1.95,
            "55681": 2.1,
            "55683": 1.65,
            "55244": 1.85,
            "55246": 1.91,
            "55247": 1.78,
            "55249": 1.98,
            "55250": 1.82,
            "55252": 1.92,
            "55215": 1.76,
            "55217": 2.04,
            "54096": 1.05,
            "54111": 1.32,
            "54141": 8.5,
        },
    }

    results = _parse_match_detail(match)
    markets = {(result.market_type, result.threshold) for result in results}

    assert ("player_3points", 1.5) in markets
    assert ("player_steals", 1.5) in markets
    assert ("player_blocks", 0.5) in markets
    assert ("player_points_rebounds", 40.5) in markets
    assert ("player_points_assists", 36.5) in markets
    assert ("player_rebounds_assists", 12.5) in markets
    assert ("player_points_rebounds_assists", 46.5) in markets

    ladder_results = [
        result
        for result in results
        if result.market_type == "player_points_milestones" and result.under_odds is None
    ]
    assert {(result.threshold, result.over_odds) for result in ladder_results} == {
        (4.5, 1.05),
        (19.5, 1.32),
        (49.5, 8.5),
    }


def test_parse_match_detail_missing_threshold():
    """Match without ouPlPoints in params is skipped."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775775600000,
        "params": {},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_get_player_match_ids_includes_supported_non_points_markets():
    matches = [
        {
            "id": 1,
            "leagueName": "Poeni igrača NBA",
            "params": {"ouPlTPRA": "45.5"},
        },
        {
            "id": 2,
            "leagueName": "Poeni igrača NBA",
            "params": {"unrelated": "1.5"},
        },
        {
            "id": 3,
            "leagueName": "Ukupno poena",
            "params": {"ouPlTPRA": "45.5"},
        },
    ]

    assert _get_player_match_ids(matches) == [1]


def test_parse_match_detail_non_player_league():
    """Match with leagueName without 'poeni igrača' is skipped."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni - Minuti",
        "kickOffTime": 1775775600000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_no_odds():
    """Match without over/under odds is skipped."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775775600000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_malformed_threshold():
    """Match with non-numeric threshold is skipped."""
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775775600000,
        "params": {"ouPlPoints": "not_a_number"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


# ── Integration: MaxBetScraper with mocked HTTP ──────────


@pytest.mark.asyncio
async def test_scraper_returns_data(player_matches):
    scraper = MaxBetScraper()
    # Mock: list returns fixture, detail returns each player match
    async def mock_get(url, **kwargs):
        if "/mob" in url:
            return {"esMatches": player_matches}
        # Detail endpoint — find by match ID in URL
        for m in player_matches:
            if str(m.get("id", "")) in url:
                return m
        return player_matches[0]

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = MaxBetScraper()
    results = await scraper.scrape_odds("euroleague")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_response():
    scraper = MaxBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"esMatches": []}
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = MaxBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = MaxBetScraper()
    assert scraper.get_bookmaker_id() == "maxbet"
    assert scraper.get_bookmaker_name() == "MaxBet"
    assert "basketball" in scraper.get_supported_leagues()


@pytest.mark.asyncio
async def test_scraper_fetches_details_concurrently_and_skips_failures():
    list_matches = [
        {
            "id": 101,
            "leagueName": "Poeni igrača NBA",
            "params": {"ouPlPoints": "26.5"},
        },
        {
            "id": 102,
            "leagueName": "Poeni igrača NBA",
            "params": {"ouPlPoints": "18.5"},
        },
        {
            "id": 103,
            "leagueName": "Poeni igrača NBA",
            "params": {"ouPlTPRA": "34.5"},
        },
    ]
    detail_matches = {
        101: {
            "home": "Player One",
            "away": "Team One",
            "leagueName": "Poeni igrača NBA",
            "kickOffTime": 1775779200000,
            "params": {"ouPlPoints": "26.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
        103: {
            "home": "Player Three",
            "away": "Team Three",
            "leagueName": "Poeni igrača NBA",
            "kickOffTime": 1775779200000,
            "params": {"ouPlTPRA": "34.5"},
            "odds": {"55215": 1.87, "55217": 1.93},
        },
    }

    class StubHttpClient:
        def __init__(self) -> None:
            self.rate_limit_per_second = 4.0
            self.active_details = 0
            self.max_active_details = 0

        async def get_json(self, url: str, **kwargs):
            if "/mob" in url:
                return {"esMatches": list_matches}

            match_id = int(url.rsplit("/", 1)[-1])
            self.active_details += 1
            self.max_active_details = max(self.max_active_details, self.active_details)
            await asyncio.sleep(0.02)
            self.active_details -= 1

            if match_id == 102:
                raise Exception("detail failed")

            return detail_matches[match_id]

    http_client = StubHttpClient()
    scraper = MaxBetScraper(http_client=http_client)

    results = await scraper.scrape_odds("basketball")

    assert http_client.max_active_details > 1
    assert {(result.player_name, result.market_type) for result in results} == {
        ("Player One", "player_points"),
        ("Player Three", "player_points_rebounds_assists"),
    }
