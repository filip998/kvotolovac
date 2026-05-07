from __future__ import annotations

import pytest

from app.models.schemas import NormalizedOdds
from app.services.analyzer import (
    Discrepancy,
    _middle_profit_margin,
    _profit_margin,
    analyze,
    find_threshold_gaps,
)


def _make_odds(
    bookmaker: str, player: str | None, threshold: float,
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
        _make_odds("meridian", "Lundberg", 19.5, over=1.80, under=2.00),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    assert discs[0].gap == 3.0
    assert discs[0].bookmaker_a_id == "mozzart"
    assert discs[0].bookmaker_b_id == "meridian"
    assert discs[0].threshold_a == 16.5
    assert discs[0].threshold_b == 19.5
    assert discs[0].odds_a == 1.85  # over from lower threshold
    assert discs[0].odds_b == 2.00  # under from higher threshold
    assert discs[0].middle_profit_margin is not None
    assert discs[0].middle_profit_margin > 0


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
    # Negative-margin same-threshold pairs are filtered out
    assert len(discs) == 0


def test_same_threshold_positive_margin_emitted():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, over=1.85, under=2.20),
        _make_odds("meridian", "Lundberg", 16.5, over=2.20, under=1.85),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    assert discs[0].gap == 0.0
    assert discs[0].profit_margin is not None
    assert discs[0].profit_margin > 0
    assert discs[0].middle_profit_margin is None


def test_same_threshold_cross_book_best_combo():
    """Picks the better cross-book combo, not worst-under."""
    odds = [
        _make_odds("a", "Lundberg", 16.5, over=2.05, under=2.15),
        _make_odds("b", "Lundberg", 16.5, over=2.15, under=1.70),
    ]
    discs = find_threshold_gaps(odds)
    # b.over + a.under = 2.15 + 2.15 → profitable
    assert len(discs) == 1
    assert discs[0].profit_margin is not None
    assert discs[0].profit_margin > 0
    assert discs[0].bookmaker_a_id == "b"  # over from b
    assert discs[0].odds_a == 2.15
    assert discs[0].odds_b == 2.15  # under from a


def test_min_gap_filter():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5),
        _make_odds("meridian", "Lundberg", 19.5),
    ]
    discs = find_threshold_gaps(odds, min_gap=4.0)
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
        _make_odds("meridian", "Lundberg", 19.5),
        _make_odds("maxbet", "Lundberg", 22.5),
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


def test_game_totals_group_without_player_name():
    odds = [
        _make_odds(
            "mozzart",
            None,
            156.5,
            over=1.85,
            under=1.90,
            market_type="game_total",
        ),
        _make_odds(
            "meridian",
            None,
            159.5,
            over=1.80,
            under=2.00,
            market_type="game_total",
        ),
    ]

    discs = find_threshold_gaps(odds)

    assert len(discs) == 1
    assert discs[0].market_type == "game_total"
    assert discs[0].player_name is None
    assert discs[0].gap == 3.0
    assert discs[0].bookmaker_a_id == "mozzart"
    assert discs[0].bookmaker_b_id == "meridian"


def test_game_total_ot_group_without_player_name():
    odds = [
        _make_odds(
            "maxbet",
            None,
            167.5,
            over=1.90,
            under=1.87,
            market_type="game_total_ot",
        ),
        _make_odds(
            "meridian",
            None,
            170.5,
            over=1.82,
            under=1.95,
            market_type="game_total_ot",
        ),
    ]

    discs = find_threshold_gaps(odds)

    assert len(discs) == 1
    assert discs[0].market_type == "game_total_ot"
    assert discs[0].player_name is None
    assert discs[0].gap == 3.0
    assert discs[0].bookmaker_a_id == "maxbet"
    assert discs[0].bookmaker_b_id == "meridian"


def test_game_total_ot_does_not_cross_with_regular_time_game_total():
    odds = [
        _make_odds(
            "maxbet",
            None,
            167.5,
            over=1.90,
            under=1.87,
            market_type="game_total_ot",
        ),
        _make_odds(
            "meridian",
            None,
            169.5,
            over=1.82,
            under=1.95,
            market_type="game_total",
        ),
    ]

    assert find_threshold_gaps(odds) == []


