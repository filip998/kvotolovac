from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.scrapers.soccerbet_scraper import (
    SoccerBetScraper,
    _ALL_GAMES_URL,
    _ALL_PLAYERS_URL,
    _DETAIL_URL,
    _FOOTBALL_GAMES_URL,
    _GROUPS_URL,
    _GROUP_LEAGUES_URL,
    _LEAGUE_PREVIEW_URL,
    _PLAYER_PREVIEW_URL,
    _TENNIS_GAMES_URL,
    _build_matchup_index,
    _extract_league_id,
    _parse_handicap_spec,
    _parse_football_outcome_match,
    _parse_player_match,
    _parse_regular_match,
    _parse_tennis_outcome_match,
    _tennis_skip_reason,
)


def _entry(tt: int, odd: float, specifier: str = "NULL", status: str = "U") -> dict:
    return {
        "tt": tt,
        "ov": odd,
        "sv": specifier,
        "bc": tt,
        "bpc": tt,
        "s": status,
    }


def _group(tt: int, *entries: tuple[str, float]) -> dict[str, dict]:
    return {specifier: _entry(tt, odd, specifier) for specifier, odd in entries}


def _group_with_status(tt: int, *entries: tuple[str, float, str]) -> dict[str, dict]:
    return {
        specifier: _entry(tt, odd, specifier, status)
        for specifier, odd, status in entries
    }


KICKOFF_MS = int((datetime.now(tz=timezone.utc) + timedelta(hours=2)).timestamp() * 1000)
FOOTBALL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "soccerbet_football_offer.json"

REGULAR_PREVIEW_MATCH = {
    "id": 514392889,
    "matchCode": 79148,
    "home": "Atlanta Hawks",
    "away": "New York Knicks",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "NBA Play off",
    "betMap": {
        "50445": _group(50445, ("total=211.5", 1.92)),
        "50444": _group(50444, ("total=211.5", 1.88)),
        "50979": _group(50979, ("total=108.5", 1.84)),
        "50980": _group(50980, ("total=108.5", 1.96)),
    },
}

REGULAR_DETAIL_MATCH = {
    **REGULAR_PREVIEW_MATCH,
    "betMap": {
        **REGULAR_PREVIEW_MATCH["betMap"],
        "227": _group(227, ("total=208.5", 1.83)),
        "228": _group(228, ("total=208.5", 1.97)),
        "224": _group(224, ("hcp=-3.5", 1.90)),
        "226": _group(226, ("hcp=-3.5", 1.90)),
    },
}

PLAYER_PREVIEW_MATCH = {
    "id": 514398866,
    "matchCode": 81538,
    "superCode": 79148,
    "home": "Jalen Brunson",
    "away": "New York Knicks",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "NBA Play off Igrači",
    "betMap": {
        "51679": _group(51679, ("total=28.5", 1.91)),
        "51681": _group(51681, ("total=28.5", 1.87)),
        "51682": _group(51682, ("total=6.5", 1.74)),
        "51684": _group(51684, ("total=6.5", 2.02)),
        "51685": _group(51685, ("total=3.5", 1.68)),
        "51687": _group(51687, ("total=3.5", 2.12)),
        "51688": _group(51688, ("total=2.5", 1.95)),
        "51690": _group(51690, ("total=2.5", 1.81)),
        "55244": _group(55244, ("total=31.5", 1.87)),
        "55246": _group(55246, ("total=31.5", 1.91)),
        "55247": _group(55247, ("total=34.5", 1.89)),
        "55249": _group(55249, ("total=34.5", 1.89)),
        "55250": _group(55250, ("total=10.5", 1.80)),
        "55252": _group(55252, ("total=10.5", 1.94)),
        "55215": _group(55215, ("total=38.5", 1.86)),
        "55217": _group(55217, ("total=38.5", 1.92)),
        "55831": _group(55831, ("NULL", 2.25)),
        "55832": _group(55832, ("NULL", 1.55)),
    },
}

EUROLEAGUE_REGULAR_PREVIEW_MATCH = {
    "id": 514392890,
    "matchCode": 79149,
    "home": "Monaco",
    "away": "Olympiacos",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "Evroliga Play off",
    "betMap": {
        "50445": _group(50445, ("total=164.5", 1.90)),
        "50444": _group(50444, ("total=164.5", 1.90)),
    },
}

