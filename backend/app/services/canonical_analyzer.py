from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from ..models.schemas import (
    CanonicalMarket,
    CanonicalOffer,
    OpportunityAnalysisBenchmarkOut,
    OpportunityAnalysisRuleBenchmarkOut,
    OpportunityLeg,
)
from .canonical_offers import _clean_part
from .opportunity_analyzer import Opportunity, _middle_profit_margin, _profit_margin
from .middle_ev import (
    MiddleEstimate,
    MiddleMarketQuote,
    build_middle_estimator,
    fallback_rank,
)


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


@dataclass(frozen=True)
class CanonicalOpportunityAnalysisResult:
    opportunities: tuple[Opportunity, ...] = ()
    benchmark: OpportunityAnalysisBenchmarkOut = field(
        default_factory=OpportunityAnalysisBenchmarkOut
    )


@dataclass
class _RuleBenchmarkAcc:
    sport: str
    market_type: str
    rule: str
    duration_ms: int = 0
    group_count: int = 0
    offer_count: int = 0
    candidate_pair_count: int = 0
    publishable_candidate_count: int = 0
    opportunity_count: int = 0

    def to_model(self) -> OpportunityAnalysisRuleBenchmarkOut:
        return OpportunityAnalysisRuleBenchmarkOut(
            sport=self.sport,
            market_type=self.market_type,
            rule=self.rule,
            duration_ms=self.duration_ms,
            group_count=self.group_count,
            offer_count=self.offer_count,
            candidate_pair_count=self.candidate_pair_count,
            publishable_candidate_count=self.publishable_candidate_count,
            opportunity_count=self.opportunity_count,
        )


@dataclass
class _OpportunityAnalysisAcc:
    canonical_offer_load_ms: int = 0
    primary_match_lookup_ms: int = 0
    grouping_ms: int = 0
    two_way_arbitrage_ms: int = 0
    line_middle_ms: int = 0
    complementary_outcomes_ms: int = 0
    dedupe_sort_ms: int = 0
    output_build_ms: int = 0
    loaded_offer_count: int = 0
    same_market_group_count: int = 0
    line_market_group_count: int = 0
    event_market_family_group_count: int = 0
    opportunity_count: int = 0
    rules: dict[tuple[str, str, str], _RuleBenchmarkAcc] = field(default_factory=dict)

    def rule(
        self,
        *,
        sport: str,
        market_type: str,
        rule: str,
    ) -> _RuleBenchmarkAcc:
        key = (sport, market_type, rule)
        bucket = self.rules.get(key)
        if bucket is None:
            bucket = _RuleBenchmarkAcc(
                sport=sport,
                market_type=market_type,
                rule=rule,
            )
            self.rules[key] = bucket
        return bucket

    def to_model(self) -> OpportunityAnalysisBenchmarkOut:
        rule_rows = [
            bucket.to_model()
            for bucket in sorted(
                self.rules.values(),
                key=lambda item: (item.sport, item.market_type, item.rule),
            )
        ]
        candidate_pair_count = sum(row.candidate_pair_count for row in rule_rows)
        publishable_candidate_count = sum(
            row.publishable_candidate_count for row in rule_rows
        )
        return OpportunityAnalysisBenchmarkOut(
            canonical_offer_load_ms=self.canonical_offer_load_ms,
            primary_match_lookup_ms=self.primary_match_lookup_ms,
            grouping_ms=self.grouping_ms,
            two_way_arbitrage_ms=self.two_way_arbitrage_ms,
            line_middle_ms=self.line_middle_ms,
            complementary_outcomes_ms=self.complementary_outcomes_ms,
            dedupe_sort_ms=self.dedupe_sort_ms,
            output_build_ms=self.output_build_ms,
            loaded_offer_count=self.loaded_offer_count,
            same_market_group_count=self.same_market_group_count,
            line_market_group_count=self.line_market_group_count,
            event_market_family_group_count=self.event_market_family_group_count,
            candidate_pair_count=candidate_pair_count,
            publishable_candidate_count=publishable_candidate_count,
            opportunity_count=self.opportunity_count,
            rules=rule_rows,
        )


def analyze_canonical_offers(
    offers: Sequence[CanonicalOffer],
    *,
    min_gap: float = 0.0,
    event_primary_match_ids: Mapping[str, str] | None = None,
    max_middle_opportunities_per_market: int | None = 10,
    enable_fitted_middles: bool = True,
    min_fitted_middle_ev_percent: float = 0.0,
) -> list[Opportunity]:
    """Find two-leg opportunities from canonical bookmaker offers."""
    return list(
        analyze_canonical_offers_with_benchmark(
            offers,
            min_gap=min_gap,
            event_primary_match_ids=event_primary_match_ids,
            max_middle_opportunities_per_market=max_middle_opportunities_per_market,
            enable_fitted_middles=enable_fitted_middles,
            min_fitted_middle_ev_percent=min_fitted_middle_ev_percent,
        ).opportunities
    )


