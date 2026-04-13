from __future__ import annotations

import pytest

from app.services.normalizer import (
    generate_match_id,
    normalize_league_id,
    normalize_market_type,
    normalize_odds,
    normalize_player_name,
    normalize_team_name,
)
from app.models.schemas import RawOddsData


def test_normalize_team_exact():
    assert normalize_team_name("Olympiacos") == "Olympiacos"
    assert normalize_team_name("olympiacos") == "Olympiacos"


def test_normalize_team_alias():
    assert normalize_team_name("Red Star") == "Crvena Zvezda"
    assert normalize_team_name("barcelona") == "FC Barcelona"
    assert normalize_team_name("Houston", "nba") == "Houston Rockets"
    assert normalize_team_name("Minnesota", "nba") == "Minnesota Timberwolves"
    assert normalize_team_name("Crv.Zvezda") == "Crvena Zvezda"
    assert normalize_team_name("Cluj Napoc") == "Universitatea Cluj"
    assert normalize_team_name("Budućnost") == "Buducnost"
    assert normalize_team_name("KK Crvena Zvezda") == "Crvena Zvezda"


def test_normalize_team_nba_aliases_are_league_scoped():
    assert normalize_team_name("Houston") == "Houston"
    assert normalize_team_name("Houston", "euroleague") == "Houston"
    assert normalize_team_name("Houston", "nba") == "Houston Rockets"


def test_normalize_team_fuzzy():
    assert normalize_team_name("Olympiakos") == "Olympiacos"


def test_normalize_player_full_name():
    assert normalize_player_name("Sasha Vezenkov") == "Sasha Vezenkov"


def test_normalize_player_abbreviated():
    assert normalize_player_name("S. Vezenkov") == "Sasha Vezenkov"
    assert normalize_player_name("Vezenkov S.") == "Sasha Vezenkov"


def test_normalize_player_initial_format():
    assert normalize_player_name("F. Campazzo") == "Facundo Campazzo"
    assert normalize_player_name("Campazzo F.") == "Facundo Campazzo"
    assert normalize_player_name("K.Durant") == "Kevin Durant"


def test_normalize_odds_does_not_overresolve_double_initial_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="Cameron McCollum",
            threshold=14.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=21.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Cameron McCollum",
        "C.J. McCollum",
    ]


def test_normalize_odds_keeps_ambiguous_single_initial_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jalen Williams",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City",
            away_team="J. Williams",
            market_type="player_points",
            player_name="J. Williams",
            threshold=17.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Jalen Williams",
        "J. Williams",
    ]


def test_normalize_odds_resolves_supported_single_initial_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Phoenix Suns",
            away_team="Denver Nuggets",
            market_type="player_points",
            player_name="Colin Gillespie",
            threshold=7.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Phoenix",
            away_team="Denver",
            market_type="player_assists",
            player_name="Colin Gillespie",
            threshold=2.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Phoenix Suns",
            away_team="Denver Nuggets",
            market_type="player_points",
            player_name="C. Gillespie",
            threshold=7.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Colin Gillespie",
        "Colin Gillespie",
        "Colin Gillespie",
    ]


def test_normalize_odds_keeps_single_initial_separate_from_multi_initial_candidates():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=21.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_assists",
            player_name="C.J. McCollum",
            threshold=4.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=20.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "C.J. McCollum",
        "C.J. McCollum",
        "C. McCollum",
    ]


def test_normalize_odds_keeps_ambiguous_short_prefix_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jalen Williams",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_assists",
            player_name="Jalen Williams",
            threshold=5.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="oktagon",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jaylin Williams",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Ja. Williams",
            threshold=17.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Jalen Williams",
        "Jalen Williams",
        "Jaylin Williams",
        "Ja. Williams",
    ]


def test_normalize_odds_resolves_unique_match_local_player_variants():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Aaron Wiggins",
            threshold=2.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Aar.Wiggins",
            threshold=2.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Jalen Green",
            threshold=4.5,
            over_odds=1.95,
            under_odds=1.75,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Jal.Green",
            threshold=4.5,
            over_odds=1.9,
            under_odds=1.8,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Mark Williams",
            threshold=7.5,
            over_odds=1.88,
            under_odds=1.92,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Mar.Williams",
            threshold=7.5,
            over_odds=1.82,
            under_odds=1.98,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Aaron Wiggins",
        "Aaron Wiggins",
        "Jalen Green",
        "Jalen Green",
        "Mark Williams",
        "Mark Williams",
    ]


