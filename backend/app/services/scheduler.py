from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from ..config import settings
from ..models.schemas import RawOddsData
from ..scrapers.base import BaseScraper
from ..scrapers.registry import registry
from ..services.normalizer import normalize_odds
from ..services.analyzer import analyze
from ..services.notifications import NotificationService, InAppNotificationProvider
from ..store import odds_store

logger = logging.getLogger(__name__)


class Scheduler:
    """Background task scheduler for periodic scraping."""

    def __init__(self, interval_minutes: int | None = None) -> None:
        self.interval_minutes = interval_minutes or settings.scrape_interval_minutes
        self._task: asyncio.Task | None = None
        self._running = False
        self._notification_service = NotificationService(
            gap_threshold=settings.notification_gap_threshold
        )
        self._notification_service.register_provider(InAppNotificationProvider())

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            logger.warning("Scheduler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started (interval=%d min)", self.interval_minutes)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Scheduler stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_cycle()
            except Exception:
                logger.exception("Scheduler cycle failed")
            await asyncio.sleep(self.interval_minutes * 60)

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
            logger.exception(
                "Scraper %s failed for league %s after %d ms",
                bookmaker_id,
                league_id,
                duration_ms,
            )
            return []

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "Scraper %s completed for league %s in %d ms (%d items)",
            bookmaker_id,
            league_id,
            duration_ms,
            len(raw),
        )
        return raw

    async def run_cycle(self) -> dict:
        """Execute one full scrape → normalize → analyze → store → notify cycle."""
        cycle_started_at = time.perf_counter()
        logger.info("Starting scrape cycle at %s", datetime.utcnow().isoformat())

        scrapers = registry.get_all()
        scrape_started_at = time.perf_counter()
        scrape_tasks = [
            self._scrape_one(scraper, league_id)
            for scraper in scrapers
            for league_id in scraper.get_supported_leagues()
        ]
        scrape_batches = await asyncio.gather(*scrape_tasks) if scrape_tasks else []
        all_raw = [item for batch in scrape_batches for item in batch]
        scrape_duration_ms = int((time.perf_counter() - scrape_started_at) * 1000)
        logger.info(
            "Scrape phase complete: %d tasks, %d raw items in %d ms",
            len(scrape_tasks),
            len(all_raw),
            scrape_duration_ms,
        )

        # Ensure bookmakers exist
        for scraper in scrapers:
            await odds_store.upsert_bookmaker(
                id=scraper.get_bookmaker_id(),
                name=scraper.get_bookmaker_name(),
            )

        # Normalize
        normalized = normalize_odds(all_raw)

        # Store matches & odds
        cycle_scraped_at = datetime.utcnow().isoformat()
        seen_matches: set[str] = set()
        for o in normalized:
            if o.match_id not in seen_matches:
                await odds_store.upsert_league(
                    id=o.league_id, name=o.league_id.title(), sport="basketball"
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
        await odds_store.set_current_snapshot(cycle_scraped_at)

        # Analyse
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
            )

        # Notify
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


scheduler = Scheduler()
