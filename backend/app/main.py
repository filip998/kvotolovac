from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import close_db, init_db
from .api.router import api_router
from .scrapers.mock_scraper import MockScraper
from .scrapers.mozzart_scraper import MozzartScraper
from .scrapers.maxbet_scraper import MaxBetScraper
from .scrapers.http_client import HttpClient
from .scrapers.registry import registry
from .services.scheduler import scheduler

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initialising database: %s", settings.db_path)
    await init_db(settings.db_path)

    # Register scrapers based on configured mode
    http_client = None
    if settings.scraper_mode == "real":
        http_client = HttpClient(
            rate_limit_per_second=settings.rate_limit_per_second,
            proxies=settings.proxy_url_list or None,
        )
        real_scrapers = {
            "mozzart": lambda: MozzartScraper(http_client),
            "maxbet": lambda: MaxBetScraper(http_client),
        }
        for bm_id in settings.bookmaker_list:
            if bm_id in real_scrapers:
                scraper = real_scrapers[bm_id]()
                registry.register(scraper)
                logger.info("Registered real scraper: %s", bm_id)
            else:
                # Fall back to mock for bookmakers without real scrapers yet
                try:
                    scraper = MockScraper(bm_id)
                    registry.register(scraper)
                    logger.info("Registered mock scraper (no real scraper yet): %s", bm_id)
                except ValueError:
                    logger.warning("No scraper available for bookmaker: %s", bm_id)
    else:
        for bm_id in settings.bookmaker_list:
            try:
                scraper = MockScraper(bm_id)
                registry.register(scraper)
                logger.info("Registered scraper: %s", bm_id)
            except ValueError:
                logger.warning("No mock scraper for bookmaker: %s", bm_id)

    # Run initial scrape so there is data immediately
    try:
        await scheduler.run_cycle()
        logger.info("Initial scrape completed")
    except Exception:
        logger.exception("Initial scrape failed")

    yield

    # Shutdown
    await scheduler.stop()
    if http_client is not None:
        await http_client.close()
    await close_db()
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
