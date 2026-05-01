from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.volcanobet_scraper import (
    FixtureContext,
    OfferBaseLookup,
    VolcanoBetScraper,
    _EVENT_MARKETS_URL,
    _FIXTURES_URL,
    _OFFER_BASE_URL,
    _SOURCE_URL,
    _build_offer_base_lookup,
    _extract_fixture_contexts,
    _extract_handicap_side,
    _parse_event_markets,
    _parse_game_handicap_ot_bet,
)

PLAYER_POINTS_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "volcanobet_player_points.json"
)
GAME_TOTALS_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "volcanobet_game_totals.json"
)
GAME_HANDICAP_OT_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "volcanobet_game_handicap_ot.json"
)


def _load_fixture(path: Path) -> list[dict]:
    with open(path) as fixture_file:
        return json.load(fixture_file)


def test_build_offer_base_lookup_discovers_basketball_market_ids_and_leagues():
    data = {
        "o": {
            "s": [
                {"i": "1", "n": "Fudbal"},
                {"i": "3", "n": "Košarka"},
            ],
            "le": [
                {"i": "64", "si": "3", "n": "NBA"},
                {"i": "248", "si": "3", "n": "Evroliga"},
                {"i": "156", "si": "1", "n": "SAD 1"},
            ],
            "m": [
                {
                    "i": "921",
                    "n": "Broj poena igrača uklj.prod.",
                    "b": [{"p": "under"}, {"p": "over"}],
                    "st": [
                        {
                            "s": "3",
                            "n": "Broj poena igrača uklj.prod.",
                            "d": "Manje ili više poena.",
                            "ml": "{0}",
                        }
                    ],
                },
                {
                    "i": "1366",
                    "n": "Broj poena igrača uklj.prod.",
                    "b": [{"p": "Yes"}, {"p": "No"}],
                    "st": [
                        {
                            "s": "3",
                            "n": "Broj poena igrača uklj.prod.",
                            "d": "Da ili Ne.",
                        }
                    ],
                },
                {
                    "i": "225",
                    "n": "Zbir poena uklj.prod.",
                    "b": [{"p": "Under"}, {"p": "Over"}],
                    "st": [
                        {
                            "s": "3",
                            "n": "Zbir poena uklj.prod.",
                            "d": "Manje ili više ukupno poena.",
                        }
                    ],
                },
            ],
        }
    }

    lookup = _build_offer_base_lookup(data)

    assert lookup.basketball_sport_id == "3"
    assert lookup.league_names == {"64": "NBA", "248": "Evroliga"}
    assert lookup.player_points_market_ids == ("921",)
    assert lookup.game_total_ot_market_ids == ("225",)


def test_extract_fixture_contexts_filters_to_upcoming_basketball_events():
    lookup = OfferBaseLookup(
        basketball_sport_id="3",
        league_names={"64": "NBA"},
        player_points_market_ids=("921",),
        game_total_ot_market_ids=("225",),
    )
    fixtures = {
        "f": [
            {
                "ai": "DETROIT123",
                "sd": "2026-04-23T01:00:00Z",
                "s": "NSY",
                "si": "3",
                "lei": "64",
                "p": [
                    {"n": "Detroit Pistons", "p": "1"},
                    {"n": "Orlando Magic", "p": "2"},
                ],
            },
            {
                "ai": "SOCCER123",
                "sd": "2026-04-23T01:30:00Z",
                "s": "NSY",
                "si": "1",
                "lei": "156",
                "p": [
                    {"n": "New York City", "p": "1"},
                    {"n": "FC Cincinnati", "p": "2"},
                ],
            },
            {
                "ai": "LIVE123",
                "sd": "2026-04-22T21:00:00Z",
                "s": "InProgress",
                "si": "3",
                "lei": "64",
                "p": [
                    {"n": "Atlanta Hawks", "p": "1"},
                    {"n": "New York Knicks", "p": "2"},
                ],
            },
            {
                "ai": "FAR123",
                "sd": "2026-05-24T23:30:00Z",
                "s": "NSY",
                "si": "3",
                "lei": "64",
                "p": [
                    {"n": "Los Angeles Lakers", "p": "1"},
                    {"n": "Denver Nuggets", "p": "2"},
                ],
            },
        ]
    }

    with patch(
        "app.scrapers.volcanobet_scraper.lookahead_cutoff",
        return_value=datetime(2026, 4, 23, 20, 0, tzinfo=timezone.utc),
    ):
        contexts = _extract_fixture_contexts(
            fixtures,
            lookup=lookup,
            now=datetime(2026, 4, 22, 20, 0, tzinfo=timezone.utc),
        )

    assert contexts == [
        FixtureContext(
            event_id="DETROIT123",
            league_id="nba",
            home_team="Detroit Pistons",
            away_team="Orlando Magic",
            start_time="2026-04-23T01:00:00+00:00",
            source_url=_SOURCE_URL,
        )
    ]


