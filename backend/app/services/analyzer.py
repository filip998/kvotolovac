from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations

from ..models.schemas import NormalizedOdds, ResolvedEventMemberOut
from .match_unification.player_identity import (
    ActiveEventMembership,
    is_player_market_candidate,
    resolve_event_players,
)
from .middle_ev import MiddleMarketQuote, estimate_middle


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
    resolved_event_id: str | None = None
    bookmaker_a_match_id: str | None = None
    bookmaker_b_match_id: str | None = None
    middle_hit_probability: float | None = None
    middle_ev: float | None = None
    middle_model_confidence: str | None = None
    middle_model_diagnostics: dict[str, object] | None = None
    middle_ev_rank: float | None = None


@dataclass(frozen=True)
class _OddsGroup:
    match_id: str
    market_type: str
    player_name: str | None
    odds: list[NormalizedOdds]
    resolved_event_id: str | None = None


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


def _middle_market_quotes(odds_list: list[NormalizedOdds]) -> list[MiddleMarketQuote]:
    quotes: list[MiddleMarketQuote] = []
    for odds in odds_list:
        if odds.over_odds is not None:
            quotes.append(
                MiddleMarketQuote(
                    bookmaker_id=odds.bookmaker_id,
                    line=odds.threshold,
                    outcome_code="over",
                    odds=odds.over_odds,
                )
            )
        if odds.under_odds is not None:
            quotes.append(
                MiddleMarketQuote(
                    bookmaker_id=odds.bookmaker_id,
                    line=odds.threshold,
                    outcome_code="under",
                    odds=odds.under_odds,
                )
            )
    return quotes


def _representative_match_id(
    *,
    resolved_event_id: str,
    odds: list[NormalizedOdds],
    event_primary_match_ids: Mapping[str, str] | None,
) -> str:
    if event_primary_match_ids and resolved_event_id in event_primary_match_ids:
        return event_primary_match_ids[resolved_event_id]
    return min(item.match_id for item in odds)


def _legacy_groups(odds_list: list[NormalizedOdds]) -> list[_OddsGroup]:
    groups: dict[tuple[str, str, str | None], list[NormalizedOdds]] = {}
    for odds in odds_list:
        key = (
            odds.match_id,
            _comparison_market_type(odds.market_type),
            odds.player_name,
        )
        groups.setdefault(key, []).append(odds)

    return [
        _OddsGroup(
            match_id=match_id,
            market_type=market_type,
            player_name=player_name,
            odds=group,
        )
        for (match_id, market_type, player_name), group in groups.items()
    ]


def _analysis_groups(
    odds_list: list[NormalizedOdds],
    *,
    event_members: list[ResolvedEventMemberOut] | None,
    event_primary_match_ids: Mapping[str, str] | None,
) -> list[_OddsGroup]:
    if event_members is None:
        return _legacy_groups(odds_list)

    membership = ActiveEventMembership.from_members(event_members)
    player_resolution = resolve_event_players(odds_list, membership)
    event_scoped_odds = player_resolution.scoped_odds
    scoped_odds_ids = {id(item.odds) for item in event_scoped_odds}

    event_groups: dict[tuple[str, str, str], list] = {}
    for item in event_scoped_odds:
        key = (
            item.resolved_event_id,
            _comparison_market_type(item.odds.market_type),
            item.event_scoped_player_key,
        )
        event_groups.setdefault(key, []).append(item)

    groups: list[_OddsGroup] = []
    for (resolved_event_id, market_type, _player_key), scoped_group in event_groups.items():
        group_odds = [item.odds for item in scoped_group]
        groups.append(
            _OddsGroup(
                match_id=_representative_match_id(
                    resolved_event_id=resolved_event_id,
                    odds=group_odds,
                    event_primary_match_ids=event_primary_match_ids,
                ),
                market_type=market_type,
                player_name=scoped_group[0].event_player_display_name,
                odds=group_odds,
                resolved_event_id=resolved_event_id,
            )
        )

    event_non_player_groups: dict[tuple[str, str, str | None], list[NormalizedOdds]] = {}
    event_grouped_odds_ids = set(scoped_odds_ids)
    for odds in odds_list:
        if id(odds) in scoped_odds_ids:
            continue
        if is_player_market_candidate(odds):
            event_grouped_odds_ids.add(id(odds))
            continue
        member = membership.member_for(
            match_id=odds.match_id,
            bookmaker_id=odds.bookmaker_id,
        )
        if member is None:
            continue
        key = (
            member.resolved_event_id,
            _comparison_market_type(odds.market_type),
            odds.player_name,
        )
        event_non_player_groups.setdefault(key, []).append(odds)
        event_grouped_odds_ids.add(id(odds))

    for (
        resolved_event_id,
        market_type,
        player_name,
    ), group_odds in event_non_player_groups.items():
        groups.append(
            _OddsGroup(
                match_id=_representative_match_id(
                    resolved_event_id=resolved_event_id,
                    odds=group_odds,
                    event_primary_match_ids=event_primary_match_ids,
                ),
                market_type=market_type,
                player_name=player_name,
                odds=group_odds,
                resolved_event_id=resolved_event_id,
            )
        )

    unresolved_or_legacy_odds = [
        odds for odds in odds_list if id(odds) not in event_grouped_odds_ids
    ]
    groups.extend(_legacy_groups(unresolved_or_legacy_odds))
    return groups


