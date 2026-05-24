from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import logging
import time

from rapidfuzz import fuzz

from ...models.schemas import (
    BenchmarkEventCoverageOut,
    BenchmarkSplitClusterOut,
    BenchmarkSplitDiagnosticsOut,
    BenchmarkSplitEventFragmentOut,
    BenchmarkSplitMemberFragmentOut,
    BenchmarkSplitSportDiagnosticsOut,
    BenchmarkSplitWeakestMemberPairOut,
    MatchUnificationResolutionBenchmarkOut,
    MatchUnificationSourceMatchSlotBenchmarkOut,
    EventReviewCaseIn,
    NormalizedOdds,
    NormalizedOutcomeOffer,
    ResolvedEventIn,
    ResolvedEventMemberIn,
)
from ...store import odds_store
from .candidate_extraction import (
    _EventCandidateExtractionStats,
    _raw_odds_sources,
    extract_event_candidates,
)
from .event_matching import (
    EventCandidate,
    _expand_dotted_token,
    _orientation_scores,
)
from .decision_model import (
    EventDiagnosticDecision,
    EventDiagnosticDecisionCategory,
    _PairResolution,
    _ResolverTextCache,
    _event_time_delta_minutes,
    _parse_event_time,
    _quorum_resolution_passes,
    _significant_team_tokens,
    best_group_pair_decision,
    with_group_context,
)
from ..team_identity import (
    CANONICAL_TEAM_AUTO_MERGE_THRESHOLD as _CANONICAL_TEAM_AUTO_MERGE_THRESHOLD,
    canonical_team_auto_merge_analysis as _canonical_team_auto_merge_analysis,
    canonical_team_similarity_score as _canonical_team_similarity_score,
    comparison_team_text as _comparison_team_text,
    unsafe_compound_subset_match as _unsafe_compound_subset_match,
)
from ..outcome_normalizer import _build_football_event_resolutions
from ..text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_RESOLVER_VERSION = "match_unification_v1"
CANONICAL_TEAM_AUTO_MERGE_THRESHOLD = _CANONICAL_TEAM_AUTO_MERGE_THRESHOLD
def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


@dataclass(frozen=True)
class SameTimeCanonicalSlot:
    sport: str
    start_time: str
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    support_bookmakers: frozenset[str]
    raw_league_id: str


@dataclass(frozen=True)
class SameTimeCanonicalMergeProposal:
    source_team_id: int
    target_team_id: int
    source_team_name: str
    target_team_name: str
    source_support: int
    target_support: int
    sport: str
    start_time: str
    bookmaker_id: str
    raw_league_id: str
    canonical_home_team: str
    canonical_away_team: str
    score: float


def _symmetric_canonical_team_score(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
) -> float:
    return _canonical_team_similarity_score(left_name, right_name, sport=sport)


def _is_unsafe_compound_subset_match(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
) -> bool:
    return _unsafe_compound_subset_match(left_name, right_name, sport=sport)


def _canonical_team_auto_merge_score(
    source_team_name: str,
    target_team_name: str,
    *,
    sport: str | None = None,
) -> float | None:
    analysis = _canonical_team_auto_merge_analysis(
        source_team_name,
        target_team_name,
        sport=sport,
        threshold=CANONICAL_TEAM_AUTO_MERGE_THRESHOLD,
    )
    if not analysis.auto_merge_safe:
        return None
    return analysis.score


def _same_time_slot_orientation(
    source_slot: SameTimeCanonicalSlot,
    target_slot: SameTimeCanonicalSlot,
) -> tuple[tuple[int, int, str, str, float], tuple[int, int, str, str, float]] | None:
    sport = source_slot.sport
    home_score = _canonical_team_auto_merge_score(
        source_slot.home_team,
        target_slot.home_team,
        sport=sport,
    )
    away_score = _canonical_team_auto_merge_score(
        source_slot.away_team,
        target_slot.away_team,
        sport=sport,
    )
    same_orientation = (
        (
            source_slot.home_team_id,
            target_slot.home_team_id,
            source_slot.home_team,
            target_slot.home_team,
            home_score,
        ),
        (
            source_slot.away_team_id,
            target_slot.away_team_id,
            source_slot.away_team,
            target_slot.away_team,
            away_score,
        ),
    ) if home_score is not None and away_score is not None else None

    cross_home_score = _canonical_team_auto_merge_score(
        source_slot.home_team,
        target_slot.away_team,
        sport=sport,
    )
    cross_away_score = _canonical_team_auto_merge_score(
        source_slot.away_team,
        target_slot.home_team,
        sport=sport,
    )
    cross_orientation = (
        (
            source_slot.home_team_id,
            target_slot.away_team_id,
            source_slot.home_team,
            target_slot.away_team,
            cross_home_score,
        ),
        (
            source_slot.away_team_id,
            target_slot.home_team_id,
            source_slot.away_team,
            target_slot.home_team,
            cross_away_score,
        ),
    ) if cross_home_score is not None and cross_away_score is not None else None

    candidates = [
        orientation
        for orientation in (same_orientation, cross_orientation)
        if orientation is not None
    ]
    if not candidates:
        return None
    if len(candidates) == 2:
        same_score = same_orientation[0][4] + same_orientation[1][4]
        cross_score = cross_orientation[0][4] + cross_orientation[1][4]
        if same_score == cross_score:
            return None
    return max(candidates, key=lambda item: item[0][4] + item[1][4])


def _candidate_event_teams(candidate) -> set[str] | None:
    if not candidate.canonical_home_team or not candidate.canonical_away_team:
        return None
    return {candidate.canonical_home_team, candidate.canonical_away_team}


