from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import logging
import re
import time

from rapidfuzz import fuzz

from ..models.schemas import (
    BenchmarkEventCoverageOut,
    BenchmarkSplitClusterOut,
    BenchmarkSplitDiagnosticsOut,
    BenchmarkSplitEventFragmentOut,
    BenchmarkSplitSportDiagnosticsOut,
    EventResolverBenchmarkOut,
    EventReviewCaseIn,
    NormalizedOdds,
    NormalizedOutcomeOffer,
    RawOddsData,
    RawOutcomeOffer,
    ResolvedEventIn,
    ResolvedEventMemberIn,
)
from ..store import odds_store
from .league_registry import resolve_league
from .normalizer import generate_match_id
from .outcome_normalizer import (
    _AGGRESSIVE_MERGE_SPORTS,
    _build_football_event_resolutions,
    _comparison_team_text,
    _event_key_from_raw,
    _same_team_context,
    _team_similarity,
    _team_qualifiers,
    FootballEventResolutionMap,
)
from .text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_RESOLVER_VERSION = "event_resolver_v1"
_HIGH_FUZZY_AVG_SCORE = 85.0
_HIGH_FUZZY_SIDE_SCORE = 75.0
_HIGH_FUZZY_AVG_SCORE_NON_SUBSET = 90.0
_HIGH_FUZZY_SIDE_SCORE_NON_SUBSET = 82.0
_REVIEW_FUZZY_AVG_SCORE = 65.0
_FUZZY_ORIENTATION_MARGIN = 8.0
_SOURCE_MATCH_MIN_SCORE = 60.0
CANONICAL_TEAM_AUTO_MERGE_THRESHOLD = 88.0
# Anchored low-confidence merge: applies only when the two groups are at the
# exact same (sport, start_time) slot AND a non-fuzzy corroborator is present
# (token subset on the weak side, or shared significant token + same league).
# Combined bookmaker count >= _ANCHORED_MIN_BOOKMAKERS guards against the
# 2-bookmaker false-positive cases preserved by the South/North Korea and
# Austria/Australia regression tests.
_ANCHORED_FUZZY_AVG_SCORE = 70.0
_ANCHORED_FUZZY_SIDE_SCORE = 50.0
_ANCHORED_MIN_BOOKMAKERS = 3
_CANONICAL_SIDE_ANCHOR_AVG_SCORE = 65.0
_CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE = 45.0
_CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS = 3
_CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS_NON_TARGETED = 5
# Same-bookmaker conflict resolution by quorum: if one exact group dwarfs the
# other in distinct bookmaker count, fold the smaller group into the larger
# despite a same-bookmaker overlap. Uses the immutable per-group bookmaker
# sets, never the (mutable) DSU root sizes, so the decision is order-independent.
_QUORUM_FUZZY_AVG_SCORE = 80.0
_QUORUM_FUZZY_SIDE_SCORE = 60.0
_QUORUM_CANONICAL_SIDE_ANCHOR_AVG_SCORE = 70.0
_QUORUM_CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE = 45.0
_QUORUM_MIN_LARGER_BOOKMAKERS = 5
_QUORUM_MIN_BOOKMAKER_DIFFERENCE = 3
_LOW_SIGNAL_TEAM_TOKENS = {
    "bc",
    "bk",
    "kk",
    "fc",
    "fk",
    "club",
    "team",
}


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


def _significant_team_tokens(team_name: str, *, sport: str | None = None) -> set[str]:
    return {
        token
        for token in _comparison_team_text(team_name, sport=sport).split()
        if token not in _LOW_SIGNAL_TEAM_TOKENS
    }


def _symmetric_canonical_team_score(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
) -> float:
    left_key = _comparison_team_text(left_name, sport=sport)
    right_key = _comparison_team_text(right_name, sport=sport)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 100.0

    left_tokens = _significant_team_tokens(left_name, sport=sport)
    right_tokens = _significant_team_tokens(right_name, sport=sport)
    if not left_tokens or not right_tokens:
        return 0.0
    if left_tokens == right_tokens:
        return 100.0
    if left_tokens < right_tokens or right_tokens < left_tokens:
        return float(
            min(
                fuzz.ratio(left_key, right_key),
                fuzz.token_sort_ratio(left_key, right_key),
            )
        )

    return float(
        min(
            fuzz.ratio(left_key, right_key),
            fuzz.token_sort_ratio(left_key, right_key),
        )
    )


def _is_unsafe_compound_subset_match(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
) -> bool:
    left_tokens = _significant_team_tokens(left_name, sport=sport)
    right_tokens = _significant_team_tokens(right_name, sport=sport)
    return bool(
        left_tokens
        and right_tokens
        and (left_tokens < right_tokens or right_tokens < left_tokens)
    )


def _canonical_team_auto_merge_score(
    source_team_name: str,
    target_team_name: str,
    *,
    sport: str | None = None,
) -> float | None:
    if not _same_team_context(source_team_name, target_team_name, sport=sport):
        return None
    if _is_unsafe_compound_subset_match(
        source_team_name,
        target_team_name,
        sport=sport,
    ):
        return None
    score = _symmetric_canonical_team_score(
        source_team_name,
        target_team_name,
        sport=sport,
    )
    if score < CANONICAL_TEAM_AUTO_MERGE_THRESHOLD:
        return None
    return score


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
class EventCandidate:
    match_id: str
    bookmaker_id: str
    sport: str
    start_time: str
    home_team_id: int | None
    away_team_id: int | None
    home_team: str
    away_team: str
    source_league_id: str | None = None
    source_league_name: str | None = None
    source_home_team: str | None = None
    source_away_team: str | None = None
    source_url: str | None = None
    source_kind: str = "normalized"

    @property
    def bookmaker_member_key(self) -> tuple[str, str]:
        return (self.match_id, self.bookmaker_id)

    @property
    def exact_event_key(self) -> tuple[str, str, tuple[int, int] | tuple[str, str]]:
        if self.home_team_id is not None and self.away_team_id is not None:
            team_key: tuple[int, int] | tuple[str, str] = tuple(
                sorted((self.home_team_id, self.away_team_id))
            )
        else:
            team_key = tuple(
                sorted(
                    (
                        normalize_identity_text(self.home_team),
                        normalize_identity_text(self.away_team),
                    )
                )
            )
        return (self.sport, self.start_time, team_key)


@dataclass(frozen=True)
class _RawEventSource:
    bookmaker_id: str
    sport: str
    start_time: str
    home_team: str
    away_team: str
    league_id: str
    league_name: str
    source_url: str | None
    source_kind: str


