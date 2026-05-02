from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations

from ..models.schemas import NormalizedOutcomeOffer, OpportunityLeg, ResolvedEventMemberOut


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
    resolved_event_id: str | None = None
    subject_type: str | None = None
    subject_key: str | None = None
    subject_name: str | None = None
    market_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class _OfferAnalysisItem:
    offer: NormalizedOutcomeOffer
    resolved_event_id: str | None = None


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
        match_id=offer.match_id,
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
                leg.match_id,
                leg.market_type,
                leg.outcome_code,
                leg.line,
                leg.odds,
            )
            for leg in opportunity.legs
        )
    )
    return (
        opportunity.resolved_event_id,
        opportunity.match_id,
        opportunity.opportunity_type,
        opportunity.market_type,
        opportunity.line,
        leg_keys,
    )


def _analyze_complementary_pairs(
    group: list[_OfferAnalysisItem],
    *,
    match_id: str,
    resolved_event_id: str | None,
) -> list[Opportunity]:
    by_key: dict[tuple[str, str], list[_OfferAnalysisItem]] = {}
    for item in group:
        by_key.setdefault(
            (item.offer.market_type, item.offer.outcome_code),
            [],
        ).append(item)

    opportunities: list[Opportunity] = []
    for result_key, double_chance_key in _COMPLEMENTARY_RESULT_PAIRS.items():
        for result_item in by_key.get(result_key, []):
            for dc_item in by_key.get(double_chance_key, []):
                result_offer = result_item.offer
                dc_offer = dc_item.offer
                if result_offer.bookmaker_id == dc_offer.bookmaker_id:
                    continue
                margin = _profit_margin(result_offer.odds, dc_offer.odds)
                if margin is None or margin <= 0:
                    continue
                opportunities.append(
                    Opportunity(
                        sport=result_offer.sport,
                        match_id=match_id,
                        opportunity_type="complementary_outcomes",
                        market_type="football_result_double_chance",
                        line=None,
                        profit_margin=margin,
                        middle_profit_margin=None,
                        legs=[_leg(result_offer), _leg(dc_offer)],
                        resolved_event_id=resolved_event_id,
                    )
                )
    return opportunities


def _analyze_total_goals(
    group: list[_OfferAnalysisItem],
    *,
    match_id: str,
    resolved_event_id: str | None,
) -> list[Opportunity]:
    totals = [
        item.offer
        for item in group
        if item.offer.market_type == "football_total_goals"
    ]
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
                        match_id=match_id,
                        opportunity_type="same_line_arbitrage",
                        market_type="football_total_goals",
                        line=a.line,
                        profit_margin=margin,
                        middle_profit_margin=None,
                        legs=[_leg(a), _leg(b)],
                        resolved_event_id=resolved_event_id,
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
                match_id=match_id,
                opportunity_type="middle",
                market_type="football_total_goals",
                line=low.line,
                profit_margin=margin,
                middle_profit_margin=middle_margin,
                legs=[_leg(low), _leg(high)],
                resolved_event_id=resolved_event_id,
            )
        )

    return opportunities


def _active_member_event_lookup(
    event_members: list[ResolvedEventMemberOut],
) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for member in sorted(
        event_members,
        key=lambda item: (item.resolved_event_id, item.id, item.match_id, item.bookmaker_id),
    ):
        if member.status != "active":
            continue
        lookup.setdefault((member.match_id, member.bookmaker_id), member.resolved_event_id)
    return lookup


def _group_match_id(
    group: list[_OfferAnalysisItem],
    *,
    resolved_event_id: str | None,
    event_primary_match_ids: Mapping[str, str] | None,
) -> str:
    if resolved_event_id is None:
        return group[0].offer.match_id
    if event_primary_match_ids and resolved_event_id in event_primary_match_ids:
        return event_primary_match_ids[resolved_event_id]
    return min(item.offer.match_id for item in group)


def analyze_outcome_offers(
    offers: list[NormalizedOutcomeOffer],
    *,
    event_members: list[ResolvedEventMemberOut] | None = None,
    event_primary_match_ids: Mapping[str, str] | None = None,
) -> list[Opportunity]:
    event_by_member = (
        _active_member_event_lookup(event_members)
        if event_members
        else {}
    )

    groups: dict[tuple[str, str], list[_OfferAnalysisItem]] = {}
    for offer in offers:
        resolved_event_id = event_by_member.get((offer.match_id, offer.bookmaker_id))
        group_id = resolved_event_id or offer.match_id
        groups.setdefault((offer.sport, group_id), []).append(
            _OfferAnalysisItem(
                offer=offer,
                resolved_event_id=resolved_event_id,
            )
        )

    deduped: dict[tuple, Opportunity] = {}
    for group in groups.values():
        resolved_event_id = group[0].resolved_event_id
        match_id = _group_match_id(
            group,
            resolved_event_id=resolved_event_id,
            event_primary_match_ids=event_primary_match_ids,
        )
        for opportunity in [
            *_analyze_complementary_pairs(
                group,
                match_id=match_id,
                resolved_event_id=resolved_event_id,
            ),
            *_analyze_total_goals(
                group,
                match_id=match_id,
                resolved_event_id=resolved_event_id,
            ),
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
