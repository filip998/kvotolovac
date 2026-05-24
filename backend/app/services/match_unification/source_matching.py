from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

from ..text_normalizer import normalize_identity_text
from .event_matching import _orientation_scores


SourceMatchStrategy = Literal[
    "exact_url",
    "listed_pair",
    "unordered_pair",
    "fallback_scan",
    "no_slot",
    "no_match",
]
SourceMatchOrientation = Literal["same_order", "reversed"]
SourceMatchDecision = Literal[
    "accepted",
    "empty_subset",
    "score_below_threshold",
    "below_slot_max",
    "not_first_slot_source",
    "no_source_above_threshold",
]

SOURCE_MATCH_MIN_SCORE = 60.0
_SCORE_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("0_59", 0.0, 60.0),
    ("60_69", 60.0, 70.0),
    ("70_79", 70.0, 80.0),
    ("80_89", 80.0, 90.0),
    ("90_99", 90.0, 100.0),
    ("100_plus", 100.0, None),
)


@dataclass(frozen=True)
class RawEventSource:
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
class SourceSlotIndex:
    all_sources: tuple[RawEventSource, ...]
    by_source_url: dict[str, tuple[RawEventSource, ...]]
    by_listed_pair: dict[tuple[str, str], tuple[RawEventSource, ...]]
    by_unordered_pair: dict[frozenset[str], tuple[RawEventSource, ...]]
    source_urls: frozenset[str]
    league_ids: frozenset[str]


@dataclass(frozen=True)
class SourceMatchQuery:
    bookmaker_id: str
    sport: str
    start_time: str
    home_team: str
    away_team: str
    source_url: str | None = None
    league_id: str | None = None

    @property
    def slot_key(self) -> tuple[str, str, str]:
        return (self.bookmaker_id, self.sport, self.start_time)

@dataclass(frozen=True)
class SourceMatchScore:
    source: RawEventSource
    score: float
    orientation: SourceMatchOrientation | None


@dataclass(frozen=True)
class SourceMatchAttempt:
    strategy: SourceMatchStrategy
    source_count: int
    scored_source_count: int
    best_score: float | None
    threshold: float
    decision: SourceMatchDecision
    reason: str
    orientation: SourceMatchOrientation | None = None
    source: RawEventSource | None = None

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted"


@dataclass(frozen=True)
class SourceMatchResult:
    strategy: SourceMatchStrategy
    source: RawEventSource | None
    score: float | None
    threshold: float
    reason: str
    orientation: SourceMatchOrientation | None
    slot_key: tuple[str, str, str]
    source_count_in_slot: int
    attempts: tuple[SourceMatchAttempt, ...] = ()
    fallback_scan_attempted: bool = False

    @property
    def matched(self) -> bool:
        return self.source is not None

    @property
    def rejected_fast_path_count(self) -> int:
        return sum(
            1
            for attempt in self.attempts
            if attempt.strategy
            in {
                "exact_url",
                "listed_pair",
                "unordered_pair",
            }
            and not attempt.accepted
            and attempt.decision != "empty_subset"
        )

    @property
    def scored_source_count(self) -> int:
        return sum(attempt.scored_source_count for attempt in self.attempts)

    @property
    def index_candidate_count(self) -> int:
        return sum(
            attempt.source_count
            for attempt in self.attempts
            if attempt.strategy
            in {
                "exact_url",
                "listed_pair",
                "unordered_pair",
            }
            and attempt.source_count
        )


@dataclass(frozen=True)
class SourceMatchScopedSummary:
    key: str
    lookup_count: int = 0
    matched_count: int = 0
    no_slot_count: int = 0
    no_match_count: int = 0
    fallback_scan_attempt_count: int = 0
    fallback_scan_hit_count: int = 0
    rejected_fast_path_count: int = 0
    scored_source_count: int = 0
    average_score: float = 0.0


