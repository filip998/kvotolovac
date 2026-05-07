from __future__ import annotations

from statistics import NormalDist

from app.services.middle_ev import MiddleMarketQuote, estimate_middle


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
