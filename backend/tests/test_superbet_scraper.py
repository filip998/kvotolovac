from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.scrapers.superbet_scraper import (
    SuperbetScraper,
    EventContext,
    _EVENTS_BY_DATE_URL,
    _EVENT_SUBSCRIPTION_URL,
    _MARKET_GROUPS_URL,
    _STRUCTURE_URL,
    _classify_market_type,
    _extract_league_id,
    _extract_side,
    _extract_threshold,
    _normalize_player_name,
    _parse_event_payload,
)


def _odd(
    price: float,
    *,
    code: str | None = None,
    name: str,
    info: str,
    status: int = 1,
    display: bool = True,
    specifiers: dict[str, str] | None = None,
) -> dict:
    metadata: dict[str, object] = {
        "name": name,
        "info": info,
    }
    if code is not None:
        metadata["code"] = code
    if specifiers is not None:
        metadata["specifiers"] = specifiers
    return {
        "price": price,
        "status": status,
        "display": display,
        "metadata": metadata,
    }


START_DT = (datetime.now(tz=timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
START_Z = START_DT.isoformat().replace("+00:00", "Z")

STRUCTURE_RESPONSE = {
    "data": {
        "sports": [{"id": "4", "localNames": {"sr-Latn-RS": "Košarka"}}],
        "categories": [
            {"id": "36", "localNames": {"sr-Latn-RS": "Međunarodne"}},
            {"id": "157", "localNames": {"sr-Latn-RS": "Nemačka"}},
        ],
        "tournaments": [
            {"id": "2383", "localNames": {"sr-Latn-RS": "Evrokup - Play-off"}},
            {"id": "350", "localNames": {"sr-Latn-RS": "Nemačka - BBL"}},
        ],
    }
}

MARKET_GROUPS_RESPONSE = {
    "data": [
        {
            "id": 169,
            "localNames": {"sr-Latn-RS": "Poeni igrača"},
            "markets": [235311, 235312],
        },
        {
            "id": 170,
            "localNames": {"sr-Latn-RS": "Asistencije"},
            "markets": [235313],
        },
        {
            "id": 171,
            "localNames": {"sr-Latn-RS": "Skokovi"},
            "markets": [235314],
        },
        {
            "id": 1001,
            "localNames": {"sr-Latn-RS": "3 poena igrača"},
            "markets": [235315],
        },
        {
            "id": 172,
            "localNames": {"sr-Latn-RS": "Igrači statistika"},
            "markets": [235316, 235317, 235318],
        },
    ]
}

DISCOVERY_EVENT_ONE = {
    "eventId": 12629345,
    "matchName": "Besiktas·JL Bourg",
    "utcDate": START_Z,
    "sportId": 4,
    "categoryId": 36,
    "tournamentId": 2383,
}

DISCOVERY_EVENT_TWO = {
    "eventId": 12645680,
    "matchName": "Ulm·Alba Berlin",
    "utcDate": START_Z,
    "sportId": 4,
    "categoryId": 157,
    "tournamentId": 350,
}

EVENT_PAYLOAD_ONE = {
    "event_id": 12629345,
    "fixture": {
        "event_name": "Besiktas·JL Bourg",
        "utc_date": START_Z,
        "category_id": 36,
        "tournament_id": 2383,
    },
    "markets": [
        {
            "id": 753,
            "name": "Ukupno poena (uklj. produžetke)",
            "odds": [
                _odd(
                    1.8,
                    code="-",
                    name="Manje od 157.5",
                    info="Manje od 157.5 poena u meču (uklj. produžetke)",
                    specifiers={"total": "157.5"},
                ),
                _odd(
                    1.9,
                    code="+",
                    name="Više od 157.5",
                    info="Više od 157.5 poena u meču (uklj. produžetke)",
                    specifiers={"total": "157.5"},
                ),
            ],
        },
        {
            "id": 235312,
            "name": "Ukupno poena igrača (uklj. produžetke)",
            "odds": [
                _odd(
                    1.87,
                    code="+",
                    name="Brown, Anthony - Više od 15.5",
                    info="Biće više od 15.5 poena (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "15.5"},
                ),
                _odd(
                    1.95,
                    code="-",
                    name="Brown, Anthony - Manje od 15.5",
                    info="Biće manje od 15.5 poena (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "15.5"},
                ),
            ],
        },
        {
            "id": 235311,
            "name": "Ukupno poena igrača  (uklj. produžetke)",
            "odds": [
                _odd(
                    1.48,
                    name="Brown, Anthony 20+",
                    info="Ostvariće 20 ili više poena (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "milestone": "20"},
                ),
                _odd(
                    1.0,
                    name="Gach, Both 20+",
                    info="Ostvariće 20 ili više poena (uklj. produžetke)",
                    status=2,
                    specifiers={"player": "Gach, Both", "milestone": "20"},
                ),
            ],
        },
        {
            "id": 235313,
            "name": "Ukupno asistencija igrača (uklj. produžetke)",
            "odds": [
                _odd(
                    2.15,
                    code="+",
                    name="Brown, Anthony - Više od 3.5",
                    info="Biće više od 3.5 asistencija (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "3.5"},
                ),
                _odd(
                    1.68,
                    code="-",
                    name="Brown, Anthony - Manje od 3.5",
                    info="Biće manje od 3.5 asistencija (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "3.5"},
                ),
            ],
        },
        {
            "id": 235314,
            "name": "Ukupno skokova igrača (uklj. produžetke)",
            "odds": [
                _odd(
                    1.72,
                    code="+",
                    name="Brown, Anthony - Više od 4.5",
                    info="Biće više od 4.5 skokova (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "4.5"},
                ),
                _odd(
                    2.02,
                    code="-",
                    name="Brown, Anthony - Manje od 4.5",
                    info="Biće manje od 4.5 skokova (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "4.5"},
                ),
            ],
        },
        {
            "id": 235315,
            "name": "3 poena igrača (uklj. produžetke)",
            "odds": [
                _odd(
                    1.74,
                    code="+",
                    name="Brown, Anthony - Više od 1.5",
                    info="Biće više od 1.5 pogođenih trojki iz igre (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "1.5"},
                ),
                _odd(
                    1.98,
                    code="-",
                    name="Brown, Anthony - Manje od 1.5",
                    info="Biće manje od 1.5 pogođenih trojki iz igre (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "1.5"},
                ),
            ],
        },
        {
            "id": 235316,
            "name": "Poeni igrača + Asistencije (uklj. produžetke)",
            "odds": [
                _odd(
                    1.83,
                    code="+",
                    name="Brown, Anthony - Više od 19.5",
                    info="Biće više od 19.5 poena + asistencija (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "19.5"},
                ),
                _odd(
                    1.91,
                    code="-",
                    name="Brown, Anthony - Manje od 19.5",
                    info="Biće manje od 19.5 poena + asistencija (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "19.5"},
                ),
            ],
        },
        {
            "id": 235317,
            "name": "Poeni igrača + Skokovi (uklj. produžetke)",
            "odds": [
                _odd(
                    1.89,
                    code="+",
                    name="Brown, Anthony - Više od 20.5",
                    info="Biće više od 20.5 poena + skokova (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "20.5"},
                ),
                _odd(
                    1.87,
                    code="-",
                    name="Brown, Anthony - Manje od 20.5",
                    info="Biće manje od 20.5 poena + skokova (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "20.5"},
                ),
            ],
        },
        {
            "id": 235318,
            "name": "Poeni igrača + Skokovi + Asistencije (uklj. produžetke)",
            "odds": [
                _odd(
                    1.93,
                    code="+",
                    name="Brown, Anthony - Više od 24.5",
                    info="Biće više od 24.5 poena + skokova + asistencija (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "24.5"},
                ),
                _odd(
                    1.82,
                    code="-",
                    name="Brown, Anthony - Manje od 24.5",
                    info="Biće manje od 24.5 poena + skokova + asistencija (uklj. produžetke)",
                    specifiers={"player": "Brown, Anthony", "total": "24.5"},
                ),
            ],
        },
    ],
}

EVENT_PAYLOAD_TWO = {
    "event_id": 12645680,
    "fixture": {
        "event_name": "Ulm·Alba Berlin",
        "utc_date": START_Z,
        "category_id": 157,
        "tournament_id": 350,
    },
    "markets": [
        {
            "id": 753,
            "name": "Ukupno poena (uklj. produžetke)",
            "odds": [
                _odd(
                    1.84,
                    code="-",
                    name="Manje od 163.5",
                    info="Manje od 163.5 poena u meču (uklj. produžetke)",
                    specifiers={"total": "163.5"},
                ),
                _odd(
                    1.9,
                    code="+",
                    name="Više od 163.5",
                    info="Više od 163.5 poena u meču (uklj. produžetke)",
                    specifiers={"total": "163.5"},
                ),
            ],
        },
        {
            "id": 235312,
            "name": "Ukupno poena igrača (uklj. produžetke)",
            "odds": [
                _odd(
                    1.79,
                    code="+",
                    name="Thomas, Matt - Više od 12.5",
                    info="Biće više od 12.5 poena (uklj. produžetke)",
                    specifiers={"player": "Thomas, Matt", "total": "12.5"},
                ),
                _odd(
                    1.96,
                    code="-",
                    name="Thomas, Matt - Manje od 12.5",
                    info="Biće manje od 12.5 poena (uklj. produžetke)",
                    specifiers={"player": "Thomas, Matt", "total": "12.5"},
                ),
            ],
        },
    ],
}

MARKET_GROUP_LOOKUP = {
    235311: "Poeni igrača",
    235312: "Poeni igrača",
    235313: "Asistencije",
    235314: "Skokovi",
    235315: "3 poena igrača",
    235316: "Igrači statistika",
    235317: "Igrači statistika",
    235318: "Igrači statistika",
}


def test_normalize_player_name_reorders_last_first_format():
    assert _normalize_player_name("Lindo Jr., Ricky") == "Ricky Lindo Jr."


def test_extract_league_id_uses_known_aliases_and_fallback():
    assert _extract_league_id("Nemačka - BBL") == "germany"
    assert _extract_league_id("VTB Liga") == "vtb_liga"


# ── Handicap (+OT) parsing ──────────────────────────────────────────────


def test_classify_market_type_recognises_handicap_market_name():
    """The Serbian-language handicap market name must classify as
    home_handicap_ot when there's no player specifier."""
    name = "Hendikep poena (uklj. produžetke)"
    assert _classify_market_type(name, None, {}) == "home_handicap_ot"
    # With a player specifier present, must NOT match (it's a whole-game market)
    assert _classify_market_type(name, None, {"player": "X"}) is None


def test_extract_threshold_handicap_flips_sign():
    """SuperBet stores hcp as team1's signed Asian handicap (negative = team1
    favoured), so the canonical home-perspective threshold is the negation."""
    assert _extract_threshold({"hcp": "-7.5"}, market_type="home_handicap_ot") == 7.5
    assert _extract_threshold({"hcp": "+3.5"}, market_type="home_handicap_ot") == -3.5
    assert _extract_threshold({"hcp": "0"}, market_type="home_handicap_ot") == 0.0
    # Without the handicap market type, hcp is ignored
    assert _extract_threshold({"hcp": "-7.5"}) is None


def test_extract_side_handicap_uses_code_one_two():
    """For handicap, code "1" = team1=home covers (over) and "2" = team2
    covers (under). Other side hints used for totals must NOT apply."""
    assert _extract_side({"code": "1"}, {}, market_type="home_handicap_ot") == "over"
    assert _extract_side({"code": "2"}, {}, market_type="home_handicap_ot") == "under"
    assert _extract_side({"code": "+"}, {}, market_type="home_handicap_ot") is None
    assert _extract_side({"code": "X"}, {}, market_type="home_handicap_ot") is None


def test_parse_event_payload_aggregates_handicap_ladder():
    """Reproduces the live Toronto vs Cleveland handicap ladder shape:
    market name 'Hendikep poena (uklj. produžetke)' with code "1"/"2" odds
    and signed hcp specifiers. Each line emits one row with threshold = -hcp.
    """
    context = EventContext(
        event_id=12345,
        league_id="nba",
        home_team="Toronto Raptors",
        away_team="Cleveland Cavaliers",
        start_time=START_DT.isoformat(),
        source_url=None,
    )

    def _hcp_odd(code: str, hcp: str, price: float) -> dict:
        return {
            "uuid": f"u-{code}-{hcp}",
            "price": price,
            "status": 1,
            "display": True,
            "metadata": {
                "code": code,
                "specifiers": {"hcp": hcp},
                "name": f"Toronto uz ({hcp}) hendikep",
                "info": "",
            },
        }

    payload = {
        "event_id": 12345,
        "markets": [
            {
                "name": "Hendikep poena (uklj. produžetke)",
                "id": 999,
                "metadata": {},
                "odds": [
                    _hcp_odd("1", "-7.5", 4.4),   # Toronto wins by 8+: hard
                    _hcp_odd("2", "-7.5", 1.17),  # Cleveland covers
                    _hcp_odd("1", "-3.5", 1.95),  # Toronto wins by 4+: closer to even
                    _hcp_odd("2", "-3.5", 1.85),
                    _hcp_odd("1", "+1.5", 1.4),   # Toronto loses by ≤1 OR wins
                    _hcp_odd("2", "+1.5", 2.85),
                ],
            }
        ],
    }
    results = _parse_event_payload(payload, context=context, market_group_lookup={})
    handi = [r for r in results if r.market_type == "home_handicap_ot"]
    assert len(handi) == 3

    by_threshold = {r.threshold: (r.over_odds, r.under_odds) for r in handi}
    # hcp=-7.5 → threshold=+7.5 (Toronto favoured by 7.5)
    assert by_threshold[7.5] == (4.4, 1.17)
    # hcp=-3.5 → threshold=+3.5
    assert by_threshold[3.5] == (1.95, 1.85)
    # hcp=+1.5 → threshold=-1.5 (Toronto underdog by 1.5)
    assert by_threshold[-1.5] == (1.4, 2.85)


def test_parse_event_payload_does_not_mix_handicap_with_totals():
    """Regression: a totals market and a handicap market in the same payload
    must both produce rows correctly (no cross-contamination of specifiers).
    """
    context = EventContext(
        event_id=1,
        league_id="nba",
        home_team="A",
        away_team="B",
        start_time=START_DT.isoformat(),
        source_url=None,
    )
    payload = {
        "event_id": 1,
        "markets": [
            {
                "name": "Ukupno poena (uklj. produžetke)",
                "id": 1,
                "metadata": {},
                "odds": [
                    {"uuid": "x", "price": 1.85, "status": 1, "display": True,
                     "metadata": {"code": "+", "specifiers": {"total": "210.5"}}},
                    {"uuid": "y", "price": 1.95, "status": 1, "display": True,
                     "metadata": {"code": "-", "specifiers": {"total": "210.5"}}},
                ],
            },
            {
                "name": "Hendikep poena (uklj. produžetke)",
                "id": 2,
                "metadata": {},
                "odds": [
                    {"uuid": "h1", "price": 1.9, "status": 1, "display": True,
                     "metadata": {"code": "1", "specifiers": {"hcp": "-3.5"}}},
                    {"uuid": "h2", "price": 1.9, "status": 1, "display": True,
                     "metadata": {"code": "2", "specifiers": {"hcp": "-3.5"}}},
                ],
            },
        ],
    }
    results = _parse_event_payload(payload, context=context, market_group_lookup={})
    by_market = {(r.market_type, r.threshold): (r.over_odds, r.under_odds) for r in results}
    assert by_market[("game_total_ot", 210.5)] == (1.85, 1.95)
    assert by_market[("home_handicap_ot", 3.5)] == (1.9, 1.9)


def test_parse_event_payload_groups_supported_superbet_markets():
    context = EventContext(
        event_id=12629345,
        league_id="evrokup_play_off",
        home_team="Besiktas",
        away_team="JL Bourg",
        start_time=START_DT.isoformat(),
        source_url="https://superbet.rs/kvote/kosarka/besiktas-vs-jl-bourg-12629345?mdt=o",
    )

    results = _parse_event_payload(
        EVENT_PAYLOAD_ONE,
        context=context,
        market_group_lookup=MARKET_GROUP_LOOKUP,
    )

    by_key = {
        (row.market_type, row.player_name, row.threshold): (row.over_odds, row.under_odds)
        for row in results
    }

    assert by_key[("game_total_ot", None, 157.5)] == (1.9, 1.8)
    assert by_key[("player_points", "Anthony Brown", 15.5)] == (1.87, 1.95)
    assert by_key[("player_points_milestones", "Anthony Brown", 19.5)] == (1.48, None)
    assert by_key[("player_assists", "Anthony Brown", 3.5)] == (2.15, 1.68)
    assert by_key[("player_rebounds", "Anthony Brown", 4.5)] == (1.72, 2.02)
    assert by_key[("player_3points", "Anthony Brown", 1.5)] == (1.74, 1.98)
    assert by_key[("player_points_assists", "Anthony Brown", 19.5)] == (1.83, 1.91)
    assert by_key[("player_points_rebounds", "Anthony Brown", 20.5)] == (1.89, 1.87)
    assert by_key[("player_points_rebounds_assists", "Anthony Brown", 24.5)] == (1.93, 1.82)
    assert all("Brown, Anthony" not in (row.player_name or "") for row in results)
    assert all(row.source_url == context.source_url for row in results)


def test_parse_event_payload_skips_three_point_attempt_markets():
    context = EventContext(
        event_id=12769041,
        league_id="nba",
        home_team="Oklahoma City Thunder",
        away_team="Phoenix Suns",
        start_time=START_DT.isoformat(),
        source_url="https://superbet.rs/kvote/kosarka/oklahoma-city-thunder-vs-phoenix-suns-12769041?mdt=o",
    )
    event_payload = {
        "event_id": 12769041,
        "fixture": {
            "event_name": "Oklahoma City Thunder·Phoenix Suns",
            "utc_date": START_Z,
            "category_id": 61,
            "tournament_id": 2177,
        },
        "markets": [
            {
                "id": 231810,
                "name": "Ukupno šuteva za 3 poena igrača (uklj. produžetke)",
                "odds": [
                    _odd(
                        1.95,
                        name="Alex Caruso - Više od 2.5",
                        info="Biće više od 2.5 šuteva iz igre za 3 poena (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "2.5"},
                    ),
                    _odd(
                        1.73,
                        name="Alex Caruso - Manje od 2.5",
                        info="Biće manje od 2.5 šuteva iz igre za 3 poena (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "2.5"},
                    ),
                ],
            }
        ],
    }

    results = _parse_event_payload(
        event_payload,
        context=context,
        market_group_lookup={**MARKET_GROUP_LOOKUP, 231810: "3 poena igrača"},
    )

    assert results == []


def test_parse_event_payload_skips_mixed_group_auxiliary_and_period_markets():
    context = EventContext(
        event_id=12769041,
        league_id="nba",
        home_team="Oklahoma City Thunder",
        away_team="Phoenix Suns",
        start_time=START_DT.isoformat(),
        source_url="https://superbet.rs/kvote/kosarka/oklahoma-city-thunder-vs-phoenix-suns-12769041?mdt=o",
    )
    event_payload = {
        "event_id": 12769041,
        "fixture": {
            "event_name": "Oklahoma City Thunder·Phoenix Suns",
            "utc_date": START_Z,
            "category_id": 61,
            "tournament_id": 2177,
        },
        "markets": [
            {
                "id": 233565,
                "name": "Ukupno poena igrača (uklj. produžetke)",
                "odds": [
                    _odd(
                        1.92,
                        name="Caruso, Alex - Više od 5.5",
                        info="Postiže više od 5.5 poena (uklj. produžetke)",
                        specifiers={"player": "Caruso, Alex", "total": "5.5"},
                    ),
                    _odd(
                        1.9,
                        name="Caruso, Alex - Manje od 5.5",
                        info="Postiže manje od 5.5 poena (uklj. produžetke)",
                        specifiers={"player": "Caruso, Alex", "total": "5.5"},
                    ),
                ],
            },
            {
                "id": 201533,
                "name": "1. četvrtina - Ukupno poena igrača",
                "odds": [
                    _odd(
                        1.83,
                        name="Alex Caruso - Više od 0.5",
                        info="Biće više od 0.5 poena u 1. četvrtini",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                    _odd(
                        1.88,
                        name="Alex Caruso - Manje od 0.5",
                        info="Biće manje od 0.5 poena u 1. četvrtini",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                ],
            },
            {
                "id": 201534,
                "name": "1. četvrtina - Ukupno asistencija igrača",
                "odds": [
                    _odd(
                        2.82,
                        name="Alex Caruso - Više od 0.5",
                        info="Biće više od 0.5 asistencija u 1. četvrtini",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                    _odd(
                        1.38,
                        name="Alex Caruso - Manje od 0.5",
                        info="Biće manje od 0.5 asistencija u 1. četvrtini",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                ],
            },
            {
                "id": 201535,
                "name": "1. četvrtina - Ukupno skokova igrača",
                "odds": [
                    _odd(
                        1.73,
                        name="Alex Caruso - Više od 0.5",
                        info="Biće više od 0.5 skokova u 1. četvrtini",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                    _odd(
                        2.0,
                        name="Alex Caruso - Manje od 0.5",
                        info="Biće manje od 0.5 skokova u 1. četvrtini",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                ],
            },
            {
                "id": 231804,
                "name": "Ukupno pogođenih slobodnih bacanja igrača (uklj. produžetke)",
                "odds": [
                    _odd(
                        2.32,
                        name="Alex Caruso - Više od 0.5",
                        info="Biće više od 0.5 pogođenih slobodnih bacanja (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                    _odd(
                        1.54,
                        name="Alex Caruso - Manje od 0.5",
                        info="Biće manje od 0.5 pogođenih slobodnih bacanja (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "0.5"},
                    ),
                ],
            },
            {
                "id": 231807,
                "name": "Ukupno šuteva iz igre igrača (uklj. produžetke)",
                "odds": [
                    _odd(
                        2.07,
                        name="Alex Caruso - Više od 4.5",
                        info="Biće više od 4.5 šuteva iz igre (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "4.5"},
                    ),
                    _odd(
                        1.64,
                        name="Alex Caruso - Manje od 4.5",
                        info="Biće manje od 4.5 šuteva iz igre (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "4.5"},
                    ),
                ],
            },
            {
                "id": 231809,
                "name": "Ukupno šuteva za 2 poena igrača (uklj. produžetke)",
                "odds": [
                    _odd(
                        1.83,
                        name="Alex Caruso - Više od 1.5",
                        info="Biće više od 1.5 šuteva iz igre za 2 poena (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "1.5"},
                    ),
                    _odd(
                        1.83,
                        name="Alex Caruso - Manje od 1.5",
                        info="Biće manje od 1.5 šuteva iz igre za 2 poena (uklj. produžetke)",
                        specifiers={"player": "Alex Caruso", "total": "1.5"},
                    ),
                ],
            },
        ],
    }

    results = _parse_event_payload(
        event_payload,
        context=context,
        market_group_lookup={
            **MARKET_GROUP_LOOKUP,
            233565: "Poeni igrača",
            201533: "Poeni igrača",
            201534: "Asistencije",
            201535: "Skokovi",
            231804: "Poeni igrača",
            231807: "Poeni igrača",
            231809: "Poeni igrača",
        },
    )

    assert [
        (row.market_type, row.player_name, row.threshold, row.over_odds, row.under_odds)
        for row in results
    ] == [
        ("player_points", "Alex Caruso", 5.5, 1.92, 1.9),
    ]


@pytest.mark.asyncio
async def test_scrape_odds_uses_structure_market_groups_and_batched_event_sse():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del headers
        if url == _STRUCTURE_URL:
            return STRUCTURE_RESPONSE
        if url == _MARKET_GROUPS_URL.format(sport_id=4):
            return MARKET_GROUPS_RESPONSE
        if url == _EVENTS_BY_DATE_URL:
            assert params is not None
            assert params["sportId"] == "4"
            assert params["offerState"] == "prematch"
            return {"data": [DISCOVERY_EVENT_ONE]}
        raise AssertionError(f"Unexpected URL: {url}")

    async def fake_get_sse_json(url: str, *, params=None, headers=None, max_messages=1, read_timeout=None):
        del headers
        assert url == _EVENT_SUBSCRIPTION_URL
        assert params == {"events": "12629345"}
        assert max_messages == 1
        assert read_timeout == 10.0
        return [[EVENT_PAYLOAD_ONE]]

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json
    http_client.get_sse_json.side_effect = fake_get_sse_json

    scraper = SuperbetScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    market_types = {row.market_type for row in results}
    assert market_types == {
        "game_total_ot",
        "player_3points",
        "player_assists",
        "player_points",
        "player_points_assists",
        "player_points_milestones",
        "player_points_rebounds",
        "player_points_rebounds_assists",
        "player_rebounds",
    }
    assert {row.league_id for row in results} == {"evrokup_play_off"}
    assert {row.home_team for row in results} == {"Besiktas"}
    assert {row.away_team for row in results} == {"JL Bourg"}
    assert any(
        row.source_url == "https://superbet.rs/kvote/kosarka/besiktas-vs-jl-bourg-12629345?mdt=o"
        for row in results
    )


@pytest.mark.asyncio
async def test_scrape_odds_retries_missing_batch_events_singly():
    async def fake_get_json(url: str, *, params=None, headers=None):
        del params, headers
        if url == _STRUCTURE_URL:
            return STRUCTURE_RESPONSE
        if url == _MARKET_GROUPS_URL.format(sport_id=4):
            return MARKET_GROUPS_RESPONSE
        if url == _EVENTS_BY_DATE_URL:
            return {"data": [DISCOVERY_EVENT_ONE, DISCOVERY_EVENT_TWO]}
        raise AssertionError(f"Unexpected URL: {url}")

    async def fake_get_sse_json(url: str, *, params=None, headers=None, max_messages=1, read_timeout=None):
        del headers, max_messages, read_timeout
        assert url == _EVENT_SUBSCRIPTION_URL
        if params == {"events": "12629345,12645680"}:
            return [[EVENT_PAYLOAD_ONE]]
        if params == {"events": "12645680"}:
            return [[EVENT_PAYLOAD_TWO]]
        raise AssertionError(f"Unexpected params: {params}")

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json
    http_client.get_sse_json.side_effect = fake_get_sse_json

    scraper = SuperbetScraper(http_client=http_client)
    results = await scraper.scrape_odds("basketball")

    assert any(row.home_team == "Besiktas" and row.player_name == "Anthony Brown" for row in results)
    assert any(row.home_team == "Ulm" and row.league_id == "germany" for row in results)
    assert any(row.player_name == "Matt Thomas" and row.threshold == 12.5 for row in results)
    requested_batches = [call.kwargs["params"]["events"] for call in http_client.get_sse_json.call_args_list]
    assert requested_batches == ["12629345,12645680", "12645680"]


# ── Football outcome offers ─────────────────────────────────────────────


from app.scrapers.superbet_scraper import (
    _OUTCOME_SPORT_SPECS,
    _parse_football_event_payload,
)
from app.models.schemas import RawOutcomeOffer


_FOOTBALL_CONTEXT = EventContext(
    event_id=12850987,
    league_id="brazil_under_20",
    home_team="Vila Nova GO U20",
    away_team="Botafogo SP U20",
    start_time=START_DT.isoformat(),
    source_url="https://superbet.rs/kvote/fudbal/vila-nova-go-u20-vs-botafogo-sp-u20-12850987?mdt=o",
)


def _football_payload() -> dict:
    """Synthesise a subscription payload mirroring the live shape."""
    return {
        "event_id": 12850987,
        "fixture": {
            "event_name": "Vila Nova GO U20·Botafogo SP U20",
            "utc_date": START_Z,
            "category_id": 74,
            "tournament_id": 86166,
        },
        "markets": [
            {
                "id": 547,
                "name": "Konačan ishod",
                "metadata": {},
                "odds": [
                    {"price": 2.35, "status": 1, "display": True,
                     "metadata": {"code": "1", "name": "1"}},
                    {"price": 3.30, "status": 1, "display": True,
                     "metadata": {"code": "0", "name": "X"}},
                    {"price": 2.62, "status": 1, "display": True,
                     "metadata": {"code": "2", "name": "2"}},
                ],
            },
            {
                "id": 531,
                "name": "Dupla šansa",
                "metadata": {},
                "odds": [
                    {"price": 1.38, "status": 1, "display": True,
                     "metadata": {"code": "10", "name": "1X"}},
                    {"price": 1.24, "status": 1, "display": True,
                     "metadata": {"code": "12", "name": "12"}},
                    {"price": 1.46, "status": 1, "display": True,
                     "metadata": {"code": "02", "name": "X2"}},
                ],
            },
            {
                "id": 200734,
                "name": "Ukupno golova",
                "metadata": {},
                "odds": [
                    # 1.5 line (must be ignored - only 2.5 is in scope)
                    {"price": 4.00, "status": 1, "display": True,
                     "metadata": {"name": "Manje 1.5", "specifiers": {"total": "1.5"}}},
                    {"price": 1.16, "status": 1, "display": True,
                     "metadata": {"name": "Više 1.5", "specifiers": {"total": "1.5"}}},
                    # 2.5 line (in scope)
                    {"price": 2.15, "status": 1, "display": True,
                     "metadata": {"name": "Manje 2.5", "specifiers": {"total": "2.5"}}},
                    {"price": 1.61, "status": 1, "display": True,
                     "metadata": {"name": "Više 2.5", "specifiers": {"total": "2.5"}}},
                ],
            },
            # Off-scope market should be ignored entirely.
            {
                "id": 539,
                "name": "Oba tima daju gol (GG)",
                "metadata": {},
                "odds": [
                    {"price": 1.55, "status": 1, "display": True,
                     "metadata": {"code": "GG", "name": "GG"}},
                ],
            },
        ],
    }


def test_parse_football_event_payload_emits_three_target_markets():
    offers = _parse_football_event_payload(_football_payload(), context=_FOOTBALL_CONTEXT)

    by_key = {(o.market_type, o.outcome_code): o for o in offers}
    # 1X2 result
    assert by_key[("football_result", "home")].odds == 2.35
    assert by_key[("football_result", "home")].raw_label == "1"
    assert by_key[("football_result", "draw")].odds == 3.30
    assert by_key[("football_result", "draw")].raw_label == "X"
    assert by_key[("football_result", "away")].odds == 2.62
    assert by_key[("football_result", "away")].raw_label == "2"
    # Double chance
    assert by_key[("football_double_chance", "home_or_draw")].odds == 1.38
    assert by_key[("football_double_chance", "home_or_draw")].raw_label == "1X"
    assert by_key[("football_double_chance", "home_or_away")].odds == 1.24
    assert by_key[("football_double_chance", "home_or_away")].raw_label == "12"
    assert by_key[("football_double_chance", "draw_or_away")].odds == 1.46
    assert by_key[("football_double_chance", "draw_or_away")].raw_label == "X2"
    # Total goals 2.5 only
    assert by_key[("football_total_goals", "over")].odds == 1.61
    assert by_key[("football_total_goals", "over")].line == 2.5
    assert by_key[("football_total_goals", "over")].raw_label == "3+"
    assert by_key[("football_total_goals", "under")].odds == 2.15
    assert by_key[("football_total_goals", "under")].line == 2.5
    assert by_key[("football_total_goals", "under")].raw_label == "0-2"
    # Off-scope and off-line offers are excluded
    assert all(not (o.market_type == "football_total_goals" and o.line == 1.5) for o in offers)
    # Identity is preserved from context, not detail
    assert all(o.home_team == "Vila Nova GO U20" for o in offers)
    assert all(o.away_team == "Botafogo SP U20" for o in offers)
    assert all(o.league_id == "brazil_under_20" for o in offers)
    assert all(o.start_time == _FOOTBALL_CONTEXT.start_time for o in offers)
    assert all(o.source_url == _FOOTBALL_CONTEXT.source_url for o in offers)
    assert all(o.bookmaker_id == "superbet" for o in offers)
    assert all(o.sport == "football" for o in offers)


def test_parse_football_event_payload_skips_inactive_or_zero_odds():
    payload = _football_payload()
    payload["markets"][0]["odds"][0]["status"] = 0  # 1 = home, status off
    payload["markets"][0]["odds"][1]["display"] = False  # X = draw, hidden
    payload["markets"][2]["odds"][2]["price"] = 1.0  # under 2.5 at price 1.0

    offers = _parse_football_event_payload(payload, context=_FOOTBALL_CONTEXT)
    by_key = {(o.market_type, o.outcome_code) for o in offers}
    assert ("football_result", "home") not in by_key
    assert ("football_result", "draw") not in by_key
    assert ("football_result", "away") in by_key
    assert ("football_total_goals", "over") in by_key
    assert ("football_total_goals", "under") not in by_key


def test_parse_football_event_payload_tolerates_total_string_variants():
    """The parser must accept ``"2.50"`` / ``" 2.5 "`` as the 2.5 line."""
    payload = _football_payload()
    # Replace the 2.5 odds with cosmetic variants
    payload["markets"][2]["odds"] = [
        {"price": 2.10, "status": 1, "display": True,
         "metadata": {"name": "Manje 2.5", "specifiers": {"total": " 2.5 "}}},
        {"price": 1.65, "status": 1, "display": True,
         "metadata": {"name": "Više 2.5", "specifiers": {"total": "2.50"}}},
    ]
    offers = _parse_football_event_payload(payload, context=_FOOTBALL_CONTEXT)
    by_key = {(o.market_type, o.outcome_code): o for o in offers}
    assert by_key[("football_total_goals", "over")].line == 2.5
    assert by_key[("football_total_goals", "over")].odds == 1.65
    assert by_key[("football_total_goals", "under")].line == 2.5
    assert by_key[("football_total_goals", "under")].odds == 2.10


def test_parse_football_event_payload_uses_context_identity_not_detail():
    """B1-style invariant: identity comes from EventContext, never from
    the per-event subscription payload — even if the detail disagrees."""
    payload = _football_payload()
    # Pollute fixture with a different team name, league, and time
    payload["fixture"] = {
        "event_name": "Wrong Home·Wrong Away",
        "utc_date": "2099-12-31T23:59:59Z",
        "category_id": 999,
        "tournament_id": 999,
    }
    offers = _parse_football_event_payload(payload, context=_FOOTBALL_CONTEXT)
    assert offers, "expected non-empty offers"
    assert all(o.home_team == _FOOTBALL_CONTEXT.home_team for o in offers)
    assert all(o.away_team == _FOOTBALL_CONTEXT.away_team for o in offers)
    assert all(o.start_time == _FOOTBALL_CONTEXT.start_time for o in offers)
    assert all(o.league_id == _FOOTBALL_CONTEXT.league_id for o in offers)


def test_parse_football_event_payload_ignores_unknown_codes():
    payload = _football_payload()
    payload["markets"][0]["odds"].append(
        {"price": 5.0, "status": 1, "display": True,
         "metadata": {"code": "9", "name": "?"}},
    )
    payload["markets"][1]["odds"].append(
        {"price": 5.0, "status": 1, "display": True,
         "metadata": {"code": "00", "name": "??"}},
    )
    offers = _parse_football_event_payload(payload, context=_FOOTBALL_CONTEXT)
    # The original 3 result + 3 double chance + 2 totals (2.5) = 8 offers
    assert len(offers) == 8


def test_outcome_spec_does_not_pollute_basketball_league_list():
    """Football must not appear in get_supported_leagues() (basketball lane)."""
    scraper = SuperbetScraper(http_client=AsyncMock())
    assert scraper.get_supported_leagues() == ["basketball"]
    assert scraper.get_supported_outcome_sports() == ["football"]
    assert "football" in _OUTCOME_SPORT_SPECS


def test_extract_league_id_default_kwarg_falls_back_to_sport_scope():
    """When the raw league name normalizes to empty, the fallback must
    honour the caller-provided default — not the basketball constant.
    Otherwise a football event with a degenerate league name (e.g.
    a label that normalizes to '') would be tagged league_id='basketball'
    and split off from the rest of the football canonical pool."""
    assert _extract_league_id("", default="football") == "football"
    assert _extract_league_id(None, default="football") == "football"
    # A non-empty but normalize-to-empty label still falls back
    assert _extract_league_id("---", default="football") == "football"
    # Basketball callers (no kwarg) keep their existing behaviour
    assert _extract_league_id("") == "basketball"
    assert _extract_league_id(None) == "basketball"


@pytest.mark.asyncio
async def test_scrape_outcome_offers_only_supports_football():
    scraper = SuperbetScraper(http_client=AsyncMock())
    assert await scraper.scrape_outcome_offers("basketball") == []
    assert await scraper.scrape_outcome_offers("tennis") == []


@pytest.mark.asyncio
async def test_scrape_outcome_offers_football_end_to_end():
    """Verifies discover -> SSE -> parse pipeline for football and that
    the market-groups call is NOT made for the football lane."""

    async def fake_get_json(url: str, *, params=None, headers=None):
        del headers
        if url == _STRUCTURE_URL:
            return {
                "data": {
                    "tournaments": [
                        {"id": 86166, "localNames": {"sr-Latn-RS": "Brasileirao Serie A"}},
                    ],
                    "categories": [
                        {"id": 74, "localNames": {"sr-Latn-RS": "Brazil"}},
                    ],
                }
            }
        if url == _MARKET_GROUPS_URL.format(sport_id=5):
            raise AssertionError(
                "Football lane must not request market-groups (no group-name "
                "lookup is needed when classification is by market id)"
            )
        if url == _EVENTS_BY_DATE_URL:
            assert params is not None
            assert params["sportId"] == "5"
            assert params["offerState"] == "prematch"
            return {
                "data": [
                    {
                        "eventId": 12850987,
                        "sportId": 5,
                        "matchName": "Vila Nova GO U20·Botafogo SP U20",
                        "tournamentId": 86166,
                        "categoryId": 74,
                        "utcDate": START_Z,
                    }
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    async def fake_get_sse_json(url: str, *, params=None, headers=None, max_messages=1, read_timeout=None):
        del headers
        assert url == _EVENT_SUBSCRIPTION_URL
        assert params == {"events": "12850987"}
        assert max_messages == 1
        assert read_timeout == 10.0
        return [[_football_payload()]]

    http_client = AsyncMock()
    http_client.get_json.side_effect = fake_get_json
    http_client.get_sse_json.side_effect = fake_get_sse_json

    scraper = SuperbetScraper(http_client=http_client)
    offers = await scraper.scrape_outcome_offers("football")

    assert {o.market_type for o in offers} == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }
    assert {o.bookmaker_id for o in offers} == {"superbet"}
    assert {o.sport for o in offers} == {"football"}
    assert {o.home_team for o in offers} == {"Vila Nova GO U20"}
    assert {o.away_team for o in offers} == {"Botafogo SP U20"}
    assert {o.league_id for o in offers} == {"brasileirao_serie_a"}
    assert {o.source_url for o in offers} == {
        "https://superbet.rs/kvote/fudbal/vila-nova-go-u20-vs-botafogo-sp-u20-12850987?mdt=o"
    }
    # Total goals only at 2.5
    totals = [o for o in offers if o.market_type == "football_total_goals"]
    assert {o.line for o in totals} == {2.5}
    assert {o.outcome_code for o in totals} == {"over", "under"}

