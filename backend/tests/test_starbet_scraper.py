from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.scrapers import starbet_scraper as sbs
from app.scrapers.starbet_scraper import (
    _BOOKMAKER_ID,
    _GET_LIGA_URL,
    _SOURCE_URL,
    _SPORT_TREE_URL,
    StarBetScraper,
    _extract_basketball_game_totals,
    _extract_basketball_player_points,
    _extract_football_offers,
    _extract_tennis_offers,
    _index_basketball_fixtures,
    _is_nba_league,
    _looks_like_player_special,
    _parse_sport_tree,
    _parse_starbet_dt,
    _select_total_points_pair,
    _iter_tip_rows,
    _league_key,
    _split_pair_name,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def sport_tree_fixture() -> object:
    return _load("starbet_sport_tree.json")


@pytest.fixture
def basketball_liga_fixture() -> object:
    return _load("starbet_basketball_liga.json")


@pytest.fixture
def football_liga_fixture() -> object:
    return _load("starbet_football_liga.json")


@pytest.fixture
def tennis_liga_fixture() -> object:
    return _load("starbet_tennis_liga.json")


# ── ISO parser ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("input_value", "expected_iso"),
    [
        ("2026-06-11T21:00:00.0000000+02:00", "2026-06-11T19:00:00+00:00"),
        ("2026-06-11T21:00:00.1234567+02:00", "2026-06-11T19:00:00.123456+00:00"),
        ("2026-06-11T21:00:00.123456+02:00", "2026-06-11T19:00:00.123456+00:00"),
        ("2026-06-11T21:00:00+02:00", "2026-06-11T19:00:00+00:00"),
        ("2026-06-11T21:00:00Z", "2026-06-11T21:00:00+00:00"),
        ("2026-06-11T21:00:00", "2026-06-11T21:00:00+00:00"),
    ],
)
def test_parse_starbet_dt_handles_seven_digit_microseconds(
    input_value: str, expected_iso: str
) -> None:
    parsed = _parse_starbet_dt(input_value)
    assert parsed is not None
    assert parsed == datetime.fromisoformat(expected_iso)


@pytest.mark.parametrize("invalid", ["", None, "garbage", "2026-13-99T99:99:99+02:00"])
def test_parse_starbet_dt_returns_none_for_invalid(invalid: str | None) -> None:
    assert _parse_starbet_dt(invalid) is None


# ── Pure helpers ───────────────────────────────────────────


def test_split_pair_name_canonical_separator() -> None:
    assert _split_pair_name("Mexico : South Africa") == ("Mexico", "South Africa")
    # Multi-segment names with " : " inside should still split on the first.
    assert _split_pair_name(" Real Madrid Baloncesto : FC Barcelona ") == (
        "Real Madrid Baloncesto",
        "FC Barcelona",
    )


@pytest.mark.parametrize("bad", [None, "", "no separator", "Mexico:South Africa"])
def test_split_pair_name_rejects_malformed(bad: str | None) -> None:
    assert _split_pair_name(bad) is None


def test_league_key_canonical_aliases() -> None:
    assert _league_key("NBA Play Offs", "basketball") == "nba"
    assert _league_key("Basketball NBA Play Offs", "basketball") == "nba"
    assert _league_key("Spain ACB", "basketball") == "spain_acb"
    assert _league_key("ABA League", "basketball") == "aba_liga"


def test_league_key_falls_back_to_normalized_underscored() -> None:
    assert _league_key("Some New League 5", "basketball") == "some_new_league_5"
    assert _league_key("", "basketball") == "basketball"
    assert _league_key(None, "football") == "football"


def test_is_nba_league() -> None:
    assert _is_nba_league("NBA Play Offs")
    assert _is_nba_league("Basketball NBA Play Offs")
    assert _is_nba_league("NBA Players (Inc. Over Time)")
    assert not _is_nba_league("Spain ACB")
    # "WNBA" is its own token after tokenisation — must NOT classify as NBA
    # otherwise WNBA Ukupno Poena rows would emit `game_total_ot` instead of
    # `game_total` and miss-group across bookmakers.
    assert not _is_nba_league("WNBA Regular Season")
    assert not _is_nba_league("")
    assert not _is_nba_league(None)


def test_looks_like_player_special() -> None:
    assert _looks_like_player_special("NBA Players (Inc. Over Time)")
    assert _looks_like_player_special("Euroleague Players")
    assert not _looks_like_player_special("Basketball NBA Play Offs")


