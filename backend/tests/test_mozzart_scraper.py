from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.mozzart_scraper import (
    MozzartScraper,
    _BASKETBALL_SPORT_ID,
    _FOOTBALL_SPORT_ID,
    _MATCHES_API_URL,
    _SPECIALS_API_URL,
    _MATCHES_HEADERS,
    _DEFAULT_HEADERS,
    _build_matches_request_body,
    _extract_league_id,
    _extract_player_and_market,
    _parse_football_outcome_match,
    _parse_game_total_items,
    _parse_handicap_items,
    _parse_items,
    _parse_signed_threshold,
    _parse_start_time,
)
from app.models.schemas import RawOddsData, RawOutcomeOffer

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


# ── Handicap parsing ──────────────────────────────────────


def test_parse_signed_threshold_handles_negatives_and_positives():
    assert _parse_signed_threshold("-4.5") == -4.5
    assert _parse_signed_threshold("3.5") == 3.5
    assert _parse_signed_threshold("0") == 0.0
    assert _parse_signed_threshold("garbage") is None
    assert _parse_signed_threshold(None) is None


def test_parse_handicap_items_signed_line_canonical_home_perspective():
    """Mozzart's specialOddValue is signed: negative = home favoured.

    Real live data: ``Houston vs La Lakers`` returned ``sov='-4.5'`` with
    ``"1"=1.9, "2"=1.9`` meaning Houston (home, listed first) is favoured by
    4.5. Storage canonicalises to ``threshold=+4.5`` (home expected to win
    by 4.5), with ``over``=home covers and ``under``=away covers.
    """
    items = [{
        "home": {"name": "Houston"},
        "visitor": {"name": "La Lakers"},
        "competition": {"name": "USA NBA"},
        "startTime": 1775775600000,
        "oddsGroup": [
            {
                "groupName": "Hendikep",
                "odds": [
                    {
                        "specialOddValue": "-4.5",
                        "value": 1.92,
                        "oddStatus": "ACTIVE",
                        "game": {"name": "Hendikep"},
                        "subgame": {"name": "1"},
                    },
                    {
                        "specialOddValue": "-4.5",
                        "value": 1.88,
                        "oddStatus": "ACTIVE",
                        "game": {"name": "Hendikep"},
                        "subgame": {"name": "2"},
                    },
                    # Different line on the same match (handicap ladder)
                    {
                        "specialOddValue": "+1.5",
                        "value": 2.20,
                        "oddStatus": "ACTIVE",
                        "game": {"name": "Hendikep"},
                        "subgame": {"name": "1"},
                    },
                    {
                        "specialOddValue": "+1.5",
                        "value": 1.65,
                        "oddStatus": "ACTIVE",
                        "game": {"name": "Hendikep"},
                        "subgame": {"name": "2"},
                    },
                ],
            }
        ],
    }]

    results = _parse_handicap_items(items)
    assert {r.market_type for r in results} == {"home_handicap_ot"}
    assert {r.home_team for r in results} == {"Houston"}
    assert {r.away_team for r in results} == {"La Lakers"}
    assert {r.league_id for r in results} == {"nba"}

    by_threshold = {r.threshold: r for r in results}
    # sov=-4.5 → threshold=+4.5 (home favoured by 4.5)
    assert by_threshold[4.5].over_odds == 1.92
    assert by_threshold[4.5].under_odds == 1.88
    # sov=+1.5 → threshold=-1.5 (home underdog by 1.5)
    assert by_threshold[-1.5].over_odds == 2.20
    assert by_threshold[-1.5].under_odds == 1.65


def test_parse_handicap_items_skips_inactive_or_missing():
    items = [{
        "home": {"name": "A"},
        "visitor": {"name": "B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [
            {
                "groupName": "Hendikep",
                "odds": [
                    # Inactive odd
                    {
                        "specialOddValue": "-3.5",
                        "value": 1.9,
                        "oddStatus": "INACTIVE",
                        "subgame": {"name": "1"},
                    },
                    # Unparseable line
                    {
                        "specialOddValue": "garbage",
                        "value": 1.9,
                        "oddStatus": "ACTIVE",
                        "subgame": {"name": "1"},
                    },
                    # Missing value
                    {
                        "specialOddValue": "-2.5",
                        "value": None,
                        "oddStatus": "ACTIVE",
                        "subgame": {"name": "1"},
                    },
                    # Unknown subgame name (neither 1 nor 2)
                    {
                        "specialOddValue": "-1.5",
                        "value": 1.9,
                        "oddStatus": "ACTIVE",
                        "subgame": {"name": "X"},
                    },
                ],
            }
        ],
    }]
    assert _parse_handicap_items(items) == []


