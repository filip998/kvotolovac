from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.balkanbet_scraper import (
    BalkanBetScraper,
    _format_filter_from,
    _parse_player_name,
    _parse_event_detail,
    _get_event_ids,
    _normalize_start_time,
)
from app.models.schemas import RawOddsData

LIST_FIXTURE = Path(__file__).parent / "fixtures" / "balkanbet_list.json"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "balkanbet_detail.json"


@pytest.fixture
def list_data() -> dict:
    with open(LIST_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def detail_data() -> dict:
    with open(DETAIL_FIXTURE) as f:
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


def test_format_filter_from_uses_naive_utc_seconds():
    assert _format_filter_from(datetime.fromisoformat("2026-04-11T19:53:04+00:00")) == (
        "2026-04-11T21:53:04"
    )


# ── _parse_player_name ────────────────────────────────────


def test_parse_player_name_normal():
    player, team = _parse_player_name("A.Plummer (Bosna)")
    assert player == "A.Plummer"
    assert team == "Bosna"


def test_parse_player_name_with_spaces():
    player, team = _parse_player_name("LeBron James (LA Lakers)")
    assert player == "LeBron James"
    assert team == "LA Lakers"


def test_parse_player_name_no_team():
    player, team = _parse_player_name("A.Plummer")
    assert player == "A.Plummer"
    assert team is None


def test_parse_player_name_empty():
    player, team = _parse_player_name("")
    assert player == ""
    assert team is None


def test_parse_player_name_nested_parens():
    # Nested parens are not expected in real data; parser treats it as no-match
    player, team = _parse_player_name("J.Smith (Team (A))")
    assert player == "J.Smith (Team (A))"
    assert team is None


# ── _get_event_ids ────────────────────────────────────────


def test_get_event_ids_from_fixture(list_data):
    ids = _get_event_ids(list_data)
    # Fixture has 2 events with marketId 2402, 1 with 9999
    assert len(ids) == 2
    assert 2503489426 in ids
    assert 2503489480 in ids
    assert 2503489500 not in ids


def test_get_event_ids_empty_data():
    assert _get_event_ids({}) == []
    assert _get_event_ids({"data": {}}) == []
    assert _get_event_ids({"data": {"events": []}}) == []


def test_get_event_ids_no_markets():
    data = {"data": {"events": [{"a": 1, "o": {}}]}}
    assert _get_event_ids(data) == []


# ── _parse_event_detail ──────────────────────────────────


def test_parse_event_detail_from_fixture(detail_data):
    results = _parse_event_detail(detail_data)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, RawOddsData)
    assert r.bookmaker_id == "balkanbet"
    assert r.player_name == "A.Plummer"
    assert r.home_team == "Bosna"
    assert r.away_team == "A.Plummer"
    assert r.market_type == "player_points"
    assert r.threshold == 12.5
    assert r.over_odds == 1.50
    assert r.under_odds == 2.40
    assert r.start_time == "2026-04-11T16:00:00+00:00"
    assert r.league_id == "basketball"


def test_parse_event_detail_empty():
    assert _parse_event_detail({}) == []


def test_parse_event_detail_no_markets():
    data = {"data": {"name": "Player (Team)", "markets": []}}
    assert _parse_event_detail(data) == []


def test_parse_event_detail_missing_outcomes():
    data = {
        "data": {
            "name": "Player (Team)",
            "markets": [
                {
                    "marketId": 2402,
                    "specialValues": ["10.5"],
                    "outcomes": [],
                }
            ],
        }
    }
    assert _parse_event_detail(data) == []


def test_parse_event_detail_missing_special_values():
    data = {
        "data": {
            "name": "Player (Team)",
            "markets": [
                {
                    "marketId": 2402,
                    "specialValues": [],
                    "outcomes": [
                        {"name": "Više", "odds": 1.5},
                        {"name": "Manje", "odds": 2.4},
                    ],
                }
            ],
        }
    }
    assert _parse_event_detail(data) == []