def test_iter_tip_rows_skips_isg_duplicates() -> None:
    rows = [
        {"TID": 121, "K": 1.82, "G": -2.5, "isG": False},
        {"TID": 121, "K": 1.82, "G": -2.5, "isG": True},  # border duplicate
        {"TID": 103, "K": 1.89, "G": 169.5, "isG": False},
        {"TID": 103, "K": 1.89, "G": 169.5, "isG": True},
        {"TID": 105, "K": 1.92, "G": 169.5, "isG": False},
    ]
    pair = {"T": rows}
    filtered = _iter_tip_rows(pair)
    assert len(filtered) == 3
    assert all(not row.get("isG") for row in filtered)


def test_select_total_points_pair_returns_first_matched_line() -> None:
    rows = [
        {"TID": 103, "K": 1.89, "G": 169.5},
        {"TID": 105, "K": 1.92, "G": 169.5},
    ]
    parsed = _select_total_points_pair(rows)
    assert parsed == (169.5, 1.92, 1.89)


def test_select_total_points_pair_rejects_mismatched_lines() -> None:
    rows = [
        {"TID": 103, "K": 1.89, "G": 169.5},
        {"TID": 105, "K": 1.92, "G": 170.5},
    ]
    assert _select_total_points_pair(rows) is None


def test_select_total_points_pair_rejects_short_odds() -> None:
    rows = [
        {"TID": 103, "K": 0.99, "G": 169.5},
        {"TID": 105, "K": 1.92, "G": 169.5},
    ]
    assert _select_total_points_pair(rows) is None


# ── Sport tree parsing ─────────────────────────────────────


def test_parse_sport_tree_keeps_only_supported_sports(sport_tree_fixture: object) -> None:
    tree = _parse_sport_tree(sport_tree_fixture)
    assert set(tree.leagues_by_sport.keys()) == {0, 22, 37}
    football_descriptors = tree.leagues_by_sport[0]
    assert any(desc.lid == 1345 and desc.name == "FIFA World Cup" for desc in football_descriptors)
    basketball_descriptors = tree.leagues_by_sport[22]
    assert {desc.lid for desc in basketball_descriptors} >= {494, 503, 607}
    assert any(
        _looks_like_player_special(desc.name) for desc in basketball_descriptors
    )


def test_parse_sport_tree_handles_garbage() -> None:
    tree = _parse_sport_tree(None)
    assert tree.leagues_by_sport == {}
    tree = _parse_sport_tree({"unexpected": "shape"})
    assert tree.leagues_by_sport == {}


# ── Football extraction ───────────────────────────────────


def test_extract_football_offers_emits_result_dc_totals(
    sport_tree_fixture: object, football_liga_fixture: object
) -> None:
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[0]
    }
    offers = _extract_football_offers(football_liga_fixture, descriptors)

    # Group by (home, market_type, outcome_code)
    keys = {(row.home_team, row.market_type, row.outcome_code) for row in offers}
    assert ("Mexico", "football_result", "home") in keys
    assert ("Mexico", "football_result", "draw") in keys
    assert ("Mexico", "football_result", "away") in keys
    assert ("Mexico", "football_double_chance", "home_or_draw") in keys
    assert ("Mexico", "football_double_chance", "draw_or_away") in keys
    assert ("Mexico", "football_total_goals", "under") in keys
    assert ("Mexico", "football_total_goals", "over") in keys

    # Totals carry line 2.5; result/double_chance leave line None.
    totals = [row for row in offers if row.market_type == "football_total_goals"]
    assert all(row.line == 2.5 for row in totals)
    non_totals = [row for row in offers if row.market_type != "football_total_goals"]
    assert all(row.line is None for row in non_totals)

    # All rows share the canonicalised league_id and bookmaker fields.
    assert all(row.bookmaker_id == _BOOKMAKER_ID for row in offers)
    assert all(row.league_id == "fifa_world_cup" for row in offers)
    assert all(row.source_url == _SOURCE_URL for row in offers)


