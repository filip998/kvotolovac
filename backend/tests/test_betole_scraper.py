from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.scrapers.betole_scraper import (
    BetOleScraper,
    _FOOTBALL_DETAIL_URL_TEMPLATE,
    _FOOTBALL_LIST_URL,
    _PLAYER_FEED_URL,
    _REGULAR_FEED_URL,
    _build_matchup_index,
    _extract_league_id,
    _parse_football_double_chance_detail_match,
    _parse_football_outcome_match,
    _parse_handicap_match,
    _parse_player_match,
    _parse_regular_match,
)
from app.models.schemas import RawOutcomeOffer

REGULAR_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "betole_regular_league.json"
PLAYER_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "betole_players_league.json"
FOOTBALL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "betole_football_offer.json"
FOOTBALL_DETAIL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "betole_football_match.json"

EXTRA_REGULAR_LEAGUE = {
    "id": "2265038",
    "name": "Brazil, NBB - Play Offs",
    "type": "LEAGUE",
    "url": "B",
    "count": 7,
}


@pytest.fixture
def regular_preview_data() -> dict:
    with open(REGULAR_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def player_preview_data() -> dict:
    with open(PLAYER_FIXTURE_PATH) as f:
        return json.load(f)


def test_extract_league_id_strips_player_suffixes():
    assert _extract_league_id("USA, NBA - Play Offs") == "nba"
    assert _extract_league_id("USA, NBA - Play Offs,Players") == "nba"
    assert _extract_league_id("USA, NBA - Play Offs,Players Duel") == "nba"


def test_parse_regular_match_returns_ot_total_with_source_url(regular_preview_data):
    results = _parse_regular_match(regular_preview_data["esMatches"][0])

    assert len(results) == 1
    assert results[0].market_type == "game_total_ot"
    assert (results[0].threshold, results[0].over_odds, results[0].under_odds) == (
        219.5,
        1.9,
        1.85,
    )
    assert results[0].source_url == "https://www.betole.com/match-special/90241113"


# ── Handicap (+OT) parsing ──────────────────────────────────────────────


def test_parse_handicap_match_positive_line_means_team1_favoured():
    """BetOle's ``handicapOvertime`` carries the home team's signed
    Asian-handicap line (negative = home favourite, positive = home
    underdog — same convention as Mozzart's ``Hendikep -X`` UI).  The
    parser negates the source so that positive threshold means home
    favoured (analyzer convention).  Pair codes: 50430 = home covers
    (over_odds), 50431 = away covers (under_odds).

    Live shape sample: Orlando vs Detroit (Detroit favoured) returned
    ``handicapOvertime='3.5'`` with "1"=1.9, "2"=1.9 — meaning Orlando
    +3.5 is the balanced line, i.e., Orlando is the underdog by 3.5.
    """
    match = {
        "id": 12345,
        "home": "Orlando",
        "away": "Detroit",
        "leagueName": "USA, NBA - Play Offs",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "3.5"},
        "odds": {"50430": 1.9, "50431": 1.9},
    }
    results = _parse_handicap_match(match)
    assert len(results) == 1
    row = results[0]
    assert row.market_type == "home_handicap_ot"
    # Source +3.5 (home is the underdog by 3.5) → threshold = -3.5
    assert row.threshold == -3.5
    assert row.over_odds == 1.9
    assert row.under_odds == 1.9
    assert row.bookmaker_id == "betole"
    assert row.home_team == "Orlando"
    assert row.away_team == "Detroit"
    assert row.player_name is None
    assert row.source_url == "https://www.betole.com/match-special/12345"


def test_parse_handicap_match_negative_line_means_team1_underdog():
    """Source -3.5 (home favourite by 3.5) → threshold = +3.5."""
    match = {
        "id": 222,
        "home": "Houston",
        "away": "LA Lakers",
        "leagueName": "USA, NBA",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-3.5"},
        "odds": {"50430": 1.9, "50431": 1.9},
    }
    results = _parse_handicap_match(match)
    assert len(results) == 1
    assert results[0].threshold == 3.5


