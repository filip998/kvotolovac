from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.meridian_scraper import (
    MeridianScraper,
    _build_basic_auth,
    _is_player_market,
    _parse_player_name,
    _parse_markets,
    _parse_start_time,
)
from app.models.schemas import RawOddsData

EVENTS_FIXTURE = Path(__file__).parent / "fixtures" / "meridian_events.json"
MARKETS_FIXTURE = Path(__file__).parent / "fixtures" / "meridian_markets.json"


@pytest.fixture
def events_data() -> dict:
    with open(EVENTS_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def markets_data() -> dict:
    with open(MARKETS_FIXTURE) as f:
        return json.load(f)


# ── Unit tests for helpers ────────────────────────────────


def test_parse_player_name_last_first():
    assert _parse_player_name("Mirotic, Nikola") == "Nikola Mirotic"


def test_parse_player_name_no_comma():
    assert _parse_player_name("LeBron James") == "LeBron James"


def test_parse_player_name_extra_spaces():
    assert _parse_player_name("  Blossomgame ,  Jaron  ") == "Jaron Blossomgame"


def test_parse_player_name_empty():
    assert _parse_player_name("") == ""


def test_parse_start_time():
    result = _parse_start_time(1775842200000)
    assert result is not None
    assert "2026" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


def test_build_basic_auth():
    auth = _build_basic_auth()
    assert isinstance(auth, str)
    assert len(auth) > 50  # base64-encoded sha512 is long


# ── Parsing real fixture data ─────────────────────────────


def test_parse_markets_returns_data(markets_data):
    payload = markets_data["markets"].get("payload", [])
    results = _parse_markets(
        payload,
        event_id=123,
        home_team="Team A",
        away_team="Team B",
        league_id="euroleague",
        start_time="2026-04-10T12:00:00+00:00",
        market_type="player_points",
    )
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


def test_parse_markets_has_player_names(markets_data):
    payload = markets_data["markets"].get("payload", [])
    results = _parse_markets(
        payload, 123, "A", "B", "euroleague", None, "player_points",
    )
    for r in results:
        assert r.player_name
        # Names should be "FirstName LastName", not "LastName, FirstName"
        assert "," not in r.player_name


def test_parse_markets_has_thresholds(markets_data):
    payload = markets_data["markets"].get("payload", [])
    results = _parse_markets(
        payload, 123, "A", "B", "euroleague", None, "player_points",
    )
    for r in results:
        assert r.threshold > 0


def test_parse_markets_has_odds(markets_data):
    payload = markets_data["markets"].get("payload", [])
    results = _parse_markets(
        payload, 123, "A", "B", "euroleague", None, "player_points",
    )
    with_both = [r for r in results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_markets_bookmaker_id(markets_data):
    payload = markets_data["markets"].get("payload", [])
    results = _parse_markets(
        payload, 123, "A", "B", "euroleague", None, "player_points",
    )
    for r in results:
        assert r.bookmaker_id == "meridian"


def test_parse_markets_market_type(markets_data):
    payload = markets_data["markets"].get("payload", [])
    results = _parse_markets(
        payload, 123, "A", "B", "euroleague", None, "player_points",
    )
    for r in results:
        assert r.market_type == "player_points"


def test_parse_markets_empty():
    assert _parse_markets([], 123, "A", "B", "x", None, "player_points") == []


def test_parse_markets_skips_null_threshold():
    """Markets with overUnder=null (milestone-style) are skipped."""
    payload = [{
        "markets": [{
            "name": "Player, Test",
            "state": "ACTIVE",
            "overUnder": None,
            "selections": [
                {"name": "5+", "price": 1.5},
                {"name": "6+", "price": 2.0},
            ],
        }],
    }]
    results = _parse_markets(payload, 1, "A", "B", "x", None, "player_points")
    assert results == []


def test_parse_markets_skips_non_player_names():
    """Fallback team-total markets (no comma in name) are filtered out."""
    payload = [{
        "markets": [
            {
                "name": "Ukupno (uklj.OT)",
                "state": "ACTIVE",
                "overUnder": 149.5,
                "selections": [
                    {"name": "Manje", "price": 1.85},
                    {"name": "Više", "price": 1.95},
                ],
            },
            {
                "name": "Mirotic, Nikola",
                "state": "ACTIVE",
                "overUnder": 18.5,
                "selections": [
                    {"name": "Manje", "price": 1.85},
                    {"name": "Više", "price": 1.95},
                ],
            },
        ],
    }]
    results = _parse_markets(payload, 1, "A", "B", "x", None, "player_points")
    assert len(results) == 1
    assert results[0].player_name == "Nikola Mirotic"


def test_parse_markets_skips_inactive():
    """Markets with state != ACTIVE are skipped."""
    payload = [{
        "markets": [{
            "name": "Player, Test",
            "state": "SUSPENDED",
            "overUnder": 15.5,
            "selections": [
                {"name": "Više", "price": 1.8},
                {"name": "Manje", "price": 2.0},
            ],
        }],
    }]
    results = _parse_markets(payload, 1, "A", "B", "x", None, "player_points")
    assert results == []


def test_parse_markets_active_with_odds():
    """Normal active market is parsed correctly."""
    payload = [{
        "markets": [{
            "name": "Mirotic, Nikola",
            "state": "ACTIVE",
            "overUnder": 18.5,
            "selections": [
                {"name": "Manje", "price": 1.85},
                {"name": "Više", "price": 1.95},
            ],
        }],
    }]
    results = _parse_markets(payload, 1, "Monaco", "Barca", "euroleague", None, "player_points")
    assert len(results) == 1
    r = results[0]
    assert r.player_name == "Nikola Mirotic"
    assert r.threshold == 18.5
    assert r.under_odds == 1.85
    assert r.over_odds == 1.95
    assert r.bookmaker_id == "meridian"
    assert r.home_team == "Monaco"
    assert r.away_team == "Barca"


# ── Integration: MeridianScraper with mocked HTTP ────────


@pytest.mark.asyncio
async def test_scraper_returns_data(events_data, markets_data):
    scraper = MeridianScraper()
    markets_payload = markets_data["markets"]

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            return events_data
        return markets_payload

    with patch.object(scraper._http, "post_json", side_effect=mock_post), \
         patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = MeridianScraper()
    results = await scraper.scrape_odds("football")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_auth_failure():
    scraper = MeridianScraper()
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("Auth error")
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_events():
    scraper = MeridianScraper()

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        return {"payload": {"events": []}}

    with patch.object(scraper._http, "post_json", side_effect=mock_post), \
         patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = MeridianScraper()
    assert scraper.get_bookmaker_id() == "meridian"
    assert scraper.get_bookmaker_name() == "Meridian"
    assert "basketball" in scraper.get_supported_leagues()


@pytest.mark.asyncio
async def test_scraper_http_error_on_events():
    scraper = MeridianScraper()

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    with patch.object(scraper._http, "post_json", side_effect=mock_post), \
         patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")
    assert results == []