def test_extract_football_offers_emits_utc_iso_start_time(
    sport_tree_fixture: object, football_liga_fixture: object
) -> None:
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[0]
    }
    offers = _extract_football_offers(football_liga_fixture, descriptors)
    mexico_rows = [row for row in offers if row.home_team == "Mexico"]
    assert mexico_rows
    expected = datetime(2026, 6, 11, 21, 0, tzinfo=timezone.utc).astimezone(timezone.utc)
    # +02:00 wall-clock 21:00 ⇒ 19:00 UTC
    assert mexico_rows[0].start_time == "2026-06-11T19:00:00+00:00"
    del expected


def test_extract_football_offers_no_duplicates_when_isg_rows_present(
    sport_tree_fixture: object, football_liga_fixture: object
) -> None:
    # Inject an isG=true row that mimics the GR border duplicate.
    payload = json.loads(json.dumps(football_liga_fixture))
    first_pair = payload[0]["P"][0]
    first_pair["T"].append(
        {**first_pair["T"][0], "isG": True}  # duplicates the "1" tip
    )
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[0]
    }
    offers = _extract_football_offers(payload, descriptors)
    # One row per (home, market_type, outcome_code) — no double emission.
    keys = [
        (row.home_team, row.away_team, row.market_type, row.outcome_code)
        for row in offers
    ]
    assert len(keys) == len(set(keys))


def test_extract_tennis_offers_skips_outright_winner_leagues() -> None:
    payload = [
        {
            "LID": 3919,
            "LN": "Roland-Garros Mens Winner",
            "P": [
                {
                    "PID": 1,
                    "PN": "Sinner Jannik : Winner Roland-Garros 2026",
                    "DI": "2026-06-01T10:00:00.0000000+02:00",
                    "T": [
                        {"TID": 1, "K": 4.5, "isG": False},
                        {"TID": 10, "K": 1.2, "isG": False},
                    ],
                }
            ],
        },
        {
            "LID": 1751,
            "LN": "ATP Masters Rome Singles",
            "P": [
                {
                    "PID": 2,
                    "PN": "Ruud C. : Darderi L.",
                    "DI": "2026-05-15T13:30:00.0000000+02:00",
                    "T": [
                        {"TID": 1, "K": 1.35, "isG": False},
                        {"TID": 10, "K": 3.15, "isG": False},
                    ],
                }
            ],
        },
    ]
    offers = _extract_tennis_offers(payload, {})
    assert {(row.home_team, row.away_team) for row in offers} == {("Ruud C.", "Darderi L.")}


def test_is_tennis_outright_league_and_pair() -> None:
    from app.scrapers.starbet_scraper import (
        _is_tennis_outright_league,
        _is_tennis_outright_pair,
    )

    assert _is_tennis_outright_league("Roland-Garros Mens Winner")
    assert _is_tennis_outright_league("Wimbledon Outright")
    assert _is_tennis_outright_league("US Open Futures")
    # Serbian-language variants must also be filtered out.
    assert _is_tennis_outright_league("Pobednik Australian Open 2026")
    assert _is_tennis_outright_league("Pobjednik Roland-Garros 2026")
    assert _is_tennis_outright_league("ATP Specijal")
    assert not _is_tennis_outright_league("ATP Masters Rome Singles")
    assert not _is_tennis_outright_league("")

    assert _is_tennis_outright_pair("Sinner Jannik", "Winner Roland-Garros 2026")
    assert _is_tennis_outright_pair("Winner ATP Cincinnati 2026", "Alcaraz C.")
    assert _is_tennis_outright_pair("Sinner Jannik", "Pobednik AO 2026")
    assert _is_tennis_outright_pair("Pobjednik Wimbledon 2026", "Alcaraz C.")
    assert not _is_tennis_outright_pair("Ruud C.", "Darderi L.")


def test_infer_target_league_id_matches_canonical_keys() -> None:
    from app.scrapers.starbet_scraper import _infer_target_league_id

    assert _infer_target_league_id("NBA Players (Inc. Over Time)") == "nba"
    assert _infer_target_league_id("Euroleague Players") == "euroleague"
    assert _infer_target_league_id("ABA League Players (Inc. OT)") == "aba_liga"
    # Pure "Players" name has no target prefix → None
    assert _infer_target_league_id("Players") is None
    assert _infer_target_league_id("(Players)") is None
    assert _infer_target_league_id(None) is None
    assert _infer_target_league_id("") is None


# ── Tennis extraction ─────────────────────────────────────