@dataclass(frozen=True)
class _OrientationScore:
    orientation: str
    home_score: float
    away_score: float

    @property
    def avg_score(self) -> float:
        return (self.home_score + self.away_score) / 2

    @property
    def weak_side_score(self) -> float:
        return min(self.home_score, self.away_score)


@dataclass(frozen=True)
class _PairResolution:
    confidence: float
    score: float
    weak_side_score: float
    orientation: str
    reason_code: str
    evidence: tuple[str, ...]


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
class _EventCandidateExtractionStats:
    football_raw_resolution_candidates_ms: int = 0
    reused_football_event_resolution_count: int = 0


@dataclass
class _EventGroupBuildStats:
    exact_group_count: int = 0
    pair_check_count: int = 0
    fuzzy_score_count: int = 0
    accepted_fuzzy_pair_count: int = 0
    review_case_count: int = 0


class _ResolverTextCache:
    def __init__(self, stats: _EventGroupBuildStats | None = None) -> None:
        self._stats = stats
        self._qualifiers: dict[tuple[str, str | None], set[str]] = {}
        self._comparison_text: dict[tuple[str, str | None], str] = {}
        self._significant_tokens: dict[tuple[str, str | None], set[str]] = {}
        self._expanded_tokens: dict[tuple[str, str, str | None], str] = {}
        self._team_similarity: dict[tuple[str, str, str | None], float] = {}
        self._orientation_scores: dict[
            tuple[str, str, str, str, str | None],
            tuple[_OrientationScore, ...],
        ] = {}

    def qualifiers(self, name: str, *, sport: str | None = None) -> set[str]:
        key = (name, sport)
        cached = self._qualifiers.get(key)
        if cached is None:
            cached = _team_qualifiers(name, sport=sport)
            self._qualifiers[key] = cached
        return cached

    def comparison_text(self, name: str, *, sport: str | None = None) -> str:
        key = (name, sport)
        cached = self._comparison_text.get(key)
        if cached is None:
            cached = _comparison_team_text(name, sport=sport)
            self._comparison_text[key] = cached
        return cached

    def significant_tokens(self, name: str, *, sport: str | None = None) -> set[str]:
        key = (name, sport)
        cached = self._significant_tokens.get(key)
        if cached is None:
            cached = {
                token
                for token in self.comparison_text(name, sport=sport).split()
                if token not in _LOW_SIGNAL_TEAM_TOKENS
            }
            self._significant_tokens[key] = cached
        return cached

    def same_context(self, left: str, right: str, *, sport: str | None = None) -> bool:
        return self.qualifiers(left, sport=sport) == self.qualifiers(right, sport=sport)

    def expanded_token(
        self,
        name: str,
        counterpart: str,
        *,
        sport: str | None = None,
    ) -> str:
        key = (name, counterpart, sport)
        cached = self._expanded_tokens.get(key)
        if cached is None:
            cached = (
                _expand_dotted_token(name, counterpart)
                if sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE
                else name
            )
            self._expanded_tokens[key] = cached
        return cached

    def team_similarity(self, left: str, right: str, *, sport: str | None = None) -> float:
        expanded_left = self.expanded_token(left, right, sport=sport)
        expanded_right = self.expanded_token(right, left, sport=sport)
        key = (expanded_left, expanded_right, sport)
        reverse_key = (expanded_right, expanded_left, sport)
        cached = self._team_similarity.get(key)
        if cached is None:
            cached = self._team_similarity.get(reverse_key)
        if cached is not None:
            return cached

        left_key = self.comparison_text(expanded_left, sport=sport)
        right_key = self.comparison_text(expanded_right, sport=sport)
        if not left_key or not right_key:
            score = 0.0
        elif left_key == right_key:
            score = 100.0
        else:
            left_tokens = self.significant_tokens(expanded_left, sport=sport)
            right_tokens = self.significant_tokens(expanded_right, sport=sport)
            if left_tokens and left_tokens == right_tokens:
                score = 100.0
            else:
                if self._stats is not None:
                    self._stats.fuzzy_score_count += 1
                score = float(fuzz.token_sort_ratio(left_key, right_key))
        self._team_similarity[key] = score
        return score

    def orientation_scores(
        self,
        left_home: str,
        left_away: str,
        right_home: str,
        right_away: str,
        *,
        sport: str | None = None,
    ) -> list[_OrientationScore]:
        key = (left_home, left_away, right_home, right_away, sport)
        cached = self._orientation_scores.get(key)
        if cached is not None:
            return list(cached)
        scores: list[_OrientationScore] = []
        if self.same_context(left_home, right_home, sport=sport) and self.same_context(left_away, right_away, sport=sport):
            scores.append(
                _OrientationScore(
                    orientation="as_listed",
                    home_score=self.team_similarity(left_home, right_home, sport=sport),
                    away_score=self.team_similarity(left_away, right_away, sport=sport),
                )
            )
        if self.same_context(left_home, right_away, sport=sport) and self.same_context(left_away, right_home, sport=sport):
            scores.append(
                _OrientationScore(
                    orientation="reversed",
                    home_score=self.team_similarity(left_home, right_away, sport=sport),
                    away_score=self.team_similarity(left_away, right_home, sport=sport),
                )
            )
        scores = sorted(scores, key=lambda score: score.avg_score, reverse=True)
        self._orientation_scores[key] = tuple(scores)
        return scores


@dataclass(frozen=True)
class EventResolverResult:
    candidates: int
    resolved_events: int
    resolved_event_members: int
    review_cases: int
    benchmark: EventResolverBenchmarkOut | None = None
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


def _parse_event_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_time_delta_minutes(left: str, right: str) -> float | None:
    if left == right:
        return 0.0
    left_dt = _parse_event_time(left)
    right_dt = _parse_event_time(right)
    if left_dt is None or right_dt is None:
        return None
    try:
        return abs((left_dt - right_dt).total_seconds()) / 60
    except TypeError:
        return None


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


def _split_candidate_for_pair(
    left: EventResolutionGroup,
    right: EventResolutionGroup,
) -> BenchmarkSplitClusterOut | None:
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

    return BenchmarkSplitClusterOut(
        sport=left.sport,
        reason_code=reason_code,
        score=round(avg_score, 2),
        shared_side=shared_side,
        start_time=min(left.start_time, right.start_time),
        max_start_delta_minutes=round(delta, 2),
        events=[_split_fragment(left), _split_fragment(right)],
    )


