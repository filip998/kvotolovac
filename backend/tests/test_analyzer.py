from __future__ import annotations

import pytest

from app.models.schemas import NormalizedOdds
from app.services.analyzer import Discrepancy, analyze, find_threshold_gaps, _profit_margin


def _make_odds(
    bookmaker: str, player: str, threshold: float,
    over: float = 1.85, under: float = 1.95,
    match_id: str = "m1",
    market_type: str = "player_points",
) -> NormalizedOdds:
    return NormalizedOdds(
        match_id=match_id,
        bookmaker_id=bookmaker,
        league_id="euroleague",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type=market_type,
        player_name=player,
        threshold=threshold,
        over_odds=over,
        under_odds=under,
    )


def test_threshold_gap_detection():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, over=1.85, under=1.95),
        _make_odds("meridian", "Lundberg", 18.5, over=1.80, under=2.00),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    assert discs[0].gap == 2.0
    assert discs[0].bookmaker_a_id == "mozzart"
    assert discs[0].bookmaker_b_id == "meridian"
    assert discs[0].threshold_a == 16.5
    assert discs[0].threshold_b == 18.5
    assert discs[0].odds_a == 1.85  # over from lower threshold
    assert discs[0].odds_b == 2.00  # under from higher threshold


def test_threshold_gap_detection_with_one_sided_lower_line():
    odds = [
        _make_odds(
            "oktagonbet",
            "Lundberg",
            9.5,
            over=1.90,
            under=None,
            market_type="player_points_milestones",
        ),
        _make_odds("mozzart", "Lundberg", 12.5, over=1.80, under=1.90),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    assert discs[0].gap == 3.0
    assert discs[0].bookmaker_a_id == "oktagonbet"
    assert discs[0].bookmaker_b_id == "mozzart"
    assert discs[0].threshold_a == 9.5
    assert discs[0].threshold_b == 12.5
    assert discs[0].odds_a == 1.90
    assert discs[0].odds_b == 1.90


def test_higher_one_sided_line_does_not_create_false_gap():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, over=1.85, under=1.95),
        _make_odds(
            "oktagonbet",
            "Lundberg",
            19.5,
            over=1.90,
            under=None,
            market_type="player_points_milestones",
        ),
    ]
    discs = find_threshold_gaps(odds)
    assert discs == []


def test_same_threshold_value_difference():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, over=1.85, under=1.95),
        _make_odds("meridian", "Lundberg", 16.5, over=1.95, under=1.85),
    ]
    discs = find_threshold_gaps(odds)
    # Should detect a value difference (0.10 diff in over odds)
    assert len(discs) >= 1
    assert discs[0].gap == 0.0


def test_min_gap_filter():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5),
        _make_odds("meridian", "Lundberg", 17.5),
    ]
    discs = find_threshold_gaps(odds, min_gap=2.0)
    assert len(discs) == 0

    discs = find_threshold_gaps(odds, min_gap=0.5)
    assert len(discs) == 1


def test_no_discrepancy_single_bookmaker():
    odds = [_make_odds("mozzart", "Lundberg", 16.5)]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 0


def test_three_bookmakers_multiple_pairs():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5),
        _make_odds("meridian", "Lundberg", 18.5),
        _make_odds("maxbet", "Lundberg", 17.5),
    ]
    discs = find_threshold_gaps(odds)
    # 3 choose 2 = 3 pairs, all with different thresholds
    assert len(discs) == 3


def test_different_players_no_cross_detection():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5),
        _make_odds("meridian", "Petrusev", 18.5),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 0


def test_different_matches_no_cross_detection():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, match_id="m1"),
        _make_odds("meridian", "Lundberg", 18.5, match_id="m2"),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 0


def test_profit_margin_calculation():
    # Odds 1.85 and 2.00 → 1/1.85 + 1/2.00 = 0.5405 + 0.5 = 1.0405
    # Margin = 1 - 1.0405 = -0.0405 (no arbitrage)
    margin = _profit_margin(1.85, 2.00)
    assert margin is not None
    assert margin < 0

    # Odds 2.10 and 2.10 → margin = 1 - (1/2.10 + 1/2.10) = 1 - 0.952 = 0.048
    margin = _profit_margin(2.10, 2.10)
    assert margin is not None
    assert margin > 0


def test_profit_margin_none_for_missing_odds():
    assert _profit_margin(None, 2.0) is None
    assert _profit_margin(2.0, None) is None
    assert _profit_margin(0.0, 2.0) is None


def test_analyze_entrypoint():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5),
        _make_odds("meridian", "Lundberg", 18.5),
    ]
    discs = analyze(odds)
    assert len(discs) >= 1