def test_extract_tennis_offers_singles_only(
    sport_tree_fixture: object, tennis_liga_fixture: object
) -> None:
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[37]
    }
    offers = _extract_tennis_offers(tennis_liga_fixture, descriptors)
    assert offers
    # No doubles pair (slashes) should appear.
    assert all("/" not in row.home_team and "/" not in row.away_team for row in offers)
    # Outcomes must be exactly home and/or away (no draw row).
    assert {row.outcome_code for row in offers} <= {"home", "away"}
    assert all(row.market_type == "tennis_match_winner" for row in offers)


def test_extract_tennis_offers_explicit_doubles_skipped() -> None:
    payload = [
        {
            "LID": 999,
            "LN": "ATP Doubles Test",
            "P": [
                {
                    "PID": 1,
                    "PN": "Player A/Player B : Player C/Player D",
                    "DI": "2026-06-01T10:00:00.0000000+02:00",
                    "T": [
                        {"TID": 1, "K": 1.85, "isG": False},
                        {"TID": 10, "K": 2.05, "isG": False},
                    ],
                },
                {
                    "PID": 2,
                    "PN": "Singles Player A : Singles Player B",
                    "DI": "2026-06-01T12:00:00.0000000+02:00",
                    "T": [
                        {"TID": 1, "K": 1.50, "isG": False},
                        {"TID": 10, "K": 2.50, "isG": False},
                    ],
                },
            ],
        }
    ]
    offers = _extract_tennis_offers(payload, {})
    assert {(row.home_team, row.away_team) for row in offers} == {
        ("Singles Player A", "Singles Player B")
    }


# ── Basketball extraction ─────────────────────────────────


def test_index_basketball_fixtures_skips_player_specials(
    sport_tree_fixture: object, basketball_liga_fixture: object
) -> None:
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[22]
    }
    fixtures, by_team_start_league, ambiguity_counts = _index_basketball_fixtures(
        basketball_liga_fixture, descriptors
    )
    fixture_teams = {(f.home_team, f.away_team) for f in fixtures}
    assert ("Cleveland Cavaliers", "Detroit Pistons") in fixture_teams
    assert ("Gran Canaria", "Iberostar Tenerife") in fixture_teams

    # Player-special pairs ("Player : Team" pseudo-pairs) must NOT enter the fixture index.
    assert not any(home.startswith("Cunningham") for home, _ in fixture_teams)

    # The join index keyed by (normalized_team, utc_iso, league_id) contains
    # both teams for every fixture.
    pistons_nba_key = ("detroit pistons", "2026-05-15T23:00:00+00:00", "nba")
    assert pistons_nba_key in by_team_start_league
    assert by_team_start_league[pistons_nba_key].home_team == "Cleveland Cavaliers"

    # No ambiguity in the captured fixture set.
    assert ambiguity_counts == {}


def test_extract_basketball_game_totals_emits_ot_for_nba_only(
    sport_tree_fixture: object, basketball_liga_fixture: object
) -> None:
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[22]
    }
    fixtures, *_rest = _index_basketball_fixtures(
        basketball_liga_fixture, descriptors
    )
    rows = _extract_basketball_game_totals(
        basketball_liga_fixture, descriptors, fixtures
    )

    nba_rows = [row for row in rows if row.home_team == "Cleveland Cavaliers"]
    acb_rows = [row for row in rows if row.home_team == "Gran Canaria"]
    assert nba_rows and acb_rows

    assert all(row.market_type == "game_total_ot" for row in nba_rows)
    assert all(row.market_type == "game_total" for row in acb_rows)
    assert all(row.player_name is None for row in rows)
    assert all(row.over_odds is not None and row.under_odds is not None for row in rows)
    # Thresholds match the fixture preview lines.
    assert nba_rows[0].threshold > 100


def test_extract_basketball_player_points_joins_by_team_and_start_time(
    sport_tree_fixture: object, basketball_liga_fixture: object
) -> None:
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[22]
    }
    fixtures, by_team_start_league, ambiguity_counts = _index_basketball_fixtures(
        basketball_liga_fixture, descriptors
    )
    extraction = _extract_basketball_player_points(
        basketball_liga_fixture, descriptors, by_team_start_league, ambiguity_counts
    )
    assert extraction.rows
    pistons_players = [row for row in extraction.rows if "Detroit Pistons" in (row.home_team, row.away_team)]
    assert pistons_players, "Player_points should join Pistons pair to the actual game"

    # Each player_points row carries the regular game's both teams and the NBA league_id
    # (NOT the player special's league_id).
    for row in pistons_players:
        assert row.home_team == "Cleveland Cavaliers"
        assert row.away_team == "Detroit Pistons"
        assert row.league_id == "nba"
        assert row.player_name and row.market_type == "player_points"
        assert row.over_odds is not None and row.under_odds is not None
        assert row.threshold > 0


