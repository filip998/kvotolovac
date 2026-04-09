from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.maxbet_scraper import (
    MaxBetScraper,
    _parse_matches,
    _parse_start_time,
)
from app.models.schemas import RawOddsData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "maxbet_specials.json"


@pytest.fixture
def fixture_data() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


# ── Unit tests for helpers ────────────────────────────────


def test_parse_start_time():
    result = _parse_start_time(1775775600000)
    assert result is not None
    assert "2026-04" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


# ── Parsing real fixture data ─────────────────────────────


def test_parse_matches_returns_data(fixture_data):
    results = _parse_matches(fixture_data["esMatches"])
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


def test_parse_matches_has_player_names(fixture_data):
    results = _parse_matches(fixture_data["esMatches"])
    player_names = [r.player_name for r in results if r.player_name]
    assert len(player_names) > 0


def test_parse_matches_has_thresholds(fixture_data):
    results = _parse_matches(fixture_data["esMatches"])
    for r in results:
        assert r.threshold > 0


def test_parse_matches_has_odds(fixture_data):
    results = _parse_matches(fixture_data["esMatches"])
    # At least some results should have both over and under odds
    with_both = [r for r in results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_matches_bookmaker_id(fixture_data):
    results = _parse_matches(fixture_data["esMatches"])
    for r in results:
        assert r.bookmaker_id == "maxbet"


def test_parse_matches_market_type(fixture_data):
    results = _parse_matches(fixture_data["esMatches"])
    for r in results:
        assert r.market_type == "player_points"


def test_parse_matches_empty():
    assert _parse_matches([]) == []


def test_parse_matches_missing_threshold():
    """Match without ouPlPoints in params is skipped."""
    matches = [{
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775775600000,
        "params": {},
        "odds": {"51679": 1.88, "51681": 1.92},
    }]
    assert _parse_matches(matches) == []


def test_parse_matches_non_player_league():
    """Match with leagueName without 'poeni igrača' is skipped."""
    matches = [{
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni - Minuti",
        "kickOffTime": 1775775600000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }]
    assert _parse_matches(matches) == []


def test_parse_matches_no_odds():
    """Match without over/under odds is skipped."""
    matches = [{
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775775600000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {},
    }]
    assert _parse_matches(matches) == []


def test_parse_matches_malformed_threshold():
    """Match with non-numeric threshold is skipped."""
    matches = [{
        "home": "Player1",
        "away": "Team A",
        "leagueName": "Poeni igrača NBA",
        "kickOffTime": 1775775600000,
        "params": {"ouPlPoints": "not_a_number"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }]
    assert _parse_matches(matches) == []


# ── Integration: MaxBetScraper with mocked HTTP ──────────


@pytest.mark.asyncio
async def test_scraper_returns_data(fixture_data):
    scraper = MaxBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fixture_data
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

    # Should return empty list, not raise
    assert results == []


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = MaxBetScraper()
    assert scraper.get_bookmaker_id() == "maxbet"
    assert scraper.get_bookmaker_name() == "MaxBet"
    assert "basketball" in scraper.get_supported_leagues()
