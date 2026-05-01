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
    _classify_supported_market_group,
    _is_game_total_ot_group,
    _is_handicap_ot_group,
    _is_player_market,
    _parse_game_total_ot_markets,
    _parse_handicap_ot_markets,
    _parse_player_name,
    _parse_markets,
    _parse_start_time,
    _parse_supported_markets,
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


@pytest.fixture
def all_supported_markets_payload() -> dict:
    return {
        "payload": [
            {
                "marketName": "Ukupno (uklj.OT) ",
                "markets": [
                    {
                        "name": "Ukupno (uklj.OT) ",
                        "state": "ACTIVE",
                        "overUnder": 222.5,
                        "selections": [
                            {"name": "Manje", "price": 1.91},
                            {"name": "Više", "price": 1.9},
                        ],
                    }
                ],
            },
            {
                "marketName": "Ukupno Poena (Uklj. OT)",
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
                ],
            },
            {
                "marketName": "Ukupno Skokova (Uklj. OT)",
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
                ],
            },
            {
                "marketName": "Ukupno Asistencija (Uklj. OT)",
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
                ],
            },
            {
                "marketName": "Ukupno Postignutih Trojki (Uklj. OT)",
                "markets": [
                    {
                        "name": "Jokic, Nikola",
                        "state": "ACTIVE",
                        "overUnder": 2.5,
                        "selections": [
                            {"name": "Više", "price": 2.15},
                            {"name": "Manje", "price": 1.67},
                        ],
                    }
                ],
            },
            {
                "marketName": "Ukupno Postignutih Trojki (Uklj. OT)",
                "markets": [
                    {
                        "name": "Nikola Jokic",
                        "state": "ACTIVE",
                        "overUnder": None,
                        "selections": [
                            {"name": "3+", "price": 2.8},
                            {"name": "4+", "price": 5.2},
                        ],
                    }
                ],
            },
            {
                "marketName": "Nikola Jokic (Denver Nuggets) Points, Assists and Rebounds",
                "markets": [
                    {
                        "name": "Nikola Jokic (Denver Nuggets) Points, Assists and Rebounds",
                        "state": "ACTIVE",
                        "overUnder": 40.5,
                        "selections": [
                            {"name": "Više", "price": 1.94},
                            {"name": "Manje", "price": 1.82},
                        ],
                    }
                ],
            },
        ]
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


def test_is_player_market_supports_pra_shape():
    assert _is_player_market("Nikola Jokic (Denver Nuggets) Points, Assists and Rebounds")
    assert not _is_player_market("AS Monaco Ukupno Poena (uklj.OT)")


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


def test_is_handicap_ot_group():
    assert _is_handicap_ot_group("Hendikep (uklj. OT)")
    assert _is_handicap_ot_group("Hendikep (uklj.OT)")
    assert not _is_handicap_ot_group("Hendikep Poena")
    assert not _is_handicap_ot_group("Ukupno (uklj.OT) ")


def test_classify_supported_market_group():
    assert _classify_supported_market_group("Ukupno Poena (Uklj. OT)") == "player_points"
    assert _classify_supported_market_group("Ukupno Skokova (Uklj. OT)") == "player_rebounds"
    assert _classify_supported_market_group("Ukupno Asistencija (Uklj. OT)") == "player_assists"
    assert _classify_supported_market_group("Ukupno Postignutih Trojki (Uklj. OT)") == "player_3points"
    assert (
        _classify_supported_market_group(
            "Nikola Jokic (Denver Nuggets) Points, Assists and Rebounds"
        )
        == "player_points_rebounds_assists"
    )
    assert _classify_supported_market_group("Diallo, Alpha Ukupno Poena (Uklj. OT)") == "player_points"
    assert _classify_supported_market_group("Ukupno (uklj.OT) ") == "game_total_ot"
    assert _classify_supported_market_group("Hendikep (uklj. OT)") == "home_handicap_ot"
    assert _classify_supported_market_group("AS Monaco Ukupno Poena (uklj.OT)") is None


def test_parse_game_total_ot_markets_returns_only_ot_totals(all_supported_markets_payload):
    payload = all_supported_markets_payload["payload"]
    results = _parse_game_total_ot_markets(
        payload,
        home_team="AS Monaco",
        away_team="FC Barcelona",
        league_id="euroleague",
        start_time="2026-04-10T12:00:00+00:00",
    )

    assert len(results) > 0
    assert {result.market_type for result in results} == {"game_total_ot"}
    assert {result.player_name for result in results} == {None}
    assert all(result.home_team == "AS Monaco" for result in results)
    assert all(result.away_team == "FC Barcelona" for result in results)
    assert all(result.threshold is not None for result in results)


