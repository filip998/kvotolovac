from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist, median
from typing import Any


FALLBACK_MIDDLE_MIN_GAP = 2.0
FALLBACK_MIDDLE_MIN_LEG_ODDS = 1.75

_FOOTBALL_POISSON_MARKETS = {"football_total_goals"}
_BASKETBALL_NORMAL_MARKETS = {
    "game_total",
    "game_total_ot",
    "home_handicap_ot",
    "player_points",
    "player_points_milestones",
    "player_rebounds",
    "player_assists",
    "player_3points",
    "player_steals",
    "player_blocks",
    "player_turnovers",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}
_CONFIDENCE_PENALTY = {
    "high": 1.0,
    "medium": 0.85,
    "low": 0.65,
}
_PROBABILITY_FLOOR = 0.01
_PROBABILITY_CEILING = 0.99
_LINE_EPSILON = 1e-9


@dataclass(frozen=True)
class MiddleMarketQuote:
    bookmaker_id: str
    line: float
    outcome_code: str
    odds: float


@dataclass(frozen=True)
class MiddleEstimate:
    should_publish: bool
    hit_probability: float | None
    expected_roi: float | None
    confidence: str | None
    diagnostics: dict[str, Any]
    rank_score: float | None
    used_fallback: bool = False


@dataclass(frozen=True)
class _ConsensusPoint:
    line: float
    probability_under: float
    observations: int


def estimate_middle(
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
) -> MiddleEstimate:
    """Estimate hit probability and EV for an over-low/under-high middle candidate."""
    gap = high_line - low_line
    fallback_min_gap = max(min_gap, FALLBACK_MIDDLE_MIN_GAP)
    base_diagnostics: dict[str, Any] = {
        "gap": round(gap, 4),
        "fallback_min_gap": fallback_min_gap,
        "fallback_min_leg_odds": FALLBACK_MIDDLE_MIN_LEG_ODDS,
        "staking": "equal_payout",
    }
    if (
        gap <= 0
        or low_odds <= 0
        or high_odds <= 0
        or outside_margin is None
        or middle_margin is None
    ):
        return _fallback_estimate(
            should_publish=False,
            reason="invalid_candidate",
            diagnostics=base_diagnostics,
        )

    if outside_margin_floor is not None and outside_margin < outside_margin_floor:
        return _fallback_estimate(
            should_publish=False,
            reason="outside_margin_below_floor",
            diagnostics={**base_diagnostics, "outside_margin_floor": outside_margin_floor},
        )

    family = _model_family(sport=sport, market_type=market_type)
    if family is None:
        return _fallback_from_candidate(
            reason="unsupported_market_family",
            diagnostics=base_diagnostics,
            gap=gap,
            low_odds=low_odds,
            high_odds=high_odds,
            min_gap=fallback_min_gap,
        )

    if not (_is_half_point_line(low_line) and _is_half_point_line(high_line)):
        return _fallback_from_candidate(
            reason="unsupported_line_fraction",
            diagnostics={**base_diagnostics, "model_family": family},
            gap=gap,
            low_odds=low_odds,
            high_odds=high_odds,
            min_gap=fallback_min_gap,
        )

    consensus = _consensus_points(market_quotes)
    if not consensus:
        return _fallback_from_candidate(
            reason="no_same_book_consensus_points",
            diagnostics={**base_diagnostics, "model_family": family},
            gap=gap,
            low_odds=low_odds,
            high_odds=high_odds,
            min_gap=fallback_min_gap,
        )

    if family == "normal":
        model = _fit_normal(consensus)
    else:
        model = _fit_poisson(consensus)

    if model is None:
        return _fallback_from_candidate(
            reason="model_fit_failed",
            diagnostics={
                **base_diagnostics,
                "model_family": family,
                "consensus_points": len(consensus),
                "consensus_lines": [point.line for point in consensus],
            },
            gap=gap,
            low_odds=low_odds,
            high_odds=high_odds,
            min_gap=fallback_min_gap,
        )

    hit_probability = _middle_hit_probability(
        family=family,
        low_line=low_line,
        high_line=high_line,
        parameter_a=model["parameter_a"],
        parameter_b=model.get("parameter_b"),
    )
    if hit_probability is None:
        return _fallback_from_candidate(
            reason="hit_probability_failed",
            diagnostics={**base_diagnostics, "model_family": family},
            gap=gap,
            low_odds=low_odds,
            high_odds=high_odds,
            min_gap=fallback_min_gap,
        )

    expected_roi = hit_probability * middle_margin + (1.0 - hit_probability) * outside_margin
    confidence = str(model["confidence"])
    rank_score = expected_roi * _CONFIDENCE_PENALTY.get(confidence, 0.5)
    diagnostics = {
        **base_diagnostics,
        "mode": "fitted",
        "model_family": family,
        "confidence": confidence,
        "consensus_points": len(consensus),
        "consensus_observations": sum(point.observations for point in consensus),
        "rmse": model["rmse"],
        "rank_penalty": _CONFIDENCE_PENALTY.get(confidence, 0.5),
    }
    if family == "normal":
        diagnostics.update({"mu": model["parameter_a"], "sigma": model["parameter_b"]})
    else:
        diagnostics.update({"lambda": model["parameter_a"]})
    if model.get("monotonic_adjusted"):
        diagnostics["monotonic_adjusted"] = True

    return MiddleEstimate(
        should_publish=expected_roi > 0,
        hit_probability=round(hit_probability, 4),
        expected_roi=round(expected_roi, 4),
        confidence=confidence,
        diagnostics=diagnostics,
        rank_score=round(rank_score, 6),
    )


