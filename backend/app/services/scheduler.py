from __future__ import annotations

import asyncio
import logging
import time
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
from ..store import odds_store

logger = logging.getLogger(__name__)


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
            normalized, unresolved_odds, matching_review_cases = normalize_odds_with_diagnostics(all_raw)

            self._scan_phase = "storing"
            cycle_scraped_at = datetime.utcnow().isoformat()
            seen_matches: set[str] = set()
            for o in normalized:
                if o.match_id not in seen_matches:
                    await odds_store.upsert_league(
                        id=o.league_id,
                        name=league_display_name(o.league_id),
                        sport="basketball",
                        country=league_country(o.league_id),
                    )
                    await odds_store.upsert_match(
                        id=o.match_id,
                        league_id=o.league_id,
                        home_team=o.home_team,
                        away_team=o.away_team,
                        start_time=o.start_time,
                    )
                    seen_matches.add(o.match_id)
                await odds_store.upsert_odds(o, scraped_at=cycle_scraped_at)
            for unresolved in unresolved_odds:
                await odds_store.insert_unresolved_odds(
                    unresolved, scraped_at=cycle_scraped_at
                )
            for review_case in matching_review_cases:
                await odds_store.insert_matching_review_case(
                    review_case, scraped_at=cycle_scraped_at
                )
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
            notified = await self._notification_service.notify_discrepancies(discrepancies)

            result = {
                "matches_scraped": len(seen_matches),
                "odds_scraped": len(normalized),
                "discrepancies_found": len(discrepancies),
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