def test_parse_event_markets_returns_player_points_from_live_fixture():
    payload = _load_fixture(PLAYER_POINTS_FIXTURE_PATH)[0]
    context = FixtureContext(
        event_id="CD681B482F827368DB4831517ECC1039",
        league_id="nba",
        home_team="Detroit Pistons",
        away_team="Orlando Magic",
        start_time="2026-04-22T23:00:00+00:00",
        source_url=_SOURCE_URL,
    )

    results = _parse_event_markets(
        payload,
        context=context,
        player_points_market_ids={"921", "2446", "custom2"},
        game_total_ot_market_ids=set(),
    )

    assert len(results) == 30
    by_key = {
        (row.market_type, row.player_name, row.threshold): row
        for row in results
    }

    cade_line = by_key[("player_points", "C.Cunningham", 28.5)]
    assert cade_line.bookmaker_id == "volcanobet"
    assert cade_line.home_team == "Detroit Pistons"
    assert cade_line.away_team == "Orlando Magic"
    assert cade_line.over_odds == 1.95
    assert cade_line.under_odds == 1.85
    assert all(row.market_type == "player_points" for row in results)


def test_parse_event_markets_returns_ot_game_totals_from_live_fixture():
    payload = _load_fixture(GAME_TOTALS_FIXTURE_PATH)[0]
    context = FixtureContext(
        event_id="CD681B482F827368DB4831517ECC1039",
        league_id="nba",
        home_team="Detroit Pistons",
        away_team="Orlando Magic",
        start_time="2026-04-22T23:00:00+00:00",
        source_url=_SOURCE_URL,
    )

    results = _parse_event_markets(
        payload,
        context=context,
        player_points_market_ids=set(),
        game_total_ot_market_ids={"225"},
    )

    assert len(results) == 13
    by_key = {
        (row.market_type, row.player_name, row.threshold): row
        for row in results
    }

    total_line = by_key[("game_total_ot", None, 218.5)]
    assert total_line.over_odds == 1.9
    assert total_line.under_odds == 1.85
    assert all(row.player_name is None for row in results)


