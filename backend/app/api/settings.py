from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..models.schemas import (
    ScrapeRuntimeSettingsUpdate,
    ScrapeSettingsResponse,
    TelegramNotificationProfileCreate,
    TelegramNotificationProfileDeleteResponse,
    TelegramNotificationProfileOut,
    TelegramNotificationProfileUpdate,
    TelegramSettingsResponse,
    TelegramTestMessageResponse,
)
from ..services.notifications import (
    TelegramBotAPIError,
    TelegramBotClient,
    TelegramBotConfigError,
)
from ..services.runtime_settings import (
    available_bookmaker_ids,
    get_scrape_settings_response,
)
from ..services.scheduler import scheduler
from ..store import odds_store

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/scrape", response_model=ScrapeSettingsResponse)
async def get_scrape_settings() -> ScrapeSettingsResponse:
    return await get_scrape_settings_response()


@router.patch("/scrape", response_model=ScrapeSettingsResponse)
async def patch_scrape_settings(
    payload: ScrapeRuntimeSettingsUpdate,
) -> ScrapeSettingsResponse:
    return await scheduler.update_scrape_settings(payload)


@router.get("/telegram", response_model=TelegramSettingsResponse)
async def get_telegram_settings() -> TelegramSettingsResponse:
    profiles = await odds_store.list_telegram_notification_profiles()
    return TelegramSettingsResponse(
        token_configured=bool(settings.telegram_bot_token.strip()),
        api_base_url=settings.telegram_api_base_url,
        profiles=profiles,
    )


@router.post(
    "/telegram/profiles",
    response_model=TelegramNotificationProfileOut,
    status_code=201,
)
async def create_telegram_profile(
    payload: TelegramNotificationProfileCreate,
) -> TelegramNotificationProfileOut:
    _validate_telegram_profile_payload(payload)
    try:
        return await odds_store.create_telegram_notification_profile(payload)
    except odds_store.TelegramCommandProfileConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch(
    "/telegram/profiles/{profile_id}",
    response_model=TelegramNotificationProfileOut,
)
async def update_telegram_profile(
    profile_id: int,
    payload: TelegramNotificationProfileUpdate,
) -> TelegramNotificationProfileOut:
    _validate_telegram_profile_payload(payload)
    try:
        profile = await odds_store.update_telegram_notification_profile(profile_id, payload)
    except odds_store.TelegramCommandProfileConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if profile is None:
        raise HTTPException(status_code=404, detail="Telegram profile not found")
    return profile


@router.delete(
    "/telegram/profiles/{profile_id}",
    response_model=TelegramNotificationProfileDeleteResponse,
)
async def delete_telegram_profile(
    profile_id: int,
) -> TelegramNotificationProfileDeleteResponse:
    deleted = await odds_store.delete_telegram_notification_profile(profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Telegram profile not found")
    return TelegramNotificationProfileDeleteResponse(profile_id=profile_id, deleted=True)


@router.post(
    "/telegram/profiles/{profile_id}/test",
    response_model=TelegramTestMessageResponse,
)
async def send_telegram_test_message(profile_id: int) -> TelegramTestMessageResponse:
    profile = await odds_store.get_telegram_notification_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Telegram profile not found")

    client = TelegramBotClient(
        token=settings.telegram_bot_token,
        api_base_url=settings.telegram_api_base_url,
    )
    try:
        result = await client.send_message(
            chat_id=profile.chat_id,
            text=f"<b>KvotoLovac test</b>\nProfile: {html.escape(profile.label)}",
        )
    except TelegramBotConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TelegramBotAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TelegramTestMessageResponse(
        profile_id=profile.id,
        ok=True,
        message_id=result.message_id,
    )


def _validate_telegram_profile_payload(
    payload: TelegramNotificationProfileCreate | TelegramNotificationProfileUpdate,
) -> None:
    values = payload.model_dump(exclude_unset=True)
    for field_name in ("label", "chat_id"):
        value = values.get(field_name)
        if value is not None and not str(value).strip():
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} must not be blank",
            )

    bookmaker_ids = values.get("bookmaker_ids")
    if bookmaker_ids is None:
        return
    allowed_bookmakers = set(available_bookmaker_ids())
    unknown_bookmakers = sorted(
        {
            str(bookmaker_id).strip()
            for bookmaker_id in bookmaker_ids
            if str(bookmaker_id).strip()
        }
        - allowed_bookmakers
    )
    if unknown_bookmakers:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown bookmaker ids: {', '.join(unknown_bookmakers)}",
        )