def test_parse_handicap_match_pickem_zero_line_emits_row():
    match = {
        "id": 333,
        "home": "A",
        "away": "B",
        "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "0"},
        "odds": {"50430": 1.88, "50431": 1.92},
    }
    results = _parse_handicap_match(match)
    assert len(results) == 1
    assert results[0].threshold == 0.0
    # 50430 = home covers (over), 50431 = away covers (under)
    assert results[0].over_odds == 1.88
    assert results[0].under_odds == 1.92


def test_parse_handicap_match_skips_unparseable_or_missing():
    bad = {
        "id": 444, "home": "A", "away": "B",
        "leagueName": "Test", "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "garbage"},
        "odds": {"50431": 1.9, "50430": 1.9},
    }
    no_odds = {
        "id": 555, "home": "A", "away": "B",
        "leagueName": "Test", "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-3.5"},
        "odds": {},
    }
    no_line = {
        "id": 666, "home": "A", "away": "B",
        "leagueName": "Test", "kickOffTime": 1777470900000,
        "params": {},
        "odds": {"50431": 1.9, "50430": 1.9},
    }
    assert _parse_handicap_match(bad) == []
    assert _parse_handicap_match(no_odds) == []
    assert _parse_handicap_match(no_line) == []


def test_parse_regular_match_does_not_emit_handicap_after_change():
    """Regression: regular-totals parser must not pick up handicap codes."""
    match = {
        "id": 777, "home": "A", "away": "B",
        "leagueName": "Test", "kickOffTime": 1777470900000,
        "params": {"handicapOvertime": "-3.5"},
        "odds": {"50431": 1.9, "50430": 1.9},
    }
    assert _parse_regular_match(match) == []


def test_parse_player_match_uses_super_code_matchup(player_preview_data, regular_preview_data):
    matchup_index = _build_matchup_index(regular_preview_data["esMatches"])

    results = _parse_player_match(player_preview_data["esMatches"][0], matchup_index)

    assert {row.market_type for row in results} == {
        "player_assists",
        "player_points",
        "player_rebounds",
    }
    assert {row.home_team for row in results} == {"Detroit Pistons"}
    assert {row.away_team for row in results} == {"Orlando Magic"}
    assert {row.player_name for row in results} == {"Ausar Thompson"}
    assert all(row.league_id == "nba" for row in results)
    assert all(row.source_url == "https://www.betole.com/match-special/90241113" for row in results)


def test_parse_player_match_falls_back_to_team_and_kickoff(player_preview_data, regular_preview_data):
    preview_match = {
        key: value for key, value in player_preview_data["esMatches"][0].items() if key != "superCode"
    }
    matchup_index = _build_matchup_index(regular_preview_data["esMatches"])

    results = _parse_player_match(preview_match, matchup_index)

    assert {row.player_name for row in results} == {"Ausar Thompson"}
    assert {row.home_team for row in results} == {"Detroit Pistons"}
    assert {row.away_team for row in results} == {"Orlando Magic"}