def test_normalize_odds_prefers_more_supported_full_name_variant():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Aaron Wiggins",
            threshold=2.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_points",
            player_name="Aaron Wiggins",
            threshold=9.5,
            over_odds=1.9,
            under_odds=1.8,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Arron Wiggins",
            threshold=2.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Aaron Wiggins",
        "Aaron Wiggins",
        "Aaron Wiggins",
    ]


def test_normalize_player_none():
    assert normalize_player_name(None) is None
    assert normalize_player_name("") is None


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [
        ("player_points", "player_points"),
        ("Player Points", "player_points"),
        ("points", "player_points"),
        ("player_rebounds", "player_rebounds"),
        ("Player Rebounds", "player_rebounds"),
        ("player_assists", "player_assists"),
        ("Player Assists", "player_assists"),
        ("player_3points", "player_3points"),
        ("Player 3 Points", "player_3points"),
        ("player_steals", "player_steals"),
        ("Player Steals", "player_steals"),
        ("player_blocks", "player_blocks"),
        ("Player Blocks", "player_blocks"),
        ("player_points_rebounds", "player_points_rebounds"),
        ("Player Points + Rebounds", "player_points_rebounds"),
        ("Points+Rebounds", "player_points_rebounds"),
        ("player_points_assists", "player_points_assists"),
        ("Player Points & Assists", "player_points_assists"),
        ("player_rebounds_assists", "player_rebounds_assists"),
        ("Player Rebounds + Assists", "player_rebounds_assists"),
        ("player_points_rebounds_assists", "player_points_rebounds_assists"),
        ("Player Points + Rebounds + Assists", "player_points_rebounds_assists"),
        ("PRA", "player_points_rebounds_assists"),
        ("player_points_milestones", "player_points_milestones"),
        ("Player Points Milestones", "player_points_milestones"),
        ("player_points_ladder", "player_points_milestones"),
        ("game_total", "game_total"),
        ("Game Total", "game_total"),
        ("total", "game_total"),
    ],
)
def test_normalize_market_type(raw_type, expected):
    assert normalize_market_type(raw_type) == expected


def test_generate_match_id_deterministic():
    id1 = generate_match_id("Partizan", "Crvena Zvezda", "euroleague")
    id2 = generate_match_id("Partizan", "Crvena Zvezda", "euroleague")
    assert id1 == id2


def test_generate_match_id_unique():
    id1 = generate_match_id("Partizan", "Crvena Zvezda", "euroleague")
    id2 = generate_match_id("Partizan", "Real Madrid", "euroleague")
    assert id1 != id2


def test_normalize_league_id_alias():
    assert normalize_league_id("usa-nba") == "nba"
    assert normalize_league_id("USA NBA") == "nba"
    assert normalize_league_id("usa_nba") == "nba"
    assert normalize_league_id("aba liga - winners stage") == "aba_liga"
    assert normalize_league_id("AdmiralBet ABA liga - plej of") == "aba_liga"
    assert normalize_league_id("italija_1") == "italy"
    assert normalize_league_id("Germany BBL") == "germany"
    assert normalize_league_id("euroleague") == "euroleague"


def test_normalize_odds_full_pipeline():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Red Star",  # alias
            market_type="player_points",
            player_name="I. Lundberg",  # abbreviated
            threshold=18.5,
            over_odds=1.80,
            under_odds=2.00,
        ),
    ]
    normalized = normalize_odds(raw)
    assert len(normalized) == 2
    # Both should map to the same match_id
    assert normalized[0].match_id == normalized[1].match_id
    # Both should resolve to canonical player name
    assert normalized[0].player_name == "Iffe Lundberg"
    assert normalized[1].player_name == "Iffe Lundberg"
    # Away team should be normalized
    assert normalized[1].away_team == "Crvena Zvezda"


def test_normalize_odds_resolves_shared_platform_matchups_and_aliases():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Houston",
            away_team="Minnesota",
            market_type="player_points",
            player_name="K.Durant",
            threshold=24.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="usa-nba",
            home_team="Houston Rockets",
            away_team="Minnesota Timberwolves",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=25.5,
            over_odds=1.88,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Houston",
            away_team="Kevin Durant",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=23.5,
            over_odds=1.58,
            under_odds=2.2,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="oktagonbet",
            league_id="nba",
            home_team="Houston Rockets",
            away_team="Kevin Durant",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=23.5,
            over_odds=1.6,
            under_odds=2.1,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert len(normalized) == 4
    assert len({offer.match_id for offer in normalized}) == 1
    assert {offer.league_id for offer in normalized} == {"nba"}
    assert {offer.home_team for offer in normalized} == {"Houston Rockets"}
    assert {offer.away_team for offer in normalized} == {"Minnesota Timberwolves"}
    assert {offer.player_name for offer in normalized} == {"Kevin Durant"}