def _overmerge_candidate_for_resolution(
    resolution: EventResolutionGroup,
) -> BenchmarkSplitClusterOut | None:
    members = list(resolution.members)
    if len(members) < 2:
        return None
    weakest_avg = 100.0
    weakest_side = 100.0
    for left_index, left in enumerate(members):
        for right in members[left_index + 1 :]:
            _orientation, avg_score, home_score, away_score = _split_pair_scores(
                _split_member_home(left),
                _split_member_away(left),
                _split_member_home(right),
                _split_member_away(right),
            )
            weakest_avg = min(weakest_avg, avg_score)
            weakest_side = min(weakest_side, home_score, away_score)

    if weakest_avg >= _OVERMERGE_AVG_SCORE and weakest_side >= _OVERMERGE_WEAK_SCORE:
        return None

    return BenchmarkSplitClusterOut(
        sport=resolution.sport,
        reason_code="possible_overmerge_conflicting_members",
        score=round(weakest_avg, 2),
        shared_side=None,
        start_time=resolution.start_time,
        max_start_delta_minutes=0.0,
        events=[_split_fragment(resolution)],
    )


def _event_split_diagnostics_benchmark(
    resolutions: list[EventResolutionGroup],
) -> BenchmarkSplitDiagnosticsOut:
    split_candidates: list[BenchmarkSplitClusterOut] = []
    sorted_resolutions = sorted(
        resolutions,
        key=lambda item: (item.sport, item.start_time, item.event_id),
    )
    for left_index, left in enumerate(sorted_resolutions):
        for right in sorted_resolutions[left_index + 1 :]:
            if left.sport != right.sport:
                break
            candidate = _split_candidate_for_pair(left, right)
            if candidate is not None:
                split_candidates.append(candidate)

    overmerge_candidates = [
        candidate
        for resolution in sorted_resolutions
        if (candidate := _overmerge_candidate_for_resolution(resolution)) is not None
    ]

    split_event_ids = {
        event.resolved_event_id
        for candidate in split_candidates
        for event in candidate.events
    }
    split_members = {
        event.resolved_event_id: event.member_count
        for candidate in split_candidates
        for event in candidate.events
    }
    overmerge_event_ids = {
        event.resolved_event_id
        for candidate in overmerge_candidates
        for event in candidate.events
    }
    overmerge_members = {
        event.resolved_event_id: event.member_count
        for candidate in overmerge_candidates
        for event in candidate.events
    }
    sports = sorted({resolution.sport for resolution in sorted_resolutions})

    top_split_candidates = sorted(
        split_candidates,
        key=lambda candidate: (
            -sum(event.member_count for event in candidate.events),
            -candidate.score,
            candidate.sport,
            candidate.start_time,
        ),
    )[:_MAX_SPLIT_DIAGNOSTIC_EXAMPLES]
    top_overmerge_candidates = sorted(
        overmerge_candidates,
        key=lambda candidate: (
            candidate.score,
            -sum(event.member_count for event in candidate.events),
            candidate.sport,
            candidate.start_time,
        ),
    )[:_MAX_SPLIT_DIAGNOSTIC_EXAMPLES]

    return BenchmarkSplitDiagnosticsOut(
        split_candidate_count=len(split_candidates),
        events_in_split_candidates=len(split_event_ids),
        members_in_split_candidates=sum(split_members.values()),
        overmerge_candidate_count=len(overmerge_candidates),
        events_in_overmerge_candidates=len(overmerge_event_ids),
        members_in_overmerge_candidates=sum(overmerge_members.values()),
        top_split_candidates=top_split_candidates,
        top_overmerge_candidates=top_overmerge_candidates,
        sports=[
            _split_sport_diagnostics(
                sport=sport,
                split_candidates=split_candidates,
                overmerge_candidates=overmerge_candidates,
            )
            for sport in sports
        ],
    )


def _split_sport_diagnostics(
    *,
    sport: str,
    split_candidates: list[BenchmarkSplitClusterOut],
    overmerge_candidates: list[BenchmarkSplitClusterOut],
) -> BenchmarkSplitSportDiagnosticsOut:
    sport_split_candidates = [
        candidate for candidate in split_candidates if candidate.sport == sport
    ]
    sport_overmerge_candidates = [
        candidate for candidate in overmerge_candidates if candidate.sport == sport
    ]
    split_event_members = {
        event.resolved_event_id: event.member_count
        for candidate in sport_split_candidates
        for event in candidate.events
    }
    overmerge_event_members = {
        event.resolved_event_id: event.member_count
        for candidate in sport_overmerge_candidates
        for event in candidate.events
    }
    return BenchmarkSplitSportDiagnosticsOut(
        sport=sport,
        split_candidate_count=len(sport_split_candidates),
        events_in_split_candidates=len(split_event_members),
        members_in_split_candidates=sum(split_event_members.values()),
        overmerge_candidate_count=len(sport_overmerge_candidates),
        events_in_overmerge_candidates=len(overmerge_event_members),
        members_in_overmerge_candidates=sum(overmerge_event_members.values()),
    )


def _league_source(raw_league_id: str, bookmaker_id: str) -> tuple[str, str]:
    resolution = resolve_league(raw_league_id, bookmaker_id=bookmaker_id)
    return resolution.league_id, resolution.display_name


def _raw_odds_sources(raw_odds: list[RawOddsData]) -> list[_RawEventSource]:
    sources: dict[tuple[str, str, str, str, str, str | None], _RawEventSource] = {}
    for raw in raw_odds:
        if not raw.start_time:
            continue
        league_id, league_name = _league_source(raw.league_id, raw.bookmaker_id)
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            normalize_identity_text(raw.home_team),
            normalize_identity_text(raw.away_team),
            raw.source_url,
        )
        sources.setdefault(
            key,
            _RawEventSource(
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
                start_time=raw.start_time,
                home_team=raw.home_team,
                away_team=raw.away_team,
                league_id=league_id,
                league_name=league_name,
                source_url=raw.source_url,
                source_kind="raw_odds",
            ),
        )
    return list(sources.values())


def _raw_outcome_sources(raw_offers: list[RawOutcomeOffer]) -> list[_RawEventSource]:
    sources: dict[tuple[str, str, str, str, str, str | None], _RawEventSource] = {}
    for raw in raw_offers:
        if not raw.start_time:
            continue
        league_id, league_name = _league_source(raw.league_id, raw.bookmaker_id)
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            normalize_identity_text(raw.home_team),
            normalize_identity_text(raw.away_team),
            raw.source_url,
        )
        sources.setdefault(
            key,
            _RawEventSource(
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
                start_time=raw.start_time,
                home_team=raw.home_team,
                away_team=raw.away_team,
                league_id=league_id,
                league_name=league_name,
                source_url=raw.source_url,
                source_kind="raw_outcome_offer",
            ),
        )
    return list(sources.values())


# Sports for which the resolver activates aggressive aliasing & dot-expansion
# heuristics. Re-exported alias of ``outcome_normalizer._AGGRESSIVE_MERGE_SPORTS``
# so the two modules cannot drift; new sports must be enabled in exactly one
# place.
_TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE: frozenset[str] = _AGGRESSIVE_MERGE_SPORTS

