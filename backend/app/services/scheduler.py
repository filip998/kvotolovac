from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..config import settings
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

    async def run_cycle(self) -> dict:
        """Execute one full scrape → normalize → analyze → store → notify cycle."""
        logger.info("Starting scrape cycle at %s", datetime.utcnow().isoformat())

        all_raw = []
        scrapers = registry.get_all()
        for scraper in scrapers:
            for league_id in scraper.get_supported_leagues():
                try:
                    raw = await scraper.scrape_odds(league_id)
                    all_raw.extend(raw)
                except Exception:
                    logger.exception(
                        "Scraper %s failed for league %s",
                        scraper.get_bookmaker_id(),
                        league_id,
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
            await odds_store.upsert_odds(o)

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
        }
        logger.info("Cycle complete: %s", result)
        return result


scheduler = Scheduler()
