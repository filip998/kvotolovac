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
    _parse_handicap_ot_event,
    _get_player_event_ids,
    _normalize_start_time,
    _resolve_matchup_from_short_name,
    _parse_football_outcome_event,
    _parse_football_double_chance_detail,
    _parse_total_line,
    _resolve_total_line,
    _dedupe_football_events,
    _football_detail_identity,
    _football_event_completeness_score,
    _BASE_DETAIL_URL,
    _FOOTBALL_PAGE_ID,
    _FOOTBALL_SPORT_ID,
)
from app.models.schemas import RawOddsData, RawOutcomeOffer

EVENTS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_events.json"
BETS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_bets.json"
TOTALS_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_game_totals.json"
FOOTBALL_FIXTURE = Path(__file__).parent / "fixtures" / "pinnbet_football.json"
FOOTBALL_DETAIL_FIXTURE = (
    Path(__file__).parent / "fixtures" / "pinnbet_football_detail.json"
)


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


# -- _parse_handicap_ot_event ------------------------------------------------


def test_parse_handicap_ot_event_fixture(totals_data):
    """Fixture has one handicap row at sBV=7.5 with outcomes 1=1.9, 2=1.92."""
    results = _parse_handicap_ot_event(totals_data[0])
    assert len(results) == 1
    row = results[0]
    assert row.market_type == "home_handicap_ot"
    assert row.threshold == -7.5  # team1 +7.5 → home margin expected -7.5
    assert row.over_odds == 1.9
    assert row.under_odds == 1.92
    assert row.home_team == "Lyon Villeurbanne"
    assert row.away_team == "Fenerbahce"
    assert row.league_id == "euroleague"
    assert row.player_name is None


def test_parse_handicap_ot_event_signed_sbv_negative_means_home_favoured():
    """Real live shape: ``Piratas de Bogota - Caribbean Storm Islands`` returned
    sBV=-22.5 with "1"=1.9 and "2"=1.85 — team1=home favoured by 22.5, so
    threshold=+22.5 (positive = home favoured under our convention)."""
    event = {
        "name": "Piratas de Bogota - Caribbean Storm Islands",
        "dateTime": "2026-04-16T20:00:00",
        "competitionName": "Liga Profesional",
        "bets": [
            {
                "betTypeId": 166,
                "betTypeName": "Hendikep (+OT)",
                "sBV": "-22.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.85, "isPlayable": True},
                ],
            }
        ],
    }
    results = _parse_handicap_ot_event(event)
    assert len(results) == 1
    row = results[0]
    assert row.threshold == 22.5
    assert row.over_odds == 1.9
    assert row.under_odds == 1.85


def test_parse_handicap_ot_event_multi_line_ladder():
    """A handicap ladder produces one row per line, sign-preserved."""
    event = {
        "name": "Home - Away",
        "dateTime": "2026-04-16T20:00:00",
        "competitionName": "Test",
        "bets": [
            {
                "betTypeId": 166,
                "betTypeName": "Hendikep (+OT)",
                "sBV": sbv,
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.9, "isPlayable": True},
                ],
            }
            for sbv in ("-3.5", "-1.5", "0", "2.5")
        ],
    }
    results = _parse_handicap_ot_event(event)
    assert sorted(r.threshold for r in results) == [-2.5, 0.0, 1.5, 3.5]


def test_parse_handicap_ot_event_skips_unplayable_or_unparseable():
    event = {
        "name": "Home - Away",
        "dateTime": "2026-04-16T20:00:00",
        "competitionName": "Test",
        "bets": [
            # unplayable
            {
                "betTypeId": 166,
                "isPlayable": False,
                "sBV": "3.5",
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.9, "isPlayable": True},
                ],
            },
            # missing sBV
            {
                "betTypeId": 166,
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": True},
                    {"name": "2", "odd": 1.9, "isPlayable": True},
                ],
            },
            # both outcomes unplayable
            {
                "betTypeId": 166,
                "sBV": "5.5",
                "isPlayable": True,
                "betOutcomes": [
                    {"name": "1", "odd": 1.9, "isPlayable": False},
                    {"name": "2", "odd": 1.9, "isPlayable": False},
                ],
            },
        ],
    }
    assert _parse_handicap_ot_event(event) == []


def test_parse_handicap_ot_event_does_not_pick_up_totals(totals_data):
    """Regression: handicap parser must ignore betTypeId 167 (totals)."""
    results = _parse_handicap_ot_event(totals_data[0])
    # Fixture has 11 totals rows but only 1 handicap row
    assert all(r.market_type == "home_handicap_ot" for r in results)
    assert len(results) == 1


