from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models.schemas import CycleBenchmarkOut
from ..services.scraper_benchmarks import recorder

router = APIRouter(prefix="/scraper-benchmarks", tags=["scraper-benchmarks"])


@router.get("", response_model=CycleBenchmarkOut)
async def get_latest_scraper_benchmarks() -> CycleBenchmarkOut:
    """Return per-scraper aggregates for the most recent completed scrape cycle.

    Returns 404 if no cycle has completed yet (e.g. server just started)."""
    snapshot = recorder.latest()
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No scrape cycle has completed yet; benchmarks unavailable",
        )
    return snapshot