def fallback_rank(
    *,
    low_line: float,
    high_line: float,
    middle_margin: float | None,
    outside_margin: float | None,
    low_odds: float,
    high_odds: float,
) -> tuple[float, ...]:
    gap = high_line - low_line
    line_scale = max((abs(low_line) + abs(high_line)) / 2.0, 1.0)
    return (
        gap / line_scale,
        middle_margin or -999.0,
        outside_margin or -999.0,
        gap,
        min(low_odds, high_odds),
    )


def _fallback_from_candidate(
    *,
    reason: str,
    diagnostics: dict[str, Any],
    gap: float,
    low_odds: float,
    high_odds: float,
    min_gap: float,
) -> MiddleEstimate:
    should_publish = (
        gap >= min_gap
        and min(low_odds, high_odds) >= FALLBACK_MIDDLE_MIN_LEG_ODDS
    )
    return _fallback_estimate(
        should_publish=should_publish,
        reason=reason,
        diagnostics=diagnostics,
    )


def _fallback_estimate(
    *,
    should_publish: bool,
    reason: str,
    diagnostics: dict[str, Any],
) -> MiddleEstimate:
    return MiddleEstimate(
        should_publish=should_publish,
        hit_probability=None,
        expected_roi=None,
        confidence="fallback" if should_publish else None,
        diagnostics={**diagnostics, "mode": "fallback", "reason": reason},
        rank_score=None,
        used_fallback=should_publish,
    )


def _model_family(*, sport: str, market_type: str) -> str | None:
    normalized_sport = sport.lower()
    if normalized_sport == "football" and market_type in _FOOTBALL_POISSON_MARKETS:
        return "poisson"
    if normalized_sport == "basketball" and market_type in _BASKETBALL_NORMAL_MARKETS:
        return "normal"
    return None


def _is_half_point_line(line: float) -> bool:
    doubled = line * 2.0
    return abs(doubled - round(doubled)) <= _LINE_EPSILON and abs(line - round(line)) > _LINE_EPSILON


def _consensus_points(quotes: list[MiddleMarketQuote]) -> list[_ConsensusPoint]:
    by_bookmaker_line: dict[tuple[str, float], dict[str, float]] = {}
    for quote in quotes:
        if quote.outcome_code not in {"over", "under"} or quote.odds <= 0:
            continue
        key = (quote.bookmaker_id, quote.line)
        by_bookmaker_line.setdefault(key, {})[quote.outcome_code] = quote.odds

    by_line: dict[float, list[float]] = {}
    for (_bookmaker_id, line), sides in by_bookmaker_line.items():
        over = sides.get("over")
        under = sides.get("under")
        if over is None or under is None:
            continue
        implied_over = 1.0 / over
        implied_under = 1.0 / under
        total = implied_over + implied_under
        if total <= 0:
            continue
        probability_under = implied_under / total
        by_line.setdefault(line, []).append(_clamp_probability(probability_under))

    points = [
        _ConsensusPoint(
            line=line,
            probability_under=float(median(probabilities)),
            observations=len(probabilities),
        )
        for line, probabilities in by_line.items()
    ]
    return _monotonic_consensus(sorted(points, key=lambda point: point.line))


def _monotonic_consensus(points: list[_ConsensusPoint]) -> list[_ConsensusPoint]:
    if len(points) < 2:
        return points

    blocks = [
        {
            "start": index,
            "end": index,
            "weight": float(point.observations),
            "value": point.probability_under,
        }
        for index, point in enumerate(points)
    ]
    index = 0
    while index < len(blocks) - 1:
        if blocks[index]["value"] <= blocks[index + 1]["value"] + _LINE_EPSILON:
            index += 1
            continue
        left = blocks[index]
        right = blocks[index + 1]
        weight = left["weight"] + right["weight"]
        value = (left["value"] * left["weight"] + right["value"] * right["weight"]) / weight
        blocks[index : index + 2] = [
            {
                "start": left["start"],
                "end": right["end"],
                "weight": weight,
                "value": value,
            }
        ]
        if index > 0:
            index -= 1

    adjusted = [0.0] * len(points)
    for block in blocks:
        for index in range(int(block["start"]), int(block["end"]) + 1):
            adjusted[index] = _clamp_probability(float(block["value"]))

    return [
        _ConsensusPoint(
            line=point.line,
            probability_under=adjusted[index],
            observations=point.observations,
        )
        for index, point in enumerate(points)
    ]