@dataclass(frozen=True)
class SourceMatchBenchmarkSummary:
    strategy_counts: dict[str, int]
    reason_counts: dict[str, int]
    attempt_reason_counts: dict[str, int]
    score_buckets: dict[str, int]
    attempt_score_buckets: dict[str, int]
    bookmakers: tuple[SourceMatchScopedSummary, ...]
    sports: tuple[SourceMatchScopedSummary, ...]


class SourceMatchBenchmarkRecorder:
    def __init__(self) -> None:
        self.strategy_counts: Counter[str] = Counter()
        self.reason_counts: Counter[str] = Counter()
        self.attempt_reason_counts: Counter[str] = Counter()
        self.score_buckets: Counter[str] = Counter()
        self.attempt_score_buckets: Counter[str] = Counter()
        self._bookmaker_stats: defaultdict[str, _SourceMatchScopeAccumulator] = (
            defaultdict(_SourceMatchScopeAccumulator)
        )
        self._sport_stats: defaultdict[str, _SourceMatchScopeAccumulator] = defaultdict(
            _SourceMatchScopeAccumulator
        )

    def record(self, result: SourceMatchResult) -> None:
        self.strategy_counts[result.strategy] += 1
        self.reason_counts[result.reason] += 1
        if result.score is not None:
            self.score_buckets[_score_bucket(result.score)] += 1
        for attempt in result.attempts:
            self.attempt_reason_counts[attempt.reason] += 1
            if attempt.best_score is not None:
                self.attempt_score_buckets[_score_bucket(attempt.best_score)] += 1

        bookmaker_id, sport, _start_time = result.slot_key
        self._record_scope(self._bookmaker_stats[bookmaker_id], result)
        self._record_scope(self._sport_stats[sport], result)

    def summary(self) -> SourceMatchBenchmarkSummary:
        return SourceMatchBenchmarkSummary(
            strategy_counts=dict(sorted(self.strategy_counts.items())),
            reason_counts=dict(sorted(self.reason_counts.items())),
            attempt_reason_counts=dict(sorted(self.attempt_reason_counts.items())),
            score_buckets={
                bucket: self.score_buckets.get(bucket, 0)
                for bucket, _lower, _upper in _SCORE_BUCKETS
            },
            attempt_score_buckets={
                bucket: self.attempt_score_buckets.get(bucket, 0)
                for bucket, _lower, _upper in _SCORE_BUCKETS
            },
            bookmakers=tuple(
                self._summary_rows(self._bookmaker_stats),
            ),
            sports=tuple(
                self._summary_rows(self._sport_stats),
            ),
        )

    def _record_scope(
        self,
        accumulator: _SourceMatchScopeAccumulator,
        result: SourceMatchResult,
    ) -> None:
        accumulator.lookup_count += 1
        accumulator.scored_source_count += result.scored_source_count
        accumulator.rejected_fast_path_count += result.rejected_fast_path_count
        if result.matched:
            accumulator.matched_count += 1
            if result.score is not None:
                accumulator.score_total += result.score
                accumulator.scored_result_count += 1
        elif result.strategy == "no_slot":
            accumulator.no_slot_count += 1
        else:
            accumulator.no_match_count += 1
        if result.fallback_scan_attempted:
            accumulator.fallback_scan_attempt_count += 1
            if result.strategy == "fallback_scan" and result.matched:
                accumulator.fallback_scan_hit_count += 1

    def _summary_rows(
        self,
        rows: dict[str, _SourceMatchScopeAccumulator],
    ) -> list[SourceMatchScopedSummary]:
        summaries: list[SourceMatchScopedSummary] = []
        for key, accumulator in rows.items():
            average_score = (
                round(accumulator.score_total / accumulator.scored_result_count, 4)
                if accumulator.scored_result_count
                else 0.0
            )
            summaries.append(
                SourceMatchScopedSummary(
                    key=key,
                    lookup_count=accumulator.lookup_count,
                    matched_count=accumulator.matched_count,
                    no_slot_count=accumulator.no_slot_count,
                    no_match_count=accumulator.no_match_count,
                    fallback_scan_attempt_count=(
                        accumulator.fallback_scan_attempt_count
                    ),
                    fallback_scan_hit_count=accumulator.fallback_scan_hit_count,
                    rejected_fast_path_count=accumulator.rejected_fast_path_count,
                    scored_source_count=accumulator.scored_source_count,
                    average_score=average_score,
                )
            )
        return sorted(
            summaries,
            key=lambda row: (
                row.lookup_count,
                row.matched_count,
                row.key,
            ),
            reverse=True,
        )


