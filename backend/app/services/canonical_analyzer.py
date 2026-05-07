from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from ..models.schemas import CanonicalMarket, CanonicalOffer, OpportunityLeg
from .canonical_offers import _clean_part
from .opportunity_analyzer import Opportunity, _middle_profit_margin, _profit_margin
from .middle_ev import MiddleEstimate, MiddleMarketQuote, estimate_middle, fallback_rank


_COMPLEMENTARY_OUTCOME_PAIRS = {
    ("result", "home"): ("double_chance", "draw_or_away"),
    ("result", "away"): ("double_chance", "home_or_draw"),
    ("result", "draw"): ("double_chance", "home_or_away"),
}
_LINE_OUTCOME_PAIR = {"over", "under"}
_TWO_WAY_MARKET_OUTCOMES = {
    "match_winner": {"home", "away"},
}
_LINE_MIDDLE_OUTSIDE_MARGIN_FLOORS = {
    "football_total_goals": -0.05,
}


@dataclass(frozen=True)
class _RuleContext:
    match_id: str
    resolved_event_id: str | None


@dataclass(frozen=True)
class _LineMiddleCandidate:
    low: CanonicalOffer
    high: CanonicalOffer
    margin: float
    middle_margin: float
    estimate: MiddleEstimate


def analyze_canonical_offers(
    offers: Sequence[CanonicalOffer],
    *,
    min_gap: float = 0.0,
    event_primary_match_ids: Mapping[str, str] | None = None,
    max_middle_opportunities_per_market: int | None = 10,
) -> list[Opportunity]:
    """Find two-leg opportunities from canonical bookmaker offers."""
    deduped: dict[tuple, Opportunity] = {}

    for group in _same_market_groups(offers).values():
        context = _context(group, event_primary_match_ids=event_primary_match_ids)
        for opportunity in _analyze_two_way_arbitrage(group, context):
            deduped[_dedupe_key(opportunity)] = opportunity

    for group in _line_market_groups(offers).values():
        context = _context(group, event_primary_match_ids=event_primary_match_ids)
        for opportunity in _analyze_line_middle(
            group,
            context,
            min_gap=min_gap,
            max_opportunities=max_middle_opportunities_per_market,
        ):
            deduped[_dedupe_key(opportunity)] = opportunity

    for group in _event_market_family_groups(offers).values():
        context = _context(group, event_primary_match_ids=event_primary_match_ids)
        for opportunity in _analyze_complementary_outcomes(group, context):
            deduped[_dedupe_key(opportunity)] = opportunity

    return sorted(
        deduped.values(),
        key=_opportunity_sort_key,
    )


def _analyze_two_way_arbitrage(
    group: Sequence[CanonicalOffer],
    context: _RuleContext,
) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    for first, second in combinations(group, 2):
        if first.bookmaker_id == second.bookmaker_id:
            continue
        if not _is_two_way_pair(first, second):
            continue
        if not _has_positive_odds(first, second):
            continue
        margin = _profit_margin(first.odds, second.odds)
        if margin is None or margin <= 0:
            continue
        market = first.market
        opportunities.append(
            Opportunity(
                sport=market.sport,
                match_id=context.match_id,
                opportunity_type="same_line_arbitrage",
                market_type=market.market_type,
                line=market.line,
                profit_margin=margin,
                middle_profit_margin=None,
                legs=[_leg(first), _leg(second)],
                resolved_event_id=context.resolved_event_id,
                subject_type=market.subject_type,
                subject_key=market.subject_key,
                subject_name=market.subject_name,
                market_keys=(market.market_key,),
            )
        )
    return opportunities


