from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    BookmakerOut,
    LeagueOut,
    ScrapeResponse,
    SystemStatus,
)
from ..scrapers.registry import registry
from ..services.scheduler import scheduler
from ..store import odds_store

router = APIRouter(tags=["system"])


@router.get("/leagues", response_model=list[LeagueOut])
async def list_leagues():
    return await odds_store.get_leagues()


@router.get("/bookmakers", response_model=list[BookmakerOut])
async def list_bookmakers():
    return await odds_store.get_bookmakers()


@router.get("/status", response_model=SystemStatus)
async def system_status():
    return await odds_store.get_system_status(
        scheduler_running=scheduler.is_running,
        scan_progress=scheduler.progress_snapshot(),
    )


@router.post("/scrape/trigger", response_model=ScrapeResponse)
async def trigger_scrape():
    if scheduler.is_cycle_in_progress:
        raise HTTPException(status_code=409, detail="Scrape already in progress")
    result = await scheduler.run_cycle()
    return ScrapeResponse(
        message="Scrape completed",
        matches_scraped=result["matches_scraped"],
        odds_scraped=result["odds_scraped"],
        discrepancies_found=result["discrepancies_found"],
    )
