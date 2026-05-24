from __future__ import annotations

from dataclasses import dataclass, field
import time

from ...models.schemas import (
    NormalizedOdds,
    NormalizedOutcomeOffer,
    RawOddsData,
    RawOutcomeOffer,
)
from ..league_registry import resolve_league
from ..normalizer import generate_match_id
from ..outcome_normalizer import (
    _build_football_event_resolutions,
    _event_key_from_raw,
    FootballEventResolutionMap,
)
from ..text_normalizer import normalize_identity_text
from .event_matching import EventCandidate
from .source_matching import (
    RawEventSource,
    SourceMatchBenchmarkRecorder,
    SourceMatcher,
    SourceMatchQuery,
    SourceMatchResult,
    SourceMatchScopedSummary,
)


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


@dataclass
class _EventCandidateExtractionStats:
    extract_raw_odds_sources_ms: int = 0
    extract_raw_outcome_sources_ms: int = 0
    extract_normalized_odds_candidates_ms: int = 0
    extract_normalized_outcome_candidates_ms: int = 0
    raw_odds_rows_scanned: int = 0
    raw_odds_sources_emitted: int = 0
    raw_outcome_offer_rows_scanned: int = 0
    raw_outcome_sources_emitted: int = 0
    normalized_odds_rows_scanned: int = 0
    normalized_odds_candidates_emitted: int = 0
    normalized_outcome_offer_rows_scanned: int = 0
    normalized_outcome_candidates_emitted: int = 0
    stored_outcome_match_bookmaker_count: int = 0
    source_match_lookup_count: int = 0
    source_match_source_count: int = 0
    source_match_scored_source_count: int = 0
    source_match_index_candidate_count: int = 0
    source_match_exact_url_hit_count: int = 0
    source_match_listed_pair_hit_count: int = 0
    source_match_unordered_pair_hit_count: int = 0
    source_match_fallback_scan_count: int = 0
    source_match_fallback_scan_hit_count: int = 0
    source_match_fallback_scan_miss_count: int = 0
    source_match_rejected_fast_path_count: int = 0
    source_match_max_sources_per_lookup: int = 0
    source_match_strategy_counts: dict[str, int] = field(default_factory=dict)
    source_match_reason_counts: dict[str, int] = field(default_factory=dict)
    source_match_attempt_reason_counts: dict[str, int] = field(default_factory=dict)
    source_match_score_buckets: dict[str, int] = field(default_factory=dict)
    source_match_attempt_score_buckets: dict[str, int] = field(default_factory=dict)
    source_match_bookmakers: list[SourceMatchScopedSummary] = field(
        default_factory=list
    )
    source_match_sports: list[SourceMatchScopedSummary] = field(default_factory=list)
    source_match_slot_lookup_counts: dict[tuple[str, str, str], int] = field(
        default_factory=dict
    )
    source_match_slot_source_counts: dict[tuple[str, str, str], int] = field(
        default_factory=dict
    )
    football_raw_candidate_count: int = 0
    football_raw_resolution_candidates_ms: int = 0
    reused_football_event_resolution_count: int = 0
    _source_match_seconds: float = 0.0
    _source_match_recorder: SourceMatchBenchmarkRecorder = field(
        default_factory=SourceMatchBenchmarkRecorder
    )

    @property
    def source_match_ms(self) -> int:
        return int(self._source_match_seconds * 1000)

    def record_source_match(
        self,
        result: SourceMatchResult,
        elapsed_seconds: float,
    ) -> None:
        self._source_match_seconds += elapsed_seconds
        self.source_match_lookup_count += 1
        self.source_match_source_count += result.source_count_in_slot
        self.source_match_scored_source_count += result.scored_source_count
        self.source_match_index_candidate_count += result.index_candidate_count
        self.source_match_rejected_fast_path_count += result.rejected_fast_path_count
        self.source_match_max_sources_per_lookup = max(
            self.source_match_max_sources_per_lookup,
            result.source_count_in_slot,
        )
        self.source_match_slot_lookup_counts[result.slot_key] = (
            self.source_match_slot_lookup_counts.get(result.slot_key, 0) + 1
        )
        self.source_match_slot_source_counts[result.slot_key] = (
            self.source_match_slot_source_counts.get(result.slot_key, 0)
            + result.source_count_in_slot
        )
        if result.strategy == "exact_url":
            self.source_match_exact_url_hit_count += 1
        elif result.strategy == "listed_pair":
            self.source_match_listed_pair_hit_count += 1
        elif result.strategy == "unordered_pair":
            self.source_match_unordered_pair_hit_count += 1
        if result.fallback_scan_attempted:
            self.source_match_fallback_scan_count += 1
            if result.strategy == "fallback_scan" and result.matched:
                self.source_match_fallback_scan_hit_count += 1
            else:
                self.source_match_fallback_scan_miss_count += 1
        self._source_match_recorder.record(result)

    def finalize_source_match_summary(self) -> None:
        started_at = time.perf_counter()
        summary = self._source_match_recorder.summary()
        self.source_match_strategy_counts = summary.strategy_counts
        self.source_match_reason_counts = summary.reason_counts
        self.source_match_attempt_reason_counts = summary.attempt_reason_counts
        self.source_match_score_buckets = summary.score_buckets
        self.source_match_attempt_score_buckets = summary.attempt_score_buckets
        self.source_match_bookmakers = list(summary.bookmakers)
        self.source_match_sports = list(summary.sports)
        self._source_match_seconds += time.perf_counter() - started_at


