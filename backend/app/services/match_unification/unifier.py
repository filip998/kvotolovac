from __future__ import annotations

import asyncio
import logging
import time

from ...models.schemas import (
    MatchUnificationBenchmarkOut,
    MatchUnificationSourceMatchSlotBenchmarkOut,
)
from . import resolution
from .store import MatchUnificationStore, OddsStoreMatchUnificationAdapter
from .types import (
    MatchUnificationInputError,
    MatchUnificationResult,
    MatchUnificationRows,
    MatchUnificationStatus,
    MatchUnificationWarning,
    PersistedScrapeSnapshot,
)

logger = logging.getLogger(__name__)


class MatchUnification:
    """Deep Module for turning a persisted scrape snapshot into a resolved event graph."""

    def __init__(self, *, store: MatchUnificationStore) -> None:
        self._store = store

    @classmethod
    def for_odds_store(cls, store=None) -> "MatchUnification":
        adapter = (
            OddsStoreMatchUnificationAdapter(store)
            if store is not None
            else OddsStoreMatchUnificationAdapter()
        )
        return cls(store=adapter)

    async def unify_after_snapshot(
        self,
        *,
        snapshot: PersistedScrapeSnapshot,
        rows: MatchUnificationRows,
    ) -> MatchUnificationResult:
        self._validate(snapshot=snapshot, rows=rows)
        try:
            return await self._unify(snapshot=snapshot, rows=rows)
        except asyncio.CancelledError:
            raise
        except MatchUnificationInputError:
            raise
        except Exception as exc:
            return self._fallback(snapshot=snapshot, exc=exc)

    def _validate(
        self,
        *,
        snapshot: PersistedScrapeSnapshot,
        rows: MatchUnificationRows,
    ) -> None:
        if not snapshot.id:
            raise MatchUnificationInputError("snapshot.id is required")
        if not snapshot.scraped_at:
            raise MatchUnificationInputError("snapshot.scraped_at is required")
        for row in [*rows.normalized_odds, *rows.normalized_outcome_offers]:
            if row.start_time is not None and not isinstance(row.start_time, str):
                raise MatchUnificationInputError(
                    "normalized start_time values must be strings"
                )

    async def _unify(
        self,
        *,
        snapshot: PersistedScrapeSnapshot,
        rows: MatchUnificationRows,
    ) -> MatchUnificationResult:
        extraction_stats = resolution._EventCandidateExtractionStats()
        extraction_started_at = time.perf_counter()
        football_event_resolutions = resolution._build_football_event_resolutions(
            list(rows.raw_outcome_offers)
        )
        candidates = resolution.extract_event_candidates(
            raw_odds=list(rows.raw_odds),
            raw_outcome_offers=list(rows.raw_outcome_offers),
            normalized_odds=list(rows.normalized_odds),
            normalized_outcome_offers=list(rows.normalized_outcome_offers),
            football_event_resolutions=football_event_resolutions,
            stats=extraction_stats,
        )
        extract_event_candidates_ms = resolution._elapsed_ms(extraction_started_at)

        group_stats = resolution._EventGroupBuildStats()
        grouping_started_at = time.perf_counter()
        resolutions, review_cases = resolution.build_event_resolution_groups(
            candidates,
            stats=group_stats,
        )
        build_event_resolution_groups_ms = resolution._elapsed_ms(grouping_started_at)
        coverage = resolution._event_coverage_benchmark(
            normalized_odds=list(rows.normalized_odds),
            normalized_outcome_offers=list(rows.normalized_outcome_offers),
            resolutions=resolutions,
            review_cases=review_cases,
        )
        split_diagnostics = resolution._event_split_diagnostics_benchmark(resolutions)

        persistence_started_at = time.perf_counter()
        persisted = await resolution.persist_event_resolution_groups(
            resolutions,
            review_cases,
            snapshot_id=snapshot.id,
            store=self._store,
        )
        persist_event_resolution_groups_ms = resolution._elapsed_ms(
            persistence_started_at
        )
        source_match_slot_rows = [
            MatchUnificationSourceMatchSlotBenchmarkOut(
                bookmaker_id=bookmaker_id,
                sport=sport,
                start_time=start_time,
                lookup_count=lookup_count,
                source_count=extraction_stats.source_match_slot_source_counts[
                    (bookmaker_id, sport, start_time)
                ],
                average_sources_per_lookup=round(
                    extraction_stats.source_match_slot_source_counts[
                        (bookmaker_id, sport, start_time)
                    ]
                    / lookup_count,
                    4,
                )
                if lookup_count
                else 0.0,
            )
            for (
                bookmaker_id,
                sport,
                start_time,
            ), lookup_count in extraction_stats.source_match_slot_lookup_counts.items()
        ]
        source_match_slot_rows = sorted(
            source_match_slot_rows,
            key=lambda row: (
                row.source_count,
                row.lookup_count,
                row.bookmaker_id,
                row.sport,
                row.start_time,
            ),
            reverse=True,
        )
        benchmark = MatchUnificationBenchmarkOut(
            state="unified",
            mode="resolved_event_graph",
            extract_event_candidates_ms=extract_event_candidates_ms,
            extract_raw_odds_sources_ms=extraction_stats.extract_raw_odds_sources_ms,
            extract_raw_outcome_sources_ms=(
                extraction_stats.extract_raw_outcome_sources_ms
            ),
            extract_normalized_odds_candidates_ms=(
                extraction_stats.extract_normalized_odds_candidates_ms
            ),
            extract_normalized_outcome_candidates_ms=(
                extraction_stats.extract_normalized_outcome_candidates_ms
            ),
            extract_source_match_ms=extraction_stats.source_match_ms,
            football_raw_resolution_candidates_ms=(
                extraction_stats.football_raw_resolution_candidates_ms
            ),
            reused_football_event_resolution_count=(
                extraction_stats.reused_football_event_resolution_count
            ),
            build_event_resolution_groups_ms=build_event_resolution_groups_ms,
            persist_event_resolution_groups_ms=persist_event_resolution_groups_ms,
            raw_odds_rows_scanned=extraction_stats.raw_odds_rows_scanned,
            raw_odds_sources_emitted=extraction_stats.raw_odds_sources_emitted,
            raw_outcome_offer_rows_scanned=(
                extraction_stats.raw_outcome_offer_rows_scanned
            ),
            raw_outcome_sources_emitted=extraction_stats.raw_outcome_sources_emitted,
            normalized_odds_rows_scanned=extraction_stats.normalized_odds_rows_scanned,
            normalized_odds_candidates_emitted=(
                extraction_stats.normalized_odds_candidates_emitted
            ),
            normalized_outcome_offer_rows_scanned=(
                extraction_stats.normalized_outcome_offer_rows_scanned
            ),
            normalized_outcome_candidates_emitted=(
                extraction_stats.normalized_outcome_candidates_emitted
            ),
            stored_outcome_match_bookmaker_count=(
                extraction_stats.stored_outcome_match_bookmaker_count
            ),
            source_match_lookup_count=extraction_stats.source_match_lookup_count,
            source_match_source_count=extraction_stats.source_match_source_count,
            source_match_scored_source_count=(
                extraction_stats.source_match_scored_source_count
            ),
            source_match_index_candidate_count=(
                extraction_stats.source_match_index_candidate_count
            ),
            source_match_exact_url_hit_count=(
                extraction_stats.source_match_exact_url_hit_count
            ),
            source_match_listed_pair_hit_count=(
                extraction_stats.source_match_listed_pair_hit_count
            ),
            source_match_unordered_pair_hit_count=(
                extraction_stats.source_match_unordered_pair_hit_count
            ),
            source_match_fallback_scan_count=(
                extraction_stats.source_match_fallback_scan_count
            ),
            source_match_max_sources_per_lookup=(
                extraction_stats.source_match_max_sources_per_lookup
            ),
            source_match_truncated_slot_count=max(0, len(source_match_slot_rows) - 20),
            football_raw_candidate_count=extraction_stats.football_raw_candidate_count,
            candidate_count=len(candidates),
            exact_group_count=group_stats.exact_group_count,
            pair_check_count=group_stats.pair_check_count,
            fuzzy_score_count=group_stats.fuzzy_score_count,
            accepted_fuzzy_pair_count=group_stats.accepted_fuzzy_pair_count,
            review_case_count=group_stats.review_case_count,
            persisted_resolved_event_count=persisted.resolved_events,
            persisted_member_count=persisted.resolved_event_members,
            persisted_review_case_count=persisted.review_cases,
            top_source_match_slots=source_match_slot_rows[:20],
        )
        status = MatchUnificationStatus(
            snapshot_id=snapshot.id,
            state="unified",
            mode="resolved_event_graph",
        )
        logger.info(
            "Unified %d source-event candidates into %d events (%d members, %d review cases)",
            len(candidates),
            persisted.resolved_events,
            persisted.resolved_event_members,
            persisted.review_cases,
        )
        return MatchUnificationResult(
            snapshot_id=snapshot.id,
            mode="resolved_event_graph",
            candidates=len(candidates),
            resolved_events=persisted.resolved_events,
            resolved_event_members=persisted.resolved_event_members,
            review_cases=persisted.review_cases,
            benchmark=benchmark,
            coverage=coverage,
            split_diagnostics=split_diagnostics,
            status=status,
        )

    def _fallback(
        self,
        *,
        snapshot: PersistedScrapeSnapshot,
        exc: Exception,
    ) -> MatchUnificationResult:
        reason = f"{type(exc).__name__}: {exc}"
        logger.exception("Match Unification failed; continuing with match_id-only analysis")
        warning = MatchUnificationWarning(
            code="match_unification_failed",
            detail=reason,
        )
        benchmark = MatchUnificationBenchmarkOut(
            state="match_id_only",
            mode="match_id_only",
            warnings=[warning.code],
            fallback_reason=reason,
        )
        status = MatchUnificationStatus(
            snapshot_id=snapshot.id,
            state="match_id_only",
            mode="match_id_only",
            warnings=(warning,),
            fallback_reason=reason,
        )
        return MatchUnificationResult(
            snapshot_id=snapshot.id,
            mode="match_id_only",
            benchmark=benchmark,
            warnings=(warning,),
            status=status,
        )