def _analyze_line_middle(
    group: Sequence[CanonicalOffer],
    context: _RuleContext,
    *,
    min_gap: float,
    max_opportunities: int | None,
) -> list[Opportunity]:
    if max_opportunities is not None and max_opportunities <= 0:
        return []

    candidates: list[_LineMiddleCandidate] = []
    market_quotes = [
        MiddleMarketQuote(
            bookmaker_id=offer.bookmaker_id,
            line=offer.market.line,
            outcome_code=offer.outcome_code,
            odds=offer.odds,
        )
        for offer in group
        if offer.market.line is not None
    ]
    for first, second in combinations(group, 2):
        if first.bookmaker_id == second.bookmaker_id:
            continue
        if first.market.line is None or second.market.line is None:
            continue
        if first.market.line == second.market.line:
            continue

        low, high = (first, second) if first.market.line < second.market.line else (second, first)
        if low.outcome_code != "over" or high.outcome_code != "under":
            continue
        if not _has_positive_odds(low, high):
            continue

        margin = _profit_margin(low.odds, high.odds)
        middle_margin = _middle_profit_margin(low.odds, high.odds)
        if not _passes_line_middle_margin_filter(
            low.market.market_type,
            margin=margin,
            middle_margin=middle_margin,
        ):
            continue

        if margin is None or middle_margin is None:
            continue
        estimate = estimate_middle(
            sport=low.market.sport,
            market_type=low.market.market_type,
            low_line=low.market.line,
            high_line=high.market.line,
            low_odds=low.odds,
            high_odds=high.odds,
            market_quotes=market_quotes,
            outside_margin=margin,
            middle_margin=middle_margin,
            min_gap=min_gap,
            outside_margin_floor=_LINE_MIDDLE_OUTSIDE_MARGIN_FLOORS.get(
                low.market.market_type
            ),
        )
        if not estimate.should_publish:
            continue
        candidates.append(
            _LineMiddleCandidate(
                low=low,
                high=high,
                margin=margin,
                middle_margin=middle_margin,
                estimate=estimate,
            )
        )

    ranked_candidates = sorted(
        candidates,
        key=_line_middle_candidate_rank,
        reverse=True,
    )
    if max_opportunities is not None:
        ranked_candidates = ranked_candidates[:max_opportunities]

    opportunities: list[Opportunity] = []
    for candidate in ranked_candidates:
        low = candidate.low
        high = candidate.high
        subject_type, subject_key, subject_name = _subject_metadata(
            low.market,
            high.market,
        )
        opportunities.append(
            Opportunity(
                sport=low.market.sport,
                match_id=context.match_id,
                opportunity_type="middle",
                market_type=low.market.market_type,
                line=low.market.line,
                profit_margin=candidate.margin,
                middle_profit_margin=candidate.middle_margin,
                legs=[_leg(low), _leg(high)],
                resolved_event_id=context.resolved_event_id,
                subject_type=subject_type,
                subject_key=subject_key,
                subject_name=subject_name,
                market_keys=tuple(sorted({low.market_key, high.market_key})),
                middle_hit_probability=candidate.estimate.hit_probability,
                middle_ev=candidate.estimate.expected_roi,
                middle_model_confidence=candidate.estimate.confidence,
                middle_model_diagnostics=candidate.estimate.diagnostics,
                middle_ev_rank=candidate.estimate.rank_score,
            )
        )
    return opportunities


def _line_middle_candidate_rank(candidate: _LineMiddleCandidate) -> tuple[float, ...]:
    low_line = candidate.low.market.line
    high_line = candidate.high.market.line
    if low_line is None or high_line is None:
        fallback = (0.0, 0.0, 0.0, 0.0, 0.0)
    else:
        fallback = fallback_rank(
            low_line=low_line,
            high_line=high_line,
            middle_margin=candidate.middle_margin,
            outside_margin=candidate.margin,
            low_odds=candidate.low.odds,
            high_odds=candidate.high.odds,
        )
    return (
        1.0 if candidate.estimate.rank_score is not None else 0.0,
        candidate.estimate.rank_score or 0.0,
        *fallback,
    )


def _opportunity_sort_key(item: Opportunity) -> tuple[float, str, str, str]:
    if item.opportunity_type == "middle":
        value = item.middle_ev_rank
        if value is None:
            value = item.profit_margin
    else:
        value = item.profit_margin
    return (
        -(value if value is not None else -999.0),
        item.match_id,
        item.opportunity_type,
        item.market_type,
    )


def _analyze_complementary_outcomes(
    group: Sequence[CanonicalOffer],
    context: _RuleContext,
) -> list[Opportunity]:
    by_key: dict[tuple[str, str], list[CanonicalOffer]] = {}
    for offer in group:
        by_key.setdefault(
            (offer.market.market_type, offer.outcome_code),
            [],
        ).append(offer)

    opportunities: list[Opportunity] = []
    for result_key, double_chance_key in _COMPLEMENTARY_OUTCOME_PAIRS.items():
        for result_offer in by_key.get(result_key, []):
            for double_chance_offer in by_key.get(double_chance_key, []):
                if result_offer.bookmaker_id == double_chance_offer.bookmaker_id:
                    continue
                if not _has_positive_odds(result_offer, double_chance_offer):
                    continue
                margin = _profit_margin(result_offer.odds, double_chance_offer.odds)
                if margin is None or margin <= 0:
                    continue
                subject_type, subject_key, subject_name = _subject_metadata(
                    result_offer.market,
                    double_chance_offer.market,
                )
                opportunities.append(
                    Opportunity(
                        sport=result_offer.market.sport,
                        match_id=context.match_id,
                        opportunity_type="complementary_outcomes",
                        market_type="football_result_double_chance",
                        line=None,
                        profit_margin=margin,
                        middle_profit_margin=None,
                        legs=[_leg(result_offer), _leg(double_chance_offer)],
                        resolved_event_id=context.resolved_event_id,
                        subject_type=subject_type,
                        subject_key=subject_key,
                        subject_name=subject_name,
                        market_keys=tuple(
                            sorted(
                                {
                                    result_offer.market_key,
                                    double_chance_offer.market_key,
                                }
                            )
                        ),
                    )
                )
    return opportunities