def test_parse_handicap_items_does_not_pick_up_other_groups():
    items = [{
        "home": {"name": "A"},
        "visitor": {"name": "B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [
            {
                "groupName": "Ukupno poena na meču",
                "odds": [
                    {
                        "specialOddValue": "160.5",
                        "value": 1.85,
                        "oddStatus": "ACTIVE",
                        "subgame": {"name": "više"},
                    }
                ],
            }
        ],
    }]
    assert _parse_handicap_items(items) == []


def test_parse_handicap_items_preserves_pickem_zero_line():
    """Pick'em (line 0) must not be lost by truthy fallbacks. Mozzart can
    legitimately emit ``specialOddValue: 0`` for a zero-handicap line; the
    parser must store it as ``threshold=0`` rather than skipping the row.
    """
    items = [{
        "home": {"name": "A"},
        "visitor": {"name": "B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [
            {
                "groupName": "Hendikep",
                "odds": [
                    {
                        "specialOddValue": 0,
                        "value": 1.92,
                        "oddStatus": "ACTIVE",
                        "subgame": {"name": "1"},
                    },
                    {
                        "specialOddValue": 0,
                        "value": 1.88,
                        "oddStatus": "ACTIVE",
                        "subgame": {"name": "2"},
                    },
                ],
            }
        ],
    }]
    results = _parse_handicap_items(items)
    assert len(results) == 1
    assert results[0].threshold == 0.0
    assert results[0].over_odds == 1.92
    assert results[0].under_odds == 1.88


def test_parse_game_total_items_does_not_pick_up_handicap_group():
    """Regression: now that Hendikep parsing exists, the totals parser must
    still ignore Hendikep groups entirely."""
    items = [{
        "home": {"name": "A"},
        "visitor": {"name": "B"},
        "competition": {"name": "Test"},
        "startTime": 1775775600000,
        "oddsGroup": [
            {
                "groupName": "Hendikep",
                "odds": [
                    {
                        "specialOddValue": "-4.5",
                        "value": 1.9,
                        "oddStatus": "ACTIVE",
                        "subgame": {"name": "1"},
                    }
                ],
            }
        ],
    }]
    assert _parse_game_total_items(items) == []


def test_matches_headers_use_prematch_web():
    """Mozzart's broad listing only returns Hendikep / Pobednik / Dupla šansa
    when called with ``medium: PREMATCH_WEB``; the rest of the scraper still
    uses ``PREMATCH_MOBILE`` for specials."""
    assert _MATCHES_HEADERS["medium"] == "PREMATCH_WEB"
    assert _DEFAULT_HEADERS["medium"] == "PREMATCH_MOBILE"


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


# ── Football outcome offers ────────────────────────────────


def _football_odd(label: str, value: float, *, status: str = "ACTIVE") -> dict:
    return {
        "oddStatus": status,
        "subgame": {"name": label},
        "value": value,
    }


def _football_match(*, odds_groups: list[dict] | None = None) -> dict:
    return {
        "id": 687289,
        "sport": {"id": 1, "name": "Fudbal"},
        "competition": {"id": 12211, "name": "SVETSKO  PRVENSTVO"},
        "startTime": 1781204400000,
        "home": {"id": 12890, "name": "Mexico"},
        "visitor": {"id": 12883, "name": "South Africa"},
        "oddsGroup": odds_groups
        if odds_groups is not None
        else [
            {
                "groupName": "Konačan ishod",
                "odds": [
                    _football_odd("1", 1.52),
                    _football_odd("X", 4.30),
                    _football_odd("2", 6.25),
                ],
            },
            {
                "groupName": "Dupla šansa",
                "odds": [
                    _football_odd("1X", 1.12),
                    _football_odd("12", 1.22),
                    _football_odd("X2", 2.55),
                ],
            },
            {
                "groupName": "Ukupno golova na meču",
                "odds": [
                    _football_odd("2+", 1.28),
                    _football_odd("0-2", 1.80),
                    _football_odd("3+", 1.90),
                ],
            },
            {
                "groupName": "Oba tima daju gol",
                "odds": [_football_odd("GG", 2.00), _football_odd("NG", 1.72)],
            },
        ],
    }


def test_build_matches_request_body_defaults_to_basketball_and_can_target_football():
    default_body = _build_matches_request_body()
    football_body = _build_matches_request_body(current_page=2, sport_id=_FOOTBALL_SPORT_ID)

    assert default_body["sportId"] == _BASKETBALL_SPORT_ID
    assert football_body["sportId"] == _FOOTBALL_SPORT_ID
    assert football_body["currentPage"] == 2
    assert football_body["matchTypeId"] == 0