def _contextual_merge_source_ids(case) -> set[int]:
    if (
        case.reason_code != "candidate_team_match_same_start_time"
        or case.suggested_team_id is None
        or case.suggested_team_name is None
        or case.start_time is None
        or case.matched_counterpart_team is None
        or case.canonical_home_team is None
        or case.canonical_away_team is None
        or case.similarity_score is None
        or case.similarity_score < CANONICAL_TEAM_AUTO_MERGE_THRESHOLD
    ):
        return set()

    target_candidate = next(
        (
            candidate
            for candidate in case.candidate_teams
            if candidate.team_id == case.suggested_team_id
        ),
        None,
    )
    if (
        target_candidate is None
        or target_candidate.score is None
        or target_candidate.score < CANONICAL_TEAM_AUTO_MERGE_THRESHOLD
        or target_candidate.slot_support is None
    ):
        return set()

    target_event_teams = _candidate_event_teams(target_candidate)
    if target_event_teams is None:
        return set()

    source_team_ids: set[int] = set()
    for candidate in case.candidate_teams:
        if (
            candidate.team_id == case.suggested_team_id
            or candidate.score is None
            or candidate.score < CANONICAL_TEAM_AUTO_MERGE_THRESHOLD
            or candidate.slot_support is None
            or target_candidate.slot_support <= candidate.slot_support
        ):
            continue
        if (
            _canonical_team_auto_merge_score(
                candidate.team_name,
                case.suggested_team_name,
                sport=case.sport,
            )
            is None
        ):
            continue

        candidate_event_teams = _candidate_event_teams(candidate)
        if candidate_event_teams is None:
            continue
        if len(target_event_teams & candidate_event_teams) != 1:
            continue

        source_team_ids.add(candidate.team_id)

    return source_team_ids


def _normalize_merge_pairings(
    pairings: list[tuple[int, int]],
) -> tuple[dict[int, int], set[int]]:
    normalized: dict[int, int] = {}
    conflicts: set[int] = set()

    for source_team_id, target_team_id in pairings:
        if source_team_id <= 0 or target_team_id <= 0 or source_team_id == target_team_id:
            continue
        existing_target = normalized.get(source_team_id)
        if existing_target is not None and existing_target != target_team_id:
            conflicts.add(source_team_id)
            continue
        normalized[source_team_id] = target_team_id

    for source_team_id in conflicts:
        normalized.pop(source_team_id, None)

    resolved: dict[int, int] = {}
    cycle_conflicts: set[int] = set()

    for source_team_id in list(normalized):
        if source_team_id in resolved or source_team_id in cycle_conflicts:
            continue

        path: list[int] = []
        visited: dict[int, int] = {}
        current_team_id = source_team_id

        while True:
            if current_team_id in cycle_conflicts:
                cycle_conflicts.update(path)
                break
            if current_team_id in resolved:
                final_target = resolved[current_team_id]
                for path_team_id in path:
                    resolved[path_team_id] = final_target
                break
            if current_team_id not in normalized:
                for path_team_id in path:
                    resolved[path_team_id] = current_team_id
                break
            if current_team_id in visited:
                cycle_conflicts.update(path)
                break

            visited[current_team_id] = len(path)
            path.append(current_team_id)
            current_team_id = normalized[current_team_id]

    conflicts.update(cycle_conflicts)
    return (
        {
            source_team_id: target_team_id
            for source_team_id, target_team_id in resolved.items()
            if source_team_id not in conflicts and source_team_id in normalized
        },
        conflicts,
    )


@dataclass(frozen=True)
class _CandidateGroup:
    index: int
    candidates: tuple[EventCandidate, ...]

    @property
    def bookmakers(self) -> set[str]:
        return {candidate.bookmaker_id for candidate in self.candidates}

    @property
    def match_ids(self) -> set[str]:
        return {candidate.match_id for candidate in self.candidates}

    @property
    def representative(self) -> EventCandidate:
        return sorted(
            self.candidates,
            key=lambda candidate: (candidate.match_id, candidate.bookmaker_id),
        )[0]


@dataclass(frozen=True)
class EventResolutionGroup:
    event_id: str
    sport: str
    start_time: str
    primary_match_id: str
    display_home_team: str
    display_away_team: str
    display_league_name: str | None
    method: str
    confidence: float
    members: tuple[EventCandidate, ...]
    evidence: tuple[str, ...]


@dataclass
class _EventGroupBuildStats:
    exact_group_count: int = 0
    pair_check_count: int = 0
    fuzzy_score_count: int = 0
    accepted_fuzzy_pair_count: int = 0
    review_case_count: int = 0


@dataclass(frozen=True)
class MatchUnificationPersistenceResult:
    candidates: int
    resolved_events: int
    resolved_event_members: int
    review_cases: int
    benchmark: MatchUnificationResolutionBenchmarkOut | None = None
    coverage: tuple[BenchmarkEventCoverageOut, ...] = ()
    split_diagnostics: BenchmarkSplitDiagnosticsOut = field(
        default_factory=BenchmarkSplitDiagnosticsOut
    )


_SourceEventKey = tuple[str, str, str]


def _source_event_key(
    *,
    bookmaker_id: str,
    sport: str,
    match_id: str,
) -> _SourceEventKey:
    return bookmaker_id, sport, match_id


def _normalized_source_event_keys(
    normalized_odds: list[NormalizedOdds],
    normalized_outcome_offers: list[NormalizedOutcomeOffer],
) -> set[_SourceEventKey]:
    return {
        _source_event_key(
            bookmaker_id=row.bookmaker_id,
            sport=row.sport,
            match_id=row.match_id,
        )
        for row in [*normalized_odds, *normalized_outcome_offers]
    }


def _review_source_event_keys(
    review_cases: list[EventReviewCaseIn],
) -> set[_SourceEventKey]:
    keys: set[_SourceEventKey] = set()
    for review_case in review_cases:
        variants = review_case.metadata.get("source_variants")
        if not isinstance(variants, list):
            continue
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            bookmaker_id = variant.get("bookmaker_id")
            match_id = variant.get("match_id")
            if isinstance(bookmaker_id, str) and isinstance(match_id, str):
                keys.add(
                    _source_event_key(
                        bookmaker_id=bookmaker_id,
                        sport=review_case.sport,
                        match_id=match_id,
                    )
                )
    return keys


