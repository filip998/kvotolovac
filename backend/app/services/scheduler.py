from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ..config import settings
from ..models.schemas import RawOddsData, RawOutcomeOffer, TeamReviewDiagnostic
from ..scrapers.base import BaseScraper
from ..scrapers.registry import registry
from ..models.schemas import ScanProgressOut
from ..services.league_registry import league_country, league_display_name
from ..services.normalizer import (
    ANCHORED_AUTO_APPLY_THRESHOLD,
    log_unresolved_shared_platform_diagnostics,
    normalize_odds_with_diagnostics,
    resolve_team_name,
)
from ..services.canonical_analyzer import analyze_canonical_offers
from ..services.event_resolver import (
    CANONICAL_TEAM_AUTO_MERGE_THRESHOLD,
    SameTimeCanonicalMergeProposal as _SameTimeMergeProposal,
    SameTimeCanonicalSlot as _SameTimeSlot,
    _contextual_merge_source_ids,
    _is_unsafe_compound_subset_match,
    _normalize_merge_pairings,
    _same_time_slot_orientation,
    resolve_and_persist_events,
)
from ..services.outcome_normalizer import normalize_outcome_offers_with_diagnostics
from ..services.notifications import NotificationService, InAppNotificationProvider
from ..services.scrape_window import (
    configured_lookahead_hours,
    filter_raw_odds_by_lookahead,
)
from ..services.scraper_benchmarks import recorder as benchmark_recorder
from ..services.team_registry import (
    CircularAliasError,
    forget_team_alias,
    get_canonical_team,
    merge_canonical_teams,
    remember_team_alias,
    unmerge_canonical_team,
)
from ..services.text_normalizer import normalize_identity_text
from ..store import odds_store

logger = logging.getLogger(__name__)


AUTO_ALIAS_REVIEW_KIND = "auto_alias_suggestion"
AUTO_CANONICAL_MERGE_REVIEW_KIND = "auto_canonical_merge_suggestion"
SAME_TIME_MIN_TARGET_SUPPORT = 2


@dataclass(frozen=True)
class _CanonicalShadowResult:
    offers_analyzed: int = 0
    opportunities_found: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CanonicalAnalysisResult:
    offers: tuple = ()
    opportunities: tuple = ()


def _is_auto_alias_candidate(case) -> bool:
    return (
        case.review_kind in {"alias_suggestion", "candidate_search"}
        and case.reason_code == "candidate_team_match_same_start_time"
        and case.suggested_team_id is not None
        and case.suggested_team_name is not None
        and case.start_time is not None
        and case.matched_counterpart_team is not None
        and case.canonical_home_team is not None
        and case.canonical_away_team is not None
        and case.similarity_score is not None
        and case.similarity_score >= ANCHORED_AUTO_APPLY_THRESHOLD
    )


def _enabled_threshold_league_ids(scraper: BaseScraper, enabled_sports: set[str]) -> list[str]:
    league_ids: list[str] = []
    for sport, sport_leagues in scraper.get_supported_odds_leagues().items():
        if sport not in enabled_sports:
            continue
        league_ids.extend(sport_leagues)
    return list(dict.fromkeys(league_ids))


async def _load_current_canonical_analysis(match_ids: set[str]) -> _CanonicalAnalysisResult:
    canonical_offers = await odds_store.get_current_canonical_offers_for_matches(
        sorted(match_ids)
    )
    event_ids = sorted(
        {
            offer.market.event_id
            for offer in canonical_offers
            if offer.market.event_id
        }
    )
    event_primary_match_ids = (
        await odds_store.get_resolved_event_primary_match_ids(event_ids)
        if event_ids
        else {}
    )
    canonical_opportunities = analyze_canonical_offers(
        canonical_offers,
        event_primary_match_ids=event_primary_match_ids,
    )
    return _CanonicalAnalysisResult(
        offers=tuple(canonical_offers),
        opportunities=tuple(canonical_opportunities),
    )


def _candidate_merge_source_ids(case) -> set[int]:
    return _contextual_merge_source_ids(case)