def _league_source(raw_league_id: str, bookmaker_id: str) -> tuple[str, str]:
    resolution = resolve_league(raw_league_id, bookmaker_id=bookmaker_id)
    return resolution.league_id, resolution.display_name


def _league_source_cached(
    raw_league_id: str,
    bookmaker_id: str,
    cache: dict[tuple[str, str], tuple[str, str]] | None,
) -> tuple[str, str]:
    if cache is None:
        return _league_source(raw_league_id, bookmaker_id)
    key = (raw_league_id, bookmaker_id)
    value = cache.get(key)
    if value is None:
        value = _league_source(raw_league_id, bookmaker_id)
        cache[key] = value
    return value


def _normalized_identity_cached(value: str, cache: dict[str, str]) -> str:
    normalized = cache.get(value)
    if normalized is None:
        normalized = normalize_identity_text(value)
        cache[value] = normalized
    return normalized


def _raw_odds_sources(
    raw_odds: list[RawOddsData],
    *,
    league_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> list[RawEventSource]:
    source_rows: dict[tuple[str, str, str, str, str, str | None], RawOddsData] = {}
    identity_cache: dict[str, str] = {}
    for raw in raw_odds:
        if not raw.start_time:
            continue
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            _normalized_identity_cached(raw.home_team, identity_cache),
            _normalized_identity_cached(raw.away_team, identity_cache),
            raw.source_url,
        )
        source_rows.setdefault(key, raw)

    sources: list[RawEventSource] = []
    for raw in source_rows.values():
        league_id, league_name = _league_source_cached(
            raw.league_id,
            raw.bookmaker_id,
            league_cache,
        )
        sources.append(
            RawEventSource(
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
    return sources


def _raw_outcome_sources(
    raw_offers: list[RawOutcomeOffer],
    *,
    league_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> list[RawEventSource]:
    source_rows: dict[tuple[str, str, str, str, str, str | None], RawOutcomeOffer] = {}
    identity_cache: dict[str, str] = {}
    for raw in raw_offers:
        if not raw.start_time:
            continue
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            _normalized_identity_cached(raw.home_team, identity_cache),
            _normalized_identity_cached(raw.away_team, identity_cache),
            raw.source_url,
        )
        source_rows.setdefault(key, raw)

    sources: list[RawEventSource] = []
    for raw in source_rows.values():
        league_id, league_name = _league_source_cached(
            raw.league_id,
            raw.bookmaker_id,
            league_cache,
        )
        sources.append(
            RawEventSource(
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
    return sources


def _best_source(
    matcher: SourceMatcher,
    candidate: EventCandidate,
    *,
    stats: _EventCandidateExtractionStats | None = None,
) -> RawEventSource | None:
    started_at = time.perf_counter()
    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id=candidate.bookmaker_id,
            sport=candidate.sport,
            start_time=candidate.start_time,
            home_team=candidate.home_team,
            away_team=candidate.away_team,
            source_url=candidate.source_url,
            league_id=candidate.source_league_id,
        )
    )
    if stats is not None:
        stats.record_source_match(
            result,
            time.perf_counter() - started_at,
        )
    return result.source


def _normalized_odds_candidate(
    row: NormalizedOdds,
    source: RawEventSource | None,
    *,
    league_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> EventCandidate | None:
    if not row.start_time:
        return None
    league_id = row.league_id
    league_name = _league_source_cached(
        row.league_id,
        row.bookmaker_id,
        league_cache,
    )[1]
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
        source_start_time=source.start_time if source else row.start_time,
        source_url=source.source_url if source and source.source_url else row.source_url,
        source_kind=source.source_kind if source else "normalized_odds",
    )


def _normalized_outcome_candidate(
    row: NormalizedOutcomeOffer,
    source: RawEventSource | None,
    *,
    league_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> EventCandidate | None:
    if not row.start_time:
        return None
    league_id = row.league_id
    league_name = _league_source_cached(
        row.league_id,
        row.bookmaker_id,
        league_cache,
    )[1]
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
        source_start_time=source.start_time if source else row.start_time,
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


def _prefer_row_with_source_url(existing, row):
    if not getattr(existing, "source_url", None) and getattr(
        row,
        "source_url",
        None,
    ):
        return row
    return existing


def _unique_normalized_odds_rows(
    rows: list[NormalizedOdds],
) -> list[NormalizedOdds]:
    unique: dict[tuple[str, str], NormalizedOdds] = {}
    for row in rows:
        if not row.start_time:
            continue
        key = (row.match_id, row.bookmaker_id)
        existing = unique.get(key)
        unique[key] = (
            row if existing is None else _prefer_row_with_source_url(existing, row)
        )
    return list(unique.values())


def _unique_normalized_outcome_rows(
    rows: list[NormalizedOutcomeOffer],
) -> list[NormalizedOutcomeOffer]:
    unique: dict[tuple[str, str], NormalizedOutcomeOffer] = {}
    for row in rows:
        if not row.start_time:
            continue
        key = (row.match_id, row.bookmaker_id)
        existing = unique.get(key)
        unique[key] = (
            row if existing is None else _prefer_row_with_source_url(existing, row)
        )
    return list(unique.values())


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
            resolution.slot.start_time,
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
                start_time=resolution.slot.start_time,
                home_team_id=resolution.slot.home_team_id,
                away_team_id=resolution.slot.away_team_id,
                home_team=resolution.slot.home_team,
                away_team=resolution.slot.away_team,
                source_league_id=league_id,
                source_league_name=league_name,
                source_home_team=raw.home_team,
                source_away_team=raw.away_team,
                source_start_time=raw.start_time,
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
    league_cache: dict[tuple[str, str], tuple[str, str]] = {}

    raw_odds_sources_started_at = time.perf_counter()
    odds_sources = _raw_odds_sources(raw_odds, league_cache=league_cache)
    if stats is not None:
        stats.extract_raw_odds_sources_ms = _elapsed_ms(raw_odds_sources_started_at)
        stats.raw_odds_rows_scanned = len(raw_odds)
        stats.raw_odds_sources_emitted = len(odds_sources)
    odds_source_matcher = SourceMatcher(odds_sources)

    raw_outcome_sources_started_at = time.perf_counter()
    outcome_sources = _raw_outcome_sources(
        raw_outcome_offers,
        league_cache=league_cache,
    )
    if stats is not None:
        stats.extract_raw_outcome_sources_ms = _elapsed_ms(
            raw_outcome_sources_started_at
        )
        stats.raw_outcome_offer_rows_scanned = len(raw_outcome_offers)
        stats.raw_outcome_sources_emitted = len(outcome_sources)
    outcome_source_matcher = SourceMatcher(outcome_sources)

    normalized_odds_started_at = time.perf_counter()
    unique_normalized_odds = _unique_normalized_odds_rows(normalized_odds)
    if stats is not None:
        stats.normalized_odds_rows_scanned = len(normalized_odds)
        stats.normalized_odds_candidates_emitted = len(unique_normalized_odds)
    for row in unique_normalized_odds:
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
        source = _best_source(odds_source_matcher, provisional, stats=stats)
        _merge_candidate(
            candidates,
            _normalized_odds_candidate(row, source, league_cache=league_cache),
        )
    if stats is not None:
        stats.extract_normalized_odds_candidates_ms = _elapsed_ms(
            normalized_odds_started_at
        )

    normalized_outcome_started_at = time.perf_counter()
    unique_normalized_outcomes = _unique_normalized_outcome_rows(
        normalized_outcome_offers
    )
    if stats is not None:
        stats.normalized_outcome_offer_rows_scanned = len(normalized_outcome_offers)
        stats.normalized_outcome_candidates_emitted = len(unique_normalized_outcomes)
    for row in unique_normalized_outcomes:
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
        source = _best_source(outcome_source_matcher, provisional, stats=stats)
        _merge_candidate(
            candidates,
            _normalized_outcome_candidate(row, source, league_cache=league_cache),
        )
    if stats is not None:
        stats.extract_normalized_outcome_candidates_ms = _elapsed_ms(
            normalized_outcome_started_at
        )

    stored_outcome_match_bookmakers = {
        (offer.match_id, offer.bookmaker_id) for offer in normalized_outcome_offers
    }
    if stats is not None:
        stats.stored_outcome_match_bookmaker_count = len(stored_outcome_match_bookmakers)
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
        stats.football_raw_candidate_count = len(football_candidates)
    for candidate in football_candidates:
        _merge_candidate(candidates, candidate)

    if stats is not None:
        stats.finalize_source_match_summary()

    return sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate.sport,
            candidate.start_time,
            candidate.match_id,
            candidate.bookmaker_id,
        ),
    )