def test_parse_football_outcome_match_emits_list_only_target_markets():
    offers = _parse_football_outcome_match(_football_match())

    assert len(offers) == 8
    assert all(isinstance(offer, RawOutcomeOffer) for offer in offers)
    assert all(offer.bookmaker_id == "mozzart" for offer in offers)
    assert all(offer.sport == "football" for offer in offers)
    assert all(offer.home_team == "Mexico" for offer in offers)
    assert all(offer.away_team == "South Africa" for offer in offers)
    assert all(offer.league_id == "svetsko_prvenstvo" for offer in offers)
    assert all(offer.start_time == "2026-06-11T19:00:00+00:00" for offer in offers)

    assert {
        (offer.market_type, offer.outcome_code, offer.line, offer.raw_label, offer.odds)
        for offer in offers
    } == {
        ("football_result", "home", None, "1", 1.52),
        ("football_result", "draw", None, "X", 4.30),
        ("football_result", "away", None, "2", 6.25),
        ("football_double_chance", "home_or_draw", None, "1X", 1.12),
        ("football_double_chance", "home_or_away", None, "12", 1.22),
        ("football_double_chance", "draw_or_away", None, "X2", 2.55),
        ("football_total_goals", "under", 2.5, "0-2", 1.80),
        ("football_total_goals", "over", 2.5, "3+", 1.90),
    }


def test_parse_football_outcome_match_normalizes_diacritics_and_labels():
    match = _football_match(
        odds_groups=[
            {
                "groupName": "Konacan ishod",
                "odds": [_football_odd(" x ", 3.2)],
            },
            {
                "groupName": "Dupla sansa",
                "odds": [_football_odd(" x2 ", 1.5)],
            },
            {
                "groupName": "Ukupno golova na mecu",
                "odds": [_football_odd("0 - 2", 1.75), _football_odd("3 +", 2.05)],
            },
        ],
    )

    assert {
        (offer.market_type, offer.outcome_code, offer.raw_label, offer.line)
        for offer in _parse_football_outcome_match(match)
    } == {
        ("football_result", "draw", "x", None),
        ("football_double_chance", "draw_or_away", "x2", None),
        ("football_total_goals", "under", "0-2", 2.5),
        ("football_total_goals", "over", "3+", 2.5),
    }


def test_parse_football_outcome_match_skips_non_target_or_invalid_odds():
    match = _football_match(
        odds_groups=[
            {
                "groupName": "Konačan ishod",
                "odds": [
                    _football_odd("1", 0),
                    _football_odd("X", 3.2, status="SUSPENDED"),
                    _football_odd("2", 2.4),
                ],
            },
            {
                "groupName": "Ukupno golova na meču",
                "odds": [
                    _football_odd("2+", 1.4),
                    _football_odd("3+", -1),
                    _football_odd("0-2", 1.7),
                ],
            },
        ],
    )

    assert {
        (offer.market_type, offer.outcome_code, offer.odds)
        for offer in _parse_football_outcome_match(match)
    } == {
        ("football_result", "away", 2.4),
        ("football_total_goals", "under", 1.7),
    }


def test_parse_football_outcome_match_requires_home_and_away():
    match = _football_match()
    match["home"] = None
    assert _parse_football_outcome_match(match) == []


def test_parse_football_outcome_match_falls_back_to_football_league():
    match = _football_match()
    match["competition"] = None

    offers = _parse_football_outcome_match(match)

    assert offers
    assert {offer.league_id for offer in offers} == {"football"}


def test_capability_isolation_for_mozzart():
    scraper = MozzartScraper()

    assert scraper.get_supported_leagues() == ["basketball"]
    assert scraper.get_supported_outcome_sports() == ["football"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_uses_only_paginated_matches_endpoint():
    page_one = [_football_match()] * 50
    page_two = [_football_match()]

    async def fake_post_json(url: str, *, json_body=None, headers=None):
        del headers
        assert url == _MATCHES_API_URL
        assert json_body is not None
        assert json_body["sportId"] == _FOOTBALL_SPORT_ID
        assert json_body["matchTypeId"] == 0
        if json_body["currentPage"] == 0:
            return {"items": page_one}
        if json_body["currentPage"] == 1:
            return {"items": page_two}
        raise AssertionError(f"Unexpected page: {json_body['currentPage']}")

    http_client = AsyncMock()
    http_client.post_json.side_effect = fake_post_json

    offers = await MozzartScraper(http_client=http_client).scrape_outcome_offers("football")

    assert len(offers) == 51 * 8
    assert http_client.post_json.call_count == 2
    assert {
        call.kwargs["json_body"]["currentPage"]
        for call in http_client.post_json.call_args_list
    } == {0, 1}


@pytest.mark.asyncio
async def test_scrape_outcome_offers_unsupported_sport_does_not_fetch():
    http_client = AsyncMock()

    offers = await MozzartScraper(http_client=http_client).scrape_outcome_offers("tennis")

    assert offers == []
    http_client.post_json.assert_not_called()