def _is_two_way_pair(first: CanonicalOffer, second: CanonicalOffer) -> bool:
    outcomes = {first.outcome_code, second.outcome_code}
    market_type = first.market.market_type
    if first.market.line is not None:
        return outcomes == _LINE_OUTCOME_PAIR
    return outcomes == _TWO_WAY_MARKET_OUTCOMES.get(market_type)


def _has_positive_odds(*offers: CanonicalOffer) -> bool:
    return all(offer.odds > 0 for offer in offers)


def _passes_line_middle_margin_filter(
    market_type: str,
    *,
    margin: float | None,
    middle_margin: float | None,
) -> bool:
    floor = _LINE_MIDDLE_OUTSIDE_MARGIN_FLOORS.get(market_type)
    if floor is None:
        return margin is not None and middle_margin is not None
    return (
        margin is not None
        and middle_margin is not None
        and middle_margin > 0
        and margin >= floor
    )


def _same_market_groups(
    offers: Sequence[CanonicalOffer],
) -> dict[str, list[CanonicalOffer]]:
    groups: dict[str, list[CanonicalOffer]] = {}
    for offer in offers:
        groups.setdefault(offer.market_key, []).append(offer)
    return groups


def _line_market_groups(
    offers: Sequence[CanonicalOffer],
) -> dict[tuple, list[CanonicalOffer]]:
    groups: dict[tuple, list[CanonicalOffer]] = {}
    for offer in offers:
        if offer.market.line is None:
            continue
        if offer.outcome_code not in _LINE_OUTCOME_PAIR:
            continue
        groups.setdefault(_market_family_key(offer, include_market_type=True), []).append(offer)
    return groups


def _event_market_family_groups(
    offers: Sequence[CanonicalOffer],
) -> dict[tuple, list[CanonicalOffer]]:
    groups: dict[tuple, list[CanonicalOffer]] = {}
    for offer in offers:
        groups.setdefault(_market_family_key(offer, include_market_type=False), []).append(offer)
    return groups


def _market_family_key(
    offer: CanonicalOffer,
    *,
    include_market_type: bool,
) -> tuple:
    market = offer.market
    key = [
        market.sport,
        _event_identity(offer),
        market.subject_type,
        _subject_identity(market),
        market.period,
        market.scope,
    ]
    if include_market_type:
        key.insert(2, market.market_type)
    return tuple(key)


def _subject_metadata(
    first: CanonicalMarket,
    second: CanonicalMarket | None = None,
) -> tuple[str | None, str | None, str | None]:
    markets = [market for market in (first, second) if market is not None]
    subject_types = {market.subject_type for market in markets if market.subject_type}
    subject_type = sorted(subject_types)[0] if len(subject_types) == 1 else None
    subject_key = next(
        (market.subject_key for market in markets if market.subject_key),
        None,
    )
    subject_name = next(
        (market.subject_name for market in markets if market.subject_name),
        None,
    )
    return subject_type, subject_key, subject_name


def _event_identity(offer: CanonicalOffer) -> str:
    if offer.market.event_id:
        return f"event:{offer.market.event_id}"
    return f"match:{offer.market.match_id}"


def _subject_identity(market: CanonicalMarket) -> str | None:
    return _clean_part(market.subject_key) or _clean_part(market.subject_name)


def _context(
    group: Sequence[CanonicalOffer],
    *,
    event_primary_match_ids: Mapping[str, str] | None,
) -> _RuleContext:
    resolved_event_id = _resolved_event_id(group)
    if resolved_event_id and event_primary_match_ids and resolved_event_id in event_primary_match_ids:
        match_id = event_primary_match_ids[resolved_event_id]
    else:
        match_id = min(offer.market.match_id for offer in group)
    return _RuleContext(match_id=match_id, resolved_event_id=resolved_event_id)


def _resolved_event_id(group: Sequence[CanonicalOffer]) -> str | None:
    event_ids = {offer.market.event_id for offer in group if offer.market.event_id}
    if len(event_ids) == 1:
        return next(iter(event_ids))
    return None


def _leg(offer: CanonicalOffer) -> OpportunityLeg:
    market = offer.market
    return OpportunityLeg(
        match_id=market.bookmaker_match_id or market.match_id,
        bookmaker_id=offer.bookmaker_id,
        source_url=offer.source_url,
        market_type=market.source_market_type,
        outcome_code=offer.outcome_code,
        odds=offer.odds,
        line=market.line,
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
        opportunity.market_keys,
        leg_keys,
    )
