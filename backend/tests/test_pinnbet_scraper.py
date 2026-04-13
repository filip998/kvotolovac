from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.pinnbet_scraper import (
    PinnBetScraper,
    _extract_league_id,
    _parse_event_name,
    _parse_event_detail,
    _get_player_event_ids,
    _normalize_start_time,
)
from app.models.schemas import RawOddsData

EVENTS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_events.json"
BETS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_bets.json"


@pytest.fixture
def events_data() -> list[dict]:
    with open(EVENTS_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def bets_data() -> dict:
    with open(BETS_FIXTURE) as f:
        return json.load(f)


# -- _normalize_start_time ----------------------------------------------------


def test_normalize_start_time_naive():
    assert _normalize_start_time("2026-04-11T16:00:00") == "2026-04-11T16:00:00+00:00"


def test_normalize_start_time_already_canonical():
    assert _normalize_start_time("2026-04-11T16:00:00+00:00") == "2026-04-11T16:00:00+00:00"


def test_normalize_start_time_none():
    assert _normalize_start_time(None) is None


def test_normalize_start_time_invalid():
    assert _normalize_start_time("not-a-date") == "not-a-date"


# -- _parse_event_name -----------------------------------------------------


def test_parse_event_name_normal():
    player, team = _parse_event_name("Alfonso Plummer - KK Bosna")
    assert player == "Alfonso Plummer"
    assert team == "KK Bosna"


def test_parse_event_name_no_separator():
    player, team = _parse_event_name("SomePlayerOnly")
    assert player == "SomePlayerOnly"
    assert team is None


def test_parse_event_name_multiple_separators():
    player, team = _parse_event_name("Player - Team A - B")
    assert player == "Player"
    assert team == "Team A - B"


def test_parse_event_name_empty():
    player, team = _parse_event_name("")
    assert player == ""
    assert team is None


def test_parse_event_name_whitespace():
    player, team = _parse_event_name("  Player  -  Team  ")
    assert player == "Player"
    assert team == "Team"


def test_extract_league_id_from_competition_name():
    event = {"competitionName": "AdmiralBet ABA liga - plej of", "competitionId": 22317}
    assert _extract_league_id(event, fallback_league_id="nba") == "aba_liga"


def test_extract_league_id_falls_back_to_competition_id():
    event = {"competitionId": 3221}
    assert _extract_league_id(event) == "nba"


def test_extract_league_id_prefers_known_competition_id_over_unknown_name():
    event = {"competitionName": "Some Random League Name", "competitionId": 3221}
    assert _extract_league_id(event) == "nba"


def test_extract_league_id_keeps_unknown_name_when_id_is_unknown():
    event = {"competitionName": "Some Random League Name", "competitionId": 999999}
    assert _extract_league_id(event) == "some random league name"


# -- _get_player_event_ids -------------------------------------------------


def test_get_player_event_ids_filters(events_data):
    result = _get_player_event_ids(events_data)
    assert len(result) == 2
    assert all(e["mappingTypeId"] == 5 for e in result)


def test_get_player_event_ids_empty():
    assert _get_player_event_ids([]) == []


def test_get_player_event_ids_no_players():
    events = [{"mappingTypeId": 1}, {"mappingTypeId": 2}]
    assert _get_player_event_ids(events) == []


def test_get_player_event_ids_returns_full_dicts(events_data):
    result = _get_player_event_ids(events_data)
    for e in result:
        assert "sportId" in e
        assert "regionId" in e
        assert "competitionId" in e
        assert "id" in e


# -- _parse_event_detail ---------------------------------------------------


def test_parse_event_detail_basic(events_data, bets_data):
    event = events_data[0]  # Alfonso Plummer
    results = _parse_event_detail(event, bets_data)
    assert len(results) == 1
    r = results[0]
    assert r.bookmaker_id == "pinnbet"
    assert r.player_name == "Alfonso Plummer"
    assert r.home_team == "KK Bosna"
    assert r.away_team == "Alfonso Plummer"
    assert r.threshold == 12.5
    assert r.over_odds == 1.50
    assert r.under_odds == 2.40
    assert r.market_type == "player_points"
    assert r.league_id == "aba_liga"
    assert r.start_time == "2026-04-11T16:00:00+00:00"


def test_parse_event_detail_only_bet_type_1200(events_data, bets_data):
    """Only betTypeId 1200 should be parsed, not 1201."""
    event = events_data[0]
    results = _parse_event_detail(event, bets_data)
    assert len(results) == 1
    assert results[0].threshold == 12.5


def test_parse_event_detail_empty_bets(events_data):
    event = events_data[0]
    results = _parse_event_detail(event, {"bets": []})
    assert results == []


def test_parse_event_detail_no_bets_key(events_data):
    event = events_data[0]
    results = _parse_event_detail(event, {})
    assert results == []


def test_parse_event_detail_non_playable_outcomes(events_data):
    event = events_data[0]
    detail = {
        "bets": [
            {
                "betTypeId": 1200,
                "sBV": "15.5",
                "betOutcomes": [
                    {"name": "vi\u0161e", "odd": 1.8, "isPlayable": False},
                    {"name": "manje", "odd": 2.0, "isPlayable": False},
                ],
            }
        ]
    }
    results = _parse_event_detail(event, detail)
    assert results == []


def test_parse_event_detail_partial_playable(events_data):
    event = events_data[0]
    detail = {
        "bets": [
            {
                "betTypeId": 1200,
                "sBV": "15.5",
                "betOutcomes": [
                    {"name": "vi\u0161e", "odd": 1.8, "isPlayable": True},
                    {"name": "manje", "odd": 2.0, "isPlayable": False},
                ],
            }
        ]
    }
    results = _parse_event_detail(event, detail)
    assert len(results) == 1
    assert results[0].over_odds == 1.8
    assert results[0].under_odds is None


def test_parse_event_detail_bad_threshold(events_data):
    event = events_data[0]
    detail = {
        "bets": [
            {
                "betTypeId": 1200,
                "sBV": "not_a_number",
                "betOutcomes": [
                    {"name": "vi\u0161e", "odd": 1.8, "isPlayable": True},
                ],
            }
        ]
    }
    results = _parse_event_detail(event, detail)
    assert results == []


def test_parse_event_detail_missing_sbv(events_data):
    event = events_data[0]
    detail = {
        "bets": [
            {
                "betTypeId": 1200,
                "betOutcomes": [
                    {"name": "vi\u0161e", "odd": 1.8, "isPlayable": True},
                ],
            }
        ]
    }
    results = _parse_event_detail(event, detail)
    assert results == []


def test_parse_event_detail_no_name():
    event = {"name": "", "dateTime": "2026-04-11T16:00:00"}
    detail = {
        "bets": [
            {
                "betTypeId": 1200,
                "sBV": "12.5",
                "betOutcomes": [
                    {"name": "vi\u0161e", "odd": 1.8, "isPlayable": True},
                ],
            }
        ]
    }
    results = _parse_event_detail(event, detail)
    assert results == []


def test_parse_event_detail_multiple_thresholds():
    event = {
        "name": "Player One - Team X",
        "dateTime": "2026-04-11T16:00:00",
    }
    detail = {
        "bets": [
            {
                "betTypeId": 1200,
                "sBV": "12.5",
                "betOutcomes": [
                    {"name": "vi\u0161e", "odd": 1.5, "isPlayable": True},
                    {"name": "manje", "odd": 2.4, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 1200,
                "sBV": "14.5",
                "betOutcomes": [
                    {"name": "vi\u0161e", "odd": 1.3, "isPlayable": True},
                    {"name": "manje", "odd": 3.0, "isPlayable": True},
                ],
            },
        ]
    }
    results = _parse_event_detail(event, detail)
    assert len(results) == 2
    thresholds = sorted([r.threshold for r in results])
    assert thresholds == [12.5, 14.5]


# -- Scraper interface -----------------------------------------------------


def test_scraper_interface():
    scraper = PinnBetScraper()
    assert scraper.get_bookmaker_id() == "pinnbet"
    assert scraper.get_bookmaker_name() == "PinnBet"
    assert "basketball" in scraper.get_supported_leagues()


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = PinnBetScraper()
    results = await scraper.scrape_odds("soccer")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = PinnBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_unexpected_response_type():
    scraper = PinnBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"error": "not a list"}
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_events():
    scraper = PinnBetScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = []
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_no_player_events():
    scraper = PinnBetScraper()
    non_player_events = [
        {"id": 1, "mappingTypeId": 1, "name": "Team A - Team B"},
    ]
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = non_player_events
        results = await scraper.scrape_odds("basketball")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_integration(events_data, bets_data):
    scraper = PinnBetScraper()
    list_call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal list_call_count
        if "getWebEventsSelections" in url:
            list_call_count += 1
            return events_data if list_call_count == 1 else []
        return bets_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    # 2 player events (mappingTypeId 5), each gets 1 bet line from fixture
    assert len(results) == 2
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "pinnbet" for r in results)
    players = {r.player_name for r in results}
    assert "Alfonso Plummer" in players
    assert "Marko Simonovic" in players


@pytest.mark.asyncio
async def test_scraper_detail_failure_skipped(events_data, bets_data):
    """If one detail fetch fails, others still succeed."""
    scraper = PinnBetScraper()
    call_count = 0
    list_call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count, list_call_count
        if "getWebEventsSelections" in url:
            list_call_count += 1
            return events_data if list_call_count == 1 else []
        call_count += 1
        if call_count == 1:
            raise Exception("detail failed")
        return bets_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 1


@pytest.mark.asyncio
async def test_scraper_concurrent_detail_fetches(events_data, bets_data):
    """Detail fetches run concurrently via semaphore."""
    scraper = PinnBetScraper()
    active = 0
    max_active = 0
    list_call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal active, max_active, list_call_count
        if "getWebEventsSelections" in url:
            list_call_count += 1
            return events_data if list_call_count == 1 else []
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return bets_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert max_active >= 2
    assert len(results) == 2