@pytest.mark.asyncio
async def test_scrape_odds_batches_event_ids_and_ignores_empty_market_sets():
    offer_base = {
        "o": {
            "s": [{"i": "3", "n": "Košarka"}],
            "le": [{"i": "64", "si": "3", "n": "NBA"}],
            "m": [
                {
                    "i": "921",
                    "n": "Broj poena igrača uklj.prod.",
                    "b": [{"p": "under"}, {"p": "over"}],
                    "st": [{"s": "3", "n": "Broj poena igrača uklj.prod.", "ml": "{0}"}],
                },
                {
                    "i": "225",
                    "n": "Zbir poena uklj.prod.",
                    "b": [{"p": "under"}, {"p": "over"}],
                    "st": [{"s": "3", "n": "Zbir poena uklj.prod."}],
                },
            ],
        }
    }
    fixtures = {
        "f": [
            {
                "ai": "DETROIT123",
                "sd": "2026-04-23T01:00:00Z",
                "s": "NSY",
                "si": "3",
                "lei": "64",
                "p": [
                    {"n": "Detroit Pistons", "p": "1"},
                    {"n": "Orlando Magic", "p": "2"},
                ],
            },
            {
                "ai": "HAWKS123",
                "sd": "2026-04-23T03:00:00Z",
                "s": "NSY",
                "si": "3",
                "lei": "64",
                "p": [
                    {"n": "Atlanta Hawks", "p": "1"},
                    {"n": "New York Knicks", "p": "2"},
                ],
            },
        ]
    }
    batch_payload = [
        {
            "e": "DETROIT123",
            "m": [
                {
                    "id": "921",
                    "d": 0,
                    "b": [
                        {
                            "id": "under",
                            "bl": "28.5",
                            "pid": "cade-1",
                            "od": 1.85,
                            "s": "O",
                            "n": "Manje",
                            "pn": "C.Cunningham",
                        },
                        {
                            "id": "over",
                            "bl": "28.5",
                            "pid": "cade-1",
                            "od": 1.95,
                            "s": "O",
                            "n": "Više",
                            "pn": "C.Cunningham",
                        },
                    ],
                },
                {
                    "id": "225",
                    "d": 0,
                    "b": [
                        {
                            "id": "under",
                            "bl": "218.5",
                            "od": 1.85,
                            "s": "O",
                            "n": "Manje",
                        },
                        {
                            "id": "over",
                            "bl": "218.5",
                            "od": 1.9,
                            "s": "O",
                            "n": "Više",
                        },
                    ],
                },
            ],
            "pc": 0,
            "a": "2026-04-22T20:00:00Z",
        },
        {
            "e": "HAWKS123",
            "m": [],
            "pc": 0,
            "a": "2026-04-22T20:00:00Z",
        },
    ]

    async def mock_get_json(url, *, params=None, headers=None):
        if url == _OFFER_BASE_URL:
            return offer_base
        if url == _FIXTURES_URL:
            return fixtures
        assert url.startswith(_EVENT_MARKETS_URL)
        assert "eventIds=DETROIT123" in url
        assert "eventIds=HAWKS123" in url
        assert "marketIds=921" in url
        assert "marketIds=225" in url
        assert params is None
        return batch_payload

    http_client = AsyncMock()
    http_client.get_json.side_effect = mock_get_json
    scraper = VolcanoBetScraper(http_client=http_client)

    with patch(
        "app.scrapers.volcanobet_scraper.current_utc_time",
        return_value=datetime(2026, 4, 22, 20, 0, tzinfo=timezone.utc),
    ):
        results = await scraper.scrape_odds("basketball")

    by_key = {
        (row.market_type, row.player_name, row.threshold): row
        for row in results
    }
    assert len(results) == 2
    assert by_key[("player_points", "C.Cunningham", 28.5)].over_odds == 1.95
    assert by_key[("game_total_ot", None, 218.5)].under_odds == 1.85


# ── Game handicap (incl. OT) parsing ──────────────────────


def test_extract_handicap_side_maps_team_codes():
    assert _extract_handicap_side("1") == "over"
    assert _extract_handicap_side("2") == "under"
    assert _extract_handicap_side(" 1 ") == "over"
    assert _extract_handicap_side("3") is None
    assert _extract_handicap_side("over") is None
    assert _extract_handicap_side(None) is None
    assert _extract_handicap_side("") is None


def test_parse_game_handicap_ot_bet_negative_line_means_home_favoured():
    bet = {"id": "1", "bl": "-3.5", "od": 1.9, "s": "O", "n": "1"}
    parsed = _parse_game_handicap_ot_bet(bet)
    assert parsed is not None
    assert parsed.market_type == "home_handicap_ot"
    assert parsed.threshold == 3.5
    assert parsed.side == "over"
    assert parsed.odd_value == 1.9
    assert parsed.player_name is None
    assert parsed.participant_id is None


def test_parse_game_handicap_ot_bet_positive_line_means_home_underdog():
    bet = {"id": "1", "bl": "+9.5", "od": 1.35, "n": "1"}
    parsed = _parse_game_handicap_ot_bet(bet)
    assert parsed is not None
    assert parsed.threshold == -9.5
    assert parsed.side == "over"


def test_parse_game_handicap_ot_bet_team2_maps_to_under():
    bet = {"id": "2", "bl": "-3.5", "od": 1.9, "n": "2"}
    parsed = _parse_game_handicap_ot_bet(bet)
    assert parsed is not None
    assert parsed.threshold == 3.5
    assert parsed.side == "under"


