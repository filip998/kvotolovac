from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..models.schemas import NormalizedOdds


@dataclass
class Discrepancy:
    match_id: str
    market_type: str
    player_name: str | None
    bookmaker_a_id: str
    bookmaker_b_id: str
    threshold_a: float
    threshold_b: float
    odds_a: float | None  # over odds from bookmaker A (lower threshold)
    odds_b: float | None  # under odds from bookmaker B (higher threshold)
    gap: float
    profit_margin: float | None
    middle_profit_margin: float | None = None


def _comparison_market_type(market_type: str) -> str:
    mapping = {
        "player_points_milestones": "player_points",
    }
    return mapping.get(market_type, market_type)


def _implied_probability(odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if odds <= 0:
        return 0.0
    return 1.0 / odds


def _profit_margin(odds_a: float | None, odds_b: float | None) -> float | None:
    """
    Calculate guaranteed edge ROI when stake sizing equalizes the low/high outcomes.
    Positive means both edge outcomes are profitable; zero means break-even outside the middle.
    """
    if not odds_a or not odds_b or odds_a <= 0 or odds_b <= 0:
        return None
    total_implied = _implied_probability(odds_a) + _implied_probability(odds_b)
    if total_implied <= 0:
        return None
    return round((1.0 / total_implied) - 1.0, 4)


def _middle_profit_margin(odds_a: float | None, odds_b: float | None) -> float | None:
    """
    Calculate ROI when the result lands inside the threshold gap and both tickets win,
    using the same balanced stakes as _profit_margin().
    """
    if not odds_a or not odds_b or odds_a <= 0 or odds_b <= 0:
        return None
    total_implied = _implied_probability(odds_a) + _implied_probability(odds_b)
    if total_implied <= 0:
        return None
    return round((2.0 / total_implied) - 1.0, 4)


def find_threshold_gaps(
    odds_list: list[NormalizedOdds],
    min_gap: float = 0.0,
) -> list[Discrepancy]:
    """
    Find threshold discrepancies: where bookmaker A offers 'over X' and
    bookmaker B offers 'under Y' with Y > X → gap of Y - X points.
    """
    # Group by (match_id, market_type, player_name)
    groups: dict[tuple, list[NormalizedOdds]] = {}
    for o in odds_list:
        key = (o.match_id, _comparison_market_type(o.market_type), o.player_name)
        groups.setdefault(key, []).append(o)

    discrepancies: list[Discrepancy] = []

    for key, group in groups.items():
        if len(group) < 2:
            continue

        # Compare every pair of bookmakers
        for a, b in combinations(group, 2):
            if a.bookmaker_id == b.bookmaker_id:
                continue

            # Ensure a has the lower threshold
            if a.threshold > b.threshold:
                a, b = b, a

            if a.threshold == b.threshold:
                # Same threshold — evaluate both cross-book combinations
                if a.over_odds and b.over_odds:
                    diff = abs(a.over_odds - b.over_odds)
                    if diff >= 0.05:
                        margin_ab = _profit_margin(a.over_odds, b.under_odds) if b.under_odds else None
                        margin_ba = _profit_margin(b.over_odds, a.under_odds) if a.under_odds else None

                        # Pick the better profitable combination
                        best_margin = None
                        best_over = a.over_odds
                        best_under = b.over_odds
                        best_a_id = a.bookmaker_id
                        best_b_id = b.bookmaker_id

                        if margin_ab is not None and margin_ab > 0:
                            best_margin = margin_ab
                            best_over = a.over_odds
                            best_under = b.under_odds
                            best_a_id = a.bookmaker_id
                            best_b_id = b.bookmaker_id

                        if margin_ba is not None and margin_ba > 0 and (best_margin is None or margin_ba > best_margin):
                            best_margin = margin_ba
                            best_over = b.over_odds
                            best_under = a.under_odds
                            best_a_id = b.bookmaker_id
                            best_b_id = a.bookmaker_id

                        if best_margin is not None and best_margin > 0:
                            discrepancies.append(
                                Discrepancy(
                                    match_id=key[0],
                                    market_type=key[1],
                                    player_name=key[2],
                                    bookmaker_a_id=best_a_id,
                                    bookmaker_b_id=best_b_id,
                                    threshold_a=a.threshold,
                                    threshold_b=b.threshold,
                                    odds_a=best_over,
                                    odds_b=best_under,
                                    gap=0.0,
                                    profit_margin=best_margin,
                                    middle_profit_margin=None,
                                )
                            )
                continue

            gap = b.threshold - a.threshold
            if gap < min_gap:
                continue

            # Bookmaker A over (lower threshold) + Bookmaker B under (higher threshold)
            if a.over_odds is None or b.under_odds is None:
                continue
            margin = _profit_margin(a.over_odds, b.under_odds)
            middle_margin = _middle_profit_margin(a.over_odds, b.under_odds)

            discrepancies.append(
                Discrepancy(
                    match_id=key[0],
                    market_type=key[1],
                    player_name=key[2],
                    bookmaker_a_id=a.bookmaker_id,
                    bookmaker_b_id=b.bookmaker_id,
                    threshold_a=a.threshold,
                    threshold_b=b.threshold,
                    odds_a=a.over_odds,
                    odds_b=b.under_odds,
                    gap=round(gap, 1),
                    profit_margin=margin,
                    middle_profit_margin=middle_margin,
                )
            )

    return discrepancies


def analyze(
    odds_list: list[NormalizedOdds],
    min_gap: float = 0.0,
) -> list[Discrepancy]:
    """Main entry: find all discrepancies across the odds list."""
    return find_threshold_gaps(odds_list, min_gap=min_gap)
