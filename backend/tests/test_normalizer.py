from __future__ import annotations

import pytest

from app.services.normalizer import (
    generate_match_id,
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