def analyze_canonical_offers_with_benchmark(
    offers: Sequence[CanonicalOffer],
    *,
    min_gap: float = 0.0,
    event_primary_match_ids: Mapping[str, str] | None = None,
    max_middle_opportunities_per_market: int | None = 10,
    enable_fitted_middles: bool = True,
    min_fitted_middle_ev_percent: float = 0.0,
    canonical_offer_load_ms: int = 0,
    primary_match_lookup_ms: int = 0,
) -> CanonicalOpportunityAnalysisResult:
    """Find canonical opportunities and return observation-only benchmark metrics."""
    metrics = _OpportunityAnalysisAcc(
        canonical_offer_load_ms=int(canonical_offer_load_ms),
        primary_match_lookup_ms=int(primary_match_lookup_ms),
        loaded_offer_count=len(offers),
    )
    deduped: dict[tuple, Opportunity] = {}

    grouping_started_at = time.perf_counter()
    same_market_groups = _same_market_groups(offers)
    line_market_groups = _line_market_groups(offers)
    event_market_family_groups = _event_market_family_groups(offers)
    metrics.grouping_ms = int((time.perf_counter() - grouping_started_at) * 1000)
    metrics.same_market_group_count = len(same_market_groups)
    metrics.line_market_group_count = len(line_market_groups)
    metrics.event_market_family_group_count = len(event_market_family_groups)

    for group in same_market_groups.values():
        context = _context(group, event_primary_match_ids=event_primary_match_ids)
        for opportunity in _analyze_two_way_arbitrage(group, context, metrics):
            deduped[_dedupe_key(opportunity)] = opportunity

    for group in line_market_groups.values():
        context = _context(group, event_primary_match_ids=event_primary_match_ids)
        for opportunity in _analyze_line_middle(
            group,
            context,
            metrics,
            min_gap=min_gap,
            max_opportunities=max_middle_opportunities_per_market,
            enable_fitted_middles=enable_fitted_middles,
            min_fitted_middle_ev_percent=min_fitted_middle_ev_percent,
        ):
            deduped[_dedupe_key(opportunity)] = opportunity

    for group in event_market_family_groups.values():
        context = _context(group, event_primary_match_ids=event_primary_match_ids)
        for opportunity in _analyze_complementary_outcomes(group, context, metrics):
            deduped[_dedupe_key(opportunity)] = opportunity

    output_build_started_at = time.perf_counter()
    opportunities = list(deduped.values())
    metrics.output_build_ms = int(
        (time.perf_counter() - output_build_started_at) * 1000
    )

    dedupe_sort_started_at = time.perf_counter()
    opportunities = sorted(opportunities, key=_opportunity_sort_key)
    metrics.dedupe_sort_ms = int((time.perf_counter() - dedupe_sort_started_at) * 1000)
    metrics.opportunity_count = len(opportunities)

    return CanonicalOpportunityAnalysisResult(
        opportunities=tuple(opportunities),
        benchmark=metrics.to_model(),
    )


def _analyze_two_way_arbitrage(
    group: Sequence[CanonicalOffer],
    context: _RuleContext,
    metrics: _OpportunityAnalysisAcc,
) -> list[Opportunity]:
    started_at = time.perf_counter()
    if not group:
        return []
    market = group[0].market
    rule_metrics = metrics.rule(
        sport=market.sport,
        market_type=market.market_type,
        rule="same_line_arbitrage",
    )
    rule_metrics.group_count += 1
    rule_metrics.offer_count += len(group)
    opportunities: list[Opportunity] = []
    try:
        for first, second in combinations(group, 2):
            rule_metrics.candidate_pair_count += 1
            if first.bookmaker_id == second.bookmaker_id:
                continue
            if not _is_two_way_pair(first, second):
                continue
            if not _has_positive_odds(first, second):
                continue
            margin = _profit_margin(first.odds, second.odds)
            if margin is None or margin <= 0:
                continue
            rule_metrics.publishable_candidate_count += 1
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
        rule_metrics.opportunity_count += len(opportunities)
        return opportunities
    finally:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        rule_metrics.duration_ms += duration_ms
        metrics.two_way_arbitrage_ms += duration_ms


def _analyze_line_middle(
    group: Sequence[CanonicalOffer],
    context: _RuleContext,
    metrics: _OpportunityAnalysisAcc,
    *,
    min_gap: float,
    max_opportunities: int | None,
    enable_fitted_middles: bool = True,
    min_fitted_middle_ev_percent: float = 0.0,
) -> list[Opportunity]:
    started_at = time.perf_counter()
    if not group:
        return []
    market = group[0].market
    rule_metrics = metrics.rule(
        sport=market.sport,
        market_type=market.market_type,
        rule="line_middle",
    )
    rule_metrics.group_count += 1
    rule_metrics.offer_count += len(group)
    try:
        return _analyze_line_middle_inner(
            group,
            context,
            rule_metrics=rule_metrics,
            min_gap=min_gap,
            max_opportunities=max_opportunities,
            enable_fitted_middles=enable_fitted_middles,
            min_fitted_middle_ev_percent=min_fitted_middle_ev_percent,
        )
    finally:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        rule_metrics.duration_ms += duration_ms
        metrics.line_middle_ms += duration_ms