# 2-letter dot-prefixes that overlap heavily with non-team words (street,
# fort, mount, port, point, doctor, mister, avenue, saint) and would
# otherwise produce false-positive expansions even within basketball
# (e.g. ``St.Petersburg`` ↔ ``Stockholm Petersburg``). Keeping these out of
# the dot-expansion logic preserves the genuine ``Ch.More`` ↔ ``Cherno More``
# case while blocking the geographic collision class.
_AMBIGUOUS_DOT_PREFIXES: frozenset[str] = frozenset(
    {"st", "ft", "mt", "pt", "dr", "mr", "av"}
)


def _expand_dotted_token(name: str, counterpart: str) -> str:
    """Substitute dot-truncated tokens (``Ch.``, ``Pl.``, ``Ch.More``) by an
    unambiguous expansion drawn from ``counterpart``.

    Only used inside the event resolver — keeps shared :func:`_team_similarity`
    untouched so football pairing is unaffected. Restrictions:

    * Token must end with ``.`` and have at least 2 characters of prefix
      (1-letter prefixes are too ambiguous, e.g. ``B.`` could be Bayern,
      Brest, Belgrade, …).
    * The prefix must not be in ``_AMBIGUOUS_DOT_PREFIXES`` — these short
      geographic / honorific prefixes (``St``, ``Mt``, ``Ft``, …) collide
      with real team-name tokens (``Stockholm``, ``Manchester``, ``Fort``,
      …) and the structural anchor check below cannot disambiguate them.
    * The counterpart must contain exactly one token starting with that
      prefix; ambiguous expansions are dropped.
    * The source name must contain at least one OTHER non-dotted token that
      already appears in the counterpart — this anchors the expansion in
      genuine name overlap and blocks coincidences like
      ``St. Petersburg`` ↔ ``Stockholm Giants`` where ``St`` would
      otherwise expand to ``Stockholm`` purely on prefix uniqueness.

    Compound tokens with internal dots (``Ch.More`` → ``Ch. More``) are
    pre-split before expansion so a missing space after the period does not
    mask the abbreviation.
    """

    spaced = re.sub(r"\.(?=\S)", ". ", name)
    counterpart_spaced = re.sub(r"\.(?=\S)", ". ", counterpart)
    counterpart_tokens = counterpart_spaced.split()
    counterpart_token_set = {token.lower().rstrip(".") for token in counterpart_tokens}
    source_tokens = spaced.split()
    has_anchor = any(
        not token.endswith(".") and token.lower() in counterpart_token_set
        for token in source_tokens
    )
    if not has_anchor:
        return name
    output: list[str] = []
    for token in source_tokens:
        if not token.endswith(".") or len(token) < 3:
            output.append(token)
            continue
        prefix = token[:-1].lower()
        if len(prefix) < 2:
            output.append(token)
            continue
        if prefix in _AMBIGUOUS_DOT_PREFIXES:
            output.append(token)
            continue
        candidates = [
            candidate
            for candidate in counterpart_tokens
            if len(candidate) > len(prefix)
            and candidate.lower().startswith(prefix)
        ]
        if len(candidates) == 1:
            output.append(candidates[0])
        else:
            output.append(token)
    return " ".join(output)


def _resolver_team_similarity(
    left: str, right: str, *, sport: str | None = None
) -> float:
    """Event-resolver-local team similarity that pre-expands dot-truncations.

    Equivalent to :func:`_team_similarity` for cases without dotted
    abbreviations.

    Sport-gated: dot expansion only fires for sports in
    ``_TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE``. Football has its own
    pairing flow with stricter handling, and the dot-expansion logic
    cannot structurally distinguish ``Ch.More`` (basketball — should expand)
    from ``St.Petersburg`` ↔ ``Stockholm Petersburg`` (football — would
    incorrectly merge two distinct cities).
    """

    expanded_left = left
    expanded_right = right
    if sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        expanded_left = _expand_dotted_token(left, right)
        expanded_right = _expand_dotted_token(right, left)
    return _team_similarity(expanded_left, expanded_right, sport=sport)


def _orientation_scores(
    left_home: str,
    left_away: str,
    right_home: str,
    right_away: str,
    *,
    sport: str | None = None,
) -> list[_OrientationScore]:
    scores: list[_OrientationScore] = []
    if _same_team_context(left_home, right_home, sport=sport) and _same_team_context(left_away, right_away, sport=sport):
        scores.append(
            _OrientationScore(
                orientation="as_listed",
                home_score=_resolver_team_similarity(left_home, right_home, sport=sport),
                away_score=_resolver_team_similarity(left_away, right_away, sport=sport),
            )
        )
    if _same_team_context(left_home, right_away, sport=sport) and _same_team_context(left_away, right_home, sport=sport):
        scores.append(
            _OrientationScore(
                orientation="reversed",
                home_score=_resolver_team_similarity(left_home, right_away, sport=sport),
                away_score=_resolver_team_similarity(left_away, right_home, sport=sport),
            )
        )
    return sorted(scores, key=lambda score: score.avg_score, reverse=True)

def _is_subset_or_equal_token_pair(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
    text_cache: _ResolverTextCache | None = None,
) -> bool:
    """True iff one team's significant tokens are a subset/equal of the other's.

    Used to guard fuzzy event auto-merge against false positives where two
    distinct teams share one significant token (e.g. ``South Korea`` /
    ``North Korea``). Subset/equal pairs (``Hermine Nantes`` /
    ``Hermine Nantes Basket``) are typically the same team with an extra
    qualifier and are safe to auto-merge at lowered score thresholds.
    """
    if text_cache is not None:
        left_tokens = text_cache.significant_tokens(left_name, sport=sport)
        right_tokens = text_cache.significant_tokens(right_name, sport=sport)
    else:
        left_tokens = _significant_team_tokens(left_name, sport=sport)
        right_tokens = _significant_team_tokens(right_name, sport=sport)
    if not left_tokens or not right_tokens:
        return False
    return left_tokens <= right_tokens or right_tokens <= left_tokens


def _weak_side_pair_is_subset_or_equal(
    left_home: str,
    left_away: str,
    right_home: str,
    right_away: str,
    score: _OrientationScore,
    *,
    sport: str | None = None,
    text_cache: _ResolverTextCache | None = None,
) -> bool:
    if score.orientation == "as_listed":
        home_pair = (left_home, right_home)
        away_pair = (left_away, right_away)
    else:
        home_pair = (left_home, right_away)
        away_pair = (left_away, right_home)
    weak_pair = home_pair if score.home_score <= score.away_score else away_pair
    return _is_subset_or_equal_token_pair(*weak_pair, sport=sport, text_cache=text_cache)


