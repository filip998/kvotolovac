from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime

from ..config import settings
from ..models.schemas import RawOddsData
from ..scrapers.base import BaseScraper
from ..scrapers.registry import registry
from ..models.schemas import ScanProgressOut
from ..services.league_registry import league_country, league_display_name
from ..services.normalizer import normalize_odds_with_diagnostics
from ..services.analyzer import analyze
from ..services.notifications import NotificationService, InAppNotificationProvider
from ..services.scraper_benchmarks import recorder as benchmark_recorder
from ..services.team_registry import (
    CircularAliasError,
    forget_team_alias,
    remember_team_alias,
)
from ..store import odds_store

logger = logging.getLogger(__name__)


AUTO_ALIAS_REVIEW_KIND = "auto_alias_suggestion"


def _auto_alias_group_key(case) -> tuple[str, str, int, str, str, str]:
    return (
        case.sport,
        case.normalized_raw_team_name,
        case.suggested_team_id or 0,
        case.start_time or "",
        case.canonical_home_team or "",
        case.canonical_away_team or "",
    )


def _is_auto_alias_candidate(case) -> bool:
    return (
        case.review_kind == "alias_suggestion"
        and case.reason_code == "candidate_team_match_same_start_time"
        and case.confidence == "high"
        and case.suggested_team_id is not None
        and case.suggested_team_name is not None
        and case.start_time is not None
        and case.matched_counterpart_team is not None
        and case.canonical_home_team is not None
        and case.canonical_away_team is not None
    )


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

    async def _auto_apply_anchored_aliases(self, team_review_cases: list) -> tuple[list, list[tuple[str, str, str]]]:
        candidate_groups: dict[tuple[str, str, int, str, str, str], list] = defaultdict(list)
        for case in team_review_cases:
            if _is_auto_alias_candidate(case):
                candidate_groups[_auto_alias_group_key(case)].append(case)

        auto_approved_cases: list = []
        applied_aliases: list[tuple[str, str, str]] = []
        try:
            for group_key, grouped_cases in candidate_groups.items():
                (
                    sport,
                    normalized_raw_team_name,
                    suggested_team_id,
                    start_time,
                    canonical_home_team,
                    canonical_away_team,
                ) = group_key
                historical_bookmakers, has_declined = await odds_store.get_team_review_case_history_summary(
                    sport=sport,
                    normalized_raw_team_name=normalized_raw_team_name,
                    suggested_team_id=suggested_team_id,
                    start_time=start_time,
                    canonical_home_team=canonical_home_team,
                    canonical_away_team=canonical_away_team,
                )
                if has_declined:
                    continue

                current_bookmakers = {case.bookmaker_id for case in grouped_cases}
                combined_bookmakers = historical_bookmakers | current_bookmakers
                repeated_scrape = any(
                    case.bookmaker_id in historical_bookmakers for case in grouped_cases
                )
                second_bookmaker_confirmation = len(combined_bookmakers) >= 2
                if not repeated_scrape and not second_bookmaker_confirmation:
                    continue

                for case in grouped_cases:
                    try:
                        resolution = await asyncio.to_thread(
                            remember_team_alias,
                            bookmaker_id=case.bookmaker_id,
                            raw_team_name=case.raw_team_name,
                            team_name=case.suggested_team_name,
                            sport=case.sport,
                            source="auto_review",
                        )
                    except (CircularAliasError, RuntimeError, ValueError):
                        logger.exception(
                            "Failed auto-saving anchored alias %s for bookmaker %s",
                            case.raw_team_name,
                            case.bookmaker_id,
                        )
                        continue

                    evidence = list(case.evidence)
                    if case.bookmaker_id in historical_bookmakers:
                        evidence.append("Auto-approved after repeated anchored alias scrape")
                    if second_bookmaker_confirmation:
                        evidence.append(
                            "Auto-approved after confirming bookmakers: "
                            + ", ".join(sorted(combined_bookmakers))
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
                    applied_aliases.append((case.bookmaker_id, case.raw_team_name, case.sport))
        except Exception:
            if applied_aliases:
                await self._rollback_auto_applied_aliases(applied_aliases)
            raise
        return auto_approved_cases, applied_aliases

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
            scrape_started_at = time.perf_counter()
            scrape_tasks = [
                self._scrape_one(scraper, league_id)
                for scraper in scrapers
                for league_id in scraper.get_supported_leagues()
            ]
            self._scan_phase = "scraping"
            self._scan_total_tasks = len(scrape_tasks)
            self._scan_active_tasks = len(scrape_tasks)
            scrape_batches = await asyncio.gather(*scrape_tasks) if scrape_tasks else []
            all_raw = [item for batch in scrape_batches for item in batch]
            scrape_duration_ms = int((time.perf_counter() - scrape_started_at) * 1000)
            logger.info(
                "Scrape phase complete: %d tasks, %d raw items in %d ms",
                len(scrape_tasks),
                len(all_raw),
                scrape_duration_ms,
            )

            self._scan_phase = "registering"
            for scraper in scrapers:
                await odds_store.upsert_bookmaker(
                    id=scraper.get_bookmaker_id(),
                    name=scraper.get_bookmaker_name(),
                )

            self._scan_phase = "normalizing"
            (
                normalized,
                unresolved_odds,
                team_review_cases,
            ) = normalize_odds_with_diagnostics(all_raw)
            applied_auto_aliases: list[tuple[str, str, str]] = []
            try:
                auto_approved_team_reviews, applied_auto_aliases = (
                    await self._auto_apply_anchored_aliases(team_review_cases)
                )
                if auto_approved_team_reviews:
                    (
                        normalized,
                        unresolved_odds,
                        team_review_cases,
                    ) = normalize_odds_with_diagnostics(all_raw)

                self._scan_phase = "storing"
                cycle_scraped_at = datetime.utcnow().isoformat()
                seen_matches: set[str] = set()
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
                await odds_store.deactivate_all_discrepancies()
                discrepancies = analyze(normalized)

                for d in discrepancies:
                    await odds_store.insert_discrepancy(
                        match_id=d.match_id,
                        market_type=d.market_type,
                        player_name=d.player_name,
                        bookmaker_a_id=d.bookmaker_a_id,
                        bookmaker_b_id=d.bookmaker_b_id,
                        threshold_a=d.threshold_a,
                        threshold_b=d.threshold_b,
                        odds_a=d.odds_a,
                        odds_b=d.odds_b,
                        gap=d.gap,
                        profit_margin=d.profit_margin,
                        middle_profit_margin=d.middle_profit_margin,
                    )

                self._scan_phase = "notifying"
                notified = await self._notification_service.notify_discrepancies(
                    discrepancies
                )
            except Exception:
                if applied_auto_aliases:
                    await self._rollback_auto_applied_aliases(applied_auto_aliases)
                raise

            result = {
                "matches_scraped": len(seen_matches),
                "odds_scraped": len(normalized),
                "discrepancies_found": len(discrepancies),
                "notifications_sent": notified,
                "scrape_duration_ms": scrape_duration_ms,
                "cycle_duration_ms": int((time.perf_counter() - cycle_started_at) * 1000),
            }

            # Aggregate per-bookmaker counts from the normalized output, then publish
            # the benchmark snapshot. Done after analysis so failures during analyze
            # don't suppress the file output.
            try:
                matches_per_bm: dict[str, int] = defaultdict(int)
                odds_per_bm: dict[str, int] = defaultdict(int)
                seen_match_per_bm: dict[str, set[str]] = defaultdict(set)
                for o in normalized:
                    odds_per_bm[o.bookmaker_id] += 1
                    if o.match_id not in seen_match_per_bm[o.bookmaker_id]:
                        seen_match_per_bm[o.bookmaker_id].add(o.match_id)
                        matches_per_bm[o.bookmaker_id] += 1
                benchmark_recorder.record_phase_durations(
                    scrape_duration_ms=scrape_duration_ms,
                    cycle_duration_ms=result["cycle_duration_ms"],
                )
                benchmark_recorder.publish(
                    matches_per_bookmaker=dict(matches_per_bm),
                    odds_per_bookmaker=dict(odds_per_bm),
                    total_unique_matches=len(seen_matches),
                )
            except Exception:
                logger.exception("Failed to publish scraper benchmark snapshot")

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