def _event_coverage_benchmark(
    *,
    normalized_odds: list[NormalizedOdds],
    normalized_outcome_offers: list[NormalizedOutcomeOffer],
    resolutions: list[EventResolutionGroup],
    review_cases: list[EventReviewCaseIn],
) -> tuple[BenchmarkEventCoverageOut, ...]:
    source_events = _normalized_source_event_keys(
        normalized_odds,
        normalized_outcome_offers,
    )
    matched_events: set[_SourceEventKey] = set()
    singleton_events: set[_SourceEventKey] = set()

    for resolution in resolutions:
        has_cross_bookmaker_match = (
            len({member.bookmaker_id for member in resolution.members}) >= 2
        )
        for member in resolution.members:
            key = _source_event_key(
                bookmaker_id=member.bookmaker_id,
                sport=member.sport,
                match_id=member.match_id,
            )
            source_events.add(key)
            if has_cross_bookmaker_match:
                matched_events.add(key)
            else:
                singleton_events.add(key)

    singleton_events -= matched_events
    grouped_events = matched_events | singleton_events
    ungrouped_events = source_events - grouped_events
    review_events = _review_source_event_keys(review_cases) & source_events

    rows: list[BenchmarkEventCoverageOut] = []
    bucket_keys = sorted(
        {(bookmaker_id, sport) for bookmaker_id, sport, _ in source_events}
    )
    for bookmaker_id, sport in bucket_keys:
        bucket_source_events = {
            key
            for key in source_events
            if key[0] == bookmaker_id and key[1] == sport
        }
        normalized_count = len(bucket_source_events)
        matched_count = len(bucket_source_events & matched_events)
        unmatched_count = len(bucket_source_events & singleton_events)
        ungrouped_count = len(bucket_source_events & ungrouped_events)
        in_review_count = len(bucket_source_events & review_events)
        not_matched_count = unmatched_count + ungrouped_count
        match_rate = (
            round(matched_count / normalized_count, 4)
            if normalized_count
            else 0.0
        )
        rows.append(
            BenchmarkEventCoverageOut(
                bookmaker_id=bookmaker_id,
                sport=sport,
                normalized_events=normalized_count,
                matched_events=matched_count,
                unmatched_events=unmatched_count,
                ungrouped_events=ungrouped_count,
                in_review_events=in_review_count,
                not_matched_events=not_matched_count,
                match_rate=match_rate,
            )
        )
    return tuple(rows)


_SPLIT_TIME_WINDOW_MINUTES = 15
_SPLIT_SAME_SIDE_SCORE = 90
_SPLIT_FUZZY_AVG_SCORE = 82
_SPLIT_FUZZY_WEAK_SCORE = 68
_OVERMERGE_AVG_SCORE = 70
_OVERMERGE_WEAK_SCORE = 45
_MAX_SPLIT_DIAGNOSTIC_EXAMPLES = 20
_MAX_SPLIT_BLOCK_TOKEN_FREQUENCY = 50
_MAX_SPLIT_HIGH_FREQUENCY_TOKEN_FALLBACKS_PER_LEFT = 4


def _split_name_score(left: str | None, right: str | None) -> float:
    left_norm = normalize_identity_text(left or "")
    right_norm = normalize_identity_text(right or "")
    if not left_norm or not right_norm:
        return 0.0
    return float(fuzz.token_set_ratio(left_norm, right_norm))


def _split_member_home(member: EventCandidate) -> str:
    return member.source_home_team or member.home_team


def _split_member_away(member: EventCandidate) -> str:
    return member.source_away_team or member.away_team


def _split_pair_scores(
    left_home: str,
    left_away: str,
    right_home: str,
    right_away: str,
) -> tuple[str, float, float, float]:
    as_home = _split_name_score(left_home, right_home)
    as_away = _split_name_score(left_away, right_away)
    rev_home = _split_name_score(left_home, right_away)
    rev_away = _split_name_score(left_away, right_home)
    as_avg = (as_home + as_away) / 2
    rev_avg = (rev_home + rev_away) / 2
    if rev_avg > as_avg:
        return "reversed", rev_avg, rev_home, rev_away
    return "as_listed", as_avg, as_home, as_away


def _split_fragment(
    resolution: EventResolutionGroup,
) -> BenchmarkSplitEventFragmentOut:
    return BenchmarkSplitEventFragmentOut(
        resolved_event_id=resolution.event_id,
        primary_match_id=resolution.primary_match_id,
        display_home_team=resolution.display_home_team,
        display_away_team=resolution.display_away_team,
        display_league_name=resolution.display_league_name,
        start_time=resolution.start_time,
        method=resolution.method,
        confidence=round(resolution.confidence, 4),
        bookmaker_ids=sorted({member.bookmaker_id for member in resolution.members}),
        match_ids=sorted({member.match_id for member in resolution.members}),
        member_count=len(resolution.members),
    )


def _split_member_fragment(member: EventCandidate) -> BenchmarkSplitMemberFragmentOut:
    return BenchmarkSplitMemberFragmentOut(
        bookmaker_id=member.bookmaker_id,
        match_id=member.match_id,
        home_team=member.home_team,
        away_team=member.away_team,
        source_home_team=member.source_home_team,
        source_away_team=member.source_away_team,
        source_kind=member.source_kind,
    )