@dataclass
class _SourceMatchScopeAccumulator:
    lookup_count: int = 0
    matched_count: int = 0
    no_slot_count: int = 0
    no_match_count: int = 0
    fallback_scan_attempt_count: int = 0
    fallback_scan_hit_count: int = 0
    rejected_fast_path_count: int = 0
    scored_source_count: int = 0
    score_total: float = 0.0
    scored_result_count: int = 0


class SourceMatcher:
    def __init__(
        self,
        sources: list[RawEventSource],
        *,
        threshold: float = SOURCE_MATCH_MIN_SCORE,
    ) -> None:
        self._sources_by_slot = build_source_slot_indexes(sources)
        self._threshold = threshold

    def match(self, query: SourceMatchQuery) -> SourceMatchResult:
        slot_key = query.slot_key
        slot_index = self._sources_by_slot.get(slot_key)
        if slot_index is None:
            return SourceMatchResult(
                strategy="no_slot",
                source=None,
                score=None,
                threshold=self._threshold,
                reason="no_slot",
                orientation=None,
                slot_key=slot_key,
                source_count_in_slot=0,
            )

        score_cache: dict[int, SourceMatchScore] = {}
        attempts: list[SourceMatchAttempt] = []
        scored_since_last_attempt = 0

        def score_source(source: RawEventSource) -> SourceMatchScore:
            nonlocal scored_since_last_attempt
            cache_key = id(source)
            if cache_key not in score_cache:
                score_cache[cache_key] = source_match_score(source, query)
                scored_since_last_attempt += 1
            return score_cache[cache_key]

        def best_from_subset(
            subset: tuple[RawEventSource, ...],
        ) -> SourceMatchScore | None:
            best: SourceMatchScore | None = None
            for source in subset:
                scored = score_source(source)
                if best is None or scored.score > best.score:
                    best = scored
            return best

        def indexed_attempt(
            strategy: SourceMatchStrategy,
            subset: tuple[RawEventSource, ...],
        ) -> SourceMatchAttempt:
            nonlocal scored_since_last_attempt
            scored_since_last_attempt = 0
            if not subset:
                return SourceMatchAttempt(
                    strategy=strategy,
                    source_count=0,
                    scored_source_count=0,
                    best_score=None,
                    threshold=self._threshold,
                    decision="empty_subset",
                    reason="empty_subset",
                )
            best = best_from_subset(subset)
            scored_count = scored_since_last_attempt
            if best is None:
                return SourceMatchAttempt(
                    strategy=strategy,
                    source_count=len(subset),
                    scored_source_count=scored_count,
                    best_score=None,
                    threshold=self._threshold,
                    decision="empty_subset",
                    reason="empty_subset",
                )
            if best.score < self._threshold:
                return _rejected_attempt(
                    strategy,
                    subset,
                    scored_count,
                    best,
                    self._threshold,
                    "score_below_threshold",
                )
            if best.score < source_match_max_score(query, slot_index):
                return _rejected_attempt(
                    strategy,
                    subset,
                    scored_count,
                    best,
                    self._threshold,
                    "below_slot_max",
                )
            if not (
                query.source_url and best.source.source_url == query.source_url
            ) and (
                not slot_index.all_sources or slot_index.all_sources[0] is not best.source
            ):
                return _rejected_attempt(
                    strategy,
                    subset,
                    scored_count,
                    best,
                    self._threshold,
                    "not_first_slot_source",
                )
            return SourceMatchAttempt(
                strategy=strategy,
                source_count=len(subset),
                scored_source_count=scored_count,
                best_score=best.score,
                threshold=self._threshold,
                decision="accepted",
                reason="accepted",
                orientation=best.orientation,
                source=best.source,
            )

        if query.source_url:
            attempt = indexed_attempt(
                "exact_url",
                slot_index.by_source_url.get(query.source_url, ()),
            )
            attempts.append(attempt)
            if attempt.accepted:
                return _accepted_result(
                    attempt,
                    slot_key,
                    len(slot_index.all_sources),
                    tuple(attempts),
                    fallback_scan_attempted=False,
                )

        listed_key = source_listed_pair_key(query.home_team, query.away_team)
        listed_attempt = indexed_attempt(
            "listed_pair",
            slot_index.by_listed_pair.get(listed_key, ()),
        )
        attempts.append(listed_attempt)
        if listed_attempt.accepted:
            return _accepted_result(
                listed_attempt,
                slot_key,
                len(slot_index.all_sources),
                tuple(attempts),
                fallback_scan_attempted=False,
            )

        unordered_key = source_unordered_pair_key(query.home_team, query.away_team)
        unordered_attempt = indexed_attempt(
            "unordered_pair",
            slot_index.by_unordered_pair.get(unordered_key, ()),
        )
        attempts.append(unordered_attempt)
        if unordered_attempt.accepted:
            return _accepted_result(
                unordered_attempt,
                slot_key,
                len(slot_index.all_sources),
                tuple(attempts),
                fallback_scan_attempted=False,
            )

        scored_since_last_attempt = 0
        best = best_from_subset(slot_index.all_sources)
        scored_count = scored_since_last_attempt
        if best is None or best.score < self._threshold:
            decision: SourceMatchDecision = (
                "score_below_threshold"
                if best is not None
                else "no_source_above_threshold"
            )
            attempt = SourceMatchAttempt(
                strategy="fallback_scan",
                source_count=len(slot_index.all_sources),
                scored_source_count=scored_count,
                best_score=best.score if best else None,
                threshold=self._threshold,
                decision=decision,
                reason=decision,
                orientation=best.orientation if best else None,
                source=best.source if best else None,
            )
            attempts.append(attempt)
            return SourceMatchResult(
                strategy="no_match",
                source=None,
                score=best.score if best else None,
                threshold=self._threshold,
                reason=attempt.reason,
                orientation=best.orientation if best else None,
                slot_key=slot_key,
                source_count_in_slot=len(slot_index.all_sources),
                attempts=tuple(attempts),
                fallback_scan_attempted=True,
            )

        fallback_attempt = SourceMatchAttempt(
            strategy="fallback_scan",
            source_count=len(slot_index.all_sources),
            scored_source_count=scored_count,
            best_score=best.score,
            threshold=self._threshold,
            decision="accepted",
            reason="accepted",
            orientation=best.orientation,
            source=best.source,
        )
        attempts.append(fallback_attempt)
        return _accepted_result(
            fallback_attempt,
            slot_key,
            len(slot_index.all_sources),
            tuple(attempts),
            fallback_scan_attempted=True,
        )


