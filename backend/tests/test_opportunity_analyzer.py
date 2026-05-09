from __future__ import annotations

from app.models.schemas import NormalizedOutcomeOffer
from app.services.opportunity_analyzer import analyze_outcome_offers


def _offer(
    bookmaker_id: str,
    market_type: str,
    outcome_code: str,
    odds: float,
    *,
    line: float | None = None,
    match_id: str = "football-match-1",
) -> NormalizedOutcomeOffer:
    return NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id="uae_2",
        sport="football",
        home_team_id=1,
        away_team_id=2,
        home_team="Hatta SC",
        away_team="Al Urooba UAE",
        market_type=market_type,
        outcome_code=outcome_code,
        odds=odds,
        line=line,
        raw_label=outcome_code,
        start_time="2030-01-01T20:00:00+00:00",
    )


def test_analyze_total_goals_same_line_arbitrage():
    opportunities = analyze_outcome_offers(
        [
            _offer("maxbet", "football_total_goals", "under", 2.15, line=2.5),
            _offer("balkanbet", "football_total_goals", "over", 2.85, line=2.5),
        ]
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.opportunity_type == "same_line_arbitrage"
    assert opportunity.market_type == "football_total_goals"
    assert opportunity.line == 2.5
    assert opportunity.profit_margin and opportunity.profit_margin > 0
    assert {leg.outcome_code for leg in opportunity.legs} == {"under", "over"}


def test_analyze_result_double_chance_complement():
    opportunities = analyze_outcome_offers(
        [
            _offer("maxbet", "football_result", "home", 2.5),
            _offer("balkanbet", "football_double_chance", "draw_or_away", 1.75),
        ]
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.opportunity_type == "complementary_outcomes"
    assert opportunity.market_type == "football_result_double_chance"
    assert opportunity.profit_margin and opportunity.profit_margin > 0
    assert {leg.outcome_code for leg in opportunity.legs} == {"home", "draw_or_away"}


def test_analyzer_requires_different_bookmakers():
    opportunities = analyze_outcome_offers(
        [
            _offer("maxbet", "football_result", "home", 2.5),
            _offer("maxbet", "football_double_chance", "draw_or_away", 1.75),
            _offer("maxbet", "football_total_goals", "under", 2.15, line=2.5),
            _offer("maxbet", "football_total_goals", "over", 2.85, line=2.5),
        ]
    )

    assert opportunities == []


def test_analyze_total_goals_middle_respects_outside_margin_floor():
    opportunities = analyze_outcome_offers(
        [
            _offer("balkanbet", "football_total_goals", "over", 2.0, line=1.5),
            _offer("maxbet", "football_total_goals", "under", 2.0, line=4.5),
        ]
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.opportunity_type == "middle"
    assert opportunity.profit_margin == 0.0
    assert opportunity.middle_profit_margin == 1.0
    assert {(leg.outcome_code, leg.line) for leg in opportunity.legs} == {
        ("over", 1.5),
        ("under", 4.5),
    }


def test_analyze_total_goals_middle_rejects_one_point_gap():
    opportunities = analyze_outcome_offers(
        [
            _offer("balkanbet", "football_total_goals", "over", 2.0, line=1.5),
            _offer("maxbet", "football_total_goals", "under", 2.0, line=2.5),
        ]
    )

    assert opportunities == []


def test_analyze_total_goals_middle_rejects_low_leg_odds():
    opportunities = analyze_outcome_offers(
        [
            _offer("balkanbet", "football_total_goals", "over", 1.70, line=1.5),
            _offer("maxbet", "football_total_goals", "under", 2.10, line=4.5),
        ]
    )

    assert opportunities == []


def test_analyze_total_goals_middle_filters_large_outside_loss():
    opportunities = analyze_outcome_offers(
        [
            _offer("balkanbet", "football_total_goals", "over", 1.65, line=1.5),
            _offer("maxbet", "football_total_goals", "under", 1.65, line=4.5),
        ]
    )

    assert opportunities == []


# ── feature gate (issue #131): kill-switch + fitted-EV threshold ──────────


def _legacy_middle_offers():
    """Football-totals middle (line gap 3.0) that survives the football margin floor and
    triggers the line_middle path rather than same_line_arbitrage."""
    return [
        _offer("balkanbet", "football_total_goals", "over", 2.0, line=1.5),
        _offer("maxbet", "football_total_goals", "under", 2.0, line=4.5),
    ]


def _legacy_same_line_arb_offers():
    return [
        _offer("maxbet", "football_total_goals", "under", 2.15, line=2.5),
        _offer("balkanbet", "football_total_goals", "over", 2.85, line=2.5),
    ]


def test_analyze_outcome_offers_default_includes_fitted_middles():
    """Regression: legacy default behaviour preserved."""
    opportunities = analyze_outcome_offers(_legacy_middle_offers())
    assert len(opportunities) == 1
    assert opportunities[0].opportunity_type == "middle"


def test_analyze_outcome_offers_kill_switch_drops_fitted_middles_only():
    """Kill-switch suppresses ONLY the cross-line middle branch — same-line arbitrage path
    remains untouched."""
    arb_only = analyze_outcome_offers(
        _legacy_same_line_arb_offers(), enable_fitted_middles=False
    )
    assert len(arb_only) == 1
    assert arb_only[0].opportunity_type == "same_line_arbitrage"

    middle_dropped = analyze_outcome_offers(
        _legacy_middle_offers(), enable_fitted_middles=False
    )
    assert middle_dropped == []


def test_analyze_outcome_offers_threshold_drops_fallback_middles():
    """Positive threshold drops fallback middles (no fitted EV)."""
    offers = _legacy_middle_offers()
    no_floor = analyze_outcome_offers(offers, min_fitted_middle_ev_percent=0.0)
    assert len(no_floor) == 1
    assert no_floor[0].middle_ev is None  # fallback path

    with_floor = analyze_outcome_offers(offers, min_fitted_middle_ev_percent=0.5)
    assert with_floor == []
