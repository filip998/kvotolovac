from __future__ import annotations

from statistics import NormalDist

from app.services import middle_ev
from app.services.middle_ev import (
    MiddleMarketQuote,
    build_middle_estimator,
    estimate_middle,
)


def _normal_quotes(*, mu: float, sigma: float, lines: list[float]) -> list[MiddleMarketQuote]:
    normal = NormalDist(mu=mu, sigma=sigma)
    quotes: list[MiddleMarketQuote] = []
    for index, line in enumerate(lines):
        p_under = normal.cdf(line)
        quotes.extend(
            [
                MiddleMarketQuote(
                    bookmaker_id=f"book-{index}",
                    line=line,
                    outcome_code="over",
                    odds=1 / (1 - p_under),
                ),
                MiddleMarketQuote(
                    bookmaker_id=f"book-{index}",
                    line=line,
                    outcome_code="under",
                    odds=1 / p_under,
                ),
            ]
        )
    return quotes


def test_normal_middle_estimate_uses_market_implied_probability():
    estimate = estimate_middle(
        sport="basketball",
        market_type="player_points",
        low_line=9.5,
        high_line=15.5,
        low_odds=2.1,
        high_odds=2.1,
        market_quotes=_normal_quotes(mu=12.5, sigma=3.0, lines=[8.5, 11.5, 14.5, 17.5]),
        outside_margin=0.05,
        middle_margin=1.10,
    )

    assert estimate.should_publish is True
    assert estimate.hit_probability is not None
    assert 0.65 < estimate.hit_probability < 0.75
    assert estimate.expected_roi is not None
    assert estimate.expected_roi > 0
    assert estimate.confidence in {"medium", "high"}
    assert estimate.rank_score is not None
    assert estimate.diagnostics["model_family"] == "normal"


def test_normal_middle_falls_back_without_two_distinct_consensus_lines():
    estimate = estimate_middle(
        sport="basketball",
        market_type="player_points",
        low_line=10.5,
        high_line=12.5,
        low_odds=1.8,
        high_odds=1.8,
        market_quotes=_normal_quotes(mu=12.5, sigma=3.0, lines=[11.5]),
        outside_margin=-0.10,
        middle_margin=0.80,
    )

    assert estimate.should_publish is True
    assert estimate.used_fallback is True
    assert estimate.hit_probability is None
    assert estimate.expected_roi is None
    assert estimate.diagnostics["reason"] == "model_fit_failed"


def test_poisson_total_goals_allows_one_point_low_confidence_fit():
    estimate = estimate_middle(
        sport="football",
        market_type="football_total_goals",
        low_line=1.5,
        high_line=4.5,
        low_odds=2.0,
        high_odds=2.0,
        market_quotes=[
            MiddleMarketQuote("book", 2.5, "over", 2.0),
            MiddleMarketQuote("book", 2.5, "under", 2.0),
        ],
        outside_margin=0.0,
        middle_margin=1.0,
        outside_margin_floor=-0.05,
    )

    assert estimate.should_publish is True
    assert estimate.confidence == "low"
    assert estimate.hit_probability is not None
    assert estimate.expected_roi is not None
    assert estimate.expected_roi > 0
    assert estimate.diagnostics["model_family"] == "poisson"


def test_non_half_point_lines_use_strict_fallback():
    estimate = estimate_middle(
        sport="football",
        market_type="football_total_goals",
        low_line=2.0,
        high_line=4.5,
        low_odds=2.0,
        high_odds=2.0,
        market_quotes=[
            MiddleMarketQuote("book", 2.5, "over", 2.0),
            MiddleMarketQuote("book", 2.5, "under", 2.0),
        ],
        outside_margin=0.0,
        middle_margin=1.0,
        outside_margin_floor=-0.05,
    )

    assert estimate.should_publish is True
    assert estimate.used_fallback is True
    assert estimate.hit_probability is None
    assert estimate.diagnostics["reason"] == "unsupported_line_fraction"


def _assert_wrapper_matches_context(
    *,
    sport: str,
    market_type: str,
    low_line: float,
    high_line: float,
    low_odds: float,
    high_odds: float,
    market_quotes: list[MiddleMarketQuote],
    outside_margin: float | None,
    middle_margin: float | None,
    min_gap: float = 0.0,
    outside_margin_floor: float | None = None,
):
    wrapper = estimate_middle(
        sport=sport,
        market_type=market_type,
        low_line=low_line,
        high_line=high_line,
        low_odds=low_odds,
        high_odds=high_odds,
        market_quotes=market_quotes,
        outside_margin=outside_margin,
        middle_margin=middle_margin,
        min_gap=min_gap,
        outside_margin_floor=outside_margin_floor,
    )
    context = build_middle_estimator(
        sport=sport,
        market_type=market_type,
        market_quotes=market_quotes,
    ).estimate(
        low_line=low_line,
        high_line=high_line,
        low_odds=low_odds,
        high_odds=high_odds,
        outside_margin=outside_margin,
        middle_margin=middle_margin,
        min_gap=min_gap,
        outside_margin_floor=outside_margin_floor,
    )

    assert context == wrapper
    assert context.diagnostics == wrapper.diagnostics
    return context


