from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.merkurxtip_scraper import (
    MerkurXTipScraper,
    _parse_match_detail,
    _parse_game_total_ot_match,
    _get_player_match_ids,
    _get_total_match_ids,
    _parse_start_time,
    _extract_league_id,
)
from app.models.schemas import RawOddsData

LEAGUE_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "merkurxtip_league.json"
MATCH_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "merkurxtip_match.json"
TOTALS_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "merkurxtip_game_total_ot.json"


@pytest.fixture
def league_data() -> dict:
    with open(LEAGUE_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def match_data() -> dict:
    with open(MATCH_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def totals_data() -> dict:
    with open(TOTALS_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def player_matches(league_data) -> list[dict]:
    """Extract only player matches from league fixture."""
    return [
        m for m in league_data["esMatches"]
        if "igrači" in m.get("leagueName", "").lower()
        and m.get("params", {}).get("ouPlPoints")
    ]


# ── Unit tests for helpers ────────────────────────────────


def test_parse_start_time():
    result = _parse_start_time(1775923200000)
    assert result is not None
    assert "2026" in result


def test_parse_start_time_none():
    assert _parse_start_time(None) is None


def test_parse_start_time_zero():
    assert _parse_start_time(0) is None


# ── _extract_league_id ────────────────────────────────────


def test_extract_league_id_acb():
    assert _extract_league_id("ACB Igrači") == "acb"


def test_extract_league_id_nba():
    assert _extract_league_id("NBA Igrači") == "nba"


def test_extract_league_id_euroleague():
    assert _extract_league_id("Euroleague Igrači") == "euroleague"


def test_extract_league_id_only_igraci():
    assert _extract_league_id("Igrači") == "basketball"


def test_extract_league_id_no_igraci():
    assert _extract_league_id("ACB Liga") == "acb liga"


def test_extract_league_id_empty():
    assert _extract_league_id("") == "basketball"


def test_extract_league_id_case_insensitive():
    assert _extract_league_id("acb igrači") == "acb"
    assert _extract_league_id("ACB IGRAČI") == "acb"


# ── _get_player_match_ids ─────────────────────────────────


def test_get_player_match_ids(league_data):
    ids = _get_player_match_ids(league_data["esMatches"])
    assert len(ids) > 0
    assert all(isinstance(i, int) for i in ids)


def test_get_player_match_ids_filters_non_player():
    matches = [
        {
            "id": 1,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
        },
        {
            "id": 2,
            "leagueName": "ACB Liga",
            "params": {"ouPlPoints": "18.5"},
        },
        {
            "id": 3,
            "leagueName": "ACB Igrači",
            "params": {"unrelated": "1.5"},
        },
    ]
    assert _get_player_match_ids(matches) == [1]


def test_get_player_match_ids_empty():
    assert _get_player_match_ids([]) == []


def test_get_player_match_ids_no_id():
    matches = [
        {
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
        },
    ]
    assert _get_player_match_ids(matches) == []


def test_get_player_match_ids_includes_non_points_markets():
    matches = [
        {
            "id": 1,
            "leagueName": "ACB Igrači",
            "params": {"ouPlTPRA": "45.5"},
        },
        {
            "id": 2,
            "leagueName": "ACB Igrači",
            "params": {"ouPlRebounds": "5.5"},
        },
    ]
    assert _get_player_match_ids(matches) == [1, 2]


def test_get_player_match_ids_skips_no_odds_params(league_data):
    """Match 132935923 has ouPlPoints but empty odds — still has param, so included."""
    ids = _get_player_match_ids(league_data["esMatches"])
    assert 132935923 in ids


def test_get_total_match_ids(totals_data):
    assert _get_total_match_ids(totals_data["list"]["esMatches"]) == [132948727]


# ── _parse_match_detail ───────────────────────────────────


def test_parse_match_detail_returns_data(match_data):
    results = _parse_match_detail(match_data)
    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


def test_parse_match_detail_bookmaker_id(match_data):
    for r in _parse_match_detail(match_data):
        assert r.bookmaker_id == "merkurxtip"


def test_parse_match_detail_has_player_names(match_data):
    for r in _parse_match_detail(match_data):
        assert r.player_name == "J.Batemon"


def test_parse_match_detail_has_thresholds(match_data):
    for r in _parse_match_detail(match_data):
        assert r.threshold > 0


def test_parse_match_detail_has_odds(match_data):
    results = _parse_match_detail(match_data)
    with_both = [r for r in results if r.over_odds and r.under_odds]
    assert len(with_both) > 0


def test_parse_match_detail_league_id(match_data):
    results = _parse_match_detail(match_data)
    assert all(r.league_id == "acb" for r in results)


def test_parse_match_detail_market_types(match_data):
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
    for r in _parse_match_detail(match_data):
        assert r.market_type in valid_types


def test_parse_match_detail_empty():
    assert _parse_match_detail({}) == []


def test_parse_match_detail_with_alt_thresholds():
    """Match with alt thresholds produces multiple player_points entries."""
    match = {
        "home": "LeBron James",
        "away": "LA Lakers",
        "leagueName": "NBA Igrači",
        "kickOffTime": 1775923200000,
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
    assert all(r.market_type == "player_points" for r in results)


def test_parse_match_detail_all_markets_and_ladders():
    """Full match with all threshold lines + fixed ladders."""
    match = {
        "home": "Nikola Jokic",
        "away": "Denver Nuggets",
        "leagueName": "NBA Igrači",
        "kickOffTime": 1775923200000,
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
            "51679": 1.91, "51681": 1.89,
            "51688": 1.7, "51690": 2.0,
            "55672": 1.8, "55674": 1.95,
            "55681": 2.1, "55683": 1.65,
            "55244": 1.85, "55246": 1.91,
            "55247": 1.78, "55249": 1.98,
            "55250": 1.82, "55252": 1.92,
            "55215": 1.76, "55217": 2.04,
            "54096": 1.05,
            "54111": 1.32,
            "54141": 8.5,
        },
    }

    results = _parse_match_detail(match)
    markets = {(r.market_type, r.threshold) for r in results}

    assert ("player_points", 28.5) in markets
    assert ("player_3points", 1.5) in markets
    assert ("player_steals", 1.5) in markets
    assert ("player_blocks", 0.5) in markets
    assert ("player_points_rebounds", 40.5) in markets
    assert ("player_points_assists", 36.5) in markets
    assert ("player_rebounds_assists", 12.5) in markets
    assert ("player_points_rebounds_assists", 46.5) in markets

    ladder_results = [
        r for r in results
        if r.market_type == "player_points_milestones" and r.under_odds is None
    ]
    assert {(r.threshold, r.over_odds) for r in ladder_results} == {
        (4.5, 1.05),
        (19.5, 1.32),
        (49.5, 8.5),
    }


def test_parse_match_detail_fixture_all_ladders(match_data):
    """The match fixture has all 11 fixed ladder entries."""
    results = _parse_match_detail(match_data)
    ladder = [r for r in results if r.market_type == "player_points_milestones"]
    assert len(ladder) == 11
    thresholds = sorted([r.threshold for r in ladder])
    assert thresholds == [4.5, 9.5, 14.5, 19.5, 24.5, 29.5, 34.5, 39.5, 44.5, 49.5, 59.5]


def test_parse_match_detail_fixture_all_threshold_lines(match_data):
    """The match fixture has all 12 threshold lines (3 points + 9 other markets)."""
    results = _parse_match_detail(match_data)
    threshold_results = [r for r in results if r.market_type != "player_points_milestones"]
    assert len(threshold_results) == 12


def test_parse_match_detail_missing_threshold():
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "ACB Igrači",
        "kickOffTime": 1775923200000,
        "params": {},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_non_player_league():
    match = {
        "home": "Real Madrid",
        "away": "Barcelona",
        "leagueName": "ACB Liga",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_no_odds():
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "ACB Igrači",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "5.5"},
        "odds": {},
    }
    assert _parse_match_detail(match) == []


def test_parse_match_detail_malformed_threshold():
    match = {
        "home": "Player1",
        "away": "Team A",
        "leagueName": "ACB Igrači",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "not_a_number"},
        "odds": {"51679": 1.88, "51681": 1.92},
    }
    assert _parse_match_detail(match) == []


def test_parse_game_total_ot_match_from_list_fixture(totals_data):
    results = _parse_game_total_ot_match(totals_data["list"]["esMatches"][0])

    assert len(results) == 1
    assert results[0].market_type == "game_total_ot"
    assert results[0].league_id == "nba"
    assert results[0].player_name is None
    assert (results[0].threshold, results[0].over_odds, results[0].under_odds) == (
        222.5,
        1.9,
        1.9,
    )


def test_parse_game_total_ot_match_from_detail_fixture(totals_data):
    results = _parse_game_total_ot_match(totals_data["detail"])

    assert len(results) == 9
    assert all(r.market_type == "game_total_ot" for r in results)
    assert sorted((r.threshold, r.over_odds, r.under_odds) for r in results) == [
        (218.5, 1.6, 2.28),
        (219.5, 1.7, 2.18),
        (220.5, 1.75, 2.1),
        (221.5, 1.8, 2.0),
        (222.5, 1.9, 1.9),
        (223.5, 2.0, 1.8),
        (224.5, 2.1, 1.75),
        (225.5, 2.2, 1.67),
        (226.5, 2.28, 1.6),
    ]


def test_parse_game_total_ot_match_skips_player_market():
    match = {
        "id": 1,
        "home": "Jokic N.",
        "away": "Denver",
        "leagueName": "NBA Igrači",
        "params": {"overUnderOvertime": "222.5"},
        "odds": {"50444": 1.9, "50445": 1.9},
    }
    assert _parse_game_total_ot_match(match) == []


# ── Integration: MerkurXTipScraper with mocked HTTP ──────


@pytest.mark.asyncio
async def test_scraper_returns_data_from_bulk_listing(match_data, league_data):
    scraper = MerkurXTipScraper()

    async def mock_get(url, **kwargs):
        if url.endswith("/sport/SK/mob"):
            return league_data
        if url.endswith("/sport/B/mob"):
            return {"esMatches": []}
        if "/league/" in url:
            pytest.fail("legacy league fallback should not run when bulk listing has player matches")
        return match_data

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) > 0
    assert all(isinstance(r, RawOddsData) for r in results)


