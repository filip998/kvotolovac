"""In-memory + on-disk benchmark recorder for scrape cycles.

Each completed scrape cycle produces:
- A per-cycle JSON snapshot at ``{benchmark_dir}/cycle-YYYYMMDD-HHMMSS.json``
- A single appended NDJSON line at ``{benchmark_dir}/cycles.ndjson`` for offline analysis

The latest cycle is also held in memory so the API can return it without re-reading files.
The most recent in-memory snapshot survives until the next cycle replaces it; nothing is
queryable historically through the API by design (use the NDJSON for that).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterator, Optional
from urllib.parse import urlsplit

from ..config import settings
from ..models.schemas import (
    AutoResolutionRerunBenchmarkOut,
    BenchmarkEventCoverageOut,
    BenchmarkRuntimeMetadataOut,
    BenchmarkSplitDiagnosticsOut,
    CycleBenchmarkOut,
    HttpTimingBenchmarkOut,
    MatchUnificationBenchmarkOut,
    OpportunityAnalysisBenchmarkOut,
    OutcomeNormalizationBenchmarkOut,
    PersistenceBenchmarkOut,
    ScrapeRuntimeSettings,
    ScraperBenchmarkOut,
    ScraperRequestBenchmarkOut,
    SportBenchmarkOut,
)
from .rate_limit_policy import RateLimitPolicy

logger = logging.getLogger(__name__)


def _endpoint_path(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    return parsed.path or "/"


class _BookmakerAcc:
    __slots__ = (
        "duration_ms",
        "raw_items",
        "leagues_attempted",
        "leagues_failed",
    )

    def __init__(self) -> None:
        self.duration_ms: int = 0
        self.raw_items: int = 0
        self.leagues_attempted: int = 0
        self.leagues_failed: int = 0


@dataclass(frozen=True)
class _HttpRequestContext:
    bookmaker_id: str
    lane: str | None
    sport: str | None
    league_id: str | None


class _HttpTimingAcc:
    __slots__ = (
        "logical_requests",
        "attempts",
        "retries",
        "errors",
        "total_elapsed_ms",
        "total_rate_limit_wait_ms",
        "total_network_ms",
        "min_latency_ms",
        "max_latency_ms",
        "status_classes",
    )

    def __init__(self) -> None:
        self.logical_requests = 0
        self.attempts = 0
        self.retries = 0
        self.errors = 0
        self.total_elapsed_ms = 0
        self.total_rate_limit_wait_ms = 0
        self.total_network_ms = 0
        self.min_latency_ms: int | None = None
        self.max_latency_ms: int | None = None
        self.status_classes: dict[str, int] = defaultdict(int)

    def record(
        self,
        *,
        elapsed_ms: int,
        attempts: int,
        rate_limit_wait_ms: int,
        network_ms: int,
        status_codes: list[int],
        error: bool,
    ) -> None:
        self.logical_requests += 1
        self.attempts += int(attempts)
        self.retries += max(0, int(attempts) - 1)
        self.errors += 1 if error else 0
        self.total_elapsed_ms += int(elapsed_ms)
        self.total_rate_limit_wait_ms += int(rate_limit_wait_ms)
        self.total_network_ms += int(network_ms)
        if self.min_latency_ms is None or elapsed_ms < self.min_latency_ms:
            self.min_latency_ms = int(elapsed_ms)
        if self.max_latency_ms is None or elapsed_ms > self.max_latency_ms:
            self.max_latency_ms = int(elapsed_ms)
        for status_code in status_codes:
            status_class = f"{int(status_code) // 100}xx"
            self.status_classes[status_class] += 1

    def to_model(self) -> HttpTimingBenchmarkOut:
        avg_latency = (
            self.total_elapsed_ms / self.logical_requests
            if self.logical_requests
            else 0.0
        )
        return HttpTimingBenchmarkOut(
            logical_requests=self.logical_requests,
            attempts=self.attempts,
            retries=self.retries,
            errors=self.errors,
            total_elapsed_ms=self.total_elapsed_ms,
            total_rate_limit_wait_ms=self.total_rate_limit_wait_ms,
            total_network_ms=self.total_network_ms,
            min_latency_ms=self.min_latency_ms,
            avg_latency_ms=round(avg_latency, 2),
            max_latency_ms=self.max_latency_ms,
            status_classes=dict(sorted(self.status_classes.items())),
        )


_HTTP_REQUEST_CONTEXT: ContextVar[_HttpRequestContext | None] = ContextVar(
    "scraper_benchmark_http_request_context", default=None
)


def _runtime_metadata(
    runtime_settings: ScrapeRuntimeSettings,
) -> BenchmarkRuntimeMetadataOut:
    proxy_count = len(settings.proxy_url_list)
    rate_limit_policy = RateLimitPolicy.from_settings()
    return BenchmarkRuntimeMetadataOut(
        scraper_mode=settings.scraper_mode,
        enabled_bookmakers=list(runtime_settings.enabled_bookmakers),
        enabled_sports=list(runtime_settings.enabled_sports),
        scrape_market_scope=runtime_settings.scrape_market_scope,
        analysis_markets=list(runtime_settings.analysis_markets),
        scrape_lookahead_hours=runtime_settings.scrape_lookahead_hours,
        rate_limit_per_second=runtime_settings.rate_limit_per_second,
        meridian_rate_limit_per_second=runtime_settings.meridian_rate_limit_per_second,
        bookmaker_rate_limits=rate_limit_policy.metadata_bookmaker_rate_limits(),
        scrape_type_rate_limits=rate_limit_policy.metadata_scrape_type_rate_limits(),
        detail_modes={
            "betole": runtime_settings.betole_detail_mode,
            "soccerbet": runtime_settings.soccerbet_detail_mode,
            "merkurxtip": runtime_settings.merkurxtip_detail_mode,
            "pinnbet": runtime_settings.pinnbet_detail_mode,
            "starbet": runtime_settings.starbet_detail_mode,
        },
        proxies_configured=proxy_count > 0,
        proxy_count=proxy_count,
        max_middle_opportunities_per_market=(
            runtime_settings.max_middle_opportunities_per_market
        ),
        enable_fitted_middles=runtime_settings.enable_fitted_middles,
        min_fitted_middle_ev_percent=runtime_settings.min_fitted_middle_ev_percent,
    )


def _merge_outcome_metrics(
    current: OutcomeNormalizationBenchmarkOut,
    update: OutcomeNormalizationBenchmarkOut,
) -> OutcomeNormalizationBenchmarkOut:
    run_details = [
        row.model_copy(update={"run_index": index})
        for index, row in enumerate(
            [*current.run_details, *update.run_details],
            start=1,
        )
    ]
    return OutcomeNormalizationBenchmarkOut(
        runs=current.runs + update.runs,
        raw_outcome_offer_count=update.raw_outcome_offer_count,
        normalized_outcome_offer_count=update.normalized_outcome_offer_count,
        unresolved_outcome_offer_count=update.unresolved_outcome_offer_count,
        football_unique_event_count=update.football_unique_event_count,
        football_event_pair_candidate_count=(
            update.football_event_pair_candidate_count
        ),
        football_event_fuzzy_score_count=update.football_event_fuzzy_score_count,
        football_event_canonical_conflict_skip_count=(
            current.football_event_canonical_conflict_skip_count
            + update.football_event_canonical_conflict_skip_count
        ),
        football_event_canonical_conflict_fuzzy_score_avoided_count=(
            current.football_event_canonical_conflict_fuzzy_score_avoided_count
            + update.football_event_canonical_conflict_fuzzy_score_avoided_count
        ),
        auto_created_football_team_count=(
            current.auto_created_football_team_count
            + update.auto_created_football_team_count
        ),
        football_team_review_case_count=update.football_team_review_case_count,
        football_team_review_alias_miss_count=(
            update.football_team_review_alias_miss_count
        ),
        football_team_review_unknown_count=update.football_team_review_unknown_count,
        football_team_review_same_slot_alias_miss_count=(
            update.football_team_review_same_slot_alias_miss_count
        ),
        football_team_review_global_alias_miss_count=(
            update.football_team_review_global_alias_miss_count
        ),
        auto_create_football_teams_ms=(
            current.auto_create_football_teams_ms
            + update.auto_create_football_teams_ms
        ),
        football_event_resolution_ms=(
            current.football_event_resolution_ms
            + update.football_event_resolution_ms
        ),
        football_event_pair_ranking_ms=(
            current.football_event_pair_ranking_ms
            + update.football_event_pair_ranking_ms
        ),
        football_event_slot_lookup_ms=(
            current.football_event_slot_lookup_ms
            + update.football_event_slot_lookup_ms
        ),
        football_event_slot_mutation_ms=(
            current.football_event_slot_mutation_ms
            + update.football_event_slot_mutation_ms
        ),
        row_normalization_ms=(
            current.row_normalization_ms + update.row_normalization_ms
        ),
        team_review_proxy_rows=update.team_review_proxy_rows,
        team_review_proxy_ms=(
            current.team_review_proxy_ms + update.team_review_proxy_ms
        ),
        team_review_proxy_slot_resolution_ms=(
            current.team_review_proxy_slot_resolution_ms
            + update.team_review_proxy_slot_resolution_ms
        ),
        team_review_proxy_case_build_ms=(
            current.team_review_proxy_case_build_ms
            + update.team_review_proxy_case_build_ms
        ),
        team_review_proxy_resolve_league_ms=(
            current.team_review_proxy_resolve_league_ms
            + update.team_review_proxy_resolve_league_ms
        ),
        team_review_proxy_resolve_team_ms=(
            current.team_review_proxy_resolve_team_ms
            + update.team_review_proxy_resolve_team_ms
        ),
        team_review_proxy_slot_candidate_ms=(
            current.team_review_proxy_slot_candidate_ms
            + update.team_review_proxy_slot_candidate_ms
        ),
        team_review_proxy_global_candidate_ms=(
            current.team_review_proxy_global_candidate_ms
            + update.team_review_proxy_global_candidate_ms
        ),
        team_review_proxy_duplicate_suppression_ms=(
            current.team_review_proxy_duplicate_suppression_ms
            + update.team_review_proxy_duplicate_suppression_ms
        ),
        team_review_proxy_resolve_team_cache_hits=(
            current.team_review_proxy_resolve_team_cache_hits
            + update.team_review_proxy_resolve_team_cache_hits
        ),
        team_review_proxy_slot_candidate_search_count=(
            current.team_review_proxy_slot_candidate_search_count
            + update.team_review_proxy_slot_candidate_search_count
        ),
        team_review_proxy_slot_candidate_cache_hits=(
            current.team_review_proxy_slot_candidate_cache_hits
            + update.team_review_proxy_slot_candidate_cache_hits
        ),
        team_review_proxy_global_candidate_search_count=(
            current.team_review_proxy_global_candidate_search_count
            + update.team_review_proxy_global_candidate_search_count
        ),
        team_review_proxy_global_candidate_cache_hits=(
            current.team_review_proxy_global_candidate_cache_hits
            + update.team_review_proxy_global_candidate_cache_hits
        ),
        team_review_proxy_duplicate_suppression_count=(
            current.team_review_proxy_duplicate_suppression_count
            + update.team_review_proxy_duplicate_suppression_count
        ),
        row_iteration_ms=current.row_iteration_ms + update.row_iteration_ms,
        missing_start_time_count=update.missing_start_time_count,
        event_resolution_offer_count=update.event_resolution_offer_count,
        direct_resolution_attempt_count=update.direct_resolution_attempt_count,
        direct_resolution_success_count=update.direct_resolution_success_count,
        skipped_unresolved_row_count=update.skipped_unresolved_row_count,
        unsupported_reversed_offer_count=update.unsupported_reversed_offer_count,
        league_resolution_ms=(
            current.league_resolution_ms + update.league_resolution_ms
        ),
        event_resolution_offer_build_ms=(
            current.event_resolution_offer_build_ms
            + update.event_resolution_offer_build_ms
        ),
        direct_team_resolution_ms=(
            current.direct_team_resolution_ms + update.direct_team_resolution_ms
        ),
        unresolved_context_ms=(
            current.unresolved_context_ms + update.unresolved_context_ms
        ),
        direct_offer_build_ms=(
            current.direct_offer_build_ms + update.direct_offer_build_ms
        ),
        football_event_time_slot_count=update.football_event_time_slot_count,
        football_event_max_events_per_slot=(
            update.football_event_max_events_per_slot
        ),
        run_details=run_details,
        bookmakers=update.bookmakers,
        top_football_event_buckets=update.top_football_event_buckets,
    )


def _cycle_sport_totals(scrapers: list[ScraperBenchmarkOut]) -> list[SportBenchmarkOut]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for scraper in scrapers:
        for sport_row in scraper.sports:
            bucket = totals[sport_row.sport]
            bucket["duration_ms"] += sport_row.duration_ms
            bucket["raw_items"] += sport_row.raw_items
            bucket["matches_after_normalization"] += sport_row.matches_after_normalization
            bucket["odds_count"] += sport_row.odds_count
            bucket["leagues_attempted"] += sport_row.leagues_attempted
            bucket["leagues_failed"] += sport_row.leagues_failed
            bucket["matched_events"] += sport_row.matched_events
            bucket["unmatched_events"] += sport_row.unmatched_events
            bucket["ungrouped_events"] += sport_row.ungrouped_events
            bucket["in_review_events"] += sport_row.in_review_events
            bucket["not_matched_events"] += sport_row.not_matched_events

    rows: list[SportBenchmarkOut] = []
    for sport, bucket in sorted(totals.items()):
        attempted = bucket["leagues_attempted"]
        normalized_events = bucket["matches_after_normalization"]
        rows.append(
            SportBenchmarkOut(
                sport=sport,
                duration_ms=bucket["duration_ms"],
                raw_items=bucket["raw_items"],
                matches_after_normalization=normalized_events,
                odds_count=bucket["odds_count"],
                leagues_attempted=attempted,
                leagues_failed=bucket["leagues_failed"],
                failure_rate=(
                    round(bucket["leagues_failed"] / attempted, 4)
                    if attempted
                    else 0.0
                ),
                matched_events=bucket["matched_events"],
                unmatched_events=bucket["unmatched_events"],
                ungrouped_events=bucket["ungrouped_events"],
                in_review_events=bucket["in_review_events"],
                not_matched_events=bucket["not_matched_events"],
                match_rate=(
                    round(bucket["matched_events"] / normalized_events, 4)
                    if normalized_events
                    else 0.0
                ),
            )
        )
    return rows


class CycleBenchmarkRecorder:
    """Accumulates per-scraper stats for one in-flight cycle, then publishes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest: CycleBenchmarkOut | None = None
        self._reset()

    def _reset(self) -> None:
        self._cycle_started_at: Optional[str] = None
        self._metadata: BenchmarkRuntimeMetadataOut | None = None
        self._scrape_duration_ms: int = 0
        self._cycle_duration_ms: int = 0
        self._buckets: dict[str, _BookmakerAcc] = defaultdict(_BookmakerAcc)
        self._sport_buckets: dict[tuple[str, str], _BookmakerAcc] = defaultdict(
            _BookmakerAcc
        )
        self._http_by_bookmaker: dict[str, _HttpTimingAcc] = defaultdict(_HttpTimingAcc)
        self._http_by_request: dict[
            tuple[str, str | None, str | None, str | None, str | None, str],
            _HttpTimingAcc,
        ] = defaultdict(_HttpTimingAcc)
        self._phase_durations_ms: dict[str, int] = {}
        self._outcome_normalization = OutcomeNormalizationBenchmarkOut()
        self._match_unification = MatchUnificationBenchmarkOut()
        self._auto_resolution_rerun = AutoResolutionRerunBenchmarkOut()
        self._persistence = PersistenceBenchmarkOut()
        self._opportunity_analysis = OpportunityAnalysisBenchmarkOut()
        self._event_split_diagnostics = BenchmarkSplitDiagnosticsOut()

    # ---- accumulation ---------------------------------------------------
    def begin_cycle(
        self,
        cycle_started_at: str,
        *,
        runtime_settings: ScrapeRuntimeSettings | None = None,
    ) -> None:
        with self._lock:
            self._reset()
            self._cycle_started_at = cycle_started_at
            self._metadata = (
                _runtime_metadata(runtime_settings)
                if runtime_settings is not None
                else None
            )

    def record_scrape_task(
        self,
        *,
        bookmaker_id: str,
        duration_ms: int,
        raw_items: int,
        failed: bool,
        sport: str | None = None,
        lane: str | None = None,
    ) -> None:
        with self._lock:
            acc = self._buckets[bookmaker_id]
            acc.duration_ms += int(duration_ms)
            acc.raw_items += int(raw_items)
            acc.leagues_attempted += 1
            if failed:
                acc.leagues_failed += 1
            if sport:
                sport_acc = self._sport_buckets[(bookmaker_id, sport)]
                sport_acc.duration_ms += int(duration_ms)
                sport_acc.raw_items += int(raw_items)
                sport_acc.leagues_attempted += 1
                if failed:
                    sport_acc.leagues_failed += 1

    def record_phase_durations(
        self, *, scrape_duration_ms: int, cycle_duration_ms: int
    ) -> None:
        with self._lock:
            self._scrape_duration_ms = int(scrape_duration_ms)
            self._cycle_duration_ms = int(cycle_duration_ms)

    def record_phase_duration(self, phase_name: str, duration_ms: int) -> None:
        with self._lock:
            self._phase_durations_ms[phase_name] = (
                self._phase_durations_ms.get(phase_name, 0) + int(duration_ms)
            )

    def record_outcome_normalization(
        self, metrics: OutcomeNormalizationBenchmarkOut
    ) -> None:
        with self._lock:
            self._outcome_normalization = _merge_outcome_metrics(
                self._outcome_normalization,
                metrics,
            )

    def record_match_unification(self, metrics: MatchUnificationBenchmarkOut) -> None:
        with self._lock:
            self._match_unification = metrics

    def record_auto_resolution_rerun(
        self, metrics: AutoResolutionRerunBenchmarkOut
    ) -> None:
        with self._lock:
            self._auto_resolution_rerun = metrics

    def record_persistence(self, metrics: PersistenceBenchmarkOut) -> None:
        with self._lock:
            self._persistence = metrics

    def record_opportunity_analysis(
        self, metrics: OpportunityAnalysisBenchmarkOut
    ) -> None:
        with self._lock:
            self._opportunity_analysis = metrics

    def record_event_split_diagnostics(
        self,
        diagnostics: BenchmarkSplitDiagnosticsOut,
    ) -> None:
        with self._lock:
            self._event_split_diagnostics = diagnostics

    @contextmanager
    def scrape_request_context(
        self,
        *,
        bookmaker_id: str,
        lane: str | None,
        sport: str | None,
        league_id: str | None,
    ) -> Iterator[None]:
        token = _HTTP_REQUEST_CONTEXT.set(
            _HttpRequestContext(
                bookmaker_id=bookmaker_id,
                lane=lane,
                sport=sport,
                league_id=league_id,
            )
        )
        try:
            yield
        finally:
            _HTTP_REQUEST_CONTEXT.reset(token)

    def record_http_request(
        self,
        *,
        method: str,
        elapsed_ms: int,
        attempts: int,
        rate_limit_wait_ms: int,
        network_ms: int,
        status_codes: list[int],
        error: bool,
        url: str | None = None,
    ) -> None:
        context = _HTTP_REQUEST_CONTEXT.get()
        if context is None:
            return
        normalized_method = method.upper()
        endpoint = _endpoint_path(url)
        with self._lock:
            self._http_by_bookmaker[context.bookmaker_id].record(
                elapsed_ms=elapsed_ms,
                attempts=attempts,
                rate_limit_wait_ms=rate_limit_wait_ms,
                network_ms=network_ms,
                status_codes=status_codes,
                error=error,
            )
            request_key = (
                context.bookmaker_id,
                context.lane,
                context.sport,
                context.league_id,
                endpoint,
                normalized_method,
            )
            self._http_by_request[request_key].record(
                elapsed_ms=elapsed_ms,
                attempts=attempts,
                rate_limit_wait_ms=rate_limit_wait_ms,
                network_ms=network_ms,
                status_codes=status_codes,
                error=error,
            )

    # ---- publish --------------------------------------------------------
    def publish(
        self,
        *,
        matches_per_bookmaker: dict[str, int],
        odds_per_bookmaker: dict[str, int],
        total_unique_matches: int,
        matches_per_bookmaker_sport: dict[tuple[str, str], int] | None = None,
        odds_per_bookmaker_sport: dict[tuple[str, str], int] | None = None,
        event_coverage: list[BenchmarkEventCoverageOut]
        | tuple[BenchmarkEventCoverageOut, ...]
        | None = None,
    ) -> CycleBenchmarkOut:
        """Build the snapshot, replace the in-memory latest, and write files.

        ``matches_per_bookmaker`` counts matches each bookmaker contributed (the same
        match covered by N bookmakers appears in N entries — that's the whole point of
        the per-scraper view). ``total_unique_matches`` is the globally deduped count
        and matches ``len(seen_matches)`` from the scheduler cycle result.
        """
        matches_per_bookmaker_sport = matches_per_bookmaker_sport or {}
        odds_per_bookmaker_sport = odds_per_bookmaker_sport or {}
        coverage_rows = list(event_coverage or [])
        coverage_by_key = {
            (row.bookmaker_id, row.sport): row for row in coverage_rows
        }
        with self._lock:
            cycle_finished_at = datetime.utcnow().isoformat()
            scrapers: list[ScraperBenchmarkOut] = []
            sport_keys = (
                set(self._sport_buckets)
                | set(matches_per_bookmaker_sport)
                | set(odds_per_bookmaker_sport)
                | set(coverage_by_key)
            )
            all_keys = (
                set(self._buckets)
                | set(matches_per_bookmaker)
                | set(odds_per_bookmaker)
                | set(self._http_by_bookmaker)
                | {bookmaker_id for bookmaker_id, _sport in sport_keys}
            )
            for bm in sorted(all_keys):
                acc = self._buckets.get(bm) or _BookmakerAcc()
                request_rows = [
                    ScraperRequestBenchmarkOut(
                        lane=lane,
                        sport=sport,
                        league_id=league_id,
                        endpoint=endpoint,
                        method=method,
                        **http_acc.to_model().model_dump(),
                    )
                    for (
                        request_bookmaker_id,
                        lane,
                        sport,
                        league_id,
                        endpoint,
                        method,
                    ),
                    http_acc in sorted(
                        self._http_by_request.items(),
                        key=lambda item: (
                            item[0][0],
                            item[0][1] or "",
                            item[0][2] or "",
                            item[0][3] or "",
                            item[0][4] or "",
                            item[0][5],
                        ),
                    )
                    if request_bookmaker_id == bm
                ]
                attempted = acc.leagues_attempted
                failure_rate = (
                    (acc.leagues_failed / attempted) if attempted > 0 else 0.0
                )
                sport_rows: list[SportBenchmarkOut] = []
                for _, sport in sorted(key for key in sport_keys if key[0] == bm):
                    sport_acc = self._sport_buckets.get((bm, sport)) or _BookmakerAcc()
                    sport_attempted = sport_acc.leagues_attempted
                    sport_failure_rate = (
                        (sport_acc.leagues_failed / sport_attempted)
                        if sport_attempted > 0
                        else 0.0
                    )
                    coverage = coverage_by_key.get((bm, sport))
                    sport_rows.append(
                        SportBenchmarkOut(
                            sport=sport,
                            duration_ms=sport_acc.duration_ms,
                            raw_items=sport_acc.raw_items,
                            matches_after_normalization=(
                                coverage.normalized_events
                                if coverage is not None
                                else int(matches_per_bookmaker_sport.get((bm, sport), 0))
                            ),
                            odds_count=int(odds_per_bookmaker_sport.get((bm, sport), 0)),
                            leagues_attempted=sport_attempted,
                            leagues_failed=sport_acc.leagues_failed,
                            failure_rate=round(sport_failure_rate, 4),
                            matched_events=(
                                coverage.matched_events if coverage is not None else 0
                            ),
                            unmatched_events=(
                                coverage.unmatched_events if coverage is not None else 0
                            ),
                            ungrouped_events=(
                                coverage.ungrouped_events if coverage is not None else 0
                            ),
                            in_review_events=(
                                coverage.in_review_events if coverage is not None else 0
                            ),
                            not_matched_events=(
                                coverage.not_matched_events if coverage is not None else 0
                            ),
                            match_rate=(
                                coverage.match_rate if coverage is not None else 0.0
                            ),
                        )
                    )
                scrapers.append(
                    ScraperBenchmarkOut(
                        bookmaker_id=bm,
                        duration_ms=acc.duration_ms,
                        raw_items=acc.raw_items,
                        matches_after_normalization=int(
                            matches_per_bookmaker.get(bm, 0)
                        ),
                        odds_count=int(odds_per_bookmaker.get(bm, 0)),
                        leagues_attempted=attempted,
                        leagues_failed=acc.leagues_failed,
                        failure_rate=round(failure_rate, 4),
                        http=self._http_by_bookmaker[bm].to_model(),
                        requests=request_rows,
                        sports=sport_rows,
                    )
                )

            cycle_sports = _cycle_sport_totals(scrapers)
            snapshot = CycleBenchmarkOut(
                cycle_started_at=self._cycle_started_at,
                cycle_finished_at=cycle_finished_at,
                scrape_duration_ms=self._scrape_duration_ms,
                cycle_duration_ms=self._cycle_duration_ms,
                total_raw_items=sum(s.raw_items for s in scrapers),
                total_matches=int(total_unique_matches),
                total_odds=sum(s.odds_count for s in scrapers),
                metadata=self._metadata,
                phase_durations_ms=dict(sorted(self._phase_durations_ms.items())),
                outcome_normalization=self._outcome_normalization,
                match_unification=self._match_unification,
                auto_resolution_rerun=self._auto_resolution_rerun,
                persistence=self._persistence,
                opportunity_analysis=self._opportunity_analysis,
                event_coverage=sorted(
                    coverage_rows,
                    key=lambda row: (row.sport, row.bookmaker_id),
                ),
                event_split_diagnostics=self._event_split_diagnostics,
                sports=cycle_sports,
                scrapers=scrapers,
            )
            self._latest = snapshot

        # Persist outside the lock — file IO shouldn't block recorders for the
        # next cycle, and we already snapshotted state into a Pydantic model.
        try:
            self._write_files(snapshot)
        except Exception:
            logger.exception("Failed to persist scraper benchmark snapshot")

        return snapshot

    def latest(self) -> CycleBenchmarkOut | None:
        with self._lock:
            return self._latest

    # ---- IO -------------------------------------------------------------
    def _write_files(self, snapshot: CycleBenchmarkOut) -> None:
        out_dir = Path(settings.benchmark_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Use UTC compact timestamp with microseconds so back-to-back manually
        # triggered cycles can't clobber each other's snapshot file.
        now = datetime.utcnow()
        ts = now.strftime("%Y%m%d-%H%M%S-%f")
        snapshot_path = out_dir / f"cycle-{ts}.json"

        payload = snapshot.model_dump()
        snapshot_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

        ndjson_path = out_dir / "cycles.ndjson"
        with ndjson_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True))
            f.write("\n")


recorder = CycleBenchmarkRecorder()