EUROLEAGUE_PLAYER_PREVIEW_MATCH = {
    "id": 514398867,
    "matchCode": 81539,
    "superCode": 79149,
    "home": "Mike James",
    "away": "Monaco",
    "kickOffTime": KICKOFF_MS,
    "leagueName": "Evroliga Play off Igrači",
    "betMap": {
        "51679": _group(51679, ("total=16.5", 1.86)),
        "51681": _group(51681, ("total=16.5", 1.92)),
        "55215": _group(55215, ("total=24.5", 1.88)),
        "55217": _group(55217, ("total=24.5", 1.88)),
    },
}

PLAYER_DETAIL_MATCH = {
    **PLAYER_PREVIEW_MATCH,
    "betMap": {
        **PLAYER_PREVIEW_MATCH["betMap"],
        "55672": _group(55672, ("total=1.5", 2.10)),
        "55674": _group(55674, ("total=1.5", 1.67)),
        "55681": _group(55681, ("total=0.5", 1.84)),
        "55683": _group(55683, ("total=0.5", 1.92)),
        "56169": _group(56169, ("total=3.5", 1.88)),
        "56171": _group(56171, ("total=3.5", 1.86)),
        "54106": _group(54106, ("NULL", 1.28)),
        "54111": _group(54111, ("NULL", 2.05)),
        "55692": _group(55692, ("total=7.5", 1.93)),
        "55694": _group(55694, ("total=7.5", 1.83)),
        "55833": _group(55833, ("NULL", 9.50)),
    },
}

TENNIS_MATCH = {
    "id": 514504284,
    "matchCode": 74001,
    "home": "Tiago Pereira",
    "away": "Joao Domingues",
    "kickOffTime": 1778407200000,
    "leagueName": "ITF Loule",
    "live": False,
    "blocked": False,
    "betMap": {
        "1": _group(1, ("NULL", 1.38)),
        "2": _group(2, ("NULL", 99.0)),
        "3": _group(3, ("NULL", 2.8)),
    },
}
TENNIS_NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)

GROUPS_RESPONSE = {"categories": [{"id": "2495"}]}
GROUP_LEAGUES_RESPONSE = {
    "categories": [
        {"id": "2516034", "name": "NBA Play off", "pmCount": 1, "playersCount": 1}
    ]
}


def _football_fixture_data() -> dict:
    with FOOTBALL_FIXTURE_PATH.open() as f:
        return json.load(f)


def _tennis_kickoff_ms(*, seconds: int = 0, minutes: int = 0) -> int:
    kickoff = TENNIS_NOW + timedelta(seconds=seconds, minutes=minutes)
    return int(kickoff.timestamp() * 1000)


def test_extract_league_id_strips_players_suffix():
    assert _extract_league_id("NBA Play off Igrači") == "nba"


def test_build_matchup_index_uses_regular_match_code():
    matchups = _build_matchup_index([REGULAR_PREVIEW_MATCH])
    assert matchups[79148].league_id == "nba"
    assert matchups[79148].home_team == "Atlanta Hawks"
    assert matchups[79148].away_team == "New York Knicks"


def test_parse_regular_match_preview_only_returns_ot_total():
    results = _parse_regular_match(REGULAR_PREVIEW_MATCH)

    assert {row.market_type for row in results} == {"game_total_ot"}
    assert {row.threshold for row in results} == {211.5}


def test_parse_regular_match_detail_returns_both_total_types():
    results = _parse_regular_match(REGULAR_DETAIL_MATCH)

    assert {row.market_type for row in results} == {"game_total", "game_total_ot"}
    assert {row.threshold for row in results if row.market_type == "game_total"} == {208.5}


def test_parse_football_outcome_match_from_live_fixture_emits_target_markets():
    match = _football_fixture_data()["esMatches"][0]

    results = _parse_football_outcome_match(match)

    assert len(results) == 8
    assert {row.bookmaker_id for row in results} == {"soccerbet"}
    assert {row.sport for row in results} == {"football"}
    assert {row.league_id for row in results} == {"etiopija_1"}
    assert {row.home_team for row in results} == {"Shire Endaselassie"}
    assert {row.away_team for row in results} == {"Ethio Electric"}
    assert {row.start_time for row in results} == {"2026-05-05T15:00:00+00:00"}
    assert {(row.market_type, row.outcome_code, row.line, row.raw_label) for row in results} == {
        ("football_result", "home", None, "1"),
        ("football_result", "draw", None, "X"),
        ("football_result", "away", None, "2"),
        ("football_double_chance", "home_or_draw", None, "1X"),
        ("football_double_chance", "home_or_away", None, "12"),
        ("football_double_chance", "draw_or_away", None, "X2"),
        ("football_total_goals", "under", 2.5, "0-2"),
        ("football_total_goals", "over", 2.5, "3+"),
    }