def test_context_estimator_matches_wrapper_for_candidate_outcomes(monkeypatch):
    normal_quotes = _normal_quotes(mu=12.5, sigma=3.0, lines=[8.5, 11.5, 14.5, 17.5])
    one_line_quotes = _normal_quotes(mu=12.5, sigma=3.0, lines=[11.5])
    empty_quotes: list[MiddleMarketQuote] = []

    cases = [
        {
            "name": "invalid_candidate",
            "sport": "basketball",
            "market_type": "player_points",
            "low_line": 10.5,
            "high_line": 12.5,
            "low_odds": 0.0,
            "high_odds": 2.0,
            "market_quotes": normal_quotes,
            "outside_margin": 0.0,
            "middle_margin": 1.0,
        },
        {
            "name": "outside_margin_below_floor",
            "sport": "football",
            "market_type": "football_total_goals",
            "low_line": 1.5,
            "high_line": 4.5,
            "low_odds": 2.0,
            "high_odds": 2.0,
            "market_quotes": [
                MiddleMarketQuote("book", 2.5, "over", 2.0),
                MiddleMarketQuote("book", 2.5, "under", 2.0),
            ],
            "outside_margin": -0.10,
            "middle_margin": 1.0,
            "outside_margin_floor": -0.05,
        },
        {
            "name": "unsupported_market_family",
            "sport": "cricket",
            "market_type": "total_runs",
            "low_line": 10.5,
            "high_line": 12.5,
            "low_odds": 1.8,
            "high_odds": 1.8,
            "market_quotes": empty_quotes,
            "outside_margin": -0.10,
            "middle_margin": 0.80,
        },
        {
            "name": "unsupported_line_fraction",
            "sport": "basketball",
            "market_type": "player_points",
            "low_line": 10.0,
            "high_line": 12.5,
            "low_odds": 1.8,
            "high_odds": 1.8,
            "market_quotes": normal_quotes,
            "outside_margin": -0.10,
            "middle_margin": 0.80,
        },
        {
            "name": "no_same_book_consensus_points",
            "sport": "basketball",
            "market_type": "player_points",
            "low_line": 10.5,
            "high_line": 12.5,
            "low_odds": 1.8,
            "high_odds": 1.8,
            "market_quotes": empty_quotes,
            "outside_margin": -0.10,
            "middle_margin": 0.80,
        },
        {
            "name": "model_fit_failed",
            "sport": "basketball",
            "market_type": "player_points",
            "low_line": 10.5,
            "high_line": 12.5,
            "low_odds": 1.8,
            "high_odds": 1.8,
            "market_quotes": one_line_quotes,
            "outside_margin": -0.10,
            "middle_margin": 0.80,
        },
        {
            "name": "fitted_publish",
            "sport": "basketball",
            "market_type": "player_points",
            "low_line": 9.5,
            "high_line": 15.5,
            "low_odds": 2.1,
            "high_odds": 2.1,
            "market_quotes": normal_quotes,
            "outside_margin": 0.05,
            "middle_margin": 1.10,
        },
        {
            "name": "fitted_no_publish",
            "sport": "basketball",
            "market_type": "player_points",
            "low_line": 9.5,
            "high_line": 15.5,
            "low_odds": 1.2,
            "high_odds": 1.2,
            "market_quotes": normal_quotes,
            "outside_margin": -0.70,
            "middle_margin": 0.20,
        },
    ]

    def hit_probability_failed(*args, **kwargs):
        return None

    for case in cases:
        name = str(case["name"])
        params = {key: value for key, value in case.items() if key != "name"}
        result = _assert_wrapper_matches_context(**params)
        if name == "fitted_publish":
            assert result.should_publish is True
            assert result.diagnostics["mode"] == "fitted"
        elif name == "fitted_no_publish":
            assert result.should_publish is False
            assert result.diagnostics["mode"] == "fitted"
        else:
            assert result.diagnostics["reason"] == name

    monkeypatch.setattr(middle_ev, "_middle_hit_probability", hit_probability_failed)
    result = _assert_wrapper_matches_context(
        sport="basketball",
        market_type="player_points",
        low_line=9.5,
        high_line=15.5,
        low_odds=2.1,
        high_odds=2.1,
        market_quotes=normal_quotes,
        outside_margin=0.05,
        middle_margin=1.10,
    )
    assert result.diagnostics["reason"] == "hit_probability_failed"


def test_context_estimator_keeps_line_fraction_check_per_candidate():
    estimator = build_middle_estimator(
        sport="basketball",
        market_type="player_points",
        market_quotes=_normal_quotes(mu=12.5, sigma=3.0, lines=[8.5, 11.5, 14.5, 17.5]),
    )

    fitted = estimator.estimate(
        low_line=9.5,
        high_line=15.5,
        low_odds=2.1,
        high_odds=2.1,
        outside_margin=0.05,
        middle_margin=1.10,
    )
    integer_line = estimator.estimate(
        low_line=10.0,
        high_line=15.5,
        low_odds=2.1,
        high_odds=2.1,
        outside_margin=0.05,
        middle_margin=1.10,
    )

    assert fitted.diagnostics["mode"] == "fitted"
    assert integer_line.used_fallback is True
    assert integer_line.diagnostics["reason"] == "unsupported_line_fraction"