def test_parse_game_total_ot_event_does_not_pick_up_handicap_after_change(totals_data):
    """Regression: totals parser must still ignore betTypeId 166 (handicap)."""
    results = _parse_game_total_ot_event(totals_data[0])
    assert all(r.market_type == "game_total_ot" for r in results)
    # Same 11 thresholds as before — handicap parser is independent
    assert len(results) == 11


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

    assert len(results) == 14
    assert all(isinstance(r, RawOddsData) for r in results)
    assert all(r.bookmaker_id == "pinnbet" for r in results)
    players = {r.player_name for r in results if r.player_name}
    assert "Alfonso Plummer" in players
    assert "Marko Simonovic" in players
    game_totals = [r for r in results if r.market_type == "game_total_ot"]
    assert len(game_totals) == 11
    assert {r.home_team for r in game_totals} == {"Lyon Villeurbanne"}
    assert {r.away_team for r in game_totals} == {"Fenerbahce"}
    handicaps = [r for r in results if r.market_type == "home_handicap_ot"]
    assert len(handicaps) == 1
    assert handicaps[0].threshold == -7.5
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

    assert len(results) == 14
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

    # 11 game totals + 1 handicap = 12 (no player rows because stubs lack details)
    assert len(results) == 12
    assert {r.market_type for r in results} == {"game_total_ot", "home_handicap_ot"}
    game_totals = [r for r in results if r.market_type == "game_total_ot"]
    handicaps = [r for r in results if r.market_type == "home_handicap_ot"]
    assert len(game_totals) == 11
    assert len(handicaps) == 1
    assert len(captured_urls) == 2


# ── Football outcome lane ─────────────────────────────────


