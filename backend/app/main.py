from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import close_db, init_db
from .api.router import api_router
from .scrapers.mock_scraper import MockScraper
from .scrapers.mozzart_scraper import MozzartScraper
from .scrapers.maxbet_scraper import MaxBetScraper
from .scrapers.oktagonbet_scraper import OktagonBetScraper
from .scrapers.meridian_scraper import MeridianScraper
from .scrapers.admiralbet_scraper import AdmiralBetScraper
from .scrapers.balkanbet_scraper import BalkanBetScraper
from .scrapers.merkurxtip_scraper import MerkurXTipScraper
from .scrapers.pinnbet_scraper import PinnBetScraper
from .scrapers.soccerbet_scraper import SoccerBetScraper
from .scrapers.superbet_scraper import SuperbetScraper
from .scrapers.betole_scraper import BetOleScraper
from .scrapers.bookmaker365_scraper import Bookmaker365Scraper
from .scrapers.volcanobet_scraper import VolcanoBetScraper
from .scrapers.starbet_scraper import StarBetScraper
from .scrapers.base import BaseScraper
from .scrapers.http_client import HttpClient
from .scrapers.registry import registry
from .services.scheduler import scheduler
from .services.runtime_settings import ensure_scrape_settings_seeded
from .services.telegram_commands import (
    TelegramCommandPoller,
    create_telegram_command_poller,
    wait_for_telegram_command_tasks,
)
from .store import odds_store
from .migrations.runner import migrate_database_to_head

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

_REAL_SCRAPER_FACTORIES: dict[str, Callable[[HttpClient], BaseScraper]] = {
    "mozzart": MozzartScraper,
    "maxbet": MaxBetScraper,
    "oktagonbet": OktagonBetScraper,
    "meridian": MeridianScraper,
    "admiralbet": AdmiralBetScraper,
    "balkanbet": BalkanBetScraper,
    "merkurxtip": MerkurXTipScraper,
    "pinnbet": PinnBetScraper,
    "soccerbet": SoccerBetScraper,
    "superbet": SuperbetScraper,
    "betole": BetOleScraper,
    "365": Bookmaker365Scraper,
    "volcanobet": VolcanoBetScraper,
    "starbet": StarBetScraper,
}


def _create_real_scrapers(
    bookmaker_ids: list[str],
    *,
    rate_limit_per_second: float,
    meridian_rate_limit_per_second: float,
    proxies: list[str] | None,
) -> tuple[list[BaseScraper], list[HttpClient]]:
    scrapers: list[BaseScraper] = []
    managed_clients: list[HttpClient] = []

    for bm_id in bookmaker_ids:
        scraper_factory = _REAL_SCRAPER_FACTORIES.get(bm_id)
        if scraper_factory is None:
            try:
                scraper = MockScraper(bm_id)
            except ValueError:
                logger.warning("No scraper available for bookmaker: %s", bm_id)
                continue

            scrapers.append(scraper)
            logger.info("Registered mock scraper (no real scraper yet): %s", bm_id)
            continue

        effective_rate_limit = rate_limit_per_second
        if bm_id == "meridian":
            effective_rate_limit = meridian_rate_limit_per_second

        http_client = HttpClient(
            rate_limit_per_second=effective_rate_limit,
            proxies=proxies,
        )
        scrapers.append(scraper_factory(http_client))
        managed_clients.append(http_client)
        logger.info("Registered real scraper: %s", bm_id)

    return scrapers, managed_clients


def _real_scraper_ids_to_register() -> list[str]:
    return sorted(set(_REAL_SCRAPER_FACTORIES) | set(settings.bookmaker_list))


async def _close_http_clients(http_clients: list[HttpClient]) -> None:
    close_errors: list[Exception] = []
    for http_client in http_clients:
        try:
            await http_client.close()
        except Exception as exc:
            logger.exception("Failed to close HTTP client during shutdown")
            close_errors.append(exc)
    if close_errors:
        if len(close_errors) == 1:
            raise close_errors[0]
        raise RuntimeError(
            f"Failed to close {len(close_errors)} HTTP clients during shutdown"
        ) from close_errors[0]


async def _shutdown_resources(
    http_clients: list[HttpClient],
    *,
    close_http_clients_func: Callable[[list[HttpClient]], Awaitable[None]] = _close_http_clients,
    close_db_func: Callable[[], Awaitable[None]] = close_db,
) -> None:
    shutdown_errors: list[Exception] = []

    try:
        await close_http_clients_func(http_clients)
    except Exception as exc:
        logger.exception("HTTP client shutdown failed")
        shutdown_errors.append(exc)

    try:
        await close_db_func()
    except Exception as exc:
        logger.exception("Database shutdown failed")
        shutdown_errors.append(exc)

    if shutdown_errors:
        if len(shutdown_errors) == 1:
            raise shutdown_errors[0]
        raise RuntimeError(
            f"Failed to shut down {len(shutdown_errors)} resources"
        ) from shutdown_errors[0]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initialising database: %s", settings.db_path)
    if settings.auto_migrate_on_startup:
        logger.warning(
            "AUTO_MIGRATE_ON_STARTUP enabled; applying pending database migrations "
            "before startup"
        )
        previous_revision, head = migrate_database_to_head(settings.db_path)
        if previous_revision == head:
            logger.info("Database already at Alembic head: %s", head)
        else:
            logger.info(
                "Database migrated to Alembic head: %s -> %s",
                previous_revision or "unversioned/missing",
                head,
            )
    await init_db(settings.db_path)
    await ensure_scrape_settings_seeded()

    # Register scrapers based on configured mode
    managed_clients: list[HttpClient] = []
    registered_scrapers: list[BaseScraper] = []
    if settings.scraper_mode == "real":
        scrapers, managed_clients = _create_real_scrapers(
            _real_scraper_ids_to_register(),
            rate_limit_per_second=settings.rate_limit_per_second,
            meridian_rate_limit_per_second=settings.meridian_rate_limit_per_second,
            proxies=settings.proxy_url_list or None,
        )
        for scraper in scrapers:
            registry.register(scraper)
        registered_scrapers = scrapers
    else:
        for bm_id in settings.bookmaker_list:
            try:
                scraper = MockScraper(bm_id)
                registry.register(scraper)
                registered_scrapers.append(scraper)
                logger.info("Registered scraper: %s", bm_id)
            except ValueError:
                logger.warning("No mock scraper for bookmaker: %s", bm_id)

    for scraper in registered_scrapers:
        await odds_store.upsert_bookmaker(
            id=scraper.get_bookmaker_id(),
            name=scraper.get_bookmaker_name(),
        )

    # Start scheduler loop in the background so the API is responsive immediately.
    await scheduler.start()
    logger.info("Scheduler background loop started")

    telegram_command_poller: TelegramCommandPoller | None = None
    if settings.telegram_commands_enabled and settings.telegram_bot_token.strip():
        telegram_command_poller = create_telegram_command_poller(scheduler)
        telegram_command_poller.start()
        logger.info("Telegram command poller started")

    yield

    # Shutdown
    if telegram_command_poller is not None:
        await telegram_command_poller.stop()
    await wait_for_telegram_command_tasks()
    await scheduler.stop()
    await _shutdown_resources(managed_clients)
    logger.info("Shutdown complete")


app = FastAPI(
    title="KvotoLovac",
    description="Odds comparison tool for Serbian bookmakers — canonical betting opportunity detection",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
async def root():
    return {"name": "KvotoLovac", "version": "0.1.0", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