def _matching_canonical_sides(
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    orientation: str,
) -> set[str]:
    if orientation == "as_listed":
        home_pair = (left_candidate.home_team_id, right_candidate.home_team_id)
        away_pair = (left_candidate.away_team_id, right_candidate.away_team_id)
    else:
        home_pair = (left_candidate.home_team_id, right_candidate.away_team_id)
        away_pair = (left_candidate.away_team_id, right_candidate.home_team_id)

    matching: set[str] = set()
    if home_pair[0] is not None and home_pair[0] == home_pair[1]:
        matching.add("home")
    if away_pair[0] is not None and away_pair[0] == away_pair[1]:
        matching.add("away")
    return matching


def _source_match_score(source: _RawEventSource, candidate: EventCandidate) -> float:
    scores = _orientation_scores(
        source.home_team,
        source.away_team,
        candidate.home_team,
        candidate.away_team,
        sport=source.sport,
    )
    if not scores:
        return 0.0
    score = scores[0].avg_score
    if source.source_url and source.source_url == candidate.source_url:
        score += 10.0
    if source.league_id == candidate.source_league_id:
        score += 3.0
    return score


def _best_source(
    sources_by_slot: dict[tuple[str, str, str], list[_RawEventSource]],
    candidate: EventCandidate,
) -> _RawEventSource | None:
    sources = sources_by_slot.get(
        (candidate.bookmaker_id, candidate.sport, candidate.start_time),
        [],
    )
    if not sources:
        return None
    best = max(sources, key=lambda source: _source_match_score(source, candidate))
    if _source_match_score(best, candidate) < _SOURCE_MATCH_MIN_SCORE:
        return None
    return best


def _normalized_odds_candidate(
    row: NormalizedOdds,
    source: _RawEventSource | None,
) -> EventCandidate | None:
    if not row.start_time:
        return None
    league_id = row.league_id
    league_name = resolve_league(row.league_id, bookmaker_id=row.bookmaker_id).display_name
    if source is not None:
        league_id = source.league_id
        league_name = source.league_name
    return EventCandidate(
        match_id=row.match_id,
        bookmaker_id=row.bookmaker_id,
        sport=row.sport,
        start_time=row.start_time,
        home_team_id=row.home_team_id or None,
        away_team_id=row.away_team_id or None,
        home_team=row.home_team,
        away_team=row.away_team,
        source_league_id=league_id,
        source_league_name=league_name,
        source_home_team=source.home_team if source else row.home_team,
        source_away_team=source.away_team if source else row.away_team,
        source_url=source.source_url if source and source.source_url else row.source_url,
        source_kind=source.source_kind if source else "normalized_odds",
    )


def _normalized_outcome_candidate(
    row: NormalizedOutcomeOffer,
    source: _RawEventSource | None,
) -> EventCandidate | None:
    if not row.start_time:
        return None
    league_id = row.league_id
    league_name = resolve_league(row.league_id, bookmaker_id=row.bookmaker_id).display_name
    if source is not None:
        league_id = source.league_id
        league_name = source.league_name
    return EventCandidate(
        match_id=row.match_id,
        bookmaker_id=row.bookmaker_id,
        sport=row.sport,
        start_time=row.start_time,
        home_team_id=row.home_team_id or None,
        away_team_id=row.away_team_id or None,
        home_team=row.home_team,
        away_team=row.away_team,
        source_league_id=league_id,
        source_league_name=league_name,
        source_home_team=source.home_team if source else row.home_team,
        source_away_team=source.away_team if source else row.away_team,
        source_url=source.source_url if source and source.source_url else row.source_url,
        source_kind=source.source_kind if source else "normalized_outcome_offer",
    )


def _merge_candidate(
    candidates: dict[tuple[str, str], EventCandidate],
    candidate: EventCandidate | None,
) -> None:
    if candidate is None:
        return
    existing = candidates.get(candidate.bookmaker_member_key)
    if existing is None:
        candidates[candidate.bookmaker_member_key] = candidate
        return
    if existing.source_kind.startswith(
        "normalized"
    ) and not candidate.source_kind.startswith("normalized"):
        candidates[candidate.bookmaker_member_key] = candidate
        return
    if existing.source_url is None and candidate.source_url is not None:
        candidates[candidate.bookmaker_member_key] = candidate


def _football_raw_resolution_candidates(
    raw_offers: list[RawOutcomeOffer],
    stored_match_bookmakers: set[tuple[str, str]],
    *,
    football_event_resolutions: FootballEventResolutionMap | None = None,
) -> list[EventCandidate]:
    if not raw_offers:
        return []
    event_resolutions = (
        football_event_resolutions
        if football_event_resolutions is not None
        else _build_football_event_resolutions(raw_offers)
    )
    seen_raw_events: set[tuple[str, str, str, str, str]] = set()
    candidates: list[EventCandidate] = []
    for raw in raw_offers:
        event_key = _event_key_from_raw(raw)
        if event_key is None or event_key in seen_raw_events:
            continue
        seen_raw_events.add(event_key)
        resolution = event_resolutions.get(event_key)
        if resolution is None:
            continue
        match_id = generate_match_id(
            resolution.slot.home_team_id,
            resolution.slot.away_team_id,
            raw.start_time,
            raw.sport,
        )
        if (match_id, raw.bookmaker_id) not in stored_match_bookmakers:
            continue
        league_id, league_name = _league_source(raw.league_id, raw.bookmaker_id)
        candidates.append(
            EventCandidate(
                match_id=match_id,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
                start_time=raw.start_time,
                home_team_id=resolution.slot.home_team_id,
                away_team_id=resolution.slot.away_team_id,
                home_team=resolution.slot.home_team,
                away_team=resolution.slot.away_team,
                source_league_id=league_id,
                source_league_name=league_name,
                source_home_team=raw.home_team,
                source_away_team=raw.away_team,
                source_url=raw.source_url,
                source_kind="raw_outcome_offer",
            )
        )
    return candidates


