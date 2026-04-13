from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.mozzart_scraper import (
    MozzartScraper,
    _MATCHES_API_URL,
    _SPECIALS_API_URL,
    _extract_league_id,
    _extract_player_and_market,
    _parse_game_total_items,
    _parse_items,
    _parse_start_time,
)
from app.models.schemas import RawOddsData

SPECIALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mozzart_specials.json"
MATCHES_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mozzart_matches.json"


@pytest.fixture
def fixture_data() -> dict:
    with open(SPECIALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def matches_fixture_data() -> dict:
    with open(MATCHES_FIXTURE_PATH) as f:
        return json.load(f)


# ── Unit tests for helpers ────────────────────────────────


def test_extract_player_name_normal():
    name, market = _extract_player_and_market("Broj poena B.Saraf")
    assert name == "B.Saraf"
    assert market == "player_points"


def test_extract_player_name_full_name():
    name, market = _extract_player_and_market("Broj poena LeBron James")
    assert name == "LeBron James"
    assert market == "player_points"


def test_extract_player_name_no_match():
    name, _ = _extract_player_and_market("Ukupno poena na meču")
    assert name is None


def test_extract_player_name_empty():
    name, _ = _extract_player_and_market("")
    assert name is None


def test_extract_rebounds():
    name, market = _extract_player_and_market("Broj skokova B.Saraf")
    assert name == "B.Saraf"
    assert market == "player_rebounds"


def test_extract_assists():
    name, market = _extract_player_and_market("Broj asistencija B.Saraf")
    assert name == "B.Saraf"
    assert market == "player_assists"


def test_parse_start_time():
    result = _parse_start_time(1775775600000)
    assert result is not None
    assert "2026-04" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


def test_extract_league_id_known_competitions():
    assert _extract_league_id("USA NBA") == "nba"
    assert _extract_league_id("Euroleague") == "euroleague"
    assert _extract_league_id("ABA League") == "aba_liga"
    assert _extract_league_id("AdmiralBet ABA liga - plej of") == "aba_liga"


def test_extract_league_id_fallback_slug():
    assert _extract_league_id("Italija 1") == "italija_1"
    assert _extract_league_id("") == "basketball"


# ── Parsing real fixture data ─────────────────────────────


def test_parse_items_returns_data(fixture_data):
    results = _parse_items(fixture_data["items"])
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


def test_parse_items_has_player_names(fixture_data):
    results = _parse_items(fixture_data["items"])
    player_names = [r.player_name for r in results if r.player_name]
    assert len(player_names) > 0
    # Names should not contain "Broj poena" prefix
    for name in player_names:
        assert "Broj poena" not in name


def test_parse_items_has_thresholds(fixture_data):
    results = _parse_items(fixture_data["items"])
    for r in results:
        assert r.threshold > 0


def test_parse_items_has_odds(fixture_data):
    results = _parse_items(fixture_data["items"])
    # At least some results should have both over and under odds
    with_both = [r for r in results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_items_bookmaker_id(fixture_data):
    results = _parse_items(fixture_data["items"])
    for r in results:
        assert r.bookmaker_id == "mozzart"


def test_parse_items_market_type(fixture_data):
    results = _parse_items(fixture_data["items"])
    valid_types = {"player_points", "player_rebounds", "player_assists"}
    for r in results:
        assert r.market_type in valid_types


def test_parse_items_has_teams(fixture_data):
    results = _parse_items(fixture_data["items"])
    for r in results:
        assert r.home_team
        assert r.away_team


def test_parse_items_empty():
    assert _parse_items([]) == []


def test_parse_items_match_with_no_odds():
    items = [{
        "home": {"name": "Team A"},
        "visitor": {"name": "Team B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [],
    }]
    assert _parse_items(items) == []


def test_parse_items_malformed_odds():
    items = [{
        "home": {"name": "Team A"},
        "visitor": {"name": "Team B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [{
            "groupName": "Broj poena igrača",
            "odds": [{
                "specialOddValue": "not_a_number",
                "value": 1.5,
                "oddStatus": "ACTIVE",
                "game": {"name": "Broj poena TestPlayer"},
                "subgame": {"name": "više"},
            }],
        }],
    }]
    # Should gracefully skip malformed data
    results = _parse_items(items)
    assert len(results) == 0


def test_parse_items_interleaved_odds_order():
    """Odds may arrive in any order — parser must aggregate correctly."""
    items = [{
        "home": {"name": "Team A"},
        "visitor": {"name": "Team B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [{
            "groupName": "Broj poena igrača",
            "odds": [
                # Player1 over first
                {"specialOddValue": "15.5", "value": 1.8, "oddStatus": "ACTIVE",
                 "game": {"name": "Broj poena Player1"}, "subgame": {"name": "više"}},
                # Player2 over
                {"specialOddValue": "20.5", "value": 1.9, "oddStatus": "ACTIVE",
                 "game": {"name": "Broj poena Player2"}, "subgame": {"name": "više"}},
                # Player1 under (out of order!)
                {"specialOddValue": "15.5", "value": 2.0, "oddStatus": "ACTIVE",
                 "game": {"name": "Broj poena Player1"}, "subgame": {"name": "manje"}},
                # Player2 under
                {"specialOddValue": "20.5", "value": 1.85, "oddStatus": "ACTIVE",
                 "game": {"name": "Broj poena Player2"}, "subgame": {"name": "manje"}},
            ],
        }],
    }]
    results = _parse_items(items)
    assert len(results) == 2

    by_player = {r.player_name: r for r in results}
    assert by_player["Player1"].over_odds == 1.8
    assert by_player["Player1"].under_odds == 2.0
    assert by_player["Player2"].over_odds == 1.9
    assert by_player["Player2"].under_odds == 1.85


def test_parse_items_uses_canonical_aba_league_id():
    items = [{
        "home": {"name": "Team A"},
        "visitor": {"name": "Team B"},
        "competition": {"name": "AdmiralBet ABA liga - plej of"},
        "startTime": 1775775600000,
        "oddsGroup": [{
            "groupName": "Broj poena igrača",
            "odds": [
                {
                    "specialOddValue": "15.5",
                    "value": 1.8,
                    "oddStatus": "ACTIVE",
                    "game": {"name": "Broj poena Player1"},
                    "subgame": {"name": "više"},
                },
                {
                    "specialOddValue": "15.5",
                    "value": 2.0,
                    "oddStatus": "ACTIVE",
                    "game": {"name": "Broj poena Player1"},
                    "subgame": {"name": "manje"},
                },
            ],
        }],
    }]

    results = _parse_items(items)
    assert len(results) == 1
    assert results[0].league_id == "aba_liga"


def test_parse_game_total_items_returns_data(matches_fixture_data):
    results = _parse_game_total_items(matches_fixture_data["items"])

    assert len(results) == 2
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.market_type == "game_total" for r in results)
    assert all(r.player_name is None for r in results)

    by_match = {(r.home_team, r.away_team): r for r in results}
    assert by_match[("Obras", "Instituto")].threshold == 156.5
    assert by_match[("Obras", "Instituto")].over_odds == 1.85
    assert by_match[("Obras", "Instituto")].under_odds == 1.85
    assert by_match[("Boca Juniors", "Independiente")].threshold == 168.5