def _analyze_line_middle_inner(
    group: Sequence[CanonicalOffer],
    context: _RuleContext,
    *,
    rule_metrics: _RuleBenchmarkAcc,
    min_gap: float,
    max_opportunities: int | None,
    enable_fitted_middles: bool = True,
    min_fitted_middle_ev_percent: float = 0.0,
) -> list[Opportunity]:
    if not enable_fitted_middles:
        return []
    if max_opportunities is not None and max_opportunities <= 0:
        return []

    market = group[0].market
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
    estimator = build_middle_estimator(
        sport=market.sport,
        market_type=market.market_type,
        market_quotes=market_quotes,
    )
    over_by_line, under_by_line = _line_middle_offer_buckets(group)
    for low_line in sorted(over_by_line):
        for high_line in sorted(under_by_line):
            if low_line >= high_line:
                continue
            for low in over_by_line[low_line]:
                for high in under_by_line[high_line]:
                    if low.bookmaker_id == high.bookmaker_id:
                        continue
                    rule_metrics.candidate_pair_count += 1
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
                    estimate = estimator.estimate(
                        low_line=low_line,
                        high_line=high_line,
                        low_odds=low.odds,
                        high_odds=high.odds,
                        outside_margin=margin,
                        middle_margin=middle_margin,
                        min_gap=min_gap,
                        outside_margin_floor=(
                            _LINE_MIDDLE_OUTSIDE_MARGIN_FLOORS.get(
                                low.market.market_type
                            )
                        ),
                    )
                    if not estimate.should_publish:
                        continue
                    if min_fitted_middle_ev_percent > 0:
                        ev = estimate.expected_roi
                        if ev is None or ev * 100 < min_fitted_middle_ev_percent:
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
    rule_metrics.publishable_candidate_count += len(candidates)

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
    rule_metrics.opportunity_count += len(opportunities)
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


def _line_middle_offer_buckets(
    group: Sequence[CanonicalOffer],
) -> tuple[dict[float, list[CanonicalOffer]], dict[float, list[CanonicalOffer]]]:
    over_by_line: dict[float, list[CanonicalOffer]] = {}
    under_by_line: dict[float, list[CanonicalOffer]] = {}
    for offer in group:
        line = offer.market.line
        if line is None:
            continue
        if offer.outcome_code == "over":
            over_by_line.setdefault(line, []).append(offer)
        elif offer.outcome_code == "under":
            under_by_line.setdefault(line, []).append(offer)
    return over_by_line, under_by_line


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
    metrics: _OpportunityAnalysisAcc,
) -> list[Opportunity]:
    started_at = time.perf_counter()
    if not group:
        return []
    has_complementary_market = any(
        offer.market.market_type in {"result", "double_chance"} for offer in group
    )
    rule_metrics = None
    if has_complementary_market:
        market = group[0].market
        rule_metrics = metrics.rule(
            sport=market.sport,
            market_type="football_result_double_chance",
            rule="complementary_outcomes",
        )
        rule_metrics.group_count += 1
        rule_metrics.offer_count += len(group)
    by_key: dict[tuple[str, str], list[CanonicalOffer]] = {}
    for offer in group:
        by_key.setdefault(
            (offer.market.market_type, offer.outcome_code),
            [],
        ).append(offer)

    opportunities: list[Opportunity] = []
    try:
        for result_key, double_chance_key in _COMPLEMENTARY_OUTCOME_PAIRS.items():
            for result_offer in by_key.get(result_key, []):
                for double_chance_offer in by_key.get(double_chance_key, []):
                    if rule_metrics is not None:
                        rule_metrics.candidate_pair_count += 1
                    if result_offer.bookmaker_id == double_chance_offer.bookmaker_id:
                        continue
                    if not _has_positive_odds(result_offer, double_chance_offer):
                        continue
                    margin = _profit_margin(result_offer.odds, double_chance_offer.odds)
                    if margin is None or margin <= 0:
                        continue
                    if rule_metrics is not None:
                        rule_metrics.publishable_candidate_count += 1
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
        if rule_metrics is not None:
            rule_metrics.opportunity_count += len(opportunities)
        return opportunities
    finally:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        if rule_metrics is not None:
            rule_metrics.duration_ms += duration_ms
        metrics.complementary_outcomes_ms += duration_ms


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
        groups.setdefault(_market_family_key(offer, include_market_type=True), []).append(
            offer
        )
    return groups


def _event_market_family_groups(
    offers: Sequence[CanonicalOffer],
) -> dict[tuple, list[CanonicalOffer]]:
    groups: dict[tuple, list[CanonicalOffer]] = {}
    for offer in offers:
        groups.setdefault(_market_family_key(offer, include_market_type=False), []).append(
            offer
        )
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