def test_fitted_ev_can_reject_raw_gap_game_total_middle():
    odds = [
        _make_odds(
            "maxbet",
            None,
            214.5,
            over=1.85,
            under=1.92,
            match_id="m2",
            market_type="game_total",
        ),
        _make_odds(
            "maxbet",
            None,
            217.5,
            over=1.90,
            under=1.87,
            match_id="m2",
            market_type="game_total",
        ),
        _make_odds(
            "mozzart",
            None,
            217.5,
            over=1.90,
            under=1.90,
            match_id="m2",
            market_type="game_total",
        ),
    ]

    discs = find_threshold_gaps(odds)

    assert discs == []


def test_same_bookmaker_alternate_lines_are_ignored_for_ot_game_totals():
    odds = [
        _make_odds(
            "maxbet",
            None,
            214.5,
            over=1.90,
            under=1.84,
            match_id="m3",
            market_type="game_total_ot",
        ),
        _make_odds(
            "maxbet",
            None,
            217.5,
            over=1.94,
            under=1.80,
            match_id="m3",
            market_type="game_total_ot",
        ),
        _make_odds(
            "meridian",
            None,
            217.5,
            over=1.88,
            under=1.96,
            match_id="m3",
            market_type="game_total_ot",
        ),
    ]

    discs = find_threshold_gaps(odds)

    assert len(discs) == 1
    assert discs[0].market_type == "game_total_ot"
    assert discs[0].bookmaker_a_id == "maxbet"
    assert discs[0].bookmaker_b_id == "meridian"
    assert discs[0].threshold_a == 214.5
    assert discs[0].threshold_b == 217.5
    assert discs[0].gap == 3.0


def test_profit_margin_calculation():
    # Odds 1.85 and 2.00 → total implied 1.0405, balanced edge ROI ≈ -3.90%
    margin = _profit_margin(1.85, 2.00)
    assert margin is not None
    assert margin < 0
    assert margin == pytest.approx(-0.039, abs=1e-4)

    # Odds 2.10 and 2.10 → balanced edge ROI = +5.0%
    margin = _profit_margin(2.10, 2.10)
    assert margin is not None
    assert margin > 0
    assert margin == pytest.approx(0.05, abs=1e-4)


def test_middle_profit_margin_calculation():
    middle_margin = _middle_profit_margin(2.00, 2.00)
    assert middle_margin == pytest.approx(1.0, abs=1e-4)

    middle_margin = _middle_profit_margin(2.10, 2.10)
    assert middle_margin == pytest.approx(1.1, abs=1e-4)


def test_profit_margin_none_for_missing_odds():
    assert _profit_margin(None, 2.0) is None
    assert _profit_margin(2.0, None) is None
    assert _profit_margin(0.0, 2.0) is None
    assert _middle_profit_margin(None, 2.0) is None
    assert _middle_profit_margin(2.0, None) is None
    assert _middle_profit_margin(0.0, 2.0) is None


def test_gap_with_two_to_one_odds_has_break_even_edge_and_positive_middle():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, over=2.00, under=1.85),
        _make_odds("meridian", "Lundberg", 19.5, over=1.80, under=2.00),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    assert discs[0].profit_margin == pytest.approx(0.0, abs=1e-4)
    assert discs[0].middle_profit_margin == pytest.approx(1.0, abs=1e-4)


def test_low_odds_negative_margin_gap_is_filtered_for_middle():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, over=1.30, under=1.30),
        _make_odds("meridian", "Lundberg", 19.5, over=1.30, under=1.30),
    ]

    assert find_threshold_gaps(odds) == []


def test_positive_middle_but_negative_edge_still_surfaced():
    """Edge is negative but middle is positive → still useful."""
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5, over=1.85, under=1.85),
        _make_odds("meridian", "Lundberg", 19.5, over=1.80, under=1.85),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    assert discs[0].profit_margin is not None
    assert discs[0].profit_margin < 0
    assert discs[0].middle_profit_margin is not None
    assert discs[0].middle_profit_margin > 0


def test_analyze_entrypoint():
    odds = [
        _make_odds("mozzart", "Lundberg", 16.5),
        _make_odds("meridian", "Lundberg", 19.5),
    ]
    discs = analyze(odds)
    assert len(discs) >= 1


# ── Asian handicap (signed thresholds) ────────────────────


def _make_handicap_odds(
    bookmaker: str,
    threshold: float,
    over: float = 1.9,
    under: float = 1.9,
    match_id: str = "m1",
) -> NormalizedOdds:
    return _make_odds(
        bookmaker,
        None,
        threshold,
        over=over,
        under=under,
        match_id=match_id,
        market_type="home_handicap_ot",
    )


