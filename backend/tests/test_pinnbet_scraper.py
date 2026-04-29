from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.pinnbet_scraper import (
    PinnBetScraper,
    _extract_league_id,
    _parse_event_name,
    _parse_event_detail,
    _parse_game_total_ot_event,
    _get_player_event_ids,
    _normalize_start_time,
    _resolve_matchup_from_short_name,
)
from app.models.schemas import RawOddsData

EVENTS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_events.json"
BETS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_bets.json"
TOTALS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_game_totals.json"


def _player_threshold_bet(
    bet_type_id: int,
    bet_type_name: str,
    threshold: float,
    over: float,
    under: float,
) -> dict:
    threshold_text = str(threshold)
    return {
        "betTypeId": bet_type_id,
        "betTypeName": bet_type_name,
        "sBV": threshold_text,
        "isPlayable": True,
        "betOutcomes": [
            {"name": "više", "odd": over, "isPlayable": True},
            {"name": "manje", "odd": under, "isPlayable": True},
        ],
    }


def _odds_signature(rows: list[RawOddsData]) -> list[tuple[str, float, float | None, float | None]]:
    return [
        (row.market_type, row.threshold, row.over_odds, row.under_odds)
        for row in rows
    ]


@pytest.fixture
def events_data() -> list[dict]:
    with open(EVENTS_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def bets_data() -> dict:
    with open(BETS_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def totals_data() -> list[dict]:
    with open(TOTALS_FIXTURE) as f:
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


def test_extract_league_id_maps_nba_playoff_name():
    event = {"competitionName": "NBA - plej of", "competitionId": 999999}
    assert _extract_league_id(event) == "nba"


def test_extract_league_id_maps_nba_playoff_competition_id():
    event = {"competitionId": 13981}
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


# -- _parse_game_total_ot_event ----------------------------------------------


def test_parse_game_total_ot_event_fixture(totals_data):
    results = _parse_game_total_ot_event(totals_data[0])

    assert len(results) == 11
    assert {r.market_type for r in results} == {"game_total_ot"}
    assert {r.league_id for r in results} == {"euroleague"}
    assert {r.home_team for r in results} == {"Lyon Villeurbanne"}
    assert {r.away_team for r in results} == {"Fenerbahce"}
    assert all(r.player_name is None for r in results)
    assert sorted(r.threshold for r in results) == [
        161.5,
        162.5,
        163.5,
        164.5,
        165.5,
        166.5,
        167.5,
        168.5,
        169.5,
        170.5,
        171.5,
    ]


def test_parse_game_total_ot_event_ignores_team_totals_and_handicap(totals_data):
    results = _parse_game_total_ot_event(totals_data[0])

    assert len(results) == 11
    assert all(result.market_type == "game_total_ot" for result in results)
    assert {result.threshold for result in results} == {
        161.5,
        162.5,
        163.5,
        164.5,
        165.5,
        166.5,
        167.5,
        168.5,
        169.5,
        170.5,
        171.5,
    }


# -- _parse_event_detail ---------------------------------------------------


def test_parse_event_detail_basic(events_data, bets_data):
    event = events_data[0]  # Alfonso Plummer
    results = _parse_event_detail(event, bets_data)
    assert len(results) == 1
    r = results[0]
    assert r.bookmaker_id == "pinnbet"
    assert r.player_name == "Alfonso Plummer"
    assert r.home_team == "KK Bosna"
    assert r.away_team == "KK Crvena Zvezda"
    assert r.threshold == 12.5
    assert r.over_odds == 1.50
    assert r.under_odds == 2.40
    assert r.market_type == "player_points"
    assert r.league_id == "aba_liga"
    assert r.start_time == "2026-04-11T16:00:00+00:00"


def test_parse_event_detail_only_bet_type_1200(events_data, bets_data):
    """Only supported threshold market types should be parsed from the fixture."""
    event = events_data[0]
    results = _parse_event_detail(event, bets_data)
    assert len(results) == 1
    assert results[0].threshold == 12.5


def test_parse_event_detail_inline_event_bets_match_detail_shape(events_data, bets_data):
    event = {**events_data[0], "bets": bets_data["bets"]}

    inline_results = _parse_event_detail(event, event)
    detail_results = _parse_event_detail(event, bets_data)

    assert _odds_signature(inline_results) == _odds_signature(detail_results)
    assert len(inline_results) == 1


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
        "shortName": "Team X-Team Y",
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


def test_parse_event_detail_parses_supported_market_types():
    event = {
        "name": "Player One - Team X",
        "shortName": "Team X-Team Y",
        "competitionName": "AdmiralBet ABA liga - plej of",
        "dateTime": "2026-04-11T16:00:00",
    }
    detail = {
        "bets": [
            {
                "betTypeId": 1201,
                "betTypeName": "Ukupno asistencija",
                "sBV": "5.5",
                "betOutcomes": [
                    {"name": "više", "odd": 1.8, "isPlayable": True},
                    {"name": "manje", "odd": 1.9, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 1202,
                "betTypeName": "Ukupno skokova",
                "sBV": "7.5",
                "betOutcomes": [
                    {"name": "više", "odd": 1.7, "isPlayable": True},
                    {"name": "manje", "odd": 2.0, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 1195,
                "betTypeName": "Ukupno postignutih trojki",
                "sBV": "2.5",
                "betOutcomes": [
                    {"name": "više", "odd": 2.2, "isPlayable": True},
                    {"name": "manje", "odd": 1.6, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 1206,
                "betTypeName": "Ukupno poena+asistencija+skokova",
                "sBV": "25.5",
                "betOutcomes": [
                    {"name": "više", "odd": 1.9, "isPlayable": True},
                    {"name": "manje", "odd": 1.9, "isPlayable": True},
                ],
            },
            {
                "betTypeId": 1207,
                "betTypeName": "Double double EF",
                "sBV": None,
                "betOutcomes": [
                    {"name": "da", "odd": 2.5, "isPlayable": True},
                ],
            },
        ]
    }

    results = _parse_event_detail(event, detail)

    assert [(r.market_type, r.threshold) for r in results] == [
        ("player_assists", 5.5),
        ("player_rebounds", 7.5),
        ("player_3points", 2.5),
        ("player_points_rebounds_assists", 25.5),
    ]
    assert {r.home_team for r in results} == {"Team X"}
    assert {r.away_team for r in results} == {"Team Y"}


def test_parse_event_detail_resolves_matchup_from_short_name_aliases():
    event = {
        "name": "Jared Butler - Crvena zvezda",
        "shortName": "Crv.Zvezda-Cluj Napoc",
        "competitionName": "AdmiralBet ABA liga - plej of",
        "competitionId": 22317,
        "dateTime": "2026-04-11T16:00:00",
    }
    detail = {
        "bets": [
            {
                "betTypeId": 1200,
                "betTypeName": "Ukupno poena",
                "sBV": "13.5",
                "betOutcomes": [
                    {"name": "više", "odd": 1.8, "isPlayable": True},
                    {"name": "manje", "odd": 1.9, "isPlayable": True},
                ],
            }
        ]
    }

    results = _parse_event_detail(event, detail)

    assert len(results) == 1
    assert results[0].home_team == "Crv.Zvezda"
    assert results[0].away_team == "Cluj Napoc"


def test_parse_event_detail_rejects_player_team_short_name_as_matchup():
    event = {
        "name": "Sargiunas I. - Rytas",
        "shortName": "Sargiunas I.-Rytas",
        "competitionName": "Litvanija 1 plej of",
        "dateTime": "2026-04-29T15:30:00",
    }
    detail = {
        "bets": [
            _player_threshold_bet(1200, "Ukupno poena", 8.5, 1.8, 1.9),
        ]
    }

    results = _parse_event_detail(event, detail)

    assert len(results) == 1
    assert results[0].player_name == "Sargiunas I."
    assert results[0].home_team == "Rytas"
    assert results[0].away_team == "Sargiunas I."


def test_parse_event_detail_cade_cunningham_inline_nba_rows_match_detail_shape():
    event = {
        "id": 2216025,
        "name": "Cade Cunningham - Detroit Pistons",
        "shortName": "Detroit Pistons-New York Knicks",
        "competitionId": 13981,
        "competitionName": "NBA - plej of",
        "regionId": 462,
        "sportId": 3,
        "mappingTypeId": 5,
        "dateTime": "2026-04-29T23:00:00",
        "bets": [
            _player_threshold_bet(1195, "Ukupno postignutih trojki", 1.5, 1.48, 2.6),
            _player_threshold_bet(1201, "Ukupno asistencija", 9.5, 2.0, 1.72),
            _player_threshold_bet(1200, "Ukupno poena", 26.5, 1.55, 2.2),
            _player_threshold_bet(1200, "Ukupno poena", 28.5, 1.89, 1.91),
            _player_threshold_bet(1200, "Ukupno poena", 30.5, 2.25, 1.55),
            _player_threshold_bet(1203, "Ukupno poena+asistencija", 37.5, 1.8, 1.9),
            _player_threshold_bet(1204, "Ukupno poena+skokova", 34.5, 1.87, 1.83),
            _player_threshold_bet(
                1206,
                "Ukupno poena+asistencija+skokova",
                43.5,
                1.8,
                1.88,
            ),
            _player_threshold_bet(1202, "Ukupno skokova", 5.5, 1.78, 1.9),
            _player_threshold_bet(1205, "Ukupno asistencija+skokova", 15.5, 1.95, 1.75),
        ],
    }
    detail = {"bets": event["bets"]}

    inline_results = _parse_event_detail(event, event)
    detail_results = _parse_event_detail(event, detail)

    assert _odds_signature(inline_results) == _odds_signature(detail_results)
    assert _odds_signature(inline_results) == [
        ("player_3points", 1.5, 1.48, 2.6),
        ("player_assists", 9.5, 2.0, 1.72),
        ("player_points", 26.5, 1.55, 2.2),
        ("player_points", 28.5, 1.89, 1.91),
        ("player_points", 30.5, 2.25, 1.55),
        ("player_points_assists", 37.5, 1.8, 1.9),
        ("player_points_rebounds", 34.5, 1.87, 1.83),
        ("player_points_rebounds_assists", 43.5, 1.8, 1.88),
        ("player_rebounds", 5.5, 1.78, 1.9),
        ("player_rebounds_assists", 15.5, 1.95, 1.75),
    ]
    assert {row.league_id for row in inline_results} == {"nba"}
    assert {row.player_name for row in inline_results} == {"Cade Cunningham"}
    assert {row.home_team for row in inline_results} == {"Detroit Pistons"}
    assert {row.away_team for row in inline_results} == {"New York Knicks"}


def test_resolve_matchup_from_short_name_prefers_full_team_over_internal_hyphen_split():
    assert _resolve_matchup_from_short_name(
        "Maccabi Tel-Aviv-Partizan",
        "Maccabi Tel Aviv",
        "euroleague",
    ) == ("Maccabi Tel-Aviv", "Partizan")


def test_resolve_matchup_from_short_name_handles_internal_hyphen_before_away_team():
    assert _resolve_matchup_from_short_name(
        "Paris-Levallois-Monaco",
        "Monaco",
        "france",
    ) == ("Paris-Levallois", "Monaco")


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
async def test_scraper_list_urls_use_24h_window(monkeypatch):
    scraper = PinnBetScraper()
    captured_urls: list[str] = []
    fixed_now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 24)
    monkeypatch.setattr("app.scrapers.pinnbet_scraper.current_utc_time", lambda: fixed_now)

    async def mock_get(url, **kwargs):
        captured_urls.append(url)
        return []

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results == []
    assert captured_urls
    for url in captured_urls:
        query = parse_qs(urlparse(url).query)
        assert query["dateFrom"] == ["2030-01-01T12:00:00"]
        assert query["dateTo"] == ["2030-01-02T12:00:00"]

    player_urls = [
        url for url in captured_urls if "getWebEventsSelections" in url and "sportId=3" in url
    ]
    assert len(player_urls) == 1
    assert "regionId=" not in player_urls[0]
    assert "competitionId=" not in player_urls[0]


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
async def test_scraper_integration(events_data, bets_data, totals_data):
    scraper = PinnBetScraper()
    player_urls: list[str] = []

    async def mock_get(url, **kwargs):
        if "getWebEventsSelections" in url:
            if "pageId=35&sportId=2" in url:
                return totals_data
            player_urls.append(url)
            return events_data
        raise AssertionError(f"Unexpected PinnBet detail call: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 13
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "pinnbet" for r in results)
    players = {r.player_name for r in results if r.player_name}
    assert "Alfonso Plummer" in players
    assert "Marko Simonovic" in players
    game_totals = [r for r in results if r.market_type == "game_total_ot"]
    assert len(game_totals) == 11
    assert {r.home_team for r in game_totals} == {"Lyon Villeurbanne"}
    assert {r.away_team for r in game_totals} == {"Fenerbahce"}
    assert len(player_urls) == 1
    assert "regionId=" not in player_urls[0]
    assert "competitionId=" not in player_urls[0]


@pytest.mark.asyncio
async def test_scraper_uses_only_list_endpoints_for_inline_player_bets(
    events_data,
    totals_data,
):
    scraper = PinnBetScraper()
    captured_urls: list[str] = []

    async def mock_get(url, **kwargs):
        captured_urls.append(url)
        if "getWebEventsSelections" in url:
            if "pageId=35&sportId=2" in url:
                return totals_data
            return events_data
        raise AssertionError(f"Unexpected PinnBet detail call: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 13
    assert len(captured_urls) == 2
    assert all("getWebEventsSelections" in url for url in captured_urls)
    assert any("pageId=3&sportId=3" in url for url in captured_urls)
    assert any("pageId=35&sportId=2" in url for url in captured_urls)


@pytest.mark.asyncio
async def test_scraper_skips_stub_player_bets_without_detail_fallback(
    events_data,
    totals_data,
):
    scraper = PinnBetScraper()
    stub_events = [
        {**event, "bets": [{"id": 1, "eventId": event["id"]}]}
        for event in events_data
        if event.get("mappingTypeId") == 5
    ]
    captured_urls: list[str] = []

    async def mock_get(url, **kwargs):
        captured_urls.append(url)
        if "getWebEventsSelections" in url:
            if "pageId=35&sportId=2" in url:
                return totals_data
            return stub_events
        raise AssertionError(f"Unexpected PinnBet detail call: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 11
    assert all(result.market_type == "game_total_ot" for result in results)
    assert len(captured_urls) == 2
