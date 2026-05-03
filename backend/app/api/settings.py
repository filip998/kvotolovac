from __future__ import annotations

from fastapi import APIRouter

from ..models.schemas import (
    ScrapeRuntimeSettingsUpdate,
    ScrapeSettingsResponse,
)
from ..services.runtime_settings import get_scrape_settings_response
from ..services.scheduler import scheduler

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/scrape", response_model=ScrapeSettingsResponse)
async def get_scrape_settings() -> ScrapeSettingsResponse:
    return await get_scrape_settings_response()


@router.patch("/scrape", response_model=ScrapeSettingsResponse)
async def patch_scrape_settings(
    payload: ScrapeRuntimeSettingsUpdate,
) -> ScrapeSettingsResponse:
    return await scheduler.update_scrape_settings(payload)