def _weakest_member_pair(
    resolution: EventResolutionGroup,
) -> BenchmarkSplitWeakestMemberPairOut | None:
    weakest_avg_pair: tuple[float, float, str, EventCandidate, EventCandidate] | None = (
        None
    )
    weakest_side_pair: tuple[float, float, str, EventCandidate, EventCandidate] | None = (
        None
    )
    members = list(resolution.members)
    for left_index, left in enumerate(members):
        for right in members[left_index + 1 :]:
            orientation, avg_score, home_score, away_score = _split_pair_scores(
                _split_member_home(left),
                _split_member_away(left),
                _split_member_home(right),
                _split_member_away(right),
            )
            weak_side_score = min(home_score, away_score)
            pair = (avg_score, weak_side_score, orientation, left, right)
            if weakest_avg_pair is None or avg_score < weakest_avg_pair[0]:
                weakest_avg_pair = pair
            if weakest_side_pair is None or weak_side_score < weakest_side_pair[1]:
                weakest_side_pair = pair
    if weakest_avg_pair is None:
        return None

    if weakest_avg_pair[0] < _OVERMERGE_AVG_SCORE:
        weakest = weakest_avg_pair
    elif (
        weakest_side_pair is not None
        and weakest_side_pair[1] < _OVERMERGE_WEAK_SCORE
    ):
        weakest = weakest_side_pair
    else:
        weakest = weakest_avg_pair

    avg_score, weak_side_score, orientation, left, right = weakest
    return BenchmarkSplitWeakestMemberPairOut(
        left=_split_member_fragment(left),
        right=_split_member_fragment(right),
        orientation=orientation,
        average_score=round(avg_score, 2),
        weak_side_score=round(weak_side_score, 2),
    )


def _split_diagnostic_decision_for_pair(
    left: EventResolutionGroup,
    right: EventResolutionGroup,
) -> EventDiagnosticDecision | None:
    """Classify a split diagnostic without changing resolved-event membership.

    Split diagnostics intentionally use display/source text with token-set
    scoring because they look for likely review targets among already assembled
    resolved events, not same-slot automatic merge eligibility.
    """

    if left.sport != right.sport:
        return None
    delta = _event_time_delta_minutes(left.start_time, right.start_time)
    if delta is None or delta > _SPLIT_TIME_WINDOW_MINUTES:
        return None

    orientation, avg_score, home_score, away_score = _split_pair_scores(
        left.display_home_team,
        left.display_away_team,
        right.display_home_team,
        right.display_away_team,
    )
    if orientation == "reversed":
        home_side = "home_to_away"
        away_side = "away_to_home"
    else:
        home_side = "home"
        away_side = "away"

    reason_code: str | None = None
    shared_side: str | None = None
    if home_score >= _SPLIT_SAME_SIDE_SCORE and away_score < _SPLIT_SAME_SIDE_SCORE:
        reason_code = "same_side_conflicting_opponent"
        shared_side = home_side
    elif away_score >= _SPLIT_SAME_SIDE_SCORE and home_score < _SPLIT_SAME_SIDE_SCORE:
        reason_code = "same_side_conflicting_opponent"
        shared_side = away_side
    elif (
        avg_score >= _SPLIT_FUZZY_AVG_SCORE
        and min(home_score, away_score) >= _SPLIT_FUZZY_WEAK_SCORE
    ):
        reason_code = "fuzzy_duplicate_resolved_events"
        shared_side = "both"

    if reason_code is None:
        return None

    return EventDiagnosticDecision(
        category=EventDiagnosticDecisionCategory.SPLIT_DIAGNOSTIC,
        reason_code=reason_code,
        score=avg_score,
        weak_side_score=min(home_score, away_score),
        orientation=orientation,
        shared_side=shared_side,
        max_start_delta_minutes=delta,
    )


def _split_candidate_for_pair(
    left: EventResolutionGroup,
    right: EventResolutionGroup,
) -> BenchmarkSplitClusterOut | None:
    decision = _split_diagnostic_decision_for_pair(left, right)
    if decision is None:
        return None

    return BenchmarkSplitClusterOut(
        sport=left.sport,
        reason_code=decision.reason_code,
        score=round(decision.score, 2),
        shared_side=decision.shared_side,
        start_time=min(left.start_time, right.start_time),
        max_start_delta_minutes=round(decision.max_start_delta_minutes, 2),
        events=[_split_fragment(left), _split_fragment(right)],
    )


def _overmerge_diagnostic_decision_for_resolution(
    resolution: EventResolutionGroup,
) -> EventDiagnosticDecision | None:
    weakest_pair = _weakest_member_pair(resolution)
    return _overmerge_diagnostic_decision_for_weakest_pair(weakest_pair)


def _overmerge_diagnostic_decision_for_weakest_pair(
    weakest_pair: BenchmarkSplitWeakestMemberPairOut | None,
) -> EventDiagnosticDecision | None:
    if weakest_pair is None:
        return None

    if (
        weakest_pair.average_score >= _OVERMERGE_AVG_SCORE
        and weakest_pair.weak_side_score >= _OVERMERGE_WEAK_SCORE
    ):
        return None

    return EventDiagnosticDecision(
        category=EventDiagnosticDecisionCategory.OVERMERGE_DIAGNOSTIC,
        reason_code="possible_overmerge_conflicting_members",
        score=weakest_pair.average_score,
        weak_side_score=weakest_pair.weak_side_score,
        orientation=weakest_pair.orientation,
    )


def _overmerge_candidate_for_resolution(
    resolution: EventResolutionGroup,
) -> BenchmarkSplitClusterOut | None:
    weakest_pair = _weakest_member_pair(resolution)
    decision = _overmerge_diagnostic_decision_for_weakest_pair(weakest_pair)
    if weakest_pair is None or decision is None:
        return None

    return BenchmarkSplitClusterOut(
        sport=resolution.sport,
        reason_code=decision.reason_code,
        score=decision.score,
        shared_side=None,
        start_time=resolution.start_time,
        max_start_delta_minutes=0.0,
        events=[_split_fragment(resolution)],
        weakest_member_pair=weakest_pair,
    )


@dataclass
class _SplitDiagnosticAggregate:
    candidate_count: int = 0
    event_members: dict[str, int] = field(default_factory=dict)


def _event_sort_seconds(value: str) -> tuple[str, float] | None:
    parsed = _parse_event_time(value)
    if parsed is None:
        return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return ("aware", parsed.timestamp())
    naive = parsed.replace(tzinfo=None)
    return ("naive", (naive - datetime(1970, 1, 1)).total_seconds())