def test_parse_game_handicap_ot_bet_pickem_zero_line():
    bet_team1 = {"id": "1", "bl": "0", "od": 1.9, "n": "1"}
    bet_team2 = {"id": "2", "bl": "0", "od": 1.9, "n": "2"}
    parsed1 = _parse_game_handicap_ot_bet(bet_team1)
    parsed2 = _parse_game_handicap_ot_bet(bet_team2)
    assert parsed1 is not None and parsed1.threshold == 0.0
    assert parsed2 is not None and parsed2.threshold == 0.0


def test_parse_game_handicap_ot_bet_skips_when_required_field_missing():
    assert _parse_game_handicap_ot_bet({"id": "1", "od": 1.9}) is None
    assert _parse_game_handicap_ot_bet({"id": "1", "bl": "-3.5"}) is None
    assert _parse_game_handicap_ot_bet({"bl": "-3.5", "od": 1.9}) is None
    assert _parse_game_handicap_ot_bet({"id": "1", "bl": "abc", "od": 1.9}) is None
    assert _parse_game_handicap_ot_bet({"id": "1", "bl": "-3.5", "od": "n/a"}) is None


def test_parse_event_markets_returns_handicap_from_live_fixture():
    payload = _load_fixture(GAME_HANDICAP_OT_FIXTURE_PATH)[0]
    context = FixtureContext(
        event_id="C47A889B26E067D0084BF84B5845544B",
        league_id="nba",
        home_team="Houston Rockets",
        away_team="L.A.Lakers",
        start_time="2026-05-02T03:30:00+00:00",
        source_url=_SOURCE_URL,
    )

    results = _parse_event_markets(
        payload,
        context=context,
        player_points_market_ids=set(),
        game_total_ot_market_ids={"225"},
        game_handicap_ot_market_ids={"223"},
    )

    handicap_rows = [r for r in results if r.market_type == "home_handicap_ot"]
    total_rows = [r for r in results if r.market_type == "game_total_ot"]

    assert len(handicap_rows) == 13
    assert len(total_rows) >= 1
    by_threshold = {row.threshold: row for row in handicap_rows}
    balanced = by_threshold[3.5]
    assert balanced.over_odds == 1.9
    assert balanced.under_odds == 1.9
    assert balanced.home_team == "Houston Rockets"
    assert balanced.away_team == "L.A.Lakers"
    assert all(r.player_name is None for r in handicap_rows)


def test_parse_event_markets_does_not_mix_handicap_and_totals():
    payload = _load_fixture(GAME_HANDICAP_OT_FIXTURE_PATH)[0]
    context = FixtureContext(
        event_id="C47A889B26E067D0084BF84B5845544B",
        league_id="nba",
        home_team="Houston Rockets",
        away_team="L.A.Lakers",
        start_time="2026-05-02T03:30:00+00:00",
        source_url=_SOURCE_URL,
    )

    results = _parse_event_markets(
        payload,
        context=context,
        player_points_market_ids=set(),
        game_total_ot_market_ids={"225"},
    )
    assert all(r.market_type == "game_total_ot" for r in results)
    assert results, "totals from the same fixture should still be parsed"


def test_parse_event_markets_does_not_mix_totals_and_handicap_inverse():
    payload = _load_fixture(GAME_HANDICAP_OT_FIXTURE_PATH)[0]
    context = FixtureContext(
        event_id="C47A889B26E067D0084BF84B5845544B",
        league_id="nba",
        home_team="Houston Rockets",
        away_team="L.A.Lakers",
        start_time="2026-05-02T03:30:00+00:00",
        source_url=_SOURCE_URL,
    )

    results = _parse_event_markets(
        payload,
        context=context,
        player_points_market_ids=set(),
        game_total_ot_market_ids=set(),
        game_handicap_ot_market_ids={"223"},
    )
    assert all(r.market_type == "home_handicap_ot" for r in results)


# ── OfferBase handicap discovery ─────────────────────────