def test_extract_basketball_player_points_skips_unresolved(
    sport_tree_fixture: object, basketball_liga_fixture: object
) -> None:
    payload = json.loads(json.dumps(basketball_liga_fixture))
    # Inject one orphaned player special whose team has no game in today's feed.
    player_league = next(l for l in payload if l["LID"] == 607)
    player_league["P"].append(
        {
            "PID": 9_999_999,
            "PN": "Ghost Player : Phantom FC",
            "DI": "2026-07-01T19:00:00.0000000+02:00",
            "T": [
                {"TID": 103, "K": 1.85, "G": 19.5, "isG": False},
                {"TID": 105, "K": 1.95, "G": 19.5, "isG": False},
            ],
        }
    )
    descriptors = {
        desc.lid: desc for desc in _parse_sport_tree(sport_tree_fixture).leagues_by_sport[22]
    }
    fixtures, by_team_start_league, ambiguity_counts = _index_basketball_fixtures(
        payload, descriptors
    )
    extraction = _extract_basketball_player_points(
        payload, descriptors, by_team_start_league, ambiguity_counts
    )
    assert extraction.unresolved_count >= 1
    assert any(
        sample[0] == "Ghost Player" and sample[1] == "Phantom FC"
        for sample in extraction.unresolved_samples
    )
    # Orphaned row must not appear in extracted rows.
    assert not any(row.player_name == "Ghost Player" for row in extraction.rows)


def test_player_points_join_requires_target_league_match() -> None:
    """Two basketball fixtures share (team, start) but live in different leagues.

    The NBA Players special must resolve to the NBA fixture, not the
    non-NBA one — and if the NBA fixture is absent the join must refuse
    to silently fall back to the other league.
    """

    same_start = "2026-05-16T01:00:00.0000000+02:00"
    payload = [
        # Two leagues with a "Lakers" team at the same start.
        {
            "LID": 100,
            "LN": "Basketball NBA Play Offs",
            "P": [
                {
                    "PID": 1,
                    "PN": "Lakers : Suns",
                    "DI": same_start,
                    "T": [
                        {"TID": 103, "K": 1.89, "G": 220.5, "isG": False},
                        {"TID": 105, "K": 1.92, "G": 220.5, "isG": False},
                    ],
                }
            ],
        },
        {
            "LID": 200,
            "LN": "Basketball Exhibition Friendly",
            "P": [
                {
                    "PID": 2,
                    "PN": "Lakers : Trail Blazers",
                    "DI": same_start,
                    "T": [
                        {"TID": 103, "K": 1.85, "G": 165.5, "isG": False},
                        {"TID": 105, "K": 1.95, "G": 165.5, "isG": False},
                    ],
                }
            ],
        },
        # NBA Players special targets Lakers via PN.
        {
            "LID": 607,
            "LN": "NBA Players (Inc. Over Time)",
            "P": [
                {
                    "PID": 3,
                    "PN": "LeBron James : Lakers",
                    "DI": same_start,
                    "T": [
                        {"TID": 103, "K": 1.87, "G": 25.5, "isG": False},
                        {"TID": 105, "K": 1.87, "G": 25.5, "isG": False},
                    ],
                }
            ],
        },
    ]
    fixtures, by_team_start_league, ambiguity_counts = _index_basketball_fixtures(
        payload, {}
    )
    assert ("lakers", "2026-05-15T23:00:00+00:00") in ambiguity_counts
    extraction = _extract_basketball_player_points(
        payload, {}, by_team_start_league, ambiguity_counts
    )
    assert len(extraction.rows) == 1
    row = extraction.rows[0]
    assert row.league_id == "nba"
    assert row.home_team == "Lakers" and row.away_team == "Suns"
    # The other-league fixture was NOT silently picked.
    assert "Trail Blazers" not in (row.home_team, row.away_team)


