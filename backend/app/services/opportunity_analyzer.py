from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..models.schemas import NormalizedOutcomeOffer, OpportunityLeg


@dataclass(frozen=True)
class Opportunity:
    sport: str
    match_id: str
    opportunity_type: str
    market_type: str
    line: float | None
    profit_margin: float | None
    middle_profit_margin: float | None
    legs: list[OpportunityLeg]


_COMPLEMENTARY_RESULT_PAIRS = {
    ("football_result", "home"): ("football_double_chance", "draw_or_away"),
    ("football_result", "away"): ("football_double_chance", "home_or_draw"),
    ("football_result", "draw"): ("football_double_chance", "home_or_away"),
}
_FOOTBALL_MIDDLE_OUTSIDE_MARGIN_FLOOR = -0.05


def _implied_probability(odds: float) -> float:
    if odds <= 0:
        return 0.0
    return 1.0 / odds


def _profit_margin(odds_a: float, odds_b: float) -> float | None:
    total_implied = _implied_probability(odds_a) + _implied_probability(odds_b)
    if total_implied <= 0:
        return None
    return round((1.0 / total_implied) - 1.0, 4)


def _middle_profit_margin(odds_a: float, odds_b: float) -> float | None:
    total_implied = _implied_probability(odds_a) + _implied_probability(odds_b)
    if total_implied <= 0:
        return None
    return round((2.0 / total_implied) - 1.0, 4)


def _leg(offer: NormalizedOutcomeOffer) -> OpportunityLeg:
    return OpportunityLeg(
        bookmaker_id=offer.bookmaker_id,
        source_url=offer.source_url,
        market_type=offer.market_type,
        outcome_code=offer.outcome_code,
        odds=offer.odds,
        line=offer.line,
        raw_label=offer.raw_label,
    )


def _dedupe_key(opportunity: Opportunity) -> tuple:
    leg_keys = tuple(
        sorted(
            (
                leg.bookmaker_id,
                leg.market_type,
                leg.outcome_code,
                leg.line,
                leg.odds,
            )
            for leg in opportunity.legs
        )
    )
    return (
        opportunity.match_id,
        opportunity.opportunity_type,
        opportunity.market_type,
        opportunity.line,
        leg_keys,
    )


def _analyze_complementary_pairs(group: list[NormalizedOutcomeOffer]) -> list[Opportunity]:
    by_key: dict[tuple[str, str], list[NormalizedOutcomeOffer]] = {}
    for offer in group:
        by_key.setdefault((offer.market_type, offer.outcome_code), []).append(offer)

    opportunities: list[Opportunity] = []
    for result_key, double_chance_key in _COMPLEMENTARY_RESULT_PAIRS.items():
        for result_offer in by_key.get(result_key, []):
            for dc_offer in by_key.get(double_chance_key, []):
                if result_offer.bookmaker_id == dc_offer.bookmaker_id:
                    continue
                margin = _profit_margin(result_offer.odds, dc_offer.odds)
                if margin is None or margin <= 0:
                    continue
                opportunities.append(
                    Opportunity(
                        sport=result_offer.sport,
                        match_id=result_offer.match_id,
                        opportunity_type="complementary_outcomes",
                        market_type="football_result_double_chance",
                        line=None,
                        profit_margin=margin,
                        middle_profit_margin=None,
                        legs=[_leg(result_offer), _leg(dc_offer)],
                    )
                )
    return opportunities


def _analyze_total_goals(group: list[NormalizedOutcomeOffer]) -> list[Opportunity]:
    totals = [offer for offer in group if offer.market_type == "football_total_goals"]
    opportunities: list[Opportunity] = []

    for a, b in combinations(totals, 2):
        if a.bookmaker_id == b.bookmaker_id or a.line is None or b.line is None:
            continue

        if a.line == b.line and {a.outcome_code, b.outcome_code} == {"over", "under"}:
            margin = _profit_margin(a.odds, b.odds)
            if margin is not None and margin > 0:
                opportunities.append(
                    Opportunity(
                        sport=a.sport,
                        match_id=a.match_id,
                        opportunity_type="same_line_arbitrage",
                        market_type="football_total_goals",
                        line=a.line,
                        profit_margin=margin,
                        middle_profit_margin=None,
                        legs=[_leg(a), _leg(b)],
                    )
                )
            continue

        low, high = (a, b) if a.line < b.line else (b, a)
        if low.outcome_code != "over" or high.outcome_code != "under":
            continue
        margin = _profit_margin(low.odds, high.odds)
        middle_margin = _middle_profit_margin(low.odds, high.odds)
        if middle_margin is None or middle_margin <= 0:
            continue
        if margin is None or margin < _FOOTBALL_MIDDLE_OUTSIDE_MARGIN_FLOOR:
            continue
        opportunities.append(
            Opportunity(
                sport=low.sport,
                match_id=low.match_id,
                opportunity_type="middle",
                market_type="football_total_goals",
                line=low.line,
                profit_margin=margin,
                middle_profit_margin=middle_margin,
                legs=[_leg(low), _leg(high)],
            )
        )

    return opportunities


def analyze_outcome_offers(offers: list[NormalizedOutcomeOffer]) -> list[Opportunity]:
    groups: dict[tuple[str, str], list[NormalizedOutcomeOffer]] = {}
    for offer in offers:
        groups.setdefault((offer.sport, offer.match_id), []).append(offer)

    deduped: dict[tuple, Opportunity] = {}
    for group in groups.values():
        for opportunity in [
            *_analyze_complementary_pairs(group),
            *_analyze_total_goals(group),
        ]:
            deduped[_dedupe_key(opportunity)] = opportunity

    return sorted(
        deduped.values(),
        key=lambda item: (
            -(item.profit_margin or -999),
            item.match_id,
            item.opportunity_type,
            item.market_type,
        ),
    )
