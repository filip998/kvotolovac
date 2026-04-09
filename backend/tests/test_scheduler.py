from __future__ import annotations

import pytest

from app.services.scheduler import Scheduler
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import ScraperRegistry, registry


@pytest.fixture(autouse=True)
def register_scrapers():
    registry._scrapers.clear()
    for bm in ("mozzart", "meridian", "maxbet"):
        registry.register(MockScraper(bm))
    yield
    registry._scrapers.clear()


@pytest.mark.asyncio
async def test_scheduler_config():
    s = Scheduler(interval_minutes=5)
    assert s.interval_minutes == 5
    assert not s.is_running


@pytest.mark.asyncio
async def test_scheduler_run_cycle():
    s = Scheduler(interval_minutes=1)
    result = await s.run_cycle()
    assert result["matches_scraped"] > 0
    assert result["odds_scraped"] > 0
    assert result["discrepancies_found"] > 0


@pytest.mark.asyncio
async def test_scheduler_start_stop():
    s = Scheduler(interval_minutes=60)
    await s.start()
    assert s.is_running
    await s.stop()
    assert not s.is_running


@pytest.mark.asyncio
async def test_scheduler_double_start():
    s = Scheduler(interval_minutes=60)
    await s.start()
    await s.start()  # should not raise
    assert s.is_running
    await s.stop()
