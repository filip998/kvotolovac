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
from .scrapers.base import BaseScraper
from .scrapers.http_client import HttpClient
from .scrapers.registry import registry
from .services.scheduler import scheduler

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
}


def _create_real_scrapers(
    bookmaker_ids: list[str],
    *,
    rate_limit_per_second: float,
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

        http_client = HttpClient(
            rate_limit_per_second=rate_limit_per_second,
            proxies=proxies,
        )
        scrapers.append(scraper_factory(http_client))
        managed_clients.append(http_client)
        logger.info("Registered real scraper: %s", bm_id)

    return scrapers, managed_clients


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
    await init_db(settings.db_path)

    # Register scrapers based on configured mode
    managed_clients: list[HttpClient] = []
    if settings.scraper_mode == "real":
        scrapers, managed_clients = _create_real_scrapers(
            settings.bookmaker_list,
            rate_limit_per_second=settings.rate_limit_per_second,
            proxies=settings.proxy_url_list or None,
        )
        for scraper in scrapers:
            registry.register(scraper)
    else:
        for bm_id in settings.bookmaker_list:
            try:
                scraper = MockScraper(bm_id)
                registry.register(scraper)
                logger.info("Registered scraper: %s", bm_id)
            except ValueError:
                logger.warning("No mock scraper for bookmaker: %s", bm_id)

    # Start scheduler loop in the background so the API is responsive immediately.
    await scheduler.start()
    logger.info("Scheduler background loop started")

    yield

    # Shutdown
    await scheduler.stop()
    await _shutdown_resources(managed_clients)
    logger.info("Shutdown complete")


app = FastAPI(
    title="KvotoLovac",
    description="Odds comparison tool for Serbian bookmakers — basketball betting line discrepancy detection",
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