@pytest.mark.asyncio
async def test_scrape_odds_uses_broad_feeds_while_matching_player_props(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
    player_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    extra_regular_preview = {
        "esMatches": [
            {
                **regular_preview_data["esMatches"][0],
                "id": 90249999,
                "matchCode": 7777,
                "leagueName": EXTRA_REGULAR_LEAGUE["name"],
                "home": "Franca",
                "away": "Botafogo",
            }
        ]
    }
    regular_feed_response = {
        "esMatches": [*regular_preview_data["esMatches"], *extra_regular_preview["esMatches"]]
    }
    player_feed_response = {
        "esMatches": [
            {
                **player_preview_data["esMatches"][0],
                "id": 90240000,
                "matchCode": 4000,
                "leagueName": "USA, NBA - Play Offs,Players Duel",
                "params": {},
                "odds": {},
            },
            *player_preview_data["esMatches"],
        ]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _REGULAR_FEED_URL:
            return regular_feed_response
        if url == _PLAYER_FEED_URL:
            return player_feed_response
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} == {
        "game_total_ot",
        "player_assists",
        "player_points",
        "player_rebounds",
    }
    assert {row.player_name for row in results if row.player_name} == {
        "Ausar Thompson",
        "Cade Cunningham",
    }
    assert {row.home_team for row in results if row.player_name} == {"Detroit Pistons"}
    assert {row.away_team for row in results if row.player_name} == {"Orlando Magic"}

    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert requested_urls == {_REGULAR_FEED_URL, _PLAYER_FEED_URL}

    regular_matchups = {
        (row.home_team, row.away_team, row.league_id)
        for row in results
        if row.player_name is None
    }
    assert regular_matchups == {
        ("Detroit Pistons", "Orlando Magic", "nba"),
        ("Franca", "Botafogo", "brazil_nbb"),
    }


@pytest.mark.asyncio
async def test_scrape_odds_returns_regular_results_when_player_leagues_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _REGULAR_FEED_URL:
            return regular_preview_data
        if url == _PLAYER_FEED_URL:
            return {"esMatches": []}
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} == {"game_total_ot"}
    assert all(row.player_name is None for row in results)

    requested_urls = {call.args[0] for call in http_client.get_json.call_args_list}
    assert requested_urls == {_REGULAR_FEED_URL, _PLAYER_FEED_URL}