def _fit_normal(points: list[_ConsensusPoint]) -> dict[str, Any] | None:
    distinct = _distinct_points(points)
    if len(distinct) < 2:
        return None

    normal = NormalDist()
    xs = [normal.inv_cdf(point.probability_under) for point in distinct]
    ys = [point.line for point in distinct]
    weights = [point.observations for point in distinct]
    weight_sum = float(sum(weights))
    x_mean = sum(x * weight for x, weight in zip(xs, weights)) / weight_sum
    y_mean = sum(y * weight for y, weight in zip(ys, weights)) / weight_sum
    denominator = sum(weight * (x - x_mean) ** 2 for x, weight in zip(xs, weights))
    if denominator <= _LINE_EPSILON:
        return None
    sigma = sum(
        weight * (x - x_mean) * (y - y_mean)
        for x, y, weight in zip(xs, ys, weights)
    ) / denominator
    if sigma <= _LINE_EPSILON or not math.isfinite(sigma):
        return None
    mu = y_mean - sigma * x_mean
    rmse = _normal_rmse(points, mu=mu, sigma=sigma)
    return {
        "parameter_a": round(mu, 6),
        "parameter_b": round(sigma, 6),
        "rmse": round(rmse, 6),
        "confidence": _confidence(len(distinct), sum(point.observations for point in distinct), rmse),
        "monotonic_adjusted": _was_monotonic_adjusted(points),
    }


def _fit_poisson(points: list[_ConsensusPoint]) -> dict[str, Any] | None:
    if not points:
        return None

    def loss(lam: float) -> float:
        return sum(
            (_poisson_cdf(math.floor(point.line), lam) - point.probability_under) ** 2
            * point.observations
            for point in points
        ) / sum(point.observations for point in points)

    left = 0.05
    right = 12.0
    for _ in range(80):
        first = left + (right - left) / 3.0
        second = right - (right - left) / 3.0
        if loss(first) < loss(second):
            right = second
        else:
            left = first
    lam = (left + right) / 2.0
    if not math.isfinite(lam) or lam <= 0:
        return None
    rmse = math.sqrt(loss(lam))
    distinct_count = len(_distinct_points(points))
    confidence = "low" if distinct_count == 1 else _confidence(
        distinct_count,
        sum(point.observations for point in points),
        rmse,
    )
    return {
        "parameter_a": round(lam, 6),
        "rmse": round(rmse, 6),
        "confidence": confidence,
        "monotonic_adjusted": _was_monotonic_adjusted(points),
    }


def _middle_hit_probability(
    *,
    family: str,
    low_line: float,
    high_line: float,
    parameter_a: float,
    parameter_b: float | None,
) -> float | None:
    if family == "normal":
        if parameter_b is None or parameter_b <= 0:
            return None
        normal = NormalDist(mu=parameter_a, sigma=parameter_b)
        return max(0.0, min(1.0, normal.cdf(high_line) - normal.cdf(low_line)))
    if family == "poisson":
        return max(
            0.0,
            min(
                1.0,
                _poisson_cdf(math.floor(high_line), parameter_a)
                - _poisson_cdf(math.floor(low_line), parameter_a),
            ),
        )
    return None


def _distinct_points(points: list[_ConsensusPoint]) -> list[_ConsensusPoint]:
    distinct: list[_ConsensusPoint] = []
    seen: set[float] = set()
    for point in points:
        if point.line in seen:
            continue
        seen.add(point.line)
        distinct.append(point)
    return distinct


def _normal_rmse(points: list[_ConsensusPoint], *, mu: float, sigma: float) -> float:
    normal = NormalDist(mu=mu, sigma=sigma)
    return math.sqrt(
        sum(
            (normal.cdf(point.line) - point.probability_under) ** 2 * point.observations
            for point in points
        )
        / sum(point.observations for point in points)
    )


def _confidence(distinct_points: int, observations: int, rmse: float) -> str:
    if distinct_points >= 4 and observations >= 6 and rmse <= 0.04:
        return "high"
    if distinct_points >= 3 and observations >= 3 and rmse <= 0.07:
        return "medium"
    return "low"


def _was_monotonic_adjusted(points: list[_ConsensusPoint]) -> bool:
    # The caller only sees post-adjustment points; ties across several lines are the signal
    # that PAVA had to pool observations.
    if len(points) < 2:
        return False
    return any(
        abs(left.probability_under - right.probability_under) <= _LINE_EPSILON
        for left, right in zip(points, points[1:])
        if abs(left.line - right.line) > _LINE_EPSILON
    )


def _poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    term = math.exp(-lam)
    total = term
    for index in range(1, k + 1):
        term *= lam / index
        total += term
    return max(0.0, min(1.0, total))


def _clamp_probability(value: float) -> float:
    return max(_PROBABILITY_FLOOR, min(_PROBABILITY_CEILING, value))