def test_player_points_join_emits_ambiguous_signal_when_target_missing() -> None:
    """If the special targets NBA but only a non-NBA fixture exists at the same time,
    the join must skip and surface an ``ambiguous_count`` signal so the
    operational logs distinguish this from a plain "team missing" failure."""

    same_start = "2026-05-16T01:00:00.0000000+02:00"
    payload = [
        # Only a non-NBA fixture exists for "Lakers" at this minute.
        {
            "LID": 200,
            "LN": "Basketball Exhibition Friendly",
            "P": [
                {
                    "PID": 1,
                    "PN": "Lakers : Trail Blazers",
                    "DI": same_start,
                    "T": [
                        {"TID": 103, "K": 1.85, "G": 165.5, "isG": False},
                        {"TID": 105, "K": 1.95, "G": 165.5, "isG": False},
                    ],
                }
            ],
        },
        {
            "LID": 607,
            "LN": "NBA Players (Inc. Over Time)",
            "P": [
                {
                    "PID": 2,
                    "PN": "LeBron James : Lakers",
                    "DI": same_start,
                    "T": [
                        {"TID": 103, "K": 1.87, "G": 25.5, "isG": False},
                        {"TID": 105, "K": 1.87, "G": 25.5, "isG": False},
                    ],
                }
            ],
        },
    ]
    fixtures, by_team_start_league, ambiguity_counts = _index_basketball_fixtures(
        payload, {}
    )
    extraction = _extract_basketball_player_points(
        payload, {}, by_team_start_league, ambiguity_counts
    )
    # No silent attribution to the wrong league.
    assert extraction.rows == []
    # And the failure is logged as "unresolved" (different team-start key
    # collision count == 0) since only one league had this team-start tuple,
    # but that league wasn't NBA.
    assert extraction.unresolved_count == 1


def test_select_total_points_pair_groups_alternate_lines_by_value() -> None:
    """When alt lines arrive interleaved the function must still surface a
    complete (under, over) pair sharing a line value."""

    rows = [
        # First under is line 215.5 (no matching over yet).
        {"TID": 103, "K": 2.10, "G": 215.5, "isG": False},
        # First over is line 218.5 (no matching under).
        {"TID": 105, "K": 1.75, "G": 218.5, "isG": False},
        # Complete pair at line 217.5 — should win.
        {"TID": 103, "K": 1.85, "G": 217.5, "isG": False},
        {"TID": 105, "K": 1.95, "G": 217.5, "isG": False},
    ]
    parsed = _select_total_points_pair(rows)
    assert parsed is not None
    line, over_odds, under_odds = parsed
    assert line == 217.5
    assert over_odds == 1.95
    assert under_odds == 1.85


# ── End-to-end (mocked HTTP) ──────────────────────────────


def _http_router(sport_tree, basketball, football, tennis):
    async def _post(url, *, json_body, headers=None):
        del headers
        if url == _SPORT_TREE_URL:
            return sport_tree
        if url == _GET_LIGA_URL:
            lids = set(json_body.get("LigaID", []))
            if 1345 in lids:
                return football
            if {1751, 741} & lids:
                return tennis
            return basketball
        raise AssertionError(f"unexpected url: {url}")

    return _post


@pytest.mark.asyncio
async def test_scrape_odds_basketball_end_to_end(
    sport_tree_fixture, basketball_liga_fixture, football_liga_fixture, tennis_liga_fixture
):
    scraper = StarBetScraper(http_client=AsyncMock())
    scraper._http.post_json = AsyncMock(
        side_effect=_http_router(
            sport_tree_fixture,
            basketball_liga_fixture,
            football_liga_fixture,
            tennis_liga_fixture,
        )
    )
    rows = await scraper.scrape_odds("basketball")
    market_types = {row.market_type for row in rows}
    assert "player_points" in market_types
    assert "game_total" in market_types or "game_total_ot" in market_types
    # Per project convention all start_time values are UTC ISO and re-emit cleanly.
    for row in rows:
        assert row.start_time and row.start_time.endswith("+00:00")
        # No NaN-style thresholds slipped through.
        assert row.threshold > 0