def build_source_slot_indexes(
    sources: list[RawEventSource],
) -> dict[tuple[str, str, str], SourceSlotIndex]:
    by_slot: dict[tuple[str, str, str], list[RawEventSource]] = defaultdict(list)
    for source in sources:
        by_slot[(source.bookmaker_id, source.sport, source.start_time)].append(source)

    indexes: dict[tuple[str, str, str], SourceSlotIndex] = {}
    for slot_key, slot_sources in by_slot.items():
        by_source_url: dict[str, list[RawEventSource]] = defaultdict(list)
        by_listed_pair: dict[tuple[str, str], list[RawEventSource]] = defaultdict(list)
        by_unordered_pair: dict[frozenset[str], list[RawEventSource]] = defaultdict(list)
        source_urls: set[str] = set()
        league_ids: set[str] = set()
        for source in slot_sources:
            if source.source_url:
                by_source_url[source.source_url].append(source)
                source_urls.add(source.source_url)
            league_ids.add(source.league_id)
            listed_key = source_listed_pair_key(source.home_team, source.away_team)
            by_listed_pair[listed_key].append(source)
            by_unordered_pair[frozenset(listed_key)].append(source)
        indexes[slot_key] = SourceSlotIndex(
            all_sources=tuple(slot_sources),
            by_source_url=_freeze_multimap(by_source_url),
            by_listed_pair=_freeze_multimap(by_listed_pair),
            by_unordered_pair=_freeze_multimap(by_unordered_pair),
            source_urls=frozenset(source_urls),
            league_ids=frozenset(league_ids),
        )
    return indexes