def _candidate_member_count(candidate: BenchmarkSplitClusterOut) -> int:
    return sum(event.member_count for event in candidate.events)


def _top_split_candidate_key(
    candidate: BenchmarkSplitClusterOut,
) -> tuple[int, float, str, str]:
    return (
        -_candidate_member_count(candidate),
        -candidate.score,
        candidate.sport,
        candidate.start_time,
    )


def _top_overmerge_candidate_key(
    candidate: BenchmarkSplitClusterOut,
) -> tuple[float, int, str, str]:
    return (
        candidate.score,
        -_candidate_member_count(candidate),
        candidate.sport,
        candidate.start_time,
    )


def _add_ranked_diagnostic_candidate(
    top_candidates: list[BenchmarkSplitClusterOut],
    candidate: BenchmarkSplitClusterOut,
    key_fn,
) -> None:
    top_candidates.append(candidate)
    top_candidates.sort(key=key_fn)
    del top_candidates[_MAX_SPLIT_DIAGNOSTIC_EXAMPLES:]


def _add_diagnostic_candidate(
    *,
    candidate: BenchmarkSplitClusterOut,
    total: _SplitDiagnosticAggregate,
    by_sport: defaultdict[str, _SplitDiagnosticAggregate],
    top_candidates: list[BenchmarkSplitClusterOut],
    key_fn,
) -> None:
    total.candidate_count += 1
    sport_aggregate = by_sport[candidate.sport]
    sport_aggregate.candidate_count += 1
    for event in candidate.events:
        total.event_members[event.resolved_event_id] = event.member_count
        sport_aggregate.event_members[event.resolved_event_id] = event.member_count
    _add_ranked_diagnostic_candidate(top_candidates, candidate, key_fn)


def _split_blocking_tokens(resolution: EventResolutionGroup) -> set[str]:
    return _significant_team_tokens(
        resolution.display_home_team,
        sport=resolution.sport,
    ) | _significant_team_tokens(
        resolution.display_away_team,
        sport=resolution.sport,
    )