@pytest.mark.asyncio
async def test_scraper_unsupported_league():
    scraper = MerkurXTipScraper()
    results = await scraper.scrape_odds("euroleague")
    assert results == []


@pytest.mark.asyncio
async def test_scraper_empty_league():
    scraper = MerkurXTipScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"esMatches": []}
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_http_error():
    scraper = MerkurXTipScraper()
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network error")
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_interface():
    scraper = MerkurXTipScraper()
    assert scraper.get_bookmaker_id() == "merkurxtip"
    assert scraper.get_bookmaker_name() == "MERKUR X TIP"
    assert "basketball" in scraper.get_supported_leagues()


@pytest.mark.asyncio
async def test_scraper_falls_back_to_legacy_leagues_when_bulk_listing_empty():
    scraper = MerkurXTipScraper()
    calls: list[str] = []
    league_matches = [
        {
            "id": 201,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "26.5"},
        },
    ]
    detail_match = {
        "home": "Player One",
        "away": "Team One",
        "leagueName": "ACB Igrači",
        "kickOffTime": 1775923200000,
        "params": {"ouPlPoints": "26.5"},
        "odds": {"51679": 1.9, "51681": 1.9},
    }

    async def mock_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/sport/SK/mob"):
            return {"esMatches": []}
        if url.endswith("/sport/B/mob"):
            return {"esMatches": []}
        if "/league/" in url:
            return {"esMatches": league_matches}
        return detail_match

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 1
    assert any("/league/" in call for call in calls)