def source_match_score(
    source: RawEventSource,
    query: SourceMatchQuery,
) -> SourceMatchScore:
    scores = _orientation_scores(
        source.home_team,
        source.away_team,
        query.home_team,
        query.away_team,
        sport=source.sport,
    )
    if not scores:
        return SourceMatchScore(source=source, score=0.0, orientation=None)
    orientation_score = scores[0]
    score = orientation_score.avg_score
    if source.source_url and source.source_url == query.source_url:
        score += 10.0
    if query.league_id and source.league_id == query.league_id:
        score += 3.0
    orientation: SourceMatchOrientation = (
        "reversed" if orientation_score.orientation == "reversed" else "same_order"
    )
    return SourceMatchScore(source=source, score=score, orientation=orientation)


def source_match_max_score(
    query: SourceMatchQuery,
    slot_index: SourceSlotIndex,
) -> float:
    score = 100.0
    if query.source_url and query.source_url in slot_index.source_urls:
        score += 10.0
    if query.league_id and query.league_id in slot_index.league_ids:
        score += 3.0
    return score


def source_listed_pair_key(
    home_team: str | None,
    away_team: str | None,
) -> tuple[str, str]:
    return (
        normalize_identity_text(home_team),
        normalize_identity_text(away_team),
    )


def source_unordered_pair_key(
    home_team: str | None,
    away_team: str | None,
) -> frozenset[str]:
    return frozenset(source_listed_pair_key(home_team, away_team))


def _freeze_multimap(rows: dict) -> dict:
    return {key: tuple(value) for key, value in rows.items()}


def _rejected_attempt(
    strategy: SourceMatchStrategy,
    subset: tuple[RawEventSource, ...],
    scored_count: int,
    best: SourceMatchScore,
    threshold: float,
    reason: SourceMatchDecision,
) -> SourceMatchAttempt:
    return SourceMatchAttempt(
        strategy=strategy,
        source_count=len(subset),
        scored_source_count=scored_count,
        best_score=best.score,
        threshold=threshold,
        decision=reason,
        reason=reason,
        orientation=best.orientation,
        source=best.source,
    )


def _accepted_result(
    attempt: SourceMatchAttempt,
    slot_key: tuple[str, str, str],
    source_count: int,
    attempts: tuple[SourceMatchAttempt, ...],
    *,
    fallback_scan_attempted: bool,
) -> SourceMatchResult:
    return SourceMatchResult(
        strategy=attempt.strategy,
        source=attempt.source,
        score=attempt.best_score,
        threshold=attempt.threshold,
        reason="accepted",
        orientation=attempt.orientation,
        slot_key=slot_key,
        source_count_in_slot=source_count,
        attempts=attempts,
        fallback_scan_attempted=fallback_scan_attempted,
    )


def _score_bucket(score: float) -> str:
    for label, lower, upper in _SCORE_BUCKETS:
        if score >= lower and (upper is None or score < upper):
            return label
    return "0_59"