def test_parse_football_outcome_match_skips_locked_or_invalid_entries():
    match = {
        "home": "Team A",
        "away": "Team B",
        "leagueName": "Test Football",
        "kickOffTime": 1777993200000,
        "betMap": {
            "9": _group_with_status(9, ("NULL", 1.42, "L")),
            "22": _group_with_status(22, ("NULL", 1.78, "U")),
            "24": _group_with_status(24, ("NULL", 0.0, "U")),
        },
    }

    results = _parse_football_outcome_match(match)

    assert [(row.market_type, row.outcome_code, row.odds) for row in results] == [
        ("football_total_goals", "under", 1.78),
    ]


def test_parse_football_outcome_match_skips_invalid_shape():
    assert _parse_football_outcome_match({}) == []
    assert _parse_football_outcome_match({"home": "Team A", "away": "Team B", "betMap": []}) == []


def test_parse_football_outcome_match_recovers_missing_home_from_match_label():
    match = {
        "home": "",
        "away": "Ethio Electric",
        "matchName": "Shire Endaselassie - Ethio Electric",
        "leagueName": "Etiopija 1",
        "kickOffTime": 1777993200000,
        "betMap": {
            "1": _group_with_status(1, ("NULL", 2.1, "U")),
            "3": _group_with_status(3, ("NULL", 3.2, "U")),
        },
    }

    results = _parse_football_outcome_match(match)

    assert {(row.home_team, row.away_team) for row in results} == {
        ("Shire Endaselassie", "Ethio Electric")
    }
    assert {row.outcome_code for row in results} == {"home", "away"}


def test_parse_tennis_outcome_match_emits_match_winner_offers():
    results = _parse_tennis_outcome_match(TENNIS_MATCH)

    assert len(results) == 2
    assert {row.bookmaker_id for row in results} == {"soccerbet"}
    assert {row.sport for row in results} == {"tennis"}
    assert {row.league_id for row in results} == {"itf_loule"}
    assert {row.home_team for row in results} == {"Tiago Pereira"}
    assert {row.away_team for row in results} == {"Joao Domingues"}
    assert {row.market_type for row in results} == {"tennis_match_winner"}
    assert {row.source_url for row in results} == {
        "https://www.soccerbet.rs/sr/sportsko-kladjenje/tenis/T"
    }
    assert {
        (row.outcome_code, row.raw_label, row.odds, row.line, row.start_time)
        for row in results
    } == {
        ("home", "1", 1.38, None, "2026-05-10T10:00:00+00:00"),
        ("away", "2", 2.8, None, "2026-05-10T10:00:00+00:00"),
    }
    assert 99.0 not in {row.odds for row in results}


def test_parse_tennis_outcome_match_emits_available_one_sided_odds():
    results = _parse_tennis_outcome_match(
        {
            **TENNIS_MATCH,
            "betMap": {
                "1": _group(1, ("NULL", 1.65)),
                "3": _group_with_status(3, ("NULL", 2.1, "L")),
            },
        }
    )

    assert [(row.outcome_code, row.odds) for row in results] == [("home", 1.65)]


def test_parse_tennis_outcome_match_allows_future_live_flagged_prematch():
    results = _parse_tennis_outcome_match(
        {
            **TENNIS_MATCH,
            "live": True,
            "kickOffTime": _tennis_kickoff_ms(minutes=10),
        },
        now=TENNIS_NOW,
    )

    assert len(results) == 2
    assert {row.outcome_code for row in results} == {"home", "away"}
    assert {row.start_time for row in results} == {"2026-05-10T12:10:00+00:00"}