def find_threshold_gaps(
    odds_list: list[NormalizedOdds],
    min_gap: float = 0.0,
    *,
    event_members: list[ResolvedEventMemberOut] | None = None,
    event_primary_match_ids: Mapping[str, str] | None = None,
) -> list[Discrepancy]:
    """
    Find threshold discrepancies: where bookmaker A offers 'over X' and
    bookmaker B offers 'under Y' with Y > X → gap of Y - X points.
    """
    discrepancies: list[Discrepancy] = []

    for group in _analysis_groups(
        odds_list,
        event_members=event_members,
        event_primary_match_ids=event_primary_match_ids,
    ):
        if len(group.odds) < 2:
            continue
        market_quotes = _middle_market_quotes(group.odds)

        # Compare every pair of bookmakers
        for a, b in combinations(group.odds, 2):
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
                        best_a_match_id = a.match_id
                        best_b_match_id = b.match_id

                        if margin_ab is not None and margin_ab > 0:
                            best_margin = margin_ab
                            best_over = a.over_odds
                            best_under = b.under_odds
                            best_a_id = a.bookmaker_id
                            best_b_id = b.bookmaker_id
                            best_a_match_id = a.match_id
                            best_b_match_id = b.match_id

                        if margin_ba is not None and margin_ba > 0 and (best_margin is None or margin_ba > best_margin):
                            best_margin = margin_ba
                            best_over = b.over_odds
                            best_under = a.under_odds
                            best_a_id = b.bookmaker_id
                            best_b_id = a.bookmaker_id
                            best_a_match_id = b.match_id
                            best_b_match_id = a.match_id

                        if best_margin is not None and best_margin > 0:
                            discrepancies.append(
                                Discrepancy(
                                    match_id=group.match_id,
                                    market_type=group.market_type,
                                    player_name=group.player_name,
                                    bookmaker_a_id=best_a_id,
                                    bookmaker_b_id=best_b_id,
                                    threshold_a=a.threshold,
                                    threshold_b=b.threshold,
                                    odds_a=best_over,
                                    odds_b=best_under,
                                    gap=0.0,
                                    profit_margin=best_margin,
                                    middle_profit_margin=None,
                                    resolved_event_id=group.resolved_event_id,
                                    bookmaker_a_match_id=best_a_match_id,
                                    bookmaker_b_match_id=best_b_match_id,
                                )
                            )
                continue

            gap = b.threshold - a.threshold
            # Bookmaker A over (lower threshold) + Bookmaker B under (higher threshold)
            if a.over_odds is None or b.under_odds is None:
                continue
            margin = _profit_margin(a.over_odds, b.under_odds)
            middle_margin = _middle_profit_margin(a.over_odds, b.under_odds)
            estimate = estimate_middle(
                sport=a.sport,
                market_type=group.market_type,
                low_line=a.threshold,
                high_line=b.threshold,
                low_odds=a.over_odds,
                high_odds=b.under_odds,
                market_quotes=market_quotes,
                outside_margin=margin,
                middle_margin=middle_margin,
                min_gap=min_gap,
            )
            if not estimate.should_publish:
                continue

            discrepancies.append(
                Discrepancy(
                    match_id=group.match_id,
                    market_type=group.market_type,
                    player_name=group.player_name,
                    bookmaker_a_id=a.bookmaker_id,
                    bookmaker_b_id=b.bookmaker_id,
                    threshold_a=a.threshold,
                    threshold_b=b.threshold,
                    odds_a=a.over_odds,
                    odds_b=b.under_odds,
                    gap=round(gap, 1),
                    profit_margin=margin,
                    middle_profit_margin=middle_margin,
                    resolved_event_id=group.resolved_event_id,
                    bookmaker_a_match_id=a.match_id,
                    bookmaker_b_match_id=b.match_id,
                    middle_hit_probability=estimate.hit_probability,
                    middle_ev=estimate.expected_roi,
                    middle_model_confidence=estimate.confidence,
                    middle_model_diagnostics=estimate.diagnostics,
                    middle_ev_rank=estimate.rank_score,
                )
            )

    return discrepancies


def analyze(
    odds_list: list[NormalizedOdds],
    min_gap: float = 0.0,
    *,
    event_members: list[ResolvedEventMemberOut] | None = None,
    event_primary_match_ids: Mapping[str, str] | None = None,
) -> list[Discrepancy]:
    """Main entry: find all discrepancies across the odds list."""
    return find_threshold_gaps(
        odds_list,
        min_gap=min_gap,
        event_members=event_members,
        event_primary_match_ids=event_primary_match_ids,
    )