def test_parse_handicap_ot_markets_signed_threshold_home_perspective():
    """Real fixture-style payload: handicap is team1's Asian handicap (signed,
    negative when home is favoured); selection ``"1"`` pays when home covers
    and ``"2"`` when away covers. We canonicalise to ``threshold = -handicap``
    so positive threshold = home favoured."""
    payload = [
        {
            "marketName": "Hendikep (uklj. OT)",
            "markets": [
                {
                    "name": "Hendikep (uklj. OT)",
                    "state": "ACTIVE",
                    "overUnder": None,
                    "handicap": -11.5,
                    "selections": [
                        {"selectionId": "x_0", "state": "ACTIVE", "name": "1", "price": 4.0},
                        {"selectionId": "x_1", "state": "ACTIVE", "name": "2", "price": 1.23},
                    ],
                },
                {
                    "name": "Hendikep (uklj. OT)",
                    "state": "ACTIVE",
                    "overUnder": None,
                    "handicap": 4.5,
                    "selections": [
                        {"selectionId": "y_0", "state": "ACTIVE", "name": "1", "price": 1.85},
                        {"selectionId": "y_1", "state": "ACTIVE", "name": "2", "price": 1.95},
                    ],
                },
            ],
        }
    ]
    results = _parse_handicap_ot_markets(
        payload,
        home_team="AS Monaco",
        away_team="FC Barcelona",
        league_id="euroleague",
        start_time="2026-04-10T12:00:00+00:00",
    )

    assert {r.market_type for r in results} == {"home_handicap_ot"}
    assert {r.home_team for r in results} == {"AS Monaco"}
    assert {r.away_team for r in results} == {"FC Barcelona"}
    by_threshold = {r.threshold: r for r in results}
    # handicap=-11.5 → threshold=+11.5 (home favoured), over=4.0 (home covers, hard)
    assert by_threshold[11.5].over_odds == 4.0
    assert by_threshold[11.5].under_odds == 1.23
    # handicap=+4.5 → threshold=-4.5 (home underdog)
    assert by_threshold[-4.5].over_odds == 1.85
    assert by_threshold[-4.5].under_odds == 1.95


def test_parse_handicap_ot_markets_skips_inactive_or_missing_handicap():
    payload = [
        {
            "marketName": "Hendikep (uklj. OT)",
            "markets": [
                # missing handicap field
                {
                    "state": "ACTIVE",
                    "selections": [
                        {"state": "ACTIVE", "name": "1", "price": 1.9},
                        {"state": "ACTIVE", "name": "2", "price": 1.9},
                    ],
                },
                # market not active
                {
                    "state": "SUSPENDED",
                    "handicap": -3.5,
                    "selections": [
                        {"state": "ACTIVE", "name": "1", "price": 1.9},
                        {"state": "ACTIVE", "name": "2", "price": 1.9},
                    ],
                },
                # both selections inactive
                {
                    "state": "ACTIVE",
                    "handicap": 5.5,
                    "selections": [
                        {"state": "SUSPENDED", "name": "1", "price": 1.9},
                        {"state": "SUSPENDED", "name": "2", "price": 1.9},
                    ],
                },
            ],
        }
    ]
    assert (
        _parse_handicap_ot_markets(
            payload,
            home_team="H",
            away_team="A",
            league_id="x",
            start_time=None,
        )
        == []
    )


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


def test_parse_supported_markets_returns_current_market_types(all_supported_markets_payload):
    payload = all_supported_markets_payload["payload"]
    results = _parse_supported_markets(
        payload,
        event_id=123,
        home_team="AS Monaco",
        away_team="FC Barcelona",
        league_id="euroleague",
        start_time="2026-04-10T12:00:00+00:00",
    )
    assert len(results) > 0
    assert {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_3points",
        "player_points_rebounds_assists",
        "game_total_ot",
    } <= {result.market_type for result in results}


def test_parse_supported_markets_accepts_player_prefixed_group_names():
    payload = [
        {
            "marketName": "Diallo, Alpha Ukupno Poena (Uklj. OT)",
            "markets": [
                {
                    "name": "Diallo, Alpha",
                    "state": "ACTIVE",
                    "overUnder": 13.5,
                    "selections": [
                        {"name": "Više", "price": 1.83},
                        {"name": "Manje", "price": 1.97},
                    ],
                }
            ],
        }
    ]

    results = _parse_supported_markets(
        payload,
        event_id=123,
        home_team="AS Monaco",
        away_team="FC Barcelona",
        league_id="euroleague",
        start_time="2026-04-10T12:00:00+00:00",
    )

    assert len(results) == 1
    assert results[0].market_type == "player_points"
    assert results[0].player_name == "Alpha Diallo"


def test_parse_supported_markets_skips_player_3points_ladders(all_supported_markets_payload):
    results = _parse_supported_markets(
        all_supported_markets_payload["payload"],
        event_id=123,
        home_team="AS Monaco",
        away_team="FC Barcelona",
        league_id="euroleague",
        start_time="2026-04-10T12:00:00+00:00",
    )

    threes = [result for result in results if result.market_type == "player_3points"]
    assert len(threes) == 1
    assert threes[0].player_name == "Nikola Jokic"
    assert threes[0].threshold == 2.5