def test_parse_tennis_outcome_match_skips_live_rows_near_start_or_past():
    near_start = {
        **TENNIS_MATCH,
        "live": True,
        "kickOffTime": _tennis_kickoff_ms(seconds=35),
    }
    past_start = {
        **TENNIS_MATCH,
        "live": True,
        "kickOffTime": _tennis_kickoff_ms(minutes=-1),
    }

    assert _parse_tennis_outcome_match(near_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(near_start, now=TENNIS_NOW) == "live_near_or_past_start"
    assert _parse_tennis_outcome_match(past_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(past_start, now=TENNIS_NOW) == "live_near_or_past_start"


def test_parse_tennis_outcome_match_skips_live_rows_with_bad_start_time():
    missing_start = {**TENNIS_MATCH, "live": True}
    missing_start.pop("kickOffTime")
    invalid_start = {**TENNIS_MATCH, "live": True, "kickOffTime": "not-an-epoch"}

    assert _parse_tennis_outcome_match(missing_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(missing_start, now=TENNIS_NOW) == "missing_start_time"
    assert _parse_tennis_outcome_match(invalid_start, now=TENNIS_NOW) == []
    assert _tennis_skip_reason(invalid_start, now=TENNIS_NOW) == "invalid_start_time"


def test_parse_tennis_outcome_match_skips_blocked_and_doubles_before_live_buffer():
    future_live_match = {
        **TENNIS_MATCH,
        "live": True,
        "kickOffTime": _tennis_kickoff_ms(minutes=10),
    }

    assert _parse_tennis_outcome_match({**future_live_match, "blocked": True}, now=TENNIS_NOW) == []
    assert _tennis_skip_reason({**future_live_match, "blocked": True}, now=TENNIS_NOW) == "blocked"
    assert _parse_tennis_outcome_match({**TENNIS_MATCH, "blocked": True}) == []
    assert _parse_tennis_outcome_match(
        {**TENNIS_MATCH, "leagueName": "ATP Rome Dublovi"}
    ) == []
    assert _parse_tennis_outcome_match(
        {**TENNIS_MATCH, "leagueName": "ATP Rome Doubles"}
    ) == []
    assert _parse_tennis_outcome_match(
        {**future_live_match, "home": "A. Player/B. Player"},
        now=TENNIS_NOW,
    ) == []
    assert _tennis_skip_reason(
        {**future_live_match, "home": "A. Player/B. Player"},
        now=TENNIS_NOW,
    ) == "doubles"
    assert _parse_tennis_outcome_match({**TENNIS_MATCH, "away": "A. Player/B. Player"}) == []


def test_parse_tennis_outcome_match_skips_invalid_rows():
    assert _parse_tennis_outcome_match({"away": "Away", "betMap": {"1": {}}}) == []
    assert _parse_tennis_outcome_match({"home": "Home", "betMap": {"1": {}}}) == []
    assert _parse_tennis_outcome_match({"home": "Home", "away": "Away", "betMap": []}) == []
    assert _parse_tennis_outcome_match(
        {
            "home": "Home",
            "away": "Away",
            "betMap": {
                "1": _group(1, ("NULL", 0.0)),
                "3": _group(3, ("NULL", "bad")),
            },
        }
    ) == []


# ── Handicap (+OT) parsing ──────────────────────────────────────────────


def test_parse_handicap_spec_handles_signed_lines():
    """``hcp=`` specifiers are signed and stored from the home team's
    perspective (negative = home favoured). Our analyzer convention is
    the opposite (positive threshold = home favoured), so the parser
    negates the parsed value."""
    assert _parse_handicap_spec("hcp=3.5") == -3.5
    assert _parse_handicap_spec("hcp=-1.5") == 1.5
    assert _parse_handicap_spec("hcp=0") == 0.0
    assert _parse_handicap_spec("total=200") is None  # totals spec, not handicap
    assert _parse_handicap_spec("garbage") is None
    assert _parse_handicap_spec(None) is None


def _hcp_entry(tt: int, hcp: float, odd: float, status: str = "U") -> dict:
    """Helper to build a SoccerBet betMap handicap entry."""
    return {"tt": tt, "ov": odd, "sv": f"hcp={hcp}", "s": status}


def test_parse_regular_match_emits_handicap_rows_with_signed_threshold():
    """Real live shape (Orlando vs Detroit, where Detroit is favoured):
    tip-type 50430 ('2' = home covers) and 50431 ('1' = away covers)
    carry hcp=X entries with signed lines.  SoccerBet's ``hcp`` is the
    home team's signed Asian-handicap line (negative = home favourite),
    so threshold = -hcp.
    """
    match = {
        "home": "Orlando",
        "away": "Detroit",
        "leagueName": "USA - NBA",
        "kickOffTime": 1777470900000,
        "betMap": {
            # Source labels lines from home perspective: hcp=+3.5 means
            # home (Orlando) +3.5 head start (home is the underdog by 3.5).
            "50431": {  # away covers
                "hcp=3.5":  _hcp_entry(50431, 3.5,  1.9),
                "hcp=2.5":  _hcp_entry(50431, 2.5,  1.77),
                "hcp=4.5":  _hcp_entry(50431, 4.5,  2.0),
                "hcp=-1.5": _hcp_entry(50431, -1.5, 1.45),
            },
            "50430": {  # home covers
                "hcp=3.5":  _hcp_entry(50430, 3.5,  1.9),
                "hcp=2.5":  _hcp_entry(50430, 2.5,  2.0),
                "hcp=4.5":  _hcp_entry(50430, 4.5,  1.78),
                "hcp=-1.5": _hcp_entry(50430, -1.5, 2.6),
            },
        },
    }
    results = _parse_regular_match(match)
    handicap = [r for r in results if r.market_type == "home_handicap_ot"]
    assert len(handicap) == 4
    by_threshold = {r.threshold: (r.over_odds, r.under_odds) for r in handicap}
    # hcp=+3.5 → threshold=-3.5 (home is the underdog by 3.5);
    # over=50430=1.9 (home covers), under=50431=1.9 (away covers)
    assert by_threshold[-3.5] == (1.9, 1.9)
    # hcp=+4.5 → threshold=-4.5 (more of a home underdog line):
    # over=50430=1.78 (home covers easier here), under=50431=2.0
    assert by_threshold[-4.5] == (1.78, 2.0)
    # hcp=-1.5 → threshold=+1.5 (line on the home-favourite side):
    # over=50430=2.6, under=50431=1.45
    assert by_threshold[1.5] == (2.6, 1.45)
    assert {r.home_team for r in handicap} == {"Orlando"}
    assert {r.away_team for r in handicap} == {"Detroit"}


def test_parse_regular_match_handicap_skips_locked_entries():
    """Locked picks (s='L') must be filtered out; only s='U' active picks emit rows."""
    match = {
        "home": "A",
        "away": "B",
        "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "betMap": {
            "50431": {
                "hcp=3.5":  _hcp_entry(50431, 3.5, 1.9, status="L"),  # locked, skip
                "hcp=2.5":  _hcp_entry(50431, 2.5, 1.77),  # active
            },
            "50430": {
                "hcp=2.5":  _hcp_entry(50430, 2.5, 2.0),
            },
        },
    }
    results = _parse_regular_match(match)
    handicap = [r for r in results if r.market_type == "home_handicap_ot"]
    assert len(handicap) == 1
    # hcp=2.5 → threshold=-2.5 after sign flip
    assert handicap[0].threshold == -2.5


def test_parse_regular_match_handicap_pickem_zero_line():
    """A zero-handicap pick'em is a legitimate line and must emit a row."""
    match = {
        "home": "A",
        "away": "B",
        "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "betMap": {
            "50431": {"hcp=0": _hcp_entry(50431, 0, 1.92)},  # away covers
            "50430": {"hcp=0": _hcp_entry(50430, 0, 1.88)},  # home covers
        },
    }
    results = _parse_regular_match(match)
    handicap = [r for r in results if r.market_type == "home_handicap_ot"]
    assert len(handicap) == 1
    assert handicap[0].threshold == 0.0
    # 50430 = home covers (over), 50431 = away covers (under)
    assert handicap[0].over_odds == 1.88
    assert handicap[0].under_odds == 1.92


def test_parse_regular_match_does_not_mix_handicap_with_totals():
    """Regression: handicap entries must not leak into game_total_ot rows."""
    match = {
        "home": "A",
        "away": "B",
        "leagueName": "Test",
        "kickOffTime": 1777470900000,
        "betMap": {
            # totals on the dedicated codes
            "50445": {"total=210.5": {"tt": 50445, "ov": 1.85, "sv": "total=210.5", "s": "U"}},
            "50444": {"total=210.5": {"tt": 50444, "ov": 1.95, "sv": "total=210.5", "s": "U"}},
            # handicap on its codes
            "50431": {"hcp=-2.5": _hcp_entry(50431, -2.5, 1.45)},
            "50430": {"hcp=-2.5": _hcp_entry(50430, -2.5, 2.6)},
        },
    }
    results = _parse_regular_match(match)
    totals = [r for r in results if r.market_type == "game_total_ot"]
    handicap = [r for r in results if r.market_type == "home_handicap_ot"]
    assert len(totals) == 1
    assert totals[0].threshold == 210.5
    assert len(handicap) == 1
    # hcp=-2.5 → threshold=+2.5 (home is the favourite by 2.5)
    assert handicap[0].threshold == 2.5


def test_parse_player_match_preview_uses_underlying_matchup():
    matchup_by_super_code = _build_matchup_index([REGULAR_PREVIEW_MATCH])

    results = _parse_player_match(PLAYER_PREVIEW_MATCH, matchup_by_super_code)

    assert {row.market_type for row in results} == {
        "player_points",
        "player_assists",
        "player_rebounds",
        "player_3points",
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }
    assert {row.home_team for row in results} == {"Atlanta Hawks"}
    assert {row.away_team for row in results} == {"New York Knicks"}
    assert {row.league_id for row in results} == {"nba"}
    assert all(row.player_name == "Jalen Brunson" for row in results)


def test_parse_player_match_skips_locked_player_picks():
    matchup_by_super_code = _build_matchup_index([REGULAR_PREVIEW_MATCH])
    player_match = {
        **PLAYER_PREVIEW_MATCH,
        "betMap": {
            "51685": _group_with_status(
                51685,
                ("total=1.5", 1.85, "U"),
                ("total=2.5", 1.85, "L"),
            ),
            "51687": _group_with_status(
                51687,
                ("total=1.5", 1.85, "U"),
                ("total=2.5", 1.85, "L"),
            ),
        },
    }

    results = _parse_player_match(player_match, matchup_by_super_code)

    rebounds = [row for row in results if row.market_type == "player_rebounds"]
    assert [(row.threshold, row.over_odds, row.under_odds) for row in rebounds] == [
        (1.5, 1.85, 1.85)
    ]


def test_parse_player_match_detail_adds_detail_only_supported_markets():
    matchup_by_super_code = _build_matchup_index([REGULAR_PREVIEW_MATCH])

    results = _parse_player_match(PLAYER_DETAIL_MATCH, matchup_by_super_code)
    market_types = {row.market_type for row in results}

    assert "player_steals" in market_types
    assert "player_blocks" in market_types
    assert "player_turnovers" in market_types
    assert "player_points_milestones" in market_types
    assert "player_points_q1" not in market_types


@pytest.mark.asyncio
async def test_scrape_odds_partial_mode_uses_broad_preview_feeds_only():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _ALL_GAMES_URL:
            return {
                "esMatches": [
                    REGULAR_PREVIEW_MATCH,
                    EUROLEAGUE_REGULAR_PREVIEW_MATCH,
                ]
            }
        if url == _ALL_PLAYERS_URL:
            return {
                "esMatches": [
                    PLAYER_PREVIEW_MATCH,
                    EUROLEAGUE_PLAYER_PREVIEW_MATCH,
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="partial")
    results = await scraper.scrape_odds("basketball")

    market_types = {row.market_type for row in results}
    assert "game_total_ot" in market_types
    assert "game_total" not in market_types
    assert "player_turnovers" not in market_types
    assert "player_steals" not in market_types
    assert any(row.player_name == "Jalen Brunson" for row in results)
    assert any(row.player_name == "Mike James" for row in results)
    assert {row.home_team for row in results if row.player_name == "Mike James"} == {"Monaco"}
    assert {row.away_team for row in results if row.player_name == "Mike James"} == {"Olympiacos"}
    called_urls = [call.args[0] for call in http_client.get_json.call_args_list]
    assert set(called_urls) == {_ALL_GAMES_URL, _ALL_PLAYERS_URL}
    assert len(called_urls) == 2


@pytest.mark.asyncio
async def test_scrape_outcome_offers_uses_football_preview_feed():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _FOOTBALL_GAMES_URL:
            return _football_fixture_data()
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("football")

    assert len(results) == 8
    assert {row.market_type for row in results} == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }
    assert [call.args[0] for call in http_client.get_json.call_args_list] == [
        _FOOTBALL_GAMES_URL,
    ]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_uses_tennis_preview_feed():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _TENNIS_GAMES_URL:
            return {
                "esMatches": [
                    TENNIS_MATCH,
                    {**TENNIS_MATCH, "id": 514504285, "home": "Yasmine Mansouri"},
                    {**TENNIS_MATCH, "id": 514504830, "leagueName": "ATP Rome Dublovi"},
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client)
    results = await scraper.scrape_outcome_offers("tennis")

    assert len(results) == 4
    assert {row.market_type for row in results} == {"tennis_match_winner"}
    assert {row.sport for row in results} == {"tennis"}
    assert [call.args[0] for call in http_client.get_json.call_args_list] == [
        _TENNIS_GAMES_URL,
    ]


@pytest.mark.asyncio
async def test_scrape_outcome_offers_ignores_unsupported_sport():
    http_client = AsyncMock()
    scraper = SoccerBetScraper(http_client=http_client)

    assert await scraper.scrape_outcome_offers("basketball") == []
    http_client.get_json.assert_not_called()


def test_scraper_supports_football_and_tennis_outcomes():
    scraper = SoccerBetScraper()
    assert scraper.get_supported_outcome_sports() == ["football", "tennis"]


@pytest.mark.asyncio
async def test_scrape_odds_full_mode_uses_detail_enrichment():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _GROUPS_URL:
            return GROUPS_RESPONSE
        if url == _GROUP_LEAGUES_URL.format(group_id="2495"):
            return GROUP_LEAGUES_RESPONSE
        if url == _LEAGUE_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [REGULAR_PREVIEW_MATCH]}
        if url == _PLAYER_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [PLAYER_PREVIEW_MATCH]}
        if url == _DETAIL_URL.format(match_code=79148):
            return REGULAR_DETAIL_MATCH
        if url == _DETAIL_URL.format(match_code=81538):
            return PLAYER_DETAIL_MATCH
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="full")
    results = await scraper.scrape_odds("basketball")

    market_types = {row.market_type for row in results}
    assert "game_total" in market_types
    assert "game_total_ot" in market_types
    assert "player_turnovers" in market_types
    assert "player_steals" in market_types
    assert "player_blocks" in market_types
    assert "player_points_milestones" in market_types