def test_build_offer_base_lookup_discovers_handicap_market_id():
    data = {
        "o": {
            "s": [{"i": "3", "n": "Košarka"}],
            "le": [{"i": "64", "si": "3", "n": "NBA"}],
            "m": [
                {
                    "i": "223",
                    "n": "Handicap (incl. overtime)",
                    "b": [{"p": "1"}, {"p": "2"}],
                    "st": [{"s": "3", "n": "Hendikep uklj.prod."}],
                },
                {
                    "i": "9999",
                    "n": "Handicap (other sport)",
                    "b": [{"p": "1"}, {"p": "2"}],
                    "st": [{"s": "1", "n": "Hendikep uklj.prod."}],
                },
                {
                    "i": "8888",
                    "n": "Handicap (incl. overtime) 1x2",
                    "b": [{"p": "1"}, {"p": "x"}, {"p": "2"}],
                    "st": [{"s": "3", "n": "Hendikep uklj.prod."}],
                },
            ],
        }
    }
    lookup = _build_offer_base_lookup(data)
    assert lookup.basketball_sport_id == "3"
    assert lookup.game_handicap_ot_market_ids == ("223",)


def test_build_offer_base_lookup_falls_back_to_static_handicap_id_when_missing():
    data = {"o": {"s": [{"i": "3", "n": "Košarka"}], "le": [], "m": []}}
    lookup = _build_offer_base_lookup(data)
    assert lookup.game_handicap_ot_market_ids == ("223",)


@pytest.mark.asyncio
async def test_scrape_odds_includes_handicap_market_id_in_event_filter():
    offer_base = {
        "o": {
            "s": [{"i": "3", "n": "Košarka"}],
            "le": [{"i": "64", "si": "3", "n": "NBA"}],
            "m": [
                {
                    "i": "921",
                    "n": "Broj poena igrača uklj.prod.",
                    "b": [{"p": "under"}, {"p": "over"}],
                    "st": [{"s": "3", "n": "Broj poena igrača uklj.prod.", "ml": "{0}"}],
                },
                {
                    "i": "225",
                    "n": "Zbir poena uklj.prod.",
                    "b": [{"p": "under"}, {"p": "over"}],
                    "st": [{"s": "3", "n": "Zbir poena uklj.prod."}],
                },
                {
                    "i": "223",
                    "n": "Handicap (incl. overtime)",
                    "b": [{"p": "1"}, {"p": "2"}],
                    "st": [{"s": "3", "n": "Hendikep uklj.prod."}],
                },
            ],
        }
    }
    fixtures = {
        "f": [
            {
                "ai": "EVT1",
                "sd": "2026-04-23T01:00:00Z",
                "s": "NSY",
                "si": "3",
                "lei": "64",
                "p": [
                    {"n": "Houston Rockets", "p": "1"},
                    {"n": "L.A.Lakers", "p": "2"},
                ],
            },
        ]
    }
    batch_payload = [
        {
            "e": "EVT1",
            "m": [
                {
                    "id": "223",
                    "d": 0,
                    "b": [
                        {"id": "1", "bl": "-3.5", "od": 1.9, "s": "O", "n": "1"},
                        {"id": "2", "bl": "-3.5", "od": 1.9, "s": "O", "n": "2"},
                    ],
                },
            ],
            "pc": 0,
            "a": "2026-04-22T20:00:00Z",
        },
    ]
    captured_urls: list[str] = []

    async def mock_get_json(url, *, params=None, headers=None):
        if url == _OFFER_BASE_URL:
            return offer_base
        if url == _FIXTURES_URL:
            return fixtures
        captured_urls.append(url)
        assert url.startswith(_EVENT_MARKETS_URL)
        return batch_payload

    http_client = AsyncMock()
    http_client.get_json.side_effect = mock_get_json
    scraper = VolcanoBetScraper(http_client=http_client)

    with patch(
        "app.scrapers.volcanobet_scraper.current_utc_time",
        return_value=datetime(2026, 4, 22, 20, 0, tzinfo=timezone.utc),
    ):
        results = await scraper.scrape_odds("basketball")

    assert len(captured_urls) == 1
    event_markets_url = captured_urls[0]
    assert "marketIds=921" in event_markets_url
    assert "marketIds=225" in event_markets_url
    assert "marketIds=223" in event_markets_url

    assert len(results) == 1
    assert results[0].market_type == "home_handicap_ot"
    assert results[0].threshold == 3.5
    assert results[0].over_odds == 1.9
    assert results[0].under_odds == 1.9
    assert results[0].home_team == "Houston Rockets"
    assert results[0].away_team == "L.A.Lakers"