@pytest.mark.asyncio
async def test_scrape_odds_builds_player_matchups_from_matched_regular_leagues(
    monkeypatch: pytest.MonkeyPatch,
    regular_preview_data,
    player_preview_data,
):
    fixture_start = datetime.fromtimestamp(1776898800, tz=timezone.utc)
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.current_utc_time",
        lambda: fixture_start - timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app.scrapers.betole_scraper.lookahead_cutoff",
        lambda now: now + timedelta(hours=24),
    )

    colliding_regular_preview = {
        "esMatches": [
            {
                **regular_preview_data["esMatches"][0],
                "id": 90248888,
                "matchCode": 8888,
                "leagueName": EXTRA_REGULAR_LEAGUE["name"],
                "home": "Franca",
                "away": "Detroit Pistons",
            }
        ]
    }
    regular_feed_response = {
        "esMatches": [
            *regular_preview_data["esMatches"],
            *colliding_regular_preview["esMatches"],
        ]
    }
    player_preview_without_super_code = {
        "esMatches": [
            {
                key: value
                for key, value in player_preview_data["esMatches"][0].items()
                if key != "superCode"
            }
        ]
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _REGULAR_FEED_URL:
            return regular_feed_response
        if url == _PLAYER_FEED_URL:
            return player_preview_without_super_code
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    player_rows = [row for row in results if row.player_name == "Ausar Thompson"]
    assert player_rows
    assert {(row.home_team, row.away_team, row.league_id) for row in player_rows} == {
        ("Detroit Pistons", "Orlando Magic", "nba")
    }


@pytest.fixture
def football_list_data() -> dict:
    with open(FOOTBALL_FIXTURE_PATH) as f:
        return json.load(f)


@pytest.fixture
def football_detail_data() -> dict:
    with open(FOOTBALL_DETAIL_FIXTURE_PATH) as f:
        return json.load(f)


def test_parse_football_outcome_match_emits_result_and_totals(football_list_data):
    results = _parse_football_outcome_match(football_list_data["esMatches"][0])

    assert len(results) == 5
    assert all(isinstance(r, RawOutcomeOffer) for r in results)
    assert {r.bookmaker_id for r in results} == {"betole"}
    assert {r.sport for r in results} == {"football"}
    assert {r.league_id for r in results} == {"saudi_arabia_saudi_professional_league"}
    assert {r.home_team for r in results} == {"Al Khaleej"}
    assert {r.away_team for r in results} == {"Al Hilal Riyadh"}
    assert {r.start_time for r in results} == {"2026-05-05T18:00:00+00:00"}
    assert {
        (r.market_type, r.outcome_code, r.line, r.raw_label, r.odds)
        for r in results
    } == {
        ("football_result", "home", None, "1", 7.6),
        ("football_result", "draw", None, "X", 5.5),
        ("football_result", "away", None, "2", 1.26),
        ("football_total_goals", "under", 2.5, "0-2", 2.93),
        ("football_total_goals", "over", 2.5, "3+", 1.35),
    }


def test_parse_football_double_chance_detail_match_emits_double_chance(football_detail_data):
    results = _parse_football_double_chance_detail_match(football_detail_data)

    assert len(results) == 3
    assert all(r.market_type == "football_double_chance" for r in results)
    assert {r.sport for r in results} == {"football"}
    assert {r.bookmaker_id for r in results} == {"betole"}
    assert {
        (r.outcome_code, r.raw_label, r.odds)
        for r in results
    } == {
        ("home_or_draw", "1X", 3.2),
        ("home_or_away", "12", 1.1),
        ("draw_or_away", "X2", 1.04),
    }


def test_parse_football_outcome_match_skips_invalid_rows():
    match = {
        "home": "Home",
        "away": "Away",
        "leagueName": "Test League",
        "kickOffTime": 1778002200000,
        "odds": {
            "1": 0,
            "2": -1,
            "3": "bad",
            "22": 2.05,
        },
    }

    results = _parse_football_outcome_match(match)

    assert len(results) == 1
    assert results[0].market_type == "football_total_goals"
    assert results[0].outcome_code == "under"


def test_parse_football_outcome_match_requires_teams_and_odds_map():
    assert _parse_football_outcome_match({"away": "Away", "odds": {"1": 1.9}}) == []
    assert _parse_football_outcome_match({"home": "Home", "odds": {"1": 1.9}}) == []
    assert _parse_football_outcome_match({"home": "Home", "away": "Away", "odds": []}) == []


def test_parse_football_double_chance_detail_match_requires_teams_and_odds():
    assert _parse_football_double_chance_detail_match(
        {"away": "Away", "odds": {"7": 1.5}}
    ) == []
    assert _parse_football_double_chance_detail_match(
        {"home": "Home", "odds": {"7": 1.5}}
    ) == []
    assert _parse_football_double_chance_detail_match(
        {"home": "Home", "away": "Away", "odds": []}
    ) == []
    # Non-double-chance codes are ignored even when present.
    assert _parse_football_double_chance_detail_match(
        {"home": "Home", "away": "Away", "odds": {"1": 1.9, "22": 2.0}}
    ) == []


def test_extract_league_id_default_kwarg_uses_football():
    assert _extract_league_id("", default="football") == "football"
    assert _extract_league_id(None, default="football") == "football"


@pytest.mark.asyncio
async def test_scraper_supports_football_outcomes():
    scraper = BetOleScraper()
    assert scraper.get_supported_outcome_sports() == ["football"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_uses_list_and_per_match_details(
    football_list_data,
    football_detail_data,
):
    list_calls: list[tuple[str, dict]] = []
    detail_calls: list[str] = []

    async def fake_get_json(url: str, *, params=None, headers=None):
        del headers
        if url == _FOOTBALL_LIST_URL:
            list_calls.append((url, params or {}))
            return football_list_data
        expected_detail = _FOOTBALL_DETAIL_URL_TEMPLATE.format(match_id=90328755)
        if url == expected_detail:
            detail_calls.append(url)
            return football_detail_data
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    assert len(results) == 8
    market_types = {r.market_type for r in results}
    assert market_types == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }
    assert {r.bookmaker_id for r in results} == {"betole"}
    assert {r.sport for r in results} == {"football"}
    # All 8 offers reference the LIST match metadata, even the detail-derived ones.
    assert {r.home_team for r in results} == {"Al Khaleej"}
    assert {r.away_team for r in results} == {"Al Hilal Riyadh"}
    assert {r.league_id for r in results} == {"saudi_arabia_saudi_professional_league"}
    assert {r.start_time for r in results} == {"2026-05-05T18:00:00+00:00"}
    assert len(list_calls) == 1
    assert list_calls[0][1].get("hours")
    assert detail_calls == [_FOOTBALL_DETAIL_URL_TEMPLATE.format(match_id=90328755)]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_returns_list_offers_when_detail_fails(
    football_list_data,
):
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _FOOTBALL_LIST_URL:
            return football_list_data
        raise RuntimeError("detail unavailable")

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 1.0
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    assert len(results) == 5
    assert {r.market_type for r in results} == {
        "football_result",
        "football_total_goals",
    }


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_overrides_detail_metadata_with_list(
    football_list_data,
):
    drifting_detail = {
        "id": 90328755,
        # Detail's home/away differ in spacing/case from the list payload —
        # if the parser used these, double-chance offers would land in a
        # different normalized event than the list-derived offers.
        "home": " AL  KHALEEJ ",
        "away": "al hilal  RIYADH",
        "leagueName": "Saudi Arabia, Saudi Professional League (Reserves)",
        "kickOffTime": 1778004000999,
        "sport": "S",
        "odds": {"7": 3.2, "8": 1.1, "9": 1.04},
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _FOOTBALL_LIST_URL:
            return football_list_data
        return drifting_detail

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    dc = [r for r in results if r.market_type == "football_double_chance"]
    assert len(dc) == 3
    # Detail-derived offers must reuse the LIST match metadata so the
    # event normalizer keys them onto the same event as the list-derived
    # result/totals offers.
    assert {r.home_team for r in dc} == {"Al Khaleej"}
    assert {r.away_team for r in dc} == {"Al Hilal Riyadh"}
    assert {r.league_id for r in dc} == {"saudi_arabia_saudi_professional_league"}
    assert {r.start_time for r in dc} == {"2026-05-05T18:00:00+00:00"}


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_overrides_detail_metadata_when_list_field_missing(
    football_list_data,
):
    # Stomp the list match's leagueName so we exercise the None-fallback
    # branch.  The list parser will derive league_id="football" (default),
    # and the detail parser must do the same — not silently fall back to
    # the detail's leagueName, which would land double-chance in a
    # different normalized event.
    list_with_missing_field = json.loads(json.dumps(football_list_data))
    list_with_missing_field["esMatches"][0]["leagueName"] = None

    leaking_detail = {
        "id": 90328755,
        "home": "Al Khaleej",
        "away": "Al Hilal Riyadh",
        # Detail still knows the league — must NOT be used.
        "leagueName": "Saudi Arabia, Saudi Professional League",
        "kickOffTime": 1778004000000,
        "sport": "S",
        "odds": {"7": 3.2, "8": 1.1, "9": 1.04},
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _FOOTBALL_LIST_URL:
            return list_with_missing_field
        return leaking_detail

    http_client = AsyncMock()
    http_client.rate_limit_per_second = 4.0
    http_client.get_json.side_effect = fake_get_json

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    list_results = [
        r for r in results if r.market_type in {"football_result", "football_total_goals"}
    ]
    dc = [r for r in results if r.market_type == "football_double_chance"]
    assert dc, "expected detail-derived double chance offers"
    # Both lanes must agree on league_id even when the list is missing
    # the field — they should both fall back to the default rather than
    # the detail's value leaking through.
    assert {r.league_id for r in dc} == {"football"}
    assert {r.league_id for r in list_results} == {"football"}


@pytest.mark.asyncio
async def test_scrape_outcome_offers_non_football_returns_empty():
    http_client = AsyncMock()
    http_client.rate_limit_per_second = 1.0

    scraper = BetOleScraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("basketball")

    assert results == []
    http_client.get_json.assert_not_called()