@pytest.mark.asyncio
async def test_scrape_odds_full_mode_uses_preview_super_code_when_detail_omits_it():
    player_detail_without_super_code = {
        key: value for key, value in PLAYER_DETAIL_MATCH.items() if key != "superCode"
    }

    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _GROUPS_URL:
            return GROUPS_RESPONSE
        if url == _GROUP_LEAGUES_URL.format(group_id="2495"):
            return GROUP_LEAGUES_RESPONSE
        if url == _LEAGUE_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [REGULAR_PREVIEW_MATCH]}
        if url == _PLAYER_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [PLAYER_PREVIEW_MATCH]}
        if url == _DETAIL_URL.format(match_code=79148):
            return REGULAR_DETAIL_MATCH
        if url == _DETAIL_URL.format(match_code=81538):
            return player_detail_without_super_code
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="full")
    results = await scraper.scrape_odds("basketball")

    assert any(row.player_name == "Jalen Brunson" for row in results)
    assert "player_turnovers" in {row.market_type for row in results}


@pytest.mark.asyncio
async def test_scrape_odds_full_mode_drops_player_rows_when_detail_fails():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _GROUPS_URL:
            return GROUPS_RESPONSE
        if url == _GROUP_LEAGUES_URL.format(group_id="2495"):
            return GROUP_LEAGUES_RESPONSE
        if url == _LEAGUE_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [REGULAR_PREVIEW_MATCH]}
        if url == _PLAYER_PREVIEW_URL.format(league_id="2516034"):
            return {"esMatches": [PLAYER_PREVIEW_MATCH]}
        if url == _DETAIL_URL.format(match_code=79148):
            return REGULAR_DETAIL_MATCH
        if url == _DETAIL_URL.format(match_code=81538):
            raise RuntimeError("player detail failed")
        raise AssertionError(f"Unexpected URL: {url}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json

    scraper = SoccerBetScraper(http_client=http_client, detail_mode="full")
    results = await scraper.scrape_odds("basketball")

    assert {row.market_type for row in results} == {"game_total", "game_total_ot"}
    assert all(row.player_name is None for row in results)