def test_parse_supported_markets_parses_pra_player_names(all_supported_markets_payload):
    results = _parse_supported_markets(
        all_supported_markets_payload["payload"],
        event_id=123,
        home_team="AS Monaco",
        away_team="FC Barcelona",
        league_id="euroleague",
        start_time="2026-04-10T12:00:00+00:00",
    )

    pra = [result for result in results if result.market_type == "player_points_rebounds_assists"]
    assert len(pra) == 1
    assert pra[0].player_name == "Nikola Jokic"
    assert pra[0].threshold == 40.5


# ── Integration: MeridianScraper with mocked HTTP ────────


@pytest.mark.asyncio
async def test_scraper_returns_data(events_data, all_supported_markets_payload):
    scraper = MeridianScraper()
    future_events = copy.deepcopy(events_data)
    for event in future_events["payload"]["events"]:
        event["header"]["startTime"] = 4_102_444_800_000
    markets_payload = all_supported_markets_payload

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            page = int(kwargs["params"]["page"])
            return future_events if page == 0 else {"payload": {"events": []}}
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
        market_calls.append((int(url.split("/events/")[1].split("/")[0]), kwargs["params"]["gameGroupId"]))
        return {"payload": [{"marketName": "Ukupno (uklj.OT) ", "markets": []}]}

    with patch.object(scraper._http, "post_json", side_effect=mock_post), patch.object(
        scraper._http, "get_json", side_effect=mock_get
    ):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert market_calls == [(101, "all")]


@pytest.mark.asyncio
async def test_scraper_fetches_all_group_once_per_event():
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
    all_payload = {
        "payload": [
            {
                "marketName": "Ukupno Poena (Uklj. OT)",
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
            },
            {
                "marketName": "Ukupno Skokova (Uklj. OT)",
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
            },
            {
                "marketName": "Ukupno Asistencija (Uklj. OT)",
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
            },
            {
                "marketName": "Ukupno Postignutih Trojki (Uklj. OT)",
                "markets": [
                    {
                        "name": "Jokic, Nikola",
                        "state": "ACTIVE",
                        "overUnder": 2.5,
                        "selections": [
                            {"name": "Više", "price": 2.15},
                            {"name": "Manje", "price": 1.67},
                        ],
                    },
                    {
                        "name": "Nikola Jokic",
                        "state": "ACTIVE",
                        "overUnder": None,
                        "selections": [
                            {"name": "3+", "price": 2.8},
                            {"name": "4+", "price": 5.2},
                        ],
                    },
                ]
            },
            {
                "marketName": "Nikola Jokic (Denver Nuggets) Points, Assists and Rebounds",
                "markets": [
                    {
                        "name": "Nikola Jokic (Denver Nuggets) Points, Assists and Rebounds",
                        "state": "ACTIVE",
                        "overUnder": 40.5,
                        "selections": [
                            {"name": "Više", "price": 1.94},
                            {"name": "Manje", "price": 1.82},
                        ],
                    },
                ]
            },
        ]
    }
    market_calls: list[str] = []

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            page = int(kwargs["params"]["page"])
            return events_payload if page == 0 else {"payload": {"events": []}}
        game_group = kwargs["params"]["gameGroupId"]
        market_calls.append(game_group)
        return all_payload

    with patch.object(scraper._http, "post_json", side_effect=mock_post), patch.object(
        scraper._http, "get_json", side_effect=mock_get
    ):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 5
    assert {result.market_type for result in results} == {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_3points",
        "player_points_rebounds_assists",
    }
    assert market_calls == ["all"]


@pytest.mark.asyncio
async def test_scraper_fetches_game_total_ot_from_all_payload(all_supported_markets_payload):
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
    markets_payload = all_supported_markets_payload
    requested_groups: list[str] = []

    async def mock_post(url, **kwargs):
        return {"access_token": "test-token", "expires_at": 9999999999000}

    async def mock_get(url, **kwargs):
        if "/sport/55/events" in url:
            page = int(kwargs["params"]["page"])
            return events_payload if page == 0 else {"payload": {"events": []}}
        requested_groups.append(kwargs["params"]["gameGroupId"])
        return markets_payload

    with patch.object(scraper._http, "post_json", side_effect=mock_post), patch.object(
        scraper._http, "get_json", side_effect=mock_get
    ):
        results = await scraper.scrape_odds("basketball")

    totals = [result for result in results if result.market_type == "game_total_ot"]
    assert requested_groups == ["all"]
    assert len(totals) > 0
    assert all(result.home_team == "Philadelphia 76ers" for result in totals)
    assert all(result.away_team == "Orlando Magic" for result in totals)
    assert {(result.threshold, result.over_odds, result.under_odds) for result in totals} == {
        (222.5, 1.9, 1.91),
    }
