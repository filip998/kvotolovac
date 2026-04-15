from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.maxbet_scraper import (
    MaxBetScraper,
    _extract_league_id,
    _parse_game_total_match,
    _parse_game_total_ot_match,
    _parse_match_detail,
    _get_player_match_ids,
    _parse_start_time,
)
from app.models.schemas import RawOddsData

SPECIALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "maxbet_specials.json"
TOTALS_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "maxbet_basketball_totals.json"
)


@pytest.fixture
def specials_fixture_data() -> dict:
    with open(SPECIALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def basketball_fixture_data() -> dict:
    with open(TOTALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def player_matches(specials_fixture_data) -> list[dict]:
    """Extract only player points matches from fixture."""
    return [m for m in specials_fixture_data["esMatches"]
            if "poeni igrača" in m.get("leagueName", "").lower()
            and m.get("params", {}).get("ouPlPoints")]


# ── Unit tests for helpers ────────────────────────────────


def test_parse_start_time():
    result = _parse_start_time(1775775600000)
    assert result is not None
    assert "2026-04" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


def test_extract_league_id_known_variants():
    assert _extract_league_id("Poeni igrača USA NBA") == "nba"
    assert _extract_league_id("Košarka NBA - Play Offs") == "nba"
    assert _extract_league_id("Košarka NBA - Promotion - Play Offs") == "nba"
    assert _extract_league_id("Poeni igrača Euroleague") == "euroleague"
    assert _extract_league_id("Poeni igrača ABA Liga - Winners stage") == "aba_liga"
    assert _extract_league_id("Poeni igrača ABA League") == "aba_liga"
    assert _extract_league_id("Košarka ARGENTINA") == "argentina_1"
    assert _extract_league_id("Košarka PUERTO RICO") == "portoriko_1"


def test_extract_league_id_fallback():
    assert _extract_league_id("Poeni igrača Germany") == "germany"
    assert _extract_league_id("Košarka URUGUAY - Winners stage") == "uruguay_winners_stage"
    assert _extract_league_id("") == "basketball"


# ── Parsing real fixture data ─────────────────────────────


def test_get_player_match_ids(specials_fixture_data):
    ids = _get_player_match_ids(specials_fixture_data["esMatches"])
    assert len(ids) > 0
    assert all(isinstance(i, int) for i in ids)


def test_parse_game_total_match_returns_regular_time_lines(basketball_fixture_data):
    results = _parse_game_total_match(basketball_fixture_data["esMatches"][0])

    assert len(results) == 2
    assert all(isinstance(r, RawOddsData) for r in results)
    assert {r.market_type for r in results} == {"game_total"}
    assert {r.player_name for r in results} == {None}
    assert {r.league_id for r in results} == {"argentina_1"}
    assert {(r.threshold, r.over_odds, r.under_odds) for r in results} == {
        (157.5, 1.85, 1.85),
        (156.5, 1.8, 1.93),
    }


def test_parse_game_total_match_ignores_overtime_only_lines(basketball_fixture_data):
    assert _parse_game_total_match(basketball_fixture_data["esMatches"][1]) == []


def test_parse_game_total_ot_match_returns_ot_lines(basketball_fixture_data):
    results = _parse_game_total_ot_match(basketball_fixture_data["esMatches"][0])

    assert len(results) == 4
    assert all(isinstance(r, RawOddsData) for r in results)
    assert {r.market_type for r in results} == {"game_total_ot"}
    assert {(r.threshold, r.over_odds, r.under_odds) for r in results} == {
        (156.5, 1.8, 2.0),
        (157.5, 1.85, 1.9),
        (158.5, 1.93, 1.8),
        (160.5, 2.05, 1.85),
    }


def test_parse_game_total_ot_match_returns_ot_only_lines(basketball_fixture_data):
    results = _parse_game_total_ot_match(basketball_fixture_data["esMatches"][1])

    assert len(results) == 1
    assert results[0].market_type == "game_total_ot"
    assert results[0].league_id == "portoriko_1"
    assert (results[0].threshold, results[0].over_odds, results[0].under_odds) == (
        184.5,
        1.83,
        1.92,
    )


def test_parse_game_total_ot_match_ignores_first_half_total_codes():
    match = {
        "home": "Lyon-Villeurb.",
        "away": "Fenerbahce",
        "leagueName": "Košarka EUROLEAGUE",
        "kickOffTime": 1776362400000,
        "params": {
            "overUnderOvertime7": "169.5",
            "overUnderP": "83.5",
        },
        "odds": {
            "50456": 1.6,
            "50457": 2.25,
            "50979": 1.88,
            "50980": 1.88,
        },
    }

    results = _parse_game_total_ot_match(match)

    assert len(results) == 1
    assert (results[0].threshold, results[0].over_odds, results[0].under_odds) == (
        169.5,
        2.25,
        1.6,
    )


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


def test_parse_match_detail_uses_canonical_aba_league_id():
    match = {
        "home": "Player One",
        "away": "Team A",
        "leagueName": "Poeni igrača ABA Liga - Winners stage",
        "kickOffTime": 1775779200000,
        "params": {"ouPlPoints": "12.5"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }

    results = _parse_match_detail(match)
    assert len(results) == 1
    assert results[0].league_id == "aba_liga"


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
async def test_scraper_returns_data(player_matches, basketball_fixture_data):
    scraper = MaxBetScraper()

    async def mock_get(url, **kwargs):
        if url.endswith("/sport/SK/mob"):
            return {"esMatches": player_matches}
        if url.endswith("/sport/B/mob"):
            return basketball_fixture_data
        for m in player_matches:
            if str(m.get("id", "")) in url:
                return m
        return player_matches[0]

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)
    assert any(r.market_type == "game_total" for r in results)
    assert any(r.market_type == "game_total_ot" for r in results)
    assert any(r.player_name for r in results)


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
async def test_scraper_returns_totals_when_player_list_fails(basketball_fixture_data):
    scraper = MaxBetScraper()

    async def mock_get(url, **kwargs):
        if url.endswith("/sport/SK/mob"):
            raise Exception("player list failed")
        if url.endswith("/sport/B/mob"):
            return basketball_fixture_data
        raise AssertionError(f"unexpected url {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert {(result.market_type, result.threshold) for result in results} == {
        ("game_total", 156.5),
        ("game_total", 157.5),
        ("game_total_ot", 156.5),
        ("game_total_ot", 157.5),
        ("game_total_ot", 158.5),
        ("game_total_ot", 160.5),
        ("game_total_ot", 184.5),
    }
    assert all(result.player_name is None for result in results)


@pytest.mark.asyncio
async def test_scraper_returns_players_when_totals_list_fails(player_matches):
    scraper = MaxBetScraper()

    async def mock_get(url, **kwargs):
        if url.endswith("/sport/B/mob"):
            raise Exception("totals list failed")
        if url.endswith("/sport/SK/mob"):
            return {"esMatches": player_matches}
        for match in player_matches:
            if str(match["id"]) in url:
                return match
        raise AssertionError(f"unexpected url {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results
    assert all(result.market_type not in {"game_total", "game_total_ot"} for result in results)
    assert any(result.player_name for result in results)


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
            if url.endswith("/sport/SK/mob"):
                return {"esMatches": list_matches}
            if url.endswith("/sport/B/mob"):
                return {"esMatches": []}

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