def _split_token_index(
    records: list[tuple[float, EventResolutionGroup, set[str]]],
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    token_index: defaultdict[str, list[int]] = defaultdict(list)
    for index, (_seconds, _resolution, tokens) in enumerate(records):
        for token in tokens:
            token_index[token].append(index)
    normal_index = {
        token: indexes
        for token, indexes in token_index.items()
        if len(indexes) <= _MAX_SPLIT_BLOCK_TOKEN_FREQUENCY
    }
    high_frequency_index = {
        token: indexes
        for token, indexes in token_index.items()
        if len(indexes) > _MAX_SPLIT_BLOCK_TOKEN_FREQUENCY
    }
    return normal_index, high_frequency_index


def _add_high_frequency_fallback_indices(
    *,
    right_indices: set[int],
    left_index: int,
    token_indexes: list[int],
) -> None:
    added = 0
    for right_index in token_indexes:
        if right_index <= left_index:
            continue
        right_indices.add(right_index)
        added += 1
        if added >= _MAX_SPLIT_HIGH_FREQUENCY_TOKEN_FALLBACKS_PER_LEFT:
            break


def _timed_split_candidate_pairs(
    records: list[tuple[float, EventResolutionGroup]],
    *,
    window_seconds: int,
):
    blocked_records = [
        (seconds, resolution, _split_blocking_tokens(resolution))
        for seconds, resolution in records
    ]
    left_start = 0
    while left_start < len(blocked_records):
        left_seconds = blocked_records[left_start][0]
        left_end = left_start + 1
        while (
            left_end < len(blocked_records)
            and blocked_records[left_end][0] == left_seconds
        ):
            left_end += 1

        window_end = left_end
        while (
            window_end < len(blocked_records)
            and blocked_records[window_end][0] - left_seconds <= window_seconds
        ):
            window_end += 1

        window_records = blocked_records[left_start:window_end]
        token_index, high_frequency_token_index = _split_token_index(window_records)
        for local_left_index, (_seconds, left, left_tokens) in enumerate(
            window_records[: left_end - left_start]
        ):
            right_indices: set[int] = set()
            for token in left_tokens:
                for right_index in token_index.get(token, []):
                    if right_index > local_left_index:
                        right_indices.add(right_index)
                _add_high_frequency_fallback_indices(
                    right_indices=right_indices,
                    left_index=local_left_index,
                    token_indexes=high_frequency_token_index.get(token, []),
                )
            for right_index in sorted(right_indices):
                yield left, window_records[right_index][1]

        left_start = left_end


def _same_start_split_candidate_pairs(resolutions: list[EventResolutionGroup]):
    records = [
        (0.0, resolution, _split_blocking_tokens(resolution))
        for resolution in resolutions
    ]
    token_index, high_frequency_token_index = _split_token_index(records)
    for left_index, (_seconds, left, left_tokens) in enumerate(records):
        right_indices: set[int] = set()
        for token in left_tokens:
            for right_index in token_index.get(token, []):
                if right_index > left_index:
                    right_indices.add(right_index)
            _add_high_frequency_fallback_indices(
                right_indices=right_indices,
                left_index=left_index,
                token_indexes=high_frequency_token_index.get(token, []),
            )
        for right_index in sorted(right_indices):
            yield left, records[right_index][1]


def _event_split_diagnostics_benchmark(
    resolutions: list[EventResolutionGroup],
) -> BenchmarkSplitDiagnosticsOut:
    sorted_resolutions = sorted(
        resolutions,
        key=lambda item: (item.sport, item.start_time, item.event_id),
    )

    split_total = _SplitDiagnosticAggregate()
    split_by_sport: defaultdict[str, _SplitDiagnosticAggregate] = defaultdict(
        _SplitDiagnosticAggregate
    )
    top_split_candidates: list[BenchmarkSplitClusterOut] = []
    timed_resolutions: defaultdict[
        tuple[str, str], list[tuple[float, EventResolutionGroup]]
    ] = defaultdict(list)
    unparsed_resolutions: defaultdict[tuple[str, str], list[EventResolutionGroup]] = (
        defaultdict(list)
    )
    for resolution in sorted_resolutions:
        sort_key = _event_sort_seconds(resolution.start_time)
        if sort_key is None:
            unparsed_resolutions[(resolution.sport, resolution.start_time)].append(
                resolution
            )
            continue
        time_kind, seconds = sort_key
        timed_resolutions[(resolution.sport, time_kind)].append((seconds, resolution))

    window_seconds = _SPLIT_TIME_WINDOW_MINUTES * 60
    for sport_resolutions in timed_resolutions.values():
        sport_resolutions.sort(key=lambda item: (item[0], item[1].event_id))
        for left, right in _timed_split_candidate_pairs(
            sport_resolutions,
            window_seconds=window_seconds,
        ):
            candidate = _split_candidate_for_pair(left, right)
            if candidate is not None:
                _add_diagnostic_candidate(
                    candidate=candidate,
                    total=split_total,
                    by_sport=split_by_sport,
                    top_candidates=top_split_candidates,
                    key_fn=_top_split_candidate_key,
                )

    for same_start_resolutions in unparsed_resolutions.values():
        same_start_resolutions.sort(key=lambda item: item.event_id)
        for left, right in _same_start_split_candidate_pairs(same_start_resolutions):
            candidate = _split_candidate_for_pair(left, right)
            if candidate is not None:
                _add_diagnostic_candidate(
                    candidate=candidate,
                    total=split_total,
                    by_sport=split_by_sport,
                    top_candidates=top_split_candidates,
                    key_fn=_top_split_candidate_key,
                )

    overmerge_total = _SplitDiagnosticAggregate()
    overmerge_by_sport: defaultdict[str, _SplitDiagnosticAggregate] = defaultdict(
        _SplitDiagnosticAggregate
    )
    top_overmerge_candidates: list[BenchmarkSplitClusterOut] = []
    for resolution in sorted_resolutions:
        candidate = _overmerge_candidate_for_resolution(resolution)
        if candidate is None:
            continue
        _add_diagnostic_candidate(
            candidate=candidate,
            total=overmerge_total,
            by_sport=overmerge_by_sport,
            top_candidates=top_overmerge_candidates,
            key_fn=_top_overmerge_candidate_key,
        )

    sports = sorted({resolution.sport for resolution in sorted_resolutions})

    return BenchmarkSplitDiagnosticsOut(
        split_candidate_count=split_total.candidate_count,
        events_in_split_candidates=len(split_total.event_members),
        members_in_split_candidates=sum(split_total.event_members.values()),
        overmerge_candidate_count=overmerge_total.candidate_count,
        events_in_overmerge_candidates=len(overmerge_total.event_members),
        members_in_overmerge_candidates=sum(overmerge_total.event_members.values()),
        top_split_candidates=top_split_candidates,
        top_overmerge_candidates=top_overmerge_candidates,
        sports=[
            _split_sport_diagnostics(
                sport=sport,
                split_aggregate=split_by_sport.get(sport),
                overmerge_aggregate=overmerge_by_sport.get(sport),
            )
            for sport in sports
        ],
    )


def _split_sport_diagnostics(
    *,
    sport: str,
    split_aggregate: _SplitDiagnosticAggregate | None,
    overmerge_aggregate: _SplitDiagnosticAggregate | None,
) -> BenchmarkSplitSportDiagnosticsOut:
    split_event_members = split_aggregate.event_members if split_aggregate else {}
    overmerge_event_members = (
        overmerge_aggregate.event_members if overmerge_aggregate else {}
    )
    return BenchmarkSplitSportDiagnosticsOut(
        sport=sport,
        split_candidate_count=(
            split_aggregate.candidate_count if split_aggregate else 0
        ),
        events_in_split_candidates=len(split_event_members),
        members_in_split_candidates=sum(split_event_members.values()),
        overmerge_candidate_count=(
            overmerge_aggregate.candidate_count if overmerge_aggregate else 0
        ),
        events_in_overmerge_candidates=len(overmerge_event_members),
        members_in_overmerge_candidates=sum(overmerge_event_members.values()),
    )


class _DisjointSet:
    def __init__(self, groups: list[_CandidateGroup]) -> None:
        self.parent = list(range(len(groups)))
        self.bookmakers = [group.bookmakers for group in groups]

    def find(self, index: int) -> int:
        if self.parent[index] != index:
            self.parent[index] = self.find(self.parent[index])
        return self.parent[index]

    def can_union(self, left: int, right: int) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        return self.bookmakers[left_root].isdisjoint(self.bookmakers[right_root])

    def union(self, left: int, right: int) -> int:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return left_root
        if len(self.bookmakers[left_root]) < len(self.bookmakers[right_root]):
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.bookmakers[left_root].update(self.bookmakers[right_root])
        return left_root


def _stable_event_id(primary_match_id: str) -> str:
    return f"evt_{primary_match_id}"


def _display_league_name(candidates: tuple[EventCandidate, ...]) -> str | None:
    counts = Counter(
        candidate.source_league_name
        for candidate in candidates
        if candidate.source_league_name
    )
    if not counts:
        return None
    return min(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def _primary_candidate_for_resolution(
    members: tuple[EventCandidate, ...],
) -> EventCandidate:
    if not members:
        raise ValueError("Cannot choose a primary event candidate from an empty group")
    first = members[0]
    if first.sport != "tennis":
        return min(members, key=lambda candidate: (candidate.match_id, candidate.bookmaker_id))
    time_counts = Counter(member.start_time for member in members)
    preferred_time = min(
        time_counts,
        key=lambda start_time: (-time_counts[start_time], start_time),
    )
    return min(
        (member for member in members if member.start_time == preferred_time),
        key=lambda candidate: (candidate.match_id, candidate.bookmaker_id),
    )


def _event_member_orientation(primary: EventCandidate, member: EventCandidate) -> str:
    scores = _orientation_scores(
        primary.home_team,
        primary.away_team,
        member.home_team,
        member.away_team,
        sport=primary.sport,
    )
    if not scores:
        return "as_listed"
    return scores[0].orientation


def _review_case_fingerprint(
    *,
    sport: str,
    start_time: str,
    match_ids: list[str],
    reason_code: str,
) -> str:
    raw = f"{_RESOLVER_VERSION}:{sport}:{start_time}:{reason_code}:{'|'.join(match_ids)}"
    return "event-review-" + hashlib.md5(raw.encode()).hexdigest()[:20]


def _event_review_case(
    left: _CandidateGroup,
    right: _CandidateGroup,
    pair: _PairResolution,
) -> EventReviewCaseIn:
    candidates = tuple([*left.candidates, *right.candidates])
    primary = min(candidates, key=lambda candidate: (candidate.match_id, candidate.bookmaker_id))
    match_ids = sorted({candidate.match_id for candidate in candidates})
    bookmaker_ids = sorted({candidate.bookmaker_id for candidate in candidates})
    league_labels = sorted(
        {candidate.source_league_name for candidate in candidates if candidate.source_league_name}
    )
    return EventReviewCaseIn(
        fingerprint=_review_case_fingerprint(
            sport=primary.sport,
            start_time=primary.start_time,
            match_ids=match_ids,
            reason_code=pair.reason_code,
        ),
        sport=primary.sport,
        start_time=primary.start_time,
        primary_match_id=primary.match_id,
        candidate_match_ids=match_ids,
        reason_code=pair.reason_code,
        confidence=pair.confidence,
        method="auto_candidate",
        source_bookmaker_ids=bookmaker_ids,
        source_league_labels=league_labels,
        evidence=list(pair.evidence),
        metadata={
            "resolver": _RESOLVER_VERSION,
            "orientation": pair.orientation,
            "score": round(pair.score, 2),
            "source_variants": [
                {
                    "match_id": candidate.match_id,
                    "bookmaker_id": candidate.bookmaker_id,
                }
                for candidate in sorted(
                    candidates,
                    key=lambda item: (item.match_id, item.bookmaker_id),
                )
            ],
        },
    )


def _build_exact_groups(candidates: list[EventCandidate]) -> list[_CandidateGroup]:
    by_exact_key: dict[
        tuple[str, str, tuple[int, int] | tuple[str, str]],
        dict[tuple[str, str], EventCandidate],
    ] = defaultdict(dict)
    for candidate in candidates:
        by_exact_key[candidate.exact_event_key][candidate.bookmaker_member_key] = candidate
    return [
        _CandidateGroup(
            index=index,
            candidates=tuple(
                sorted(group.values(), key=lambda item: item.bookmaker_member_key)
            ),
        )
        for index, group in enumerate(by_exact_key.values())
    ]


def build_event_resolution_groups(
    candidates: list[EventCandidate],
    *,
    stats: _EventGroupBuildStats | None = None,
) -> tuple[list[EventResolutionGroup], list[EventReviewCaseIn]]:
    exact_groups = _build_exact_groups(candidates)
    if stats is not None:
        stats.exact_group_count = len(exact_groups)
    dsu = _DisjointSet(exact_groups)
    text_cache = _ResolverTextCache(stats)
    accepted_pairs: list[tuple[int, int, _PairResolution]] = []
    review_cases: dict[str, EventReviewCaseIn] = {}

    groups_by_slot: dict[tuple[str, str], list[_CandidateGroup]] = defaultdict(list)
    for group in exact_groups:
        representative = group.representative
        bucket_key = (
            (representative.sport, "__tennis_time_drift__")
            if representative.sport == "tennis"
            else (representative.sport, representative.start_time)
        )
        groups_by_slot[bucket_key].append(group)

    for groups in groups_by_slot.values():
        sorted_groups = sorted(
            groups,
            key=lambda group: (group.representative.match_id, group.index),
        )
        for left_index, left in enumerate(sorted_groups):
            for right in sorted_groups[left_index + 1 :]:
                if left.representative.exact_event_key == right.representative.exact_event_key:
                    continue
                # Skip pairs whose groups are already in the same DSU component
                # because of an earlier accepted merge. Without this guard,
                # `dsu.can_union` returning False on already-merged pairs would
                # be misinterpreted as a same-bookmaker conflict and emit a
                # spurious review case (or, worse, a spurious quorum audit on
                # an already-merged component).
                if dsu.find(left.index) == dsu.find(right.index):
                    continue
                if stats is not None:
                    stats.pair_check_count += 1
                pair_decision = best_group_pair_decision(
                    left,
                    right,
                    text_cache=text_cache,
                )
                if pair_decision is None:
                    continue
                can_union = dsu.can_union(left.index, right.index)
                quorum_passes = (
                    not can_union
                    and pair_decision.requires_group_context
                    and pair_decision.pair is not None
                    and _quorum_resolution_passes(left, right, pair_decision.pair)
                )
                decision = with_group_context(
                    pair_decision,
                    can_union=can_union,
                    quorum_passes=quorum_passes,
                    combined_bookmaker_count=len(left.bookmakers | right.bookmakers),
                )
                if decision.causes_union and decision.pair is not None:
                    dsu.union(left.index, right.index)
                    accepted_pairs.append((left.index, right.index, decision.pair))
                    if stats is not None:
                        stats.accepted_fuzzy_pair_count += 1
                    if decision.quorum_override:
                        # Log the override for operator visibility instead of
                        # emitting an audit review case. The override is
                        # explicitly intended to clear pairs from the manual
                        # queue per the user's "few wrong is OK" preference,
                        # so adding a parallel audit row would defeat that
                        # goal and accumulate stale entries every cycle.
                        left_rep = left.candidates[0] if left.candidates else None
                        right_rep = right.candidates[0] if right.candidates else None
                        logger.info(
                            "match_unification.quorum_override "
                            "sport=%s start_time=%s "
                            "left_teams=%s vs %s "
                            "right_teams=%s vs %s "
                            "match_ids=%s vs %s "
                            "score=%.1f weak=%.1f "
                            "bookmakers=%s vs %s",
                            left_rep.sport if left_rep else "?",
                            left_rep.start_time if left_rep else "?",
                            left_rep.home_team if left_rep else "?",
                            left_rep.away_team if left_rep else "?",
                            right_rep.home_team if right_rep else "?",
                            right_rep.away_team if right_rep else "?",
                            sorted(left.match_ids),
                            sorted(right.match_ids),
                            decision.pair.score,
                            decision.pair.weak_side_score,
                            sorted(left.bookmakers),
                            sorted(right.bookmakers),
                        )
                    continue
                if decision.emits_review_case and decision.pair is not None:
                    review_case = _event_review_case(left, right, decision.pair)
                    review_cases[review_case.fingerprint] = review_case

    groups_by_root: dict[int, list[_CandidateGroup]] = defaultdict(list)
    for group in exact_groups:
        groups_by_root[dsu.find(group.index)].append(group)

    accepted_pairs_by_root: dict[int, list[_PairResolution]] = defaultdict(list)
    for left_index, right_index, pair in accepted_pairs:
        root = dsu.find(left_index)
        if root == dsu.find(right_index):
            accepted_pairs_by_root[root].append(pair)

    resolutions: list[EventResolutionGroup] = []
    for root, component_groups in groups_by_root.items():
        members = tuple(
            sorted(
                (
                    candidate
                    for group in component_groups
                    for candidate in group.candidates
                ),
                key=lambda candidate: (candidate.match_id, candidate.bookmaker_id),
            )
        )
        if not members:
            continue
        primary = _primary_candidate_for_resolution(members)
        pair_evidence = tuple(
            evidence
            for pair in accepted_pairs_by_root.get(root, [])
            for evidence in pair.evidence
        )
        exact_evidence = (
            f"Exact start time: {primary.start_time}",
            (
                "Exact canonical team grouping"
                if not pair_evidence
                else "Resolved with exact kickoff and high-confidence fuzzy team evidence"
            ),
        )
        method = "auto_fuzzy_high" if pair_evidence else "exact"
        confidence = (
            min(pair.confidence for pair in accepted_pairs_by_root[root])
            if pair_evidence
            else 1.0
        )
        resolutions.append(
            EventResolutionGroup(
                event_id=_stable_event_id(primary.match_id),
                sport=primary.sport,
                start_time=primary.start_time,
                primary_match_id=primary.match_id,
                display_home_team=primary.home_team,
                display_away_team=primary.away_team,
                display_league_name=_display_league_name(members),
                method=method,
                confidence=confidence,
                members=members,
                evidence=tuple(dict.fromkeys([*exact_evidence, *pair_evidence])),
            )
        )

    if stats is not None:
        stats.review_case_count = len(review_cases)

    return (
        sorted(
            resolutions,
            key=lambda resolution: (
                resolution.sport,
                resolution.start_time,
                resolution.event_id,
            ),
        ),
        sorted(review_cases.values(), key=lambda case: case.fingerprint),
    )


async def persist_event_resolution_groups(
    resolutions: list[EventResolutionGroup],
    review_cases: list[EventReviewCaseIn],
    *,
    snapshot_id: str | None = None,
    store=odds_store,
) -> MatchUnificationPersistenceResult:
    events: list[ResolvedEventIn] = []
    members: list[ResolvedEventMemberIn] = []
    for resolution in resolutions:
        source_league_labels = sorted(
            {
                member.source_league_name
                for member in resolution.members
                if member.source_league_name
            }
        )
        source_match_ids = sorted({member.match_id for member in resolution.members})
        events.append(
            ResolvedEventIn(
                id=resolution.event_id,
                sport=resolution.sport,
                start_time=resolution.start_time,
                primary_match_id=resolution.primary_match_id,
                confidence=resolution.confidence,
                method=resolution.method,
                display_home_team=resolution.display_home_team,
                display_away_team=resolution.display_away_team,
                display_league_name=resolution.display_league_name,
                metadata={
                    "resolver": _RESOLVER_VERSION,
                    "source_league_labels": source_league_labels,
                    "source_match_ids": source_match_ids,
                    "evidence": list(resolution.evidence),
                },
            )
        )
        primary = next(
            (
                member
                for member in resolution.members
                if member.match_id == resolution.primary_match_id
            ),
            _primary_candidate_for_resolution(resolution.members),
        )
        for member in resolution.members:
            members.append(
                ResolvedEventMemberIn(
                    resolved_event_id=resolution.event_id,
                    match_id=member.match_id,
                    bookmaker_id=member.bookmaker_id,
                    orientation=_event_member_orientation(primary, member),
                    confidence=resolution.confidence,
                    source_url=member.source_url,
                    source_league_id=member.source_league_id,
                    source_league_name=member.source_league_name,
                    source_home_team=member.source_home_team or member.home_team,
                    source_away_team=member.source_away_team or member.away_team,
                    source_start_time=member.source_start_time or member.start_time,
                    evidence=list(resolution.evidence),
                    metadata={
                        "resolver": _RESOLVER_VERSION,
                        "source_kind": member.source_kind,
                        "display_home_team": member.home_team,
                        "display_away_team": member.away_team,
                    },
                )
            )

    result = await store.persist_event_resolution_batch(
        snapshot_id=snapshot_id,
        events=events,
        members=members,
        review_cases=review_cases,
    )

    return MatchUnificationPersistenceResult(
        candidates=len(members),
        resolved_events=result.resolved_events,
        resolved_event_members=result.resolved_event_members,
        review_cases=result.review_cases,
    )