def test_parse_event_detail_bad_threshold():
    data = {
        "data": {
            "name": "Player (Team)",
            "markets": [
                {
                    "marketId": 2402,
                    "specialValues": ["not_a_number"],
                    "outcomes": [
                        {"name": "Više", "odds": 1.5},
                        {"name": "Manje", "odds": 2.4},
                    ],
                }
            ],
        }
    }
    assert _parse_event_detail(data) == []


def test_parse_event_detail_wrong_market_id():
    data = {
        "data": {
            "name": "Player (Team)",
            "markets": [
                {
                    "marketId": 9999,
                    "specialValues": ["10.5"],
                    "outcomes": [
                        {"name": "Više", "odds": 1.5},
                        {"name": "Manje", "odds": 2.4},
                    ],
                }
            ],
        }
    }
    assert _parse_event_detail(data) == []


def test_parse_event_detail_only_over():
    data = {
        "data": {
            "name": "Player (Team)",
            "markets": [
                {
                    "marketId": 2402,
                    "specialValues": ["15.5"],
                    "outcomes": [{"name": "Više", "odds": 1.80}],
                }
            ],
        }
    }
    results = _parse_event_detail(data)
    assert len(results) == 1
    assert results[0].over_odds == 1.80
    assert results[0].under_odds is None


def test_parse_event_detail_no_name():
    data = {
        "data": {
            "name": "",
            "markets": [
                {
                    "marketId": 2402,
                    "specialValues": ["10.5"],
                    "outcomes": [
                        {"name": "Više", "odds": 1.5},
                        {"name": "Manje", "odds": 2.4},
                    ],
                }
            ],
        }
    }
    results = _parse_event_detail(data)
    assert len(results) == 1
    assert results[0].player_name == ""
    assert results[0].home_team == ""


# ── Integration: BalkanBetScraper with mocked HTTP ───────


@pytest.mark.asyncio
async def test_scraper_returns_data(detail_data):
    scraper = BalkanBetScraper()
    list_response = {
        "data": {
            "events": [
                {"a": 100, "o": {"m1": {"a": 1, "b": 2402}}},
                {"a": 101, "o": {"m2": {"a": 2, "b": 2402}}},
            ]
        }
    }

    async def mock_get(url, **kwargs):
        if "/events/" in url:
            return detail_data
        return list_response

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 2
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "balkanbet" for r in results)


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
async def test_scraper_interface():
    scraper = BalkanBetScraper()
    assert scraper.get_bookmaker_id() == "balkanbet"
    assert scraper.get_bookmaker_name() == "BalkanBet"
    assert "basketball" in scraper.get_supported_leagues()


@pytest.mark.asyncio
async def test_scraper_detail_failure_skipped():
    """One detail failure does not prevent other events from being scraped."""
    list_response = {
        "data": {
            "events": [
                {"a": 200, "o": {"m1": {"a": 1, "b": 2402}}},
                {"a": 201, "o": {"m2": {"a": 2, "b": 2402}}},
                {"a": 202, "o": {"m3": {"a": 3, "b": 2402}}},
            ]
        }
    }
    good_detail = {
        "data": {
            "name": "J.Doe (TeamX)",
            "startsAt": "2026-04-12T19:00:00.000Z",
            "markets": [
                {
                    "marketId": 2402,
                    "specialValues": ["20.5"],
                    "outcomes": [
                        {"name": "Više", "odds": 1.70},
                        {"name": "Manje", "odds": 2.10},
                    ],
                }
            ],
        }
    }

    class StubHttpClient:
        def __init__(self) -> None:
            self.rate_limit_per_second = 4.0
            self.active = 0
            self.max_active = 0

        async def get_json(self, url: str, **kwargs):
            if "/events/" not in url:
                return list_response
            event_id = int(url.rsplit("/", 1)[-1])
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            if event_id == 201:
                raise Exception("detail failed")
            return good_detail

    http_client = StubHttpClient()
    scraper = BalkanBetScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert http_client.max_active > 1
    assert len(results) == 2
    assert all(r.player_name == "J.Doe" for r in results)


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
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        captured_params[0]["filter[from]"],
    )