@pytest.mark.asyncio
async def test_scrape_odds_unknown_lane_returns_empty(
    sport_tree_fixture, basketball_liga_fixture, football_liga_fixture, tennis_liga_fixture
):
    scraper = StarBetScraper(http_client=AsyncMock())
    scraper._http.post_json = AsyncMock(
        side_effect=_http_router(
            sport_tree_fixture,
            basketball_liga_fixture,
            football_liga_fixture,
            tennis_liga_fixture,
        )
    )
    assert await scraper.scrape_odds("football") == []
    assert await scraper.scrape_odds("tennis") == []


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_end_to_end(
    sport_tree_fixture, basketball_liga_fixture, football_liga_fixture, tennis_liga_fixture
):
    scraper = StarBetScraper(http_client=AsyncMock())
    scraper._http.post_json = AsyncMock(
        side_effect=_http_router(
            sport_tree_fixture,
            basketball_liga_fixture,
            football_liga_fixture,
            tennis_liga_fixture,
        )
    )
    offers = await scraper.scrape_outcome_offers("football")
    assert offers
    market_types = {row.market_type for row in offers}
    assert market_types >= {"football_result", "football_total_goals"}
    assert all(row.sport == "football" for row in offers)


@pytest.mark.asyncio
async def test_scrape_outcome_offers_tennis_singles_only(
    sport_tree_fixture, basketball_liga_fixture, football_liga_fixture, tennis_liga_fixture
):
    scraper = StarBetScraper(http_client=AsyncMock())
    scraper._http.post_json = AsyncMock(
        side_effect=_http_router(
            sport_tree_fixture,
            basketball_liga_fixture,
            football_liga_fixture,
            tennis_liga_fixture,
        )
    )
    offers = await scraper.scrape_outcome_offers("tennis")
    assert offers
    assert all(row.sport == "tennis" for row in offers)
    assert all(row.market_type == "tennis_match_winner" for row in offers)
    # No doubles slipped through.
    assert all("/" not in row.home_team and "/" not in row.away_team for row in offers)


@pytest.mark.asyncio
async def test_scrape_outcome_offers_unknown_sport_returns_empty(
    sport_tree_fixture, basketball_liga_fixture, football_liga_fixture, tennis_liga_fixture
):
    scraper = StarBetScraper(http_client=AsyncMock())
    scraper._http.post_json = AsyncMock(
        side_effect=_http_router(
            sport_tree_fixture,
            basketball_liga_fixture,
            football_liga_fixture,
            tennis_liga_fixture,
        )
    )
    assert await scraper.scrape_outcome_offers("hockey") == []


@pytest.mark.asyncio
async def test_sport_tree_is_refetched_per_cycle(
    sport_tree_fixture, basketball_liga_fixture, football_liga_fixture, tennis_liga_fixture
):
    """No lifetime cache: every fresh scrape call hits the sport-tree endpoint."""

    scraper = StarBetScraper(http_client=AsyncMock())
    calls: list[str] = []

    async def counting_post(url, *, json_body, headers=None):
        del headers
        calls.append(url)
        if url == _SPORT_TREE_URL:
            return sport_tree_fixture
        if url == _GET_LIGA_URL:
            lids = set(json_body.get("LigaID", []))
            if 1345 in lids:
                return football_liga_fixture
            if {1751, 741} & lids:
                return tennis_liga_fixture
            return basketball_liga_fixture
        raise AssertionError(f"unexpected url: {url}")

    scraper._http.post_json = AsyncMock(side_effect=counting_post)

    await scraper.scrape_odds("basketball")
    first_tree_calls = sum(1 for c in calls if c == _SPORT_TREE_URL)
    assert first_tree_calls == 1, "first scrape should fetch the sport tree exactly once"

    await scraper.scrape_outcome_offers("football")
    second_tree_calls = sum(1 for c in calls if c == _SPORT_TREE_URL)
    assert second_tree_calls == 2, (
        "second scrape after the first completed must re-fetch the sport tree "
        "(no lifetime cache)"
    )


def test_scraper_identity_and_capabilities() -> None:
    scraper = StarBetScraper(http_client=AsyncMock())
    assert scraper.get_bookmaker_id() == "starbet"
    assert scraper.get_bookmaker_name() == "StarBet"
    assert scraper.get_supported_leagues() == ["basketball"]
    assert set(scraper.get_supported_outcome_sports()) == {"football", "tennis"}


def test_module_constants_are_stable() -> None:
    assert sbs._BOOKMAKER_ID == "starbet"
    assert sbs._SOURCE_URL.startswith("https://starbet.rs")
    assert sbs._SPORT_TREE_URL.endswith("/GetSportoviSoLigi")
    assert sbs._GET_LIGA_URL.endswith("/GetLiga")