def test_parse_game_total_items_ignores_team_totals_and_uses_group_threshold():
    items = [{
        "home": {"name": "Team A"},
        "visitor": {"name": "Team B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [
            {
                "groupName": "Ukupno poena domaćin",
                "specialOddValue": "80.5",
                "odds": [
                    {"value": 1.8, "oddStatus": "ACTIVE", "subgame": {"name": "više"}},
                    {"value": 1.9, "oddStatus": "ACTIVE", "subgame": {"name": "manje"}},
                ],
            },
            {
                "groupName": "  Ukupno poena na meču  ",
                "specialOddValue": "160.5",
                "odds": [
                    {"value": 1.85, "oddStatus": "ACTIVE", "subgame": {"name": "više"}},
                    {"value": 1.95, "oddStatus": "ACTIVE", "subgame": {"name": "manje"}},
                ],
            },
        ],
    }]

    results = _parse_game_total_items(items)

    assert len(results) == 1
    assert results[0].threshold == 160.5
    assert results[0].over_odds == 1.85
    assert results[0].under_odds == 1.95


# ── Integration: MozzartScraper with mocked HTTP ──────────


@pytest.mark.asyncio
async def test_scraper_returns_data(fixture_data):
    scraper = MozzartScraper()
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = fixture_data
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


@pytest.mark.asyncio
async def test_scraper_returns_player_props_and_game_totals(fixture_data, matches_fixture_data):
    scraper = MozzartScraper()
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [fixture_data, matches_fixture_data]
        results = await scraper.scrape_odds("basketball")

    market_types = {result.market_type for result in results}
    game_totals = [result for result in results if result.market_type == "game_total"]

    assert "player_points" in market_types
    assert "game_total" in market_types
    assert len(game_totals) == 2
    assert all(result.player_name is None for result in game_totals)
    assert mock_post.await_args_list[0].args[0] == _SPECIALS_API_URL
    assert mock_post.await_args_list[1].args[0] == _MATCHES_API_URL
    assert mock_post.await_args_list[1].kwargs["json_body"]["date"] == "all"


@pytest.mark.asyncio
async def test_scraper_paginates_match_totals_until_short_page(fixture_data, matches_fixture_data):
    scraper = MozzartScraper()
    page_zero = {"items": [{} for _ in range(50)]}
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [fixture_data, page_zero, matches_fixture_data]
        results = await scraper.scrape_odds("basketball")

    match_calls = [
        call for call in mock_post.await_args_list
        if call.args[0] == _MATCHES_API_URL
    ]

    assert any(result.market_type == "game_total" for result in results)
    assert [call.kwargs["json_body"]["currentPage"] for call in match_calls] == [0, 1]
    assert all(call.kwargs["json_body"]["date"] == "all" for call in match_calls)


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = MozzartScraper()
    results = await scraper.scrape_odds("euroleague")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_response():
    scraper = MozzartScraper()
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"items": [], "matchCount": 0}
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = MozzartScraper()
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")

    # Should return empty list, not raise
    assert results == []


@pytest.mark.asyncio
async def test_scraper_returns_totals_when_specials_fail(matches_fixture_data):
    scraper = MozzartScraper()
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [Exception("Network error"), matches_fixture_data]
        results = await scraper.scrape_odds("basketball")

    assert {result.market_type for result in results} == {"game_total"}
    assert len(results) == 2


@pytest.mark.asyncio
async def test_scraper_returns_player_props_when_matches_fail(fixture_data):
    scraper = MozzartScraper()
    with patch.object(scraper._http, "post_json", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [fixture_data, Exception("Network error")]
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert "game_total" not in {result.market_type for result in results}


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = MozzartScraper()
    assert scraper.get_bookmaker_id() == "mozzart"
    assert scraper.get_bookmaker_name() == "Mozzart"
    assert "basketball" in scraper.get_supported_leagues()