def extract_event_candidates(
    *,
    raw_odds: list[RawOddsData],
    raw_outcome_offers: list[RawOutcomeOffer],
    normalized_odds: list[NormalizedOdds],
    normalized_outcome_offers: list[NormalizedOutcomeOffer],
    football_event_resolutions: FootballEventResolutionMap | None = None,
    stats: _EventCandidateExtractionStats | None = None,
) -> list[EventCandidate]:
    """Build one source-event candidate per bookmaker/match from the current scrape."""

    candidates: dict[tuple[str, str], EventCandidate] = {}
    odds_sources_by_slot: dict[tuple[str, str, str], list[_RawEventSource]] = defaultdict(list)
    for source in _raw_odds_sources(raw_odds):
        odds_sources_by_slot[(source.bookmaker_id, source.sport, source.start_time)].append(source)

    outcome_sources_by_slot: dict[tuple[str, str, str], list[_RawEventSource]] = defaultdict(list)
    for source in _raw_outcome_sources(raw_outcome_offers):
        outcome_sources_by_slot[
            (source.bookmaker_id, source.sport, source.start_time)
        ].append(source)

    for row in normalized_odds:
        provisional = EventCandidate(
            match_id=row.match_id,
            bookmaker_id=row.bookmaker_id,
            sport=row.sport,
            start_time=row.start_time or "",
            home_team_id=row.home_team_id or None,
            away_team_id=row.away_team_id or None,
            home_team=row.home_team,
            away_team=row.away_team,
            source_league_id=row.league_id,
            source_url=row.source_url,
        )
        if not row.start_time:
            continue
        source = _best_source(odds_sources_by_slot, provisional)
        _merge_candidate(candidates, _normalized_odds_candidate(row, source))

    for row in normalized_outcome_offers:
        provisional = EventCandidate(
            match_id=row.match_id,
            bookmaker_id=row.bookmaker_id,
            sport=row.sport,
            start_time=row.start_time or "",
            home_team_id=row.home_team_id or None,
            away_team_id=row.away_team_id or None,
            home_team=row.home_team,
            away_team=row.away_team,
            source_league_id=row.league_id,
            source_url=row.source_url,
        )
        if not row.start_time:
            continue
        source = _best_source(outcome_sources_by_slot, provisional)
        _merge_candidate(candidates, _normalized_outcome_candidate(row, source))

    stored_outcome_match_bookmakers = {
        (offer.match_id, offer.bookmaker_id) for offer in normalized_outcome_offers
    }
    football_candidates_started_at = time.perf_counter()
    football_candidates = _football_raw_resolution_candidates(
        raw_outcome_offers,
        stored_outcome_match_bookmakers,
        football_event_resolutions=football_event_resolutions,
    )
    if stats is not None and football_event_resolutions is not None:
        stats.reused_football_event_resolution_count = len(football_candidates)
    if stats is not None:
        stats.football_raw_resolution_candidates_ms = _elapsed_ms(
            football_candidates_started_at
        )
    for candidate in football_candidates:
        _merge_candidate(candidates, candidate)

    return sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate.sport,
            candidate.start_time,
            candidate.match_id,
            candidate.bookmaker_id,
        ),
    )


def _shared_significant_tokens(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
    text_cache: _ResolverTextCache | None = None,
) -> set[str]:
    if text_cache is not None:
        return text_cache.significant_tokens(
            left_name, sport=sport
        ) & text_cache.significant_tokens(right_name, sport=sport)
    return _significant_team_tokens(left_name, sport=sport) & _significant_team_tokens(
        right_name,
        sport=sport,
    )


def _anchored_low_conf_detail(
    *,
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    top: _OrientationScore,
    combined_bookmaker_count: int,
    text_cache: _ResolverTextCache | None = None,
) -> str | None:
    """Lower-threshold corroborated merge for same-slot pairs.

    The subset/equal branch is cross-sport so explicit women-marker variants
    can merge outside basketball. The looser shared-token + same-league branch
    remains restricted to the historically tuned aggressive sports because it
    can otherwise merge common false positives such as ``Manchester United`` ↔
    ``Manchester City``.

    Requires:

    * Average score :math:`\\geq` :data:`_ANCHORED_FUZZY_AVG_SCORE` and weak side
      :math:`\\geq` :data:`_ANCHORED_FUZZY_SIDE_SCORE`.
    * One non-fuzzy corroborator:

      - Weak side is a token subset / equal of the strong side, **or**
      - Weak side shares at least one significant token **and** both
        candidates resolve to the same source league.

    * Combined unique bookmakers across the two exact groups
      :math:`\\geq` :data:`_ANCHORED_MIN_BOOKMAKERS`. This guards against the
      2-bookmaker false-positive cases preserved by the South/North Korea
      and Austria/Australia regression tests.
    """

    if combined_bookmaker_count < _ANCHORED_MIN_BOOKMAKERS:
        return None

    matching_canonical_sides = _matching_canonical_sides(
        left_candidate,
        right_candidate,
        top.orientation,
    )
    if matching_canonical_sides:
        min_bookmakers = (
            _CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS
            if left_candidate.sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE
            and right_candidate.sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE
            else _CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS_NON_TARGETED
        )
        if (
            combined_bookmaker_count >= min_bookmakers
            and top.avg_score >= _CANONICAL_SIDE_ANCHOR_AVG_SCORE
            and top.weak_side_score >= _CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE
        ):
            return "canonical side anchored"

    if top.avg_score < _ANCHORED_FUZZY_AVG_SCORE:
        return None
    if top.weak_side_score < _ANCHORED_FUZZY_SIDE_SCORE:
        return None

    if top.orientation == "as_listed":
        home_pair = (left_candidate.home_team, right_candidate.home_team)
        away_pair = (left_candidate.away_team, right_candidate.away_team)
    else:
        home_pair = (left_candidate.home_team, right_candidate.away_team)
        away_pair = (left_candidate.away_team, right_candidate.home_team)
    weak_pair = home_pair if top.home_score <= top.away_score else away_pair

    if _is_subset_or_equal_token_pair(
        *weak_pair,
        sport=left_candidate.sport,
        text_cache=text_cache,
    ):
        return "token subset anchored"

    if left_candidate.sport not in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        return None
    if right_candidate.sport not in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        return None

    if not _shared_significant_tokens(
        *weak_pair,
        sport=left_candidate.sport,
        text_cache=text_cache,
    ):
        return None

    left_league = left_candidate.source_league_id
    right_league = right_candidate.source_league_id
    if left_league and right_league and left_league == right_league:
        return "league anchored"
    return None


def _passes_anchored_low_conf(
    *,
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    top: _OrientationScore,
    combined_bookmaker_count: int,
    text_cache: _ResolverTextCache | None = None,
) -> bool:
    return (
        _anchored_low_conf_detail(
            left_candidate=left_candidate,
            right_candidate=right_candidate,
            top=top,
            combined_bookmaker_count=combined_bookmaker_count,
            text_cache=text_cache,
        )
        is not None
    )