@pytest.fixture
def football_data() -> list[dict]:
    with open(FOOTBALL_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def football_detail_data() -> dict:
    with open(FOOTBALL_DETAIL_FIXTURE) as f:
        return json.load(f)


def test_parse_football_outcome_event_emits_result_and_2_5_totals_only(football_data):
    event = football_data[0]
    offers = _parse_football_outcome_event(event)

    by_market: dict[str, list[RawOutcomeOffer]] = {}
    for o in offers:
        by_market.setdefault(o.market_type, []).append(o)

    # Partial mode never sees double chance — list parser must not emit it.
    assert "football_double_chance" not in by_market
    assert sorted(by_market) == ["football_result", "football_total_goals"]
    assert {o.outcome_code for o in by_market["football_result"]} == {
        "home",
        "draw",
        "away",
    }
    totals = by_market["football_total_goals"]
    assert {t.outcome_code for t in totals} == {"over", "under"}
    assert all(t.line == 2.5 for t in totals)
    assert {t.raw_label for t in totals} == {"0-2", "3+"}

    sample = by_market["football_result"][0]
    assert sample.bookmaker_id == "pinnbet"
    assert sample.sport == "football"
    assert sample.home_team == "Bayern Munich"
    assert sample.away_team == "Paris SG"
    # PinnBet naive datetimes treated as UTC and emitted with explicit offset
    assert sample.start_time == "2026-04-15T19:00:00+00:00"


def test_parse_football_outcome_event_skips_non_2_5_lines(football_data):
    event = football_data[0]
    offers = _parse_football_outcome_event(event)
    totals = [o for o in offers if o.market_type == "football_total_goals"]
    assert len(totals) == 2
    assert all(t.line == 2.5 for t in totals)


def test_parse_football_outcome_event_skips_non_playable_bets(football_data):
    event = football_data[0]
    for bet in event["bets"]:
        bet["isPlayable"] = False
    assert _parse_football_outcome_event(event) == []


def test_parse_football_outcome_event_skips_non_playable_outcomes(football_data):
    event = football_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 1:
            for outcome in bet["betOutcomes"]:
                outcome["isPlayable"] = False
            break
    offers = _parse_football_outcome_event(event)
    assert [o for o in offers if o.market_type == "football_result"] == []
    # Totals at 2.5 still come through.
    assert any(o.market_type == "football_total_goals" for o in offers)


def test_parse_football_outcome_event_skips_zero_or_negative_odds(football_data):
    event = football_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 1:
            for outcome in bet["betOutcomes"]:
                outcome["odd"] = 0
            break
    offers = _parse_football_outcome_event(event)
    assert [o for o in offers if o.market_type == "football_result"] == []


def test_parse_football_outcome_event_normalizes_outcome_names(football_data):
    event = football_data[0]
    # Stress: stray whitespace + lowercase on result outcomes; stripped+upper
    # in the parser must still classify them.
    for bet in event["bets"]:
        if bet.get("betTypeId") == 1:
            bet["betOutcomes"][0]["name"] = " 1 "
            bet["betOutcomes"][1]["name"] = "x"
            break
    offers = _parse_football_outcome_event(event)
    result_codes = {
        o.outcome_code for o in offers if o.market_type == "football_result"
    }
    assert result_codes == {"home", "draw", "away"}


def test_parse_football_outcome_event_classifies_totals_diacritic_insensitive(
    football_data,
):
    event = football_data[0]
    # Stress: "Više"/"Manje" with stray whitespace must still classify, AND
    # the unaccented "vise" form (some bookmaker payloads strip diacritics)
    # must also normalize via normalize_identity_text.
    for bet in event["bets"]:
        if bet.get("betTypeId") == 2 and bet.get("sBV") == "2.5":
            for outcome in bet["betOutcomes"]:
                if outcome["name"] == "više":
                    outcome["name"] = "  vise  "
                elif outcome["name"] == "manje":
                    outcome["name"] = "Manje"
            break
    offers = _parse_football_outcome_event(event)
    totals = [o for o in offers if o.market_type == "football_total_goals"]
    assert {t.outcome_code for t in totals} == {"over", "under"}
    assert {t.raw_label for t in totals} == {"3+", "0-2"}


def test_parse_football_outcome_event_drops_offer_when_event_name_unparseable(
    football_data,
):
    event = football_data[0]
    event["name"] = "NoSeparator"
    assert _parse_football_outcome_event(event) == []


def test_parse_football_outcome_event_skips_unknown_outcome_codes(football_data):
    event = football_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 1:
            for outcome in bet["betOutcomes"]:
                outcome["name"] = "WAT"
            break
    offers = _parse_football_outcome_event(event)
    assert [o for o in offers if o.market_type == "football_result"] == []


def test_parse_football_outcome_event_falls_back_to_default_league(football_data):
    event = football_data[0]
    event["competitionName"] = None
    event["competitionId"] = None
    offers = _parse_football_outcome_event(event)
    assert offers
    assert all(o.league_id == "football" for o in offers)


def test_parse_football_outcome_event_falls_back_for_degenerate_competition(
    football_data,
):
    event = football_data[0]
    event["competitionName"] = "---"
    event["competitionId"] = None
    offers = _parse_football_outcome_event(event)
    assert offers
    assert all(o.league_id == "football" for o in offers)


def test_parse_total_line_tolerates_string_variants():
    assert _parse_total_line("2.5") == 2.5
    assert _parse_total_line("2.50") == 2.5
    assert _parse_total_line(" 2.5 ") == 2.5
    assert _parse_total_line(2.5) == 2.5
    assert _parse_total_line(0.5) == 0.5
    assert _parse_total_line(None) is None
    assert _parse_total_line("") is None
    assert _parse_total_line("not-a-number") is None


def test_resolve_total_line_prefers_bet_level_and_falls_back_to_outcome():
    assert _resolve_total_line({"sBV": "2.5"}, {"sBV": None}) == 2.5
    assert _resolve_total_line({"sBV": None}, {"sBV": "2.5"}) == 2.5
    assert _resolve_total_line({"sBV": "2.5"}, {"sBV": "2.5"}) == 2.5
    # Disagreement → drop, defense in depth.
    assert _resolve_total_line({"sBV": "2.5"}, {"sBV": "3.5"}) is None
    assert _resolve_total_line({"sBV": None}, {"sBV": None}) is None


def test_parse_football_outcome_event_uses_outcome_level_sbv_when_bet_level_missing(
    football_data,
):
    event = football_data[0]
    for bet in event["bets"]:
        if bet.get("betTypeId") == 2 and bet.get("sBV") == "2.5":
            bet["sBV"] = None  # drop bet-level line, outcome-level "2.5" remains
            break
    offers = _parse_football_outcome_event(event)
    totals = [o for o in offers if o.market_type == "football_total_goals"]
    assert {t.outcome_code for t in totals} == {"over", "under"}
    assert all(t.line == 2.5 for t in totals)


def test_parse_football_double_chance_detail_emits_three_offers(
    football_data, football_detail_data
):
    list_event = football_data[0]
    offers = _parse_football_double_chance_detail(list_event, football_detail_data)
    assert len(offers) == 3
    assert {o.outcome_code for o in offers} == {
        "home_or_draw",
        "home_or_away",
        "draw_or_away",
    }
    assert {o.raw_label for o in offers} == {"1X", "12", "X2"}
    assert all(o.market_type == "football_double_chance" for o in offers)
    # Identity must come from the LIST event, never the detail payload.
    assert all(o.home_team == "Bayern Munich" for o in offers)
    assert all(o.away_team == "Paris SG" for o in offers)
    assert all(o.start_time == "2026-04-15T19:00:00+00:00" for o in offers)
    assert all(o.league_id == "uefa champions league" for o in offers)
    assert all(o.line is None for o in offers)


def test_parse_football_double_chance_detail_ignores_hostile_detail_metadata(
    football_data, football_detail_data
):
    """B1 invariant — even when the detail payload supplies its own
    home/away/start/league/source metadata that disagrees with the list,
    the emitted offers MUST anchor on the list-event identity so list-
    derived and detail-derived offers normalize to the same canonical event.
    """
    list_event = football_data[0]
    hostile_detail = {
        **football_detail_data,
        # Fields that a future regression might be tempted to read instead
        # of the list event.  All of these must be ignored.
        "name": "Wrong Home - Wrong Away",
        "homeTeam": "Wrong Home",
        "awayTeam": "Wrong Away",
        "competitionName": "Wrong League",
        "competitionId": 99999,
        "dateTime": "2099-12-31T23:59:59",
        "sourceUrl": "https://attacker.invalid/",
    }
    offers = _parse_football_double_chance_detail(list_event, hostile_detail)
    assert len(offers) == 3
    assert all(o.home_team == "Bayern Munich" for o in offers)
    assert all(o.away_team == "Paris SG" for o in offers)
    assert all(o.start_time == "2026-04-15T19:00:00+00:00" for o in offers)
    assert all(o.league_id == "uefa champions league" for o in offers)


def test_parse_football_double_chance_detail_falls_back_when_list_league_is_none(
    football_data, football_detail_data
):
    """B1 invariant edge case — when a list field that contributes to
    identity is absent (here ``competitionName``), the parser must fall
    back to the football scope's default league_id, NOT pull it from the
    detail payload.  Pins the "even when the list field is None" branch.
    """
    list_event = {**football_data[0], "competitionName": None, "competitionId": None}
    hostile_detail = {
        **football_detail_data,
        "competitionName": "Detail-Provided League",
        "competitionId": 42,
    }
    offers = _parse_football_double_chance_detail(list_event, hostile_detail)
    assert offers
    assert all(o.league_id == "football" for o in offers)
    assert all(o.home_team == "Bayern Munich" for o in offers)
    assert all(o.away_team == "Paris SG" for o in offers)


def test_parse_football_double_chance_detail_drops_when_event_name_unparseable(
    football_data, football_detail_data
):
    list_event = {**football_data[0], "name": "NoSeparator"}
    assert _parse_football_double_chance_detail(list_event, football_detail_data) == []


def test_parse_football_double_chance_detail_handles_missing_bets_array(football_data):
    list_event = football_data[0]
    assert _parse_football_double_chance_detail(list_event, {}) == []
    assert _parse_football_double_chance_detail(list_event, {"bets": None}) == []
    assert _parse_football_double_chance_detail(list_event, {"bets": "garbage"}) == []


def test_dedupe_football_events_picks_best_row_for_duplicate_event_id():
    less_complete = {
        "id": 999,
        "name": "Foo - Bar",
        "dateTime": "2026-04-15T19:00:00",
        "competitionId": None,
        "regionId": None,
        "sportId": 1,
    }
    more_complete = {
        "id": 999,
        "name": "Foo - Bar",
        "dateTime": "2026-04-15T19:00:00",
        "competitionId": 12,
        "competitionName": "Friendly",
        "regionId": 7,
        "sportId": 1,
        "bets": [{"betTypeId": 1, "isPlayable": True, "betOutcomes": []}],
    }
    by_id = _dedupe_football_events([less_complete, more_complete])
    assert list(by_id.keys()) == [999]
    assert by_id[999] is more_complete

    # Order shouldn't matter — best row still wins.
    by_id = _dedupe_football_events([more_complete, less_complete])
    assert by_id[999] is more_complete


def test_dedupe_football_events_skips_events_without_id():
    by_id = _dedupe_football_events(
        [
            {"name": "no-id", "dateTime": "2026-04-15T19:00:00"},
            {"id": "not-int", "name": "x"},
            {"id": 1, "name": "Foo - Bar", "dateTime": "2026-04-15T19:00:00"},
        ]
    )
    assert list(by_id.keys()) == [1]


def test_football_detail_identity_returns_none_for_missing_fields():
    assert (
        _football_detail_identity(
            {"sportId": 1, "regionId": 2, "competitionId": 3, "id": 4}
        )
        == (1, 2, 3, 4)
    )
    assert _football_detail_identity({"sportId": 1, "regionId": 2, "competitionId": 3}) is None
    assert _football_detail_identity(
        {"sportId": 1, "regionId": 2, "competitionId": None, "id": 4}
    ) is None
    assert _football_detail_identity(
        {"sportId": "x", "regionId": 2, "competitionId": 3, "id": 4}
    ) is None


def test_football_event_completeness_score_orders_rows():
    rich = {
        "id": 1,
        "sportId": 1,
        "regionId": 2,
        "competitionId": 3,
        "competitionName": "League",
        "bets": [{}],
    }
    no_bets = {**rich, "bets": []}
    no_competition = {**rich, "competitionName": None}
    no_ids = {"id": 1, "sportId": 1, "regionId": None, "competitionId": None}
    assert _football_event_completeness_score(rich) > _football_event_completeness_score(no_bets)
    assert _football_event_completeness_score(rich) > _football_event_completeness_score(no_competition)
    assert _football_event_completeness_score(rich) > _football_event_completeness_score(no_ids)


def test_get_supported_outcome_sports_isolates_football_from_basketball_capability():
    scraper = PinnBetScraper()
    # threshold-odds lane: basketball only — football MUST NOT leak here,
    # otherwise the unified pipeline would call scrape_odds("football") every cycle.
    assert scraper.get_supported_leagues() == ["basketball"]
    # outcome-offer lane: football
    assert scraper.get_supported_outcome_sports() == ["football"]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_returns_empty_for_non_football_without_http():
    scraper = PinnBetScraper(detail_mode="full")
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        results = await scraper.scrape_outcome_offers("basketball")
    assert results == []
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_outcome_offers_partial_mode_uses_only_list_endpoint(
    football_data, football_detail_data
):
    scraper = PinnBetScraper(detail_mode="partial")
    captured: list[str] = []

    async def mock_get(url, **kwargs):
        captured.append(url)
        if "getWebEventsSelections" in url:
            return football_data
        raise AssertionError(
            f"Unexpected detail call in partial mode: {url}"
        )

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("football")

    assert len(captured) == 1
    parsed = urlparse(captured[0])
    assert parsed.path.endswith("/api/offer/getWebEventsSelections")
    qs = parse_qs(parsed.query)
    assert qs["pageId"] == [str(_FOOTBALL_PAGE_ID)]
    assert qs["sportId"] == [str(_FOOTBALL_SPORT_ID)]
    assert qs["isLive"] == ["false"]
    assert qs["eventMappingTypes"] == ["1", "2", "3", "4", "5"]

    # Partial mode emits result + 2.5 totals only — no double chance.
    market_types = {o.market_type for o in results}
    assert market_types == {"football_result", "football_total_goals"}


@pytest.mark.asyncio
async def test_scrape_outcome_offers_full_mode_fetches_detail_and_emits_double_chance(
    football_data, football_detail_data
):
    scraper = PinnBetScraper(detail_mode="full")
    captured_detail_urls: list[str] = []

    async def mock_get(url, **kwargs):
        if "getWebEventsSelections" in url:
            return football_data
        if "/betsAndGroups/" in url:
            captured_detail_urls.append(url)
            return football_detail_data
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("football")

    # Full mode issues exactly one detail fetch per deduped event with full IDs.
    assert len(captured_detail_urls) == 1
    expected = (
        f"{_BASE_DETAIL_URL}/{_FOOTBALL_SPORT_ID}/287/12345/2215282"
    )
    assert captured_detail_urls[0] == expected

    market_types = {o.market_type for o in results}
    assert market_types == {
        "football_result",
        "football_total_goals",
        "football_double_chance",
    }
    dc = [o for o in results if o.market_type == "football_double_chance"]
    assert {o.outcome_code for o in dc} == {
        "home_or_draw",
        "home_or_away",
        "draw_or_away",
    }


@pytest.mark.asyncio
async def test_scrape_outcome_offers_full_mode_skips_detail_when_ids_missing(
    football_data,
):
    scraper = PinnBetScraper(detail_mode="full")
    # Drop competition/region IDs so detail URL cannot be built.
    event = {**football_data[0], "regionId": None, "competitionId": None}

    async def mock_get(url, **kwargs):
        if "getWebEventsSelections" in url:
            return [event]
        raise AssertionError(f"Unexpected detail call: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("football")

    # List-derived offers still emitted; double chance absent.
    market_types = {o.market_type for o in results}
    assert "football_double_chance" not in market_types
    assert "football_result" in market_types
    assert "football_total_goals" in market_types


@pytest.mark.asyncio
async def test_scrape_outcome_offers_full_mode_tolerates_detail_failure(
    football_data,
):
    scraper = PinnBetScraper(detail_mode="full")

    async def mock_get(url, **kwargs):
        if "getWebEventsSelections" in url:
            return football_data
        if "/betsAndGroups/" in url:
            raise Exception("boom")
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("football")

    # Detail failure must not poison list-derived offers.
    market_types = {o.market_type for o in results}
    assert "football_double_chance" not in market_types
    assert "football_result" in market_types
    assert "football_total_goals" in market_types


@pytest.mark.asyncio
async def test_scrape_outcome_offers_dedupes_events_before_emitting(
    football_data,
):
    scraper = PinnBetScraper(detail_mode="partial")
    # Same eventId duplicated — must not double-emit list-derived offers.
    duplicated = [football_data[0], {**football_data[0]}]

    async def mock_get(url, **kwargs):
        if "getWebEventsSelections" in url:
            return duplicated
        raise AssertionError(f"Unexpected URL: {url}")

    with patch.object(scraper._http, "get_json", side_effect=mock_get):
        results = await scraper.scrape_outcome_offers("football")

    # Single emission per (market, outcome_code).
    seen = set()
    for o in results:
        key = (o.market_type, o.outcome_code)
        assert key not in seen, f"Duplicate offer emitted: {key}"
        seen.add(key)


@pytest.mark.asyncio
async def test_scrape_outcome_offers_handles_list_failure():
    scraper = PinnBetScraper(detail_mode="full")
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("boom")
        results = await scraper.scrape_outcome_offers("football")
    assert results == []


@pytest.mark.asyncio
async def test_scrape_outcome_offers_handles_non_list_response():
    scraper = PinnBetScraper(detail_mode="full")
    with patch.object(scraper._http, "get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"error": "bad request"}
        results = await scraper.scrape_outcome_offers("football")
    assert results == []


def test_set_runtime_detail_mode_overrides_init_default():
    scraper = PinnBetScraper(detail_mode="partial")
    assert scraper._detail_mode == "partial"
    scraper.set_runtime_detail_mode("full")
    assert scraper._detail_mode == "full"
    scraper.set_runtime_detail_mode("partial")
    assert scraper._detail_mode == "partial"


def test_scheduler_applies_pinnbet_detail_mode_from_runtime_settings():
    """Pin the scheduler branch that wires ``runtime_settings.pinnbet_detail_mode``
    onto the registered PinnBet scraper.  Without this branch in
    ``_apply_runtime_scraper_settings``, runtime overrides would silently
    no-op on the scraper instance.
    """
    from app.models.schemas import ScrapeRuntimeSettings
    from app.services.scheduler import Scheduler

    scraper = PinnBetScraper(detail_mode="partial")
    assert scraper._detail_mode == "partial"

    runtime_settings = ScrapeRuntimeSettings(
        enabled_bookmakers=["pinnbet"],
        enabled_sports=["basketball", "football"],
        scrape_market_scope="all",
        analysis_markets=["all"],
        scrape_lookahead_hours=24,
        scrape_interval_minutes=10,
        max_middle_opportunities_per_market=10,
        rate_limit_per_second=1.0,
        meridian_rate_limit_per_second=2.0,
        soccerbet_detail_mode="partial",
        merkurxtip_detail_mode="partial",
        pinnbet_detail_mode="full",
        notification_gap_threshold=1.5,
        persist_inapp_notifications=False,
    )

    Scheduler(interval_minutes=1)._apply_runtime_scraper_settings(scraper, runtime_settings)
    assert scraper._detail_mode == "full"

    runtime_settings_partial = runtime_settings.model_copy(
        update={"pinnbet_detail_mode": "partial"}
    )
    Scheduler(interval_minutes=1)._apply_runtime_scraper_settings(
        scraper, runtime_settings_partial
    )
    assert scraper._detail_mode == "partial"