def test_normalize_odds_drops_unresolved_shared_platform_rows():
    raw = [
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Houston",
            away_team="Kevin Durant",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=23.5,
            over_odds=1.58,
            under_odds=2.2,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    assert normalize_odds(raw) == []


def test_normalize_odds_uses_deterministic_match_orientation():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Minnesota",
            away_team="Houston",
            market_type="player_points",
            player_name="K.Durant",
            threshold=24.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="usa-nba",
            home_team="Houston Rockets",
            away_team="Minnesota Timberwolves",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=25.5,
            over_odds=1.88,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Houston",
            away_team="Kevin Durant",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=23.5,
            over_odds=1.58,
            under_odds=2.2,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert len(normalized) == 3
    assert len({offer.match_id for offer in normalized}) == 1
    assert {offer.home_team for offer in normalized} == {"Houston Rockets"}
    assert {offer.away_team for offer in normalized} == {"Minnesota Timberwolves"}


def test_normalize_odds_normalizes_new_market_types():
    raw = [
        RawOddsData(
            bookmaker_id="oktagonbet",
            league_id="euroleague",
            home_team="Olympiakos",
            away_team="Barcelona",
            market_type="Player Points + Rebounds",
            player_name="S. Vezenkov",
            threshold=26.5,
            over_odds=1.83,
            under_odds=1.97,
        ),
    ]

    normalized = normalize_odds(raw)

    assert normalized[0].market_type == "player_points_rebounds"
    assert normalized[0].home_team == "Olympiacos"
    assert normalized[0].away_team == "FC Barcelona"
    assert normalized[0].player_name == "Sasha Vezenkov"


def test_normalize_odds_resolves_prefix_player_variants_with_shared_matchup():
    raw = [
        RawOddsData(
            bookmaker_id="pinnbet",
            league_id="aba_liga",
            home_team="Crv.Zvezda",
            away_team="Cluj Napoc",
            market_type="player_points",
            player_name="Ja.Butler",
            threshold=11.5,
            over_odds=1.45,
            under_odds=2.5,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="Jar.Butler",
            threshold=13.5,
            over_odds=1.82,
            under_odds=1.97,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="oktagonbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="Jared Butler",
            threshold=15.5,
            over_odds=2.30,
            under_odds=1.53,
            start_time="2026-04-13T16:00:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Jared Butler",
        "Jared Butler",
        "Jared Butler",
    ]
    assert {offer.home_team for offer in normalized} == {"Crvena Zvezda"}
    assert {offer.away_team for offer in normalized} == {"Universitatea Cluj"}


def test_normalize_odds_merges_hyphen_and_space_surnames():
    raw = [
        RawOddsData(
            bookmaker_id="pinnbet",
            league_id="aba_liga",
            home_team="Dubai",
            away_team="Buducnost",
            market_type="player_points",
            player_name="Codi Miller-McIntyre",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T18:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="aba_liga",
            home_team="Dubai",
            away_team="Budućnost",
            market_type="player_points",
            player_name="Codi Miller McIntyre",
            threshold=12.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-13T18:00:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Codi Miller-McIntyre",
        "Codi Miller-McIntyre",
    ]


def test_normalize_odds_prefers_full_name_over_initial_and_diacritic_variants():
    raw = [
        RawOddsData(
            bookmaker_id="pinnbet",
            league_id="aba_liga",
            home_team="Crv.Zvezda",
            away_team="Cluj Napoc",
            market_type="player_points",
            player_name="S.Miljenovic",
            threshold=9.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="S. Miljenović",
            threshold=11.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="oktagonbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="Stefan Miljenović",
            threshold=13.5,
            over_odds=1.7,
            under_odds=2.1,
            start_time="2026-04-13T16:00:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Stefan Miljenović",
        "Stefan Miljenović",
        "Stefan Miljenović",
    ]


def test_normalize_preserves_thresholds():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
    ]
    normalized = normalize_odds(raw)
    assert normalized[0].threshold == 16.5
    assert normalized[0].over_odds == 1.85