class Scheduler:
    """Background task scheduler for periodic scraping."""

    def __init__(self, interval_minutes: int | None = None) -> None:
        self.interval_minutes = interval_minutes or settings.scrape_interval_minutes
        self._task: asyncio.Task | None = None
        self._cycle_task: asyncio.Task | None = None
        self._running = False
        self._cycle_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._scan_phase = "idle"
        self._scan_started_at: str | None = None
        self._scan_total_tasks = 0
        self._scan_completed_tasks = 0
        self._scan_failed_tasks = 0
        self._scan_active_tasks = 0
        self._notification_service = NotificationService(
            gap_threshold=settings.notification_gap_threshold
        )
        if settings.persist_inapp_notifications:
            self._notification_service.register_provider(InAppNotificationProvider())

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_cycle_in_progress(self) -> bool:
        return self._cycle_task is not None and not self._cycle_task.done()

    def progress_snapshot(self) -> ScanProgressOut:
        return ScanProgressOut(
            in_progress=self._scan_phase != "idle",
            phase=self._scan_phase,
            started_at=self._scan_started_at,
            total_tasks=self._scan_total_tasks,
            completed_tasks=self._scan_completed_tasks,
            failed_tasks=self._scan_failed_tasks,
            active_tasks=self._scan_active_tasks,
        )

    def _reset_progress(self) -> None:
        self._scan_phase = "idle"
        self._scan_started_at = None
        self._scan_total_tasks = 0
        self._scan_completed_tasks = 0
        self._scan_failed_tasks = 0
        self._scan_active_tasks = 0

    async def _same_time_canonical_merge_candidates(
        self,
        raw_rows: list[RawOddsData],
    ) -> tuple[list[TeamReviewDiagnostic], list[tuple[int, int]]]:
        slot_bookmakers: dict[
            tuple[str, str, tuple[int, int]],
            set[str],
        ] = defaultdict(set)
        slot_examples: dict[tuple[str, str, tuple[int, int]], _SameTimeSlot] = {}

        for raw in raw_rows:
            if raw.start_time is None:
                continue
            home_resolution = resolve_team_name(
                raw.home_team,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
            )
            away_resolution = resolve_team_name(
                raw.away_team,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
            )
            if (
                home_resolution.team_id is None
                or away_resolution.team_id is None
                or home_resolution.team_id == away_resolution.team_id
            ):
                continue
            home_team = get_canonical_team(home_resolution.team_id)
            away_team = get_canonical_team(away_resolution.team_id)
            if (
                home_team is None
                or away_team is None
                or home_team.sport != raw.sport
                or away_team.sport != raw.sport
            ):
                continue

            key = (
                raw.sport,
                raw.start_time,
                tuple(sorted((home_team.id, away_team.id))),
            )
            slot_bookmakers[key].add(raw.bookmaker_id)
            slot_examples.setdefault(
                key,
                _SameTimeSlot(
                    sport=raw.sport,
                    start_time=raw.start_time,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    home_team=home_team.display_name,
                    away_team=away_team.display_name,
                    support_bookmakers=frozenset({raw.bookmaker_id}),
                    raw_league_id=raw.league_id,
                ),
            )

        supported_slots = [
            _SameTimeSlot(
                sport=slot.sport,
                start_time=slot.start_time,
                home_team_id=slot.home_team_id,
                away_team_id=slot.away_team_id,
                home_team=slot.home_team,
                away_team=slot.away_team,
                support_bookmakers=frozenset(slot_bookmakers[key]),
                raw_league_id=slot.raw_league_id,
            )
            for key, slot in slot_examples.items()
        ]
        slots_by_time: dict[tuple[str, str], list[_SameTimeSlot]] = defaultdict(list)
        for slot in supported_slots:
            slots_by_time[(slot.sport, slot.start_time)].append(slot)

        proposals_by_source: dict[int, list[_SameTimeMergeProposal]] = defaultdict(list)
        for same_time_slots in slots_by_time.values():
            for index, left_slot in enumerate(same_time_slots):
                for right_slot in same_time_slots[index + 1 :]:
                    if len(left_slot.support_bookmakers) == len(right_slot.support_bookmakers):
                        continue
                    target_slot, source_slot = (
                        (left_slot, right_slot)
                        if len(left_slot.support_bookmakers) > len(right_slot.support_bookmakers)
                        else (right_slot, left_slot)
                    )
                    if (
                        len(target_slot.support_bookmakers)
                        < SAME_TIME_MIN_TARGET_SUPPORT
                    ):
                        continue
                    orientation = _same_time_slot_orientation(source_slot, target_slot)
                    if orientation is None:
                        continue
                    for (
                        source_team_id,
                        target_team_id,
                        source_team_name,
                        target_team_name,
                        score,
                    ) in orientation:
                        if source_team_id == target_team_id:
                            continue
                        proposals_by_source[source_team_id].append(
                            _SameTimeMergeProposal(
                                source_team_id=source_team_id,
                                target_team_id=target_team_id,
                                source_team_name=source_team_name,
                                target_team_name=target_team_name,
                                source_support=len(source_slot.support_bookmakers),
                                target_support=len(target_slot.support_bookmakers),
                                sport=source_slot.sport,
                                start_time=source_slot.start_time,
                                bookmaker_id=sorted(source_slot.support_bookmakers)[0],
                                raw_league_id=source_slot.raw_league_id,
                                canonical_home_team=target_slot.home_team,
                                canonical_away_team=target_slot.away_team,
                                score=score,
                            )
                        )

        approved_cases: list[TeamReviewDiagnostic] = []
        pairings: list[tuple[int, int]] = []
        for source_team_id, proposals in proposals_by_source.items():
            targets = {proposal.target_team_id for proposal in proposals}
            if len(targets) != 1:
                logger.warning(
                    "Skipping same-time canonical auto-merge for team %s due to multiple possible targets",
                    source_team_id,
                )
                continue
            proposal = max(
                proposals,
                key=lambda item: (item.target_support, item.score),
            )
            _, has_declined = await odds_store.get_team_review_case_history_summary(
                sport=proposal.sport,
                normalized_raw_team_name=normalize_identity_text(proposal.source_team_name),
                suggested_team_id=proposal.target_team_id,
                start_time=proposal.start_time,
                canonical_home_team=proposal.canonical_home_team,
                canonical_away_team=proposal.canonical_away_team,
            )
            if has_declined:
                continue
            approved_cases.append(
                TeamReviewDiagnostic(
                    bookmaker_id=proposal.bookmaker_id,
                    raw_league_id=proposal.raw_league_id,
                    normalized_raw_league_id=normalize_identity_text(
                        proposal.raw_league_id
                    ),
                    sport=proposal.sport,
                    raw_team_name=proposal.source_team_name,
                    normalized_raw_team_name=normalize_identity_text(
                        proposal.source_team_name
                    ),
                    suggested_team_id=proposal.target_team_id,
                    suggested_team_name=proposal.target_team_name,
                    start_time=proposal.start_time,
                    review_kind=AUTO_CANONICAL_MERGE_REVIEW_KIND,
                    reason_code="same_time_both_sides_canonical_merge",
                    confidence="very_high",
                    similarity_score=proposal.score,
                    matched_counterpart_team=proposal.target_team_name,
                    canonical_home_team=proposal.canonical_home_team,
                    canonical_away_team=proposal.canonical_away_team,
                    evidence=[
                        f"Exact start time: {proposal.start_time}",
                        "Auto-approved guarded same-time canonical merge",
                        (
                            "Stronger same-slot support: "
                            f"target x{proposal.target_support}, source x{proposal.source_support}"
                        ),
                        (
                            "Strict symmetric team similarity "
                            f"{proposal.score:g} >= {CANONICAL_TEAM_AUTO_MERGE_THRESHOLD:g}"
                        ),
                    ],
                )
            )
            pairings.append((proposal.source_team_id, proposal.target_team_id))

        return approved_cases, pairings

    def _build_case_alias_requests(
        self,
        case,
        raw_rows: list[RawOddsData] | None,
    ) -> list[tuple[str, str, str, str]]:
        requests = [
            (
                case.bookmaker_id,
                case.raw_team_name,
                case.suggested_team_name,
                case.sport,
            )
        ]
        if raw_rows is None:
            return requests

        losing_team_ids = _candidate_merge_source_ids(case)
        if not losing_team_ids or not case.matched_counterpart_team:
            return requests

        counterpart_resolution = resolve_team_name(
            case.matched_counterpart_team,
            sport=case.sport,
        )
        if counterpart_resolution.team_id is None:
            return requests

        seen_requests = {
            (case.bookmaker_id, case.raw_team_name, case.sport),
        }
        for raw in raw_rows:
            if raw.sport != case.sport or raw.start_time != case.start_time:
                continue

            home_resolution = resolve_team_name(
                raw.home_team,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
            )
            away_resolution = resolve_team_name(
                raw.away_team,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
            )

            if (
                home_resolution.team_id in losing_team_ids
                and away_resolution.team_id == counterpart_resolution.team_id
            ):
                request_key = (raw.bookmaker_id, raw.home_team, raw.sport)
                if request_key not in seen_requests:
                    seen_requests.add(request_key)
                    requests.append(
                        (
                            raw.bookmaker_id,
                            raw.home_team,
                            case.suggested_team_name,
                            raw.sport,
                        )
                    )

            if (
                away_resolution.team_id in losing_team_ids
                and home_resolution.team_id == counterpart_resolution.team_id
            ):
                request_key = (raw.bookmaker_id, raw.away_team, raw.sport)
                if request_key not in seen_requests:
                    seen_requests.add(request_key)
                    requests.append(
                        (
                            raw.bookmaker_id,
                            raw.away_team,
                            case.suggested_team_name,
                            raw.sport,
                        )
                    )

        return requests

    async def _auto_apply_anchored_aliases(
        self,
        team_review_cases: list,
        raw_rows: list[RawOddsData] | None = None,
    ) -> tuple[list, list[tuple[str, str, str]], list[tuple[int, int]]]:
        auto_approved_cases: list = []
        applied_aliases: list[tuple[str, str, str]] = []
        pending_merge_pairings: list[tuple[int, int]] = []
        seen_alias_targets: dict[tuple[str, str, str], str] = {}

        try:
            for case in team_review_cases:
                is_auto_alias_candidate = _is_auto_alias_candidate(case)
                contextual_merge_source_ids = _contextual_merge_source_ids(case)
                if not is_auto_alias_candidate and not contextual_merge_source_ids:
                    continue

                _, has_declined = await odds_store.get_team_review_case_history_summary(
                    sport=case.sport,
                    normalized_raw_team_name=case.normalized_raw_team_name,
                    suggested_team_id=case.suggested_team_id,
                    start_time=case.start_time,
                    canonical_home_team=case.canonical_home_team,
                    canonical_away_team=case.canonical_away_team,
                )
                if has_declined:
                    continue
                if (
                    is_auto_alias_candidate
                    and case.suggested_team_name is not None
                    and _is_unsafe_compound_subset_match(
                        case.raw_team_name,
                        case.suggested_team_name,
                    )
                ):
                    continue

                if not is_auto_alias_candidate:
                    evidence = list(case.evidence)
                    evidence.append(
                        "Auto-approved canonical merge from exact event context "
                        f"(score {case.similarity_score:g}, threshold {CANONICAL_TEAM_AUTO_MERGE_THRESHOLD:g})"
                    )
                    evidence.append(
                        "Very-high team evidence: exact kickoff, shared canonical counterpart, strict team similarity, and stronger target support"
                    )
                    auto_approved_cases.append(
                        case.model_copy(
                            update={
                                "review_kind": AUTO_CANONICAL_MERGE_REVIEW_KIND,
                                "status": "approved",
                                "confidence": "very_high",
                                "evidence": evidence,
                            },
                        )
                    )
                    pending_merge_pairings.extend(
                        (source_team_id, case.suggested_team_id)
                        for source_team_id in contextual_merge_source_ids
                    )
                    continue

                case_applied_aliases: list[tuple[str, str, str]] = []
                resolution = None
                case_failed = False

                for bookmaker_id, raw_team_name, target_team_name, sport in self._build_case_alias_requests(
                    case,
                    raw_rows,
                ):
                    alias_key = (bookmaker_id, raw_team_name, sport)
                    existing_target = seen_alias_targets.get(alias_key)
                    if existing_target is not None:
                        if existing_target != target_team_name:
                            logger.warning(
                                "Skipping auto-approved alias %s for bookmaker %s due to conflicting in-cycle targets: %s vs %s",
                                raw_team_name,
                                bookmaker_id,
                                existing_target,
                                target_team_name,
                            )
                            case_failed = True
                            break
                        continue

                    try:
                        alias_resolution = await asyncio.to_thread(
                            remember_team_alias,
                            bookmaker_id=bookmaker_id,
                            raw_team_name=raw_team_name,
                            team_name=target_team_name,
                            sport=sport,
                            source="auto_review",
                        )
                    except (CircularAliasError, RuntimeError, ValueError):
                        logger.exception(
                            "Failed auto-saving anchored alias %s for bookmaker %s",
                            raw_team_name,
                            bookmaker_id,
                        )
                        case_failed = True
                        break

                    seen_alias_targets[alias_key] = alias_resolution.team_name
                    case_applied_aliases.append(alias_key)
                    if alias_key == (case.bookmaker_id, case.raw_team_name, case.sport):
                        resolution = alias_resolution

                if case_failed:
                    if case_applied_aliases:
                        await self._rollback_auto_applied_aliases(case_applied_aliases)
                        for alias_key in case_applied_aliases:
                            seen_alias_targets.pop(alias_key, None)
                    continue

                if resolution is None:
                    # The primary alias target was already applied earlier in this cycle.
                    resolution = resolve_team_name(
                        case.raw_team_name,
                        bookmaker_id=case.bookmaker_id,
                        sport=case.sport,
                    )
                    if resolution.team_id is None:
                        continue

                applied_aliases.extend(case_applied_aliases)
                evidence = list(case.evidence)
                evidence.append(
                    "Auto-approved in the same scrape after anchored fuzzy match "
                    f"(score {case.similarity_score:g}, threshold {ANCHORED_AUTO_APPLY_THRESHOLD})"
                )
                merge_source_ids = _candidate_merge_source_ids(case) | contextual_merge_source_ids
                if merge_source_ids:
                    evidence.append(
                        "Applied same-scrape bookmaker overrides for competing canonical labels"
                    )

                auto_approved_cases.append(
                    case.model_copy(
                        update={
                            "suggested_team_id": resolution.team_id,
                            "suggested_team_name": resolution.team_name,
                            "review_kind": AUTO_ALIAS_REVIEW_KIND,
                            "status": "approved",
                            "evidence": evidence,
                        },
                    )
                )
                pending_merge_pairings.extend(
                    (source_team_id, resolution.team_id)
                    for source_team_id in merge_source_ids
                )
        except Exception:
            if applied_aliases:
                await self._rollback_auto_applied_aliases(applied_aliases)
            raise

        return auto_approved_cases, applied_aliases, pending_merge_pairings

    async def _rollback_auto_applied_aliases(
        self,
        applied_aliases: list[tuple[str, str, str]],
    ) -> None:
        for bookmaker_id, raw_team_name, sport in reversed(applied_aliases):
            try:
                await asyncio.to_thread(
                    forget_team_alias,
                    bookmaker_id=bookmaker_id,
                    raw_team_name=raw_team_name,
                    sport=sport,
                    expected_source="auto_review",
                )
            except Exception:
                logger.exception(
                    "Failed rolling back auto-saved alias %s for bookmaker %s",
                    raw_team_name,
                    bookmaker_id,
                )

    async def _rollback_auto_applied_merges(
        self,
        applied_merges: list[tuple[int, int]],
    ) -> None:
        for source_team_id, target_team_id in reversed(applied_merges):
            try:
                await asyncio.to_thread(
                    unmerge_canonical_team,
                    source_team_id=source_team_id,
                )
            except Exception:
                logger.exception(
                    "Failed rolling back auto-merged canonical team %s from %s",
                    source_team_id,
                    target_team_id,
                )

    async def _apply_canonical_merges(
        self,
        pending_merge_pairings: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        applied_pairings: list[tuple[int, int]] = []
        normalized_pairings, conflicts = _normalize_merge_pairings(pending_merge_pairings)
        for source_team_id in sorted(conflicts):
            logger.warning(
                "Skipping auto-merge for canonical team %s due to conflicting targets in the same scrape",
                source_team_id,
            )

        for source_team_id, target_team_id in sorted(normalized_pairings.items()):
            source_team = await asyncio.to_thread(get_canonical_team, source_team_id)
            target_team = await asyncio.to_thread(
                get_canonical_team,
                target_team_id,
                follow_merge=True,
            )
            if source_team is None or target_team is None:
                continue
            if source_team.id == target_team.id:
                continue
            try:
                await asyncio.to_thread(
                    merge_canonical_teams,
                    source_team_id=source_team.id,
                    target_team_id=target_team.id,
                )
                applied_pairings.append((source_team.id, target_team.id))
            except ValueError:
                logger.exception(
                    "Failed auto-merging canonical team %s into %s",
                    source_team.id,
                    target_team.id,
                )
        return applied_pairings

    async def start(self) -> None:
        if self._running:
            logger.warning("Scheduler already running")
            return
        self._running = True
        self._wake_event.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started (interval=%d min)", self.interval_minutes)

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._task and not self._task.done():
            await self._task
        self._task = None
        logger.info("Scheduler stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_cycle()
            except Exception:
                logger.exception("Scheduler cycle failed")
            if not self._running:
                break
            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=self.interval_minutes * 60
                )
            except asyncio.TimeoutError:
                pass

    async def _scrape_one(
        self, scraper: BaseScraper, league_id: str
    ) -> list[RawOddsData]:
        bookmaker_id = scraper.get_bookmaker_id()
        started_at = time.perf_counter()

        try:
            raw = await scraper.scrape_odds(league_id)
            if not isinstance(raw, list):
                raise TypeError(
                    f"Expected list[RawOddsData], got {type(raw).__name__}"
                )
            if not all(isinstance(item, RawOddsData) for item in raw):
                raise TypeError("Expected list[RawOddsData] with valid items")
        except Exception:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._scan_failed_tasks += 1
            self._scan_completed_tasks += 1
            self._scan_active_tasks = max(0, self._scan_active_tasks - 1)
            benchmark_recorder.record_scrape_task(
                bookmaker_id=bookmaker_id,
                duration_ms=duration_ms,
                raw_items=0,
                failed=True,
            )
            logger.exception(
                "Scraper %s failed for league %s after %d ms",
                bookmaker_id,
                league_id,
                duration_ms,
            )
            return []

        filtered_raw = filter_raw_odds_by_lookahead(raw)
        dropped_count = len(raw) - len(filtered_raw)
        raw = filtered_raw
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self._scan_completed_tasks += 1
        self._scan_active_tasks = max(0, self._scan_active_tasks - 1)
        benchmark_recorder.record_scrape_task(
            bookmaker_id=bookmaker_id,
            duration_ms=duration_ms,
            raw_items=len(raw),
            failed=False,
        )
        logger.info(
            "Scraper %s completed for league %s in %d ms (%d items)",
            bookmaker_id,
            league_id,
            duration_ms,
            len(raw),
        )
        if dropped_count:
            logger.info(
                "Scraper %s dropped %d items outside %dh lookahead",
                bookmaker_id,
                dropped_count,
                configured_lookahead_hours(),
            )
        return raw

    async def _scrape_outcome_one(
        self, scraper: BaseScraper, sport: str
    ) -> list[RawOutcomeOffer]:
        bookmaker_id = scraper.get_bookmaker_id()
        started_at = time.perf_counter()

        try:
            raw = await scraper.scrape_outcome_offers(sport)
            if not isinstance(raw, list):
                raise TypeError(
                    f"Expected list[RawOutcomeOffer], got {type(raw).__name__}"
                )
            if not all(isinstance(item, RawOutcomeOffer) for item in raw):
                raise TypeError("Expected list[RawOutcomeOffer] with valid items")
        except Exception:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._scan_failed_tasks += 1
            self._scan_completed_tasks += 1
            self._scan_active_tasks = max(0, self._scan_active_tasks - 1)
            benchmark_recorder.record_scrape_task(
                bookmaker_id=bookmaker_id,
                duration_ms=duration_ms,
                raw_items=0,
                failed=True,
            )
            logger.exception(
                "Outcome scraper %s failed for sport %s after %d ms",
                bookmaker_id,
                sport,
                duration_ms,
            )
            return []

        filtered_raw = filter_raw_odds_by_lookahead(raw)
        dropped_count = len(raw) - len(filtered_raw)
        raw = filtered_raw
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self._scan_completed_tasks += 1
        self._scan_active_tasks = max(0, self._scan_active_tasks - 1)
        benchmark_recorder.record_scrape_task(
            bookmaker_id=bookmaker_id,
            duration_ms=duration_ms,
            raw_items=len(raw),
            failed=False,
        )
        logger.info(
            "Outcome scraper %s completed for sport %s in %d ms (%d items)",
            bookmaker_id,
            sport,
            duration_ms,
            len(raw),
        )
        if dropped_count:
            logger.info(
                "Outcome scraper %s dropped %d items outside %dh lookahead",
                bookmaker_id,
                dropped_count,
                configured_lookahead_hours(),
            )
        return raw

    async def _run_cycle_once(self) -> dict:
        """Execute one full scrape → normalize → analyze → store → notify cycle."""
        try:
            cycle_started_at = time.perf_counter()
            cycle_started_at_iso = datetime.utcnow().isoformat()
            self._scan_phase = "starting"
            self._scan_started_at = cycle_started_at_iso
            self._scan_total_tasks = 0
            self._scan_completed_tasks = 0
            self._scan_failed_tasks = 0
            self._scan_active_tasks = 0
            benchmark_recorder.begin_cycle(cycle_started_at_iso)
            logger.info("Starting scrape cycle at %s", cycle_started_at_iso)

            scrapers = registry.get_all()
            enabled_sports = set(settings.enabled_sport_list)
            scrape_started_at = time.perf_counter()
            scrape_tasks = [
                self._scrape_one(scraper, league_id)
                for scraper in scrapers
                for league_id in _enabled_threshold_league_ids(scraper, enabled_sports)
            ]
            outcome_scrape_tasks = [
                self._scrape_outcome_one(scraper, sport)
                for scraper in scrapers
                for sport in scraper.get_supported_outcome_sports()
                if sport in enabled_sports
            ]
            self._scan_phase = "scraping"
            self._scan_total_tasks = len(scrape_tasks) + len(outcome_scrape_tasks)
            self._scan_active_tasks = len(scrape_tasks) + len(outcome_scrape_tasks)
            scrape_batches = await asyncio.gather(*scrape_tasks) if scrape_tasks else []
            outcome_scrape_batches = (
                await asyncio.gather(*outcome_scrape_tasks)
                if outcome_scrape_tasks
                else []
            )
            all_raw = [item for batch in scrape_batches for item in batch]
            all_raw_outcome_offers = [
                item for batch in outcome_scrape_batches for item in batch
            ]
            scrape_duration_ms = int((time.perf_counter() - scrape_started_at) * 1000)
            logger.info(
                "Scrape phase complete: %d tasks, %d raw odds items, %d raw outcome offers in %d ms",
                len(scrape_tasks) + len(outcome_scrape_tasks),
                len(all_raw),
                len(all_raw_outcome_offers),
                scrape_duration_ms,
            )

            self._scan_phase = "registering"
            for scraper in scrapers:
                await odds_store.upsert_bookmaker(
                    id=scraper.get_bookmaker_id(),
                    name=scraper.get_bookmaker_name(),
                )

            self._scan_phase = "normalizing"
            normalized = []
            normalized_outcome_offers = []
            seen_matches: set[str] = set()
            opportunities = []
            canonical_shadow = _CanonicalShadowResult()
            notified = 0
            pending_auto_merges: list[tuple[int, int]] = []
            (
                normalized,
                unresolved_odds,
                team_review_cases,
            ) = normalize_odds_with_diagnostics(
                all_raw,
                log_unresolved_shared_platform=False,
            )
            (
                normalized_outcome_offers,
                unresolved_outcome_offers,
                outcome_team_review_cases,
            ) = normalize_outcome_offers_with_diagnostics(all_raw_outcome_offers)
            unresolved_odds = [*unresolved_odds, *unresolved_outcome_offers]
            team_review_cases = [*team_review_cases, *outcome_team_review_cases]
            applied_auto_aliases: list[tuple[str, str, str]] = []
            applied_auto_merges: list[tuple[int, int]] = []
            try:
                (
                    same_time_auto_reviews,
                    same_time_auto_merges,
                ) = await self._same_time_canonical_merge_candidates(all_raw)
                (
                    auto_approved_team_reviews,
                    applied_auto_aliases,
                    pending_auto_merges,
                ) = await self._auto_apply_anchored_aliases(
                    team_review_cases,
                    all_raw,
                )
                auto_approved_team_reviews = [
                    *same_time_auto_reviews,
                    *auto_approved_team_reviews,
                ]
                pending_auto_merges = [
                    *same_time_auto_merges,
                    *pending_auto_merges,
                ]
                if pending_auto_merges:
                    applied_auto_merges = await self._apply_canonical_merges(
                        pending_auto_merges
                    )
                if auto_approved_team_reviews or applied_auto_merges:
                    (
                        normalized,
                        unresolved_odds,
                        team_review_cases,
                    ) = normalize_odds_with_diagnostics(all_raw)
                    (
                        normalized_outcome_offers,
                        unresolved_outcome_offers,
                        outcome_team_review_cases,
                    ) = normalize_outcome_offers_with_diagnostics(all_raw_outcome_offers)
                    unresolved_odds = [*unresolved_odds, *unresolved_outcome_offers]
                    team_review_cases = [*team_review_cases, *outcome_team_review_cases]
                else:
                    log_unresolved_shared_platform_diagnostics(unresolved_odds)

                self._scan_phase = "storing"
                cycle_scraped_at = datetime.utcnow().isoformat()
                for o in normalized:
                    if o.match_id not in seen_matches:
                        await odds_store.upsert_league(
                            id=o.league_id,
                            name=league_display_name(o.league_id),
                            sport=o.sport,
                            country=league_country(o.league_id),
                        )
                        await odds_store.upsert_match(
                            id=o.match_id,
                            league_id=o.league_id,
                            home_team=o.home_team,
                            away_team=o.away_team,
                            sport=o.sport,
                            home_team_id=o.home_team_id,
                            away_team_id=o.away_team_id,
                            start_time=o.start_time,
                        )
                        seen_matches.add(o.match_id)
                    await odds_store.upsert_odds(o, scraped_at=cycle_scraped_at)
                for offer in normalized_outcome_offers:
                    if offer.match_id not in seen_matches:
                        await odds_store.upsert_league(
                            id=offer.league_id,
                            name=league_display_name(offer.league_id),
                            sport=offer.sport,
                            country=league_country(offer.league_id),
                        )
                        await odds_store.upsert_match(
                            id=offer.match_id,
                            league_id=offer.league_id,
                            home_team=offer.home_team,
                            away_team=offer.away_team,
                            sport=offer.sport,
                            home_team_id=offer.home_team_id,
                            away_team_id=offer.away_team_id,
                            start_time=offer.start_time,
                        )
                        seen_matches.add(offer.match_id)
                    await odds_store.upsert_outcome_offer(
                        offer,
                        scraped_at=cycle_scraped_at,
                    )
                await resolve_and_persist_events(
                    raw_odds=all_raw,
                    raw_outcome_offers=all_raw_outcome_offers,
                    normalized_odds=normalized,
                    normalized_outcome_offers=normalized_outcome_offers,
                )
                for unresolved in unresolved_odds:
                    await odds_store.insert_unresolved_odds(
                        unresolved, scraped_at=cycle_scraped_at
                    )
                for team_review_case in team_review_cases:
                    await odds_store.insert_team_review_case(
                        team_review_case, scraped_at=cycle_scraped_at
                    )
                for team_review_case in auto_approved_team_reviews:
                    case_id = await odds_store.insert_team_review_case(
                        team_review_case, scraped_at=cycle_scraped_at
                    )
                    await odds_store.mark_team_review_case_approved(case_id)
                await odds_store.set_current_snapshot(cycle_scraped_at)

                self._scan_phase = "analyzing"
                canonical_analysis = _CanonicalAnalysisResult()
                canonical_analysis_failed = False
                try:
                    canonical_analysis = await _load_current_canonical_analysis(
                        match_ids=seen_matches,
                    )
                except Exception:
                    canonical_analysis_failed = True
                    logger.exception("Canonical opportunity analysis failed")
                opportunities = list(canonical_analysis.opportunities)

                await odds_store.deactivate_opportunities()
                if not canonical_analysis_failed:
                    for opportunity in opportunities:
                        await odds_store.insert_opportunity(
                            opportunity,
                            detected_at=cycle_scraped_at,
                        )

                if canonical_analysis_failed:
                    canonical_shadow = _CanonicalShadowResult(
                        warnings=("canonical_analysis_failed",)
                    )
                else:
                    canonical_shadow = _CanonicalShadowResult(
                        offers_analyzed=len(canonical_analysis.offers),
                        opportunities_found=len(canonical_analysis.opportunities),
                    )
                if canonical_shadow.warnings:
                    logger.warning(
                        "Canonical shadow analysis warnings: %s",
                        ", ".join(canonical_shadow.warnings),
                    )

                self._scan_phase = "notifying"
                notified = await self._notification_service.notify_opportunities(opportunities)
            except Exception:
                await odds_store.rollback_pending_transaction()
                if applied_auto_merges:
                    await self._rollback_auto_applied_merges(applied_auto_merges)
                if applied_auto_aliases:
                    await self._rollback_auto_applied_aliases(applied_auto_aliases)
                raise
            finally:
                # Publish per-bookmaker benchmark snapshot regardless of whether
                # downstream phases (normalize/store/analyze/notify) succeeded so
                # operators can still see scrape-side timings on failed cycles.
                try:
                    matches_per_bm: dict[str, int] = defaultdict(int)
                    odds_per_bm: dict[str, int] = defaultdict(int)
                    seen_match_per_bm: dict[str, set[str]] = defaultdict(set)
                    for o in normalized:
                        odds_per_bm[o.bookmaker_id] += 1
                        if o.match_id not in seen_match_per_bm[o.bookmaker_id]:
                            seen_match_per_bm[o.bookmaker_id].add(o.match_id)
                            matches_per_bm[o.bookmaker_id] += 1
                    for offer in normalized_outcome_offers:
                        odds_per_bm[offer.bookmaker_id] += 1
                        if offer.match_id not in seen_match_per_bm[offer.bookmaker_id]:
                            seen_match_per_bm[offer.bookmaker_id].add(offer.match_id)
                            matches_per_bm[offer.bookmaker_id] += 1
                    benchmark_recorder.record_phase_durations(
                        scrape_duration_ms=scrape_duration_ms,
                        cycle_duration_ms=int(
                            (time.perf_counter() - cycle_started_at) * 1000
                        ),
                    )
                    benchmark_recorder.publish(
                        matches_per_bookmaker=dict(matches_per_bm),
                        odds_per_bookmaker=dict(odds_per_bm),
                        total_unique_matches=len(seen_matches),
                    )
                except Exception:
                    logger.exception("Failed to publish scraper benchmark snapshot")

            try:
                self._scan_phase = "retaining"
                cleanup_counts = await odds_store.cleanup_retained_data(cycle_scraped_at)
                logger.info("Retention cleanup complete: %s", cleanup_counts)
            except Exception:
                logger.exception("Retention cleanup failed after a successful scrape cycle")

            result = {
                "matches_scraped": len(seen_matches),
                "odds_scraped": len(normalized),
                "outcome_offers_scraped": len(normalized_outcome_offers),
                "opportunities_found": len(opportunities),
                "canonical_offers_analyzed": canonical_shadow.offers_analyzed,
                "canonical_opportunities_found": canonical_shadow.opportunities_found,
                "canonical_shadow_warnings": list(canonical_shadow.warnings),
                "notifications_sent": notified,
                "scrape_duration_ms": scrape_duration_ms,
                "cycle_duration_ms": int((time.perf_counter() - cycle_started_at) * 1000),
            }

            logger.info("Cycle complete: %s", result)
            return result
        finally:
            self._reset_progress()

    async def run_cycle(self) -> dict:
        existing_cycle = self._cycle_task
        if existing_cycle is not None and not existing_cycle.done():
            return await asyncio.shield(existing_cycle)

        async with self._cycle_lock:
            existing_cycle = self._cycle_task
            if existing_cycle is not None and not existing_cycle.done():
                return await asyncio.shield(existing_cycle)
            self._cycle_task = asyncio.create_task(self._run_cycle_once())
            cycle_task = self._cycle_task

        try:
            return await asyncio.shield(cycle_task)
        finally:
            async with self._cycle_lock:
                if self._cycle_task is cycle_task and cycle_task.done():
                    self._cycle_task = None


scheduler = Scheduler()
