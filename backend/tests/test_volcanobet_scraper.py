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
    _parse_event_markets,
)

PLAYER_POINTS_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "volcanobet_player_points.json"
)
GAME_TOTALS_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "volcanobet_game_totals.json"
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