def _quorum_resolution_passes(
    left: _CandidateGroup,
    right: _CandidateGroup,
    pair: _PairResolution,
) -> bool:
    """Same-bookmaker conflict override using only the immutable per-group
    bookmaker sets.

    Restricted to basketball (``_TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE``):
    football has its own outcome_normalizer pairing flow with stricter
    handling, and the quorum thresholds were tuned for the basketball
    Heidelberg-style fragmentations the user reported.

    The DSU root sizes mutate during the pair loop, so basing this decision on
    them would make the outcome order-dependent. Reading ``left.bookmakers``
    and ``right.bookmakers`` (the original exact-group sets) keeps the override
    deterministic.

    Required:

    * ``pair.score >= _QUORUM_FUZZY_AVG_SCORE`` and
      ``pair.weak_side_score >= _QUORUM_FUZZY_SIDE_SCORE`` (high-confidence
      fuzzy match already established).
    * Larger group has ``>= _QUORUM_MIN_LARGER_BOOKMAKERS`` bookmakers.
    * Larger group exceeds the smaller by ``>= _QUORUM_MIN_BOOKMAKER_DIFFERENCE``
      bookmakers (4 vs 4 would not qualify; 9 vs 2 does).
    """

    representative = next(iter(left.candidates), None)
    if representative is None:
        return False
    if representative.sport not in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        return False

    passes_standard_threshold = (
        pair.score >= _QUORUM_FUZZY_AVG_SCORE
        and pair.weak_side_score >= _QUORUM_FUZZY_SIDE_SCORE
    )
    passes_canonical_anchor_threshold = False
    if not passes_standard_threshold:
        for left_candidate in left.candidates:
            for right_candidate in right.candidates:
                if not _matching_canonical_sides(
                    left_candidate,
                    right_candidate,
                    pair.orientation,
                ):
                    continue
                passes_canonical_anchor_threshold = (
                    pair.score >= _QUORUM_CANONICAL_SIDE_ANCHOR_AVG_SCORE
                    and pair.weak_side_score
                    >= _QUORUM_CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE
                )
                if passes_canonical_anchor_threshold:
                    break
            if passes_canonical_anchor_threshold:
                break

    if not passes_standard_threshold and not passes_canonical_anchor_threshold:
        return False

    larger_count = max(len(left.bookmakers), len(right.bookmakers))
    smaller_count = min(len(left.bookmakers), len(right.bookmakers))
    if larger_count < _QUORUM_MIN_LARGER_BOOKMAKERS:
        return False
    if larger_count - smaller_count < _QUORUM_MIN_BOOKMAKER_DIFFERENCE:
        return False
    return True


def _group_pair_resolution(
    left: _CandidateGroup,
    right: _CandidateGroup,
    *,
    text_cache: _ResolverTextCache | None = None,
) -> _PairResolution | None:
    text_cache = text_cache or _ResolverTextCache()
    best: _PairResolution | None = None
    combined_bookmaker_count = len(left.bookmakers | right.bookmakers)
    for left_candidate in left.candidates:
        for right_candidate in right.candidates:
            scores = text_cache.orientation_scores(
                left_candidate.home_team,
                left_candidate.away_team,
                right_candidate.home_team,
                right_candidate.away_team,
                sport=left_candidate.sport,
            )
            if not scores:
                continue
            top = scores[0]
            if (
                len(scores) > 1
                and top.avg_score >= _REVIEW_FUZZY_AVG_SCORE
                and top.avg_score - scores[1].avg_score < _FUZZY_ORIENTATION_MARGIN
            ):
                resolution = _PairResolution(
                    confidence=top.avg_score / 100,
                    score=top.avg_score,
                    weak_side_score=top.weak_side_score,
                    orientation=top.orientation,
                    reason_code="ambiguous_event_orientation",
                    evidence=(
                        f"Exact start time: {left_candidate.start_time}",
                        "Multiple team orientations scored too closely for automatic grouping",
                    ),
                )
            elif (
                _weak_side_pair_is_subset_or_equal(
                    left_candidate.home_team,
                    left_candidate.away_team,
                    right_candidate.home_team,
                    right_candidate.away_team,
                    top,
                    sport=left_candidate.sport,
                    text_cache=text_cache,
                )
                and top.avg_score >= _HIGH_FUZZY_AVG_SCORE
                and top.weak_side_score >= _HIGH_FUZZY_SIDE_SCORE
            ) or (
                top.avg_score >= _HIGH_FUZZY_AVG_SCORE_NON_SUBSET
                and top.weak_side_score >= _HIGH_FUZZY_SIDE_SCORE_NON_SUBSET
            ):
                resolution = _PairResolution(
                    confidence=top.avg_score / 100,
                    score=top.avg_score,
                    weak_side_score=top.weak_side_score,
                    orientation=top.orientation,
                    reason_code="high_confidence_fuzzy_event_match",
                    evidence=(
                        f"Exact start time: {left_candidate.start_time}",
                        (
                            "High-confidence cross-bookmaker fuzzy teams: "
                            f"{left_candidate.home_team} vs {left_candidate.away_team} ↔ "
                            f"{right_candidate.home_team} vs {right_candidate.away_team} "
                            f"({top.orientation}, score {top.avg_score:.1f})"
                        ),
                    ),
                )
            elif (
                anchored_detail := _anchored_low_conf_detail(
                    left_candidate=left_candidate,
                    right_candidate=right_candidate,
                    top=top,
                    combined_bookmaker_count=combined_bookmaker_count,
                    text_cache=text_cache,
                )
            ) is not None:
                resolution = _PairResolution(
                    confidence=top.avg_score / 100,
                    score=top.avg_score,
                    weak_side_score=top.weak_side_score,
                    orientation=top.orientation,
                    reason_code="high_confidence_fuzzy_event_match",
                    evidence=(
                        f"Exact start time: {left_candidate.start_time}",
                        (
                            "Anchored low-confidence cross-bookmaker fuzzy teams: "
                            f"{left_candidate.home_team} vs {left_candidate.away_team} ↔ "
                            f"{right_candidate.home_team} vs {right_candidate.away_team} "
                            f"({top.orientation}, score {top.avg_score:.1f}, "
                            f"weak {top.weak_side_score:.1f}, "
                            f"{combined_bookmaker_count} bookmakers, {anchored_detail})"
                        ),
                    ),
                )
            elif top.avg_score >= _REVIEW_FUZZY_AVG_SCORE:
                resolution = _PairResolution(
                    confidence=top.avg_score / 100,
                    score=top.avg_score,
                    weak_side_score=top.weak_side_score,
                    orientation=top.orientation,
                    reason_code="possible_event_equivalence_low_confidence",
                    evidence=(
                        f"Exact start time: {left_candidate.start_time}",
                        (
                            "Potential cross-bookmaker event match below auto threshold: "
                            f"{left_candidate.home_team} vs {left_candidate.away_team} ↔ "
                            f"{right_candidate.home_team} vs {right_candidate.away_team} "
                            f"({top.orientation}, score {top.avg_score:.1f})"
                        ),
                    ),
                )
            else:
                continue

            if best is None or resolution.score > best.score:
                best = resolution
    return best


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
        groups_by_slot[(representative.sport, representative.start_time)].append(group)

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
                pair = _group_pair_resolution(left, right, text_cache=text_cache)
                if pair is None:
                    continue
                if pair.reason_code == "high_confidence_fuzzy_event_match":
                    if dsu.can_union(left.index, right.index):
                        dsu.union(left.index, right.index)
                        accepted_pairs.append((left.index, right.index, pair))
                        if stats is not None:
                            stats.accepted_fuzzy_pair_count += 1
                    elif _quorum_resolution_passes(left, right, pair):
                        dsu.union(left.index, right.index)
                        quorum_pair = _PairResolution(
                            confidence=pair.confidence,
                            score=pair.score,
                            weak_side_score=pair.weak_side_score,
                            orientation=pair.orientation,
                            reason_code="high_confidence_fuzzy_event_match",
                            evidence=(
                                *pair.evidence,
                                (
                                    "Quorum-resolved same-bookmaker conflict: "
                                    f"larger group has {len(left.bookmakers | right.bookmakers)} "
                                    "combined bookmakers and dominant size advantage"
                                ),
                            ),
                        )
                        accepted_pairs.append((left.index, right.index, quorum_pair))
                        if stats is not None:
                            stats.accepted_fuzzy_pair_count += 1
                        # Log the override for operator visibility instead of
                        # emitting an audit review case. The override is
                        # explicitly intended to clear pairs from the manual
                        # queue per the user's "few wrong is OK" preference,
                        # so adding a parallel audit row would defeat that
                        # goal and accumulate stale entries every cycle.
                        left_rep = left.candidates[0] if left.candidates else None
                        right_rep = right.candidates[0] if right.candidates else None
                        logger.info(
                            "event_resolver.quorum_override "
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
                            pair.score,
                            pair.weak_side_score,
                            sorted(left.bookmakers),
                            sorted(right.bookmakers),
                        )
                    else:
                        conflict_pair = _PairResolution(
                            confidence=pair.confidence,
                            score=pair.score,
                            weak_side_score=pair.weak_side_score,
                            orientation=pair.orientation,
                            reason_code="conflicting_same_bookmaker_event_candidate",
                            evidence=(
                                *pair.evidence,
                                (
                                    "Automatic grouping skipped because both groups "
                                    "contain the same bookmaker"
                                ),
                            ),
                        )
                        review_case = _event_review_case(left, right, conflict_pair)
                        review_cases[review_case.fingerprint] = review_case
                    continue
                review_case = _event_review_case(left, right, pair)
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
        primary = members[0]
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
) -> EventResolverResult:
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
        primary = min(
            resolution.members,
            key=lambda candidate: (candidate.match_id, candidate.bookmaker_id),
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
                    source_start_time=member.start_time,
                    evidence=list(resolution.evidence),
                    metadata={
                        "resolver": _RESOLVER_VERSION,
                        "source_kind": member.source_kind,
                        "display_home_team": member.home_team,
                        "display_away_team": member.away_team,
                    },
                )
            )

    result = await odds_store.persist_event_resolution_batch(
        snapshot_id=snapshot_id,
        events=events,
        members=members,
        review_cases=review_cases,
    )

    return EventResolverResult(
        candidates=len(members),
        resolved_events=result["resolved_events"],
        resolved_event_members=result["resolved_event_members"],
        review_cases=result["review_cases"],
    )