def test_handicap_negative_threshold_gap_detected_as_middle():
    """Both books rate home as the underdog but disagree on margin.

    Storage convention: positive threshold = home favoured, negative = home
    underdog (``threshold`` = home expected margin).

    a=-6.5 (mozzart): home expected to lose by ~6.5
    b=-3.5 (meridian): home expected to lose by ~3.5
    Analyzer orders by threshold ascending so a=-6.5 is the lower side.
    over@a (home_margin > -6.5, easy: home keeps it within 6 or wins) +
    under@b (home_margin < -3.5, requires home to lose by 4+).
    Middle window: -6.5 < margin < -3.5 (home loses by 4, 5, or 6) → both win.
    """
    odds = [
        _make_handicap_odds("mozzart", -6.5, over=1.9, under=1.9),
        _make_handicap_odds("meridian", -3.5, over=1.9, under=1.9),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    d = discs[0]
    assert d.market_type == "home_handicap_ot"
    assert d.threshold_a == -6.5
    assert d.threshold_b == -3.5
    assert d.gap == pytest.approx(3.0)
    # over_odds comes from the LOWER threshold (a=mozzart, threshold=-6.5)
    assert d.bookmaker_a_id == "mozzart"
    assert d.bookmaker_b_id == "meridian"
    assert d.odds_a == 1.9  # mozzart over
    assert d.odds_b == 1.9  # meridian under
    assert d.middle_profit_margin is not None
    assert d.middle_profit_margin > 0


def test_handicap_mixed_sign_threshold_gap_detected():
    """One book rates home as favourite, the other as underdog — produces a
    very wide middle window that the analyzer should still flag.

    a=-2.5 (home expected to lose by 2.5, i.e. underdog)
    b=+3.5 (home expected to win by 3.5, i.e. favourite)
    over@a (margin > -2.5) + under@b (margin < +3.5) overlap at -2.5 < margin < +3.5
    (home loses by 2 or 1, draws, or wins by 1/2/3) → both bets win.
    """
    odds = [
        _make_handicap_odds("mozzart", -2.5, over=1.9, under=1.9),
        _make_handicap_odds("meridian", 3.5, over=1.9, under=1.9),
    ]
    discs = find_threshold_gaps(odds)
    assert len(discs) == 1
    d = discs[0]
    assert d.threshold_a == -2.5
    assert d.threshold_b == 3.5
    assert d.gap == pytest.approx(6.0)
    assert d.middle_profit_margin is not None
    assert d.middle_profit_margin > 0


def test_handicap_same_negative_line_arbitrage_picks_better_combo():
    """Same negative line, asymmetric odds — the same-line arbitrage path
    should still find the best cross-book over/under combo for handicap.

    Odds chosen so 1/2.05 + 1/2.00 < 1 → small but positive arb when betting
    over@mozzart and under@meridian."""
    odds = [
        # mozzart: high over (priced low for "covers")
        _make_handicap_odds("mozzart", -4.5, over=2.05, under=1.85),
        # meridian: high under (priced low for "doesn't cover")
        _make_handicap_odds("meridian", -4.5, over=1.85, under=2.00),
    ]
    discs = find_threshold_gaps(odds)
    # Same-line same-sign arb: over@mozzart + under@meridian
    assert len(discs) == 1
    d = discs[0]
    assert d.threshold_a == -4.5
    assert d.threshold_b == -4.5
    assert d.gap == 0.0
    assert d.profit_margin is not None
    assert d.profit_margin > 0
    # Best combo should be the profitable one
    assert d.odds_a == 2.05  # mozzart over
    assert d.odds_b == 2.00  # meridian under


def test_handicap_no_gap_when_single_bookmaker():
    odds = [
        _make_handicap_odds("mozzart", -4.5),
        _make_handicap_odds("mozzart", -3.5),
    ]
    assert find_threshold_gaps(odds) == []


def test_handicap_does_not_pair_with_player_points_market():
    """Different market_types must never be paired even at matching thresholds."""
    odds = [
        _make_handicap_odds("mozzart", 2.5),
        _make_odds("meridian", "Lundberg", 2.5, market_type="player_points"),
    ]
    assert find_threshold_gaps(odds) == []