@pytest.mark.asyncio
async def test_scraper_fetches_details_concurrently_and_skips_failures():
    league_matches = [
        {
            "id": 201,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "26.5"},
        },
        {
            "id": 202,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
        },
        {
            "id": 203,
            "leagueName": "ACB Igrači",
            "params": {"ouPlTPRA": "34.5"},
        },
    ]
    detail_matches = {
        201: {
            "home": "Player One",
            "away": "Team One",
            "leagueName": "ACB Igrači",
            "kickOffTime": 1775923200000,
            "params": {"ouPlPoints": "26.5"},
            "odds": {"51679": 1.9, "51681": 1.9},
        },
        203: {
            "home": "Player Three",
            "away": "Team Three",
            "leagueName": "ACB Igrači",
            "kickOffTime": 1775923200000,
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
                return {"esMatches": league_matches}
            if url.endswith("/sport/B/mob"):
                return {"esMatches": []}

            match_id = int(url.rsplit("/", 1)[-1])
            self.active_details += 1
            self.max_active_details = max(self.max_active_details, self.active_details)
            await asyncio.sleep(0.02)
            self.active_details -= 1

            if match_id == 202:
                raise Exception("detail failed")

            return detail_matches[match_id]

    http_client = StubHttpClient()
    scraper = MerkurXTipScraper(http_client=http_client)

    results = await scraper.scrape_odds("basketball")

    assert http_client.max_active_details > 1
    assert {(r.player_name, r.market_type) for r in results} == {
        ("Player One", "player_points"),
        ("Player Three", "player_points_rebounds_assists"),
    }


@pytest.mark.asyncio
async def test_scraper_detail_failure_does_not_crash():
    """If all detail fetches fail, we get empty results, not an exception."""
    league_matches = [
        {
            "id": 301,
            "leagueName": "ACB Igrači",
            "params": {"ouPlPoints": "18.5"},
        },
    ]

    async def mock_get(url, **kwargs):
        if url.endswith("/sport/SK/mob"):
            return {"esMatches": league_matches}
        if url.endswith("/sport/B/mob"):
            return {"esMatches": []}
        raise Exception("detail failed")

    scraper = MerkurXTipScraper()
    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert results == []


@pytest.mark.asyncio
async def test_scraper_returns_ot_totals(totals_data):
    scraper = MerkurXTipScraper()

    async def mock_get(url, **kwargs):
        if url.endswith("/sport/SK/mob"):
            return {"esMatches": []}
        if url.endswith("/sport/B/mob"):
            return totals_data["list"]
        if "/league/" in url:
            return {"esMatches": []}
        if "/match/132948727" in url:
            return totals_data["detail"]
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 9
    assert {r.market_type for r in results} == {"game_total_ot"}
    assert sorted(r.threshold for r in results) == [
        218.5,
        219.5,
        220.5,
        221.5,
        222.5,
        223.5,
        224.5,
        225.5,
        226.5,
    ]


@pytest.mark.asyncio
async def test_scraper_detail_replaces_list_total_line_when_same_threshold(totals_data):
    scraper = MerkurXTipScraper()
    list_data = json.loads(json.dumps(totals_data["list"]))
    detail_data = json.loads(json.dumps(totals_data["detail"]))
    list_data["esMatches"][0]["odds"]["50444"] = 1.83
    list_data["esMatches"][0]["odds"]["50445"] = 1.87
    detail_data["kickOffTime"] = list_data["esMatches"][0]["kickOffTime"] + 300000

    async def mock_get(url, **kwargs):
        if url.endswith("/sport/SK/mob"):
            return {"esMatches": []}
        if url.endswith("/sport/B/mob"):
            return list_data
        if "/league/" in url:
            return {"esMatches": []}
        if "/match/132948727" in url:
            return detail_data
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_odds("basketball")

    assert len(results) == 9
    base_line = next(result for result in results if result.threshold == 222.5)
    assert (base_line.over_odds, base_line.under_odds) == (1.9, 1.9)
    assert base_line.start_time == "2026-04-15T23:35:00+00:00"