async def resolve_and_persist_events(
    *,
    snapshot_id: str | None = None,
    raw_odds: list[RawOddsData],
    raw_outcome_offers: list[RawOutcomeOffer],
    normalized_odds: list[NormalizedOdds],
    normalized_outcome_offers: list[NormalizedOutcomeOffer],
    football_event_resolutions: FootballEventResolutionMap | None = None,
) -> EventResolverResult:
    extraction_stats = _EventCandidateExtractionStats()
    extraction_started_at = time.perf_counter()
    candidates = extract_event_candidates(
        raw_odds=raw_odds,
        raw_outcome_offers=raw_outcome_offers,
        normalized_odds=normalized_odds,
        normalized_outcome_offers=normalized_outcome_offers,
        football_event_resolutions=football_event_resolutions,
        stats=extraction_stats,
    )
    extract_event_candidates_ms = _elapsed_ms(extraction_started_at)

    group_stats = _EventGroupBuildStats()
    grouping_started_at = time.perf_counter()
    resolutions, review_cases = build_event_resolution_groups(
        candidates,
        stats=group_stats,
    )
    build_event_resolution_groups_ms = _elapsed_ms(grouping_started_at)
    coverage = _event_coverage_benchmark(
        normalized_odds=normalized_odds,
        normalized_outcome_offers=normalized_outcome_offers,
        resolutions=resolutions,
        review_cases=review_cases,
    )
    split_diagnostics = _event_split_diagnostics_benchmark(resolutions)

    persistence_started_at = time.perf_counter()
    result = await persist_event_resolution_groups(
        resolutions,
        review_cases,
        snapshot_id=snapshot_id,
    )
    persist_event_resolution_groups_ms = _elapsed_ms(persistence_started_at)
    benchmark = EventResolverBenchmarkOut(
        extract_event_candidates_ms=extract_event_candidates_ms,
        football_raw_resolution_candidates_ms=(
            extraction_stats.football_raw_resolution_candidates_ms
        ),
        reused_football_event_resolution_count=(
            extraction_stats.reused_football_event_resolution_count
        ),
        build_event_resolution_groups_ms=build_event_resolution_groups_ms,
        persist_event_resolution_groups_ms=persist_event_resolution_groups_ms,
        candidate_count=len(candidates),
        exact_group_count=group_stats.exact_group_count,
        pair_check_count=group_stats.pair_check_count,
        fuzzy_score_count=group_stats.fuzzy_score_count,
        accepted_fuzzy_pair_count=group_stats.accepted_fuzzy_pair_count,
        review_case_count=group_stats.review_case_count,
        persisted_resolved_event_count=result.resolved_events,
        persisted_member_count=result.resolved_event_members,
        persisted_review_case_count=result.review_cases,
    )
    logger.info(
        "Resolved %d source-event candidates into %d events (%d members, %d review cases)",
        len(candidates),
        result.resolved_events,
        result.resolved_event_members,
        result.review_cases,
    )
    return EventResolverResult(
        candidates=len(candidates),
        resolved_events=result.resolved_events,
        resolved_event_members=result.resolved_event_members,
        review_cases=result.review_cases,
        benchmark=benchmark,
        coverage=coverage,
        split_diagnostics=split_diagnostics,
    )
