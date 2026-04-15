from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.meridian_scraper import (
    MeridianScraper,
    _build_basic_auth,
    _build_event_context,
    _get_detail_fetch_concurrency,
    _is_game_total_ot_group,
    _is_player_market,
    _parse_game_total_ot_events,
    _parse_player_name,
    _parse_markets,
    _parse_start_time,
)
from app.scrapers.http_client import HttpClient
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


@pytest.fixture
def offer_leagues_data() -> dict:
    return {
        "payload": {
            "leagues": [
                {
                    "leagueId": 77,
                    "leagueName": "NBA",
                    "leagueSlug": "usa-nba",
                    "events": [
                        {
                            "header": {
                                "eventId": 18722964,
                                "state": "ACTIVE",
                                "startTime": 4_102_444_800_000,
                                "rivals": ["Philadelphia 76ers", "Orlando Magic"],
                                "league": {
                                    "leagueId": 77,
                                    "name": "NBA",
                                    "slug": "usa-nba",
                                },
                            },
                            "positions": [
                                {
                                    "index": 0,
                                    "groups": [
                                        {
                                            "name": "Pobednik (uklj.OT )",
                                            "overUnder": None,
                                            "selections": [
                                                {"name": "1", "price": 1.79},
                                                {"name": "2", "price": 2.04},
                                            ],
                                        }
                                    ],
                                },
                                {
                                    "index": 1,
                                    "groups": [
                                        {
                                            "name": "Ukupno (uklj.OT) ",
                                            "overUnder": 222.5,
                                            "selections": [
                                                {"name": "Manje", "price": 1.91},
                                                {"name": "Više", "price": 1.9},
                                            ],
                                        }
                                    ],
                                },
                                {
                                    "index": 2,
                                    "groups": [
                                        {
                                            "name": "Ukupno Poena",
                                            "overUnder": 219.5,
                                            "selections": [
                                                {"name": "Manje", "price": 1.86},
                                                {"name": "Više", "price": 1.94},
                                            ],
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "header": {
                                "eventId": 18723322,
                                "state": "ACTIVE",
                                "startTime": 4_102_448_400_000,
                                "rivals": ["LA Clippers", "Golden State Warriors"],
                                "league": {
                                    "leagueId": 77,
                                    "name": "NBA",
                                    "slug": "usa-nba",
                                },
                            },
                            "positions": [
                                {
                                    "index": 0,
                                    "groups": [
                                        {
                                            "name": "Ukupno (uklj.OT) ",
                                            "overUnder": 221.5,
                                            "selections": [
                                                {"name": "Manje", "price": 1.92},
                                                {"name": "Više", "price": 1.89},
                                            ],
                                        }
                                    ],
                                }
                            ],
                        },
                    ],
                }
            ]
        }
    }


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


def test_get_detail_fetch_concurrency_uses_http_limit():
    client = HttpClient(rate_limit_per_second=4.0)
    assert _get_detail_fetch_concurrency(client, 20) == 4


def test_get_detail_fetch_concurrency_respects_cap():
    client = HttpClient(rate_limit_per_second=20.0)
    assert _get_detail_fetch_concurrency(client, 50) == 8


def test_build_event_context_skips_past_or_invalid_events():
    now_epoch_ms = 2_000
    valid = {
        "header": {
            "eventId": 1,
            "state": "ACTIVE",
            "rivals": ["A", "B"],
            "startTime": 3_000,
            "league": {"slug": "nba"},
        }
    }
    past = {
        "header": {
            "eventId": 2,
            "state": "ACTIVE",
            "rivals": ["A", "B"],
            "startTime": 1_000,
            "league": {"slug": "nba"},
        }
    }
    invalid = {"header": {"eventId": None, "state": "ACTIVE", "rivals": ["A"], "startTime": 3_000}}

    assert _build_event_context(valid, now_epoch_ms=now_epoch_ms) is not None
    assert _build_event_context(past, now_epoch_ms=now_epoch_ms) is None
    assert _build_event_context(invalid, now_epoch_ms=now_epoch_ms) is None


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


def test_is_game_total_ot_group():
    assert _is_game_total_ot_group("Ukupno (uklj.OT) ")
    assert not _is_game_total_ot_group("Ukupno Poena")


def test_parse_game_total_ot_events_returns_only_ot_totals(offer_leagues_data):
    results = _parse_game_total_ot_events(
        offer_leagues_data["payload"]["leagues"],
        now_epoch_ms=0,
    )

    assert len(results) == 2
    assert {result.market_type for result in results} == {"game_total_ot"}
    assert {result.player_name for result in results} == {None}
    assert {(result.home_team, result.away_team, result.threshold, result.over_odds, result.under_odds) for result in results} == {
        ("Philadelphia 76ers", "Orlando Magic", 222.5, 1.9, 1.91),
        ("LA Clippers", "Golden State Warriors", 221.5, 1.89, 1.92),
    }


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
async def test_scraper_returns_data(events_data, markets_data, offer_leagues_data):
    scraper = MeridianScraper()
    future_events = copy.deepcopy(events_data)
    for event in future_events["payload"]["events"]:
        event["header"]["startTime"] = 4_102_444_800_000
    markets_payload = markets_data["markets"]

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            page = int(kwargs["params"]["page"])
            return future_events if page == 0 else {"payload": {"events": []}}
        if "/offer/sport/55/league" in url:
            return offer_leagues_data
        return markets_payload

    with patch.object(scraper._http, "post_json", side_effect=mock_post), \
         patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert any(result.market_type == "game_total_ot" for result in results)


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


@pytest.mark.asyncio
async def test_scraper_filters_events_before_market_fetch():
    scraper = MeridianScraper()
    events_payload = {
        "payload": {
            "events": [
                {
                    "header": {
                        "eventId": 101,
                        "state": "ACTIVE",
                        "rivals": ["Future A", "Future B"],
                        "startTime": 4_102_444_800_000,
                        "league": {"slug": "nba"},
                    }
                },
                {
                    "header": {
                        "eventId": 102,
                        "state": "ACTIVE",
                        "rivals": ["Past A", "Past B"],
                        "startTime": 946_684_800_000,
                        "league": {"slug": "nba"},
                    }
                },
                {
                    "header": {
                        "eventId": 103,
                        "state": "SUSPENDED",
                        "rivals": ["Bad A", "Bad B"],
                        "startTime": 4_102_444_800_000,
                        "league": {"slug": "nba"},
                    }
                },
            ]
        }
    }
    market_calls: list[tuple[int, str]] = []

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            page = int(kwargs["params"]["page"])
            return events_payload if page == 0 else {"payload": {"events": []}}
        if "/offer/sport/55/league" in url:
            return {"payload": {"leagues": []}}
        market_calls.append((int(url.split("/events/")[1].split("/")[0]), kwargs["params"]["gameGroupId"]))
        return {"payload": []}

    with patch.object(scraper._http, "post_json", side_effect=mock_post), patch.object(
        scraper._http, "get_json", side_effect=mock_get
    ):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert market_calls == [(101, "1ace0bb3-759d-41a1-8964-7dc8aac38cfe")]


@pytest.mark.asyncio
async def test_scraper_fetches_secondary_groups_only_for_point_hits():
    scraper = MeridianScraper()
    events_payload = {
        "payload": {
            "events": [
                {
                    "header": {
                        "eventId": 101,
                        "state": "ACTIVE",
                        "rivals": ["Team A", "Team B"],
                        "startTime": 4_102_444_800_000,
                        "league": {"slug": "nba"},
                    }
                }
            ]
        }
    }
    points_payload = {
        "payload": [
            {
                "markets": [
                    {
                        "name": "Jokic, Nikola",
                        "state": "ACTIVE",
                        "overUnder": 28.5,
                        "selections": [
                            {"name": "Više", "price": 1.8},
                            {"name": "Manje", "price": 2.0},
                        ],
                    }
                ]
            }
        ]
    }
    rebounds_payload = {
        "payload": [
            {
                "markets": [
                    {
                        "name": "Jokic, Nikola",
                        "state": "ACTIVE",
                        "overUnder": 11.5,
                        "selections": [
                            {"name": "Više", "price": 1.7},
                            {"name": "Manje", "price": 2.1},
                        ],
                    }
                ]
            }
        ]
    }
    assists_payload = {
        "payload": [
            {
                "markets": [
                    {
                        "name": "Jokic, Nikola",
                        "state": "ACTIVE",
                        "overUnder": 9.5,
                        "selections": [
                            {"name": "Više", "price": 1.9},
                            {"name": "Manje", "price": 1.9},
                        ],
                    }
                ]
            }
        ]
    }
    market_calls: list[str] = []

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            page = int(kwargs["params"]["page"])
            return events_payload if page == 0 else {"payload": {"events": []}}
        if "/offer/sport/55/league" in url:
            return {"payload": {"leagues": []}}
        game_group = kwargs["params"]["gameGroupId"]
        market_calls.append(game_group)
        if game_group == "1ace0bb3-759d-41a1-8964-7dc8aac38cfe":
            return points_payload
        if game_group == "ce657e80-2e15-47b9-bbcb-871f6e597a22":
            return rebounds_payload
        return assists_payload

    with patch.object(scraper._http, "post_json", side_effect=mock_post), patch.object(
        scraper._http, "get_json", side_effect=mock_get
    ):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 3
    assert {result.market_type for result in results} == {
        "player_points",
        "player_rebounds",
        "player_assists",
    }
    assert market_calls == [
        "1ace0bb3-759d-41a1-8964-7dc8aac38cfe",
        "ce657e80-2e15-47b9-bbcb-871f6e597a22",
        "1d5c0101-d012-42dc-8d21-b3da1dfd1fd1",
    ]


@pytest.mark.asyncio
async def test_scraper_fetches_game_total_ot_from_offer_endpoint(markets_data, offer_leagues_data):
    scraper = MeridianScraper()
    events_payload = {
        "payload": {
            "events": [
                {
                    "header": {
                        "eventId": 101,
                        "state": "ACTIVE",
                        "rivals": ["Philadelphia 76ers", "Orlando Magic"],
                        "startTime": 4_102_444_800_000,
                        "league": {"leagueId": 77, "slug": "usa-nba"},
                    }
                }
            ]
        }
    }
    markets_payload = markets_data["markets"]
    requested_leagues: list[str] = []

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            page = int(kwargs["params"]["page"])
            return events_payload if page == 0 else {"payload": {"events": []}}
        if "/offer/sport/55/league" in url:
            requested_leagues.append(kwargs["params"]["leagues"])
            return offer_leagues_data
        return markets_payload

    with patch.object(scraper._http, "post_json", side_effect=mock_post), patch.object(
        scraper._http, "get_json", side_effect=mock_get
    ):
        results = await scraper.scrape_odds("basketball")

    totals = [result for result in results if result.market_type == "game_total_ot"]
    assert requested_leagues == ["77"]
    assert {(result.home_team, result.away_team, result.threshold, result.over_odds, result.under_odds) for result in totals} == {
        ("Philadelphia 76ers", "Orlando Magic", 222.5, 1.9, 1.91),
        ("LA Clippers", "Golden State Warriors", 221.5, 1.89, 1.92),
    }
