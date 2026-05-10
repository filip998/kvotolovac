from __future__ import annotations

import asyncio
import json
import logging
from typing import Iterable

from fastapi import HTTPException
from pydantic import ValidationError

from ..config import settings
from ..database import get_db
from ..models.schemas import (
    ScrapeRuntimeSettings,
    ScrapeRuntimeSettingsUpdate,
    ScrapeSettingsBookmakerOption,
    ScrapeSettingsMarketOption,
    ScrapeSettingsOptions,
    ScrapeSettingsResponse,
)
from ..scrapers.registry import registry
from .market_allowlist import (
    ANALYSIS_MARKET_OPTIONS,
    DEFAULT_ANALYSIS_MARKETS,
    MarketAllowlistError,
    legacy_analysis_markets_for_scope,
    normalize_analysis_markets,
)


logger = logging.getLogger(__name__)

_SETTINGS_ROW_ID = 1
_SUPPORTED_SPORTS = ("basketball", "football", "tennis")
_SETTINGS_ROLLOUT_VERSION_KEY = "_enabled_sports_rollout_version"
_SETTINGS_ROLLOUT_VERSION = 1
_ROLLOUT_ENABLED_SPORTS = ("tennis",)
_SCRAPE_INTERVAL_MINUTES_MAX = 24 * 60
_SCRAPE_LOOKAHEAD_HOURS_MAX = 24 * 365 * 10
_MAX_MIDDLE_OPPORTUNITIES_PER_MARKET_MAX = 1000
_MIN_FITTED_MIDDLE_EV_PERCENT_MAX = 100.0
_RATE_LIMIT_PER_SECOND_MAX = 20.0
_SETTINGS_LOCK: asyncio.Lock | None = None
_SETTINGS_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


def _get_settings_lock() -> asyncio.Lock:
    global _SETTINGS_LOCK, _SETTINGS_LOCK_LOOP

    loop = asyncio.get_running_loop()
    if _SETTINGS_LOCK is None or _SETTINGS_LOCK_LOOP is not loop:
        _SETTINGS_LOCK = asyncio.Lock()
        _SETTINGS_LOCK_LOOP = loop
    return _SETTINGS_LOCK


def default_scrape_runtime_settings(
    *,
    prefer_registered_bookmakers: bool = True,
) -> ScrapeRuntimeSettings:
    configured_bookmakers = settings.bookmaker_list
    registered_bookmakers = registry.get_ids()
    if prefer_registered_bookmakers and registered_bookmakers:
        enabled_bookmakers = registered_bookmakers
    else:
        enabled_bookmakers = configured_bookmakers
    return ScrapeRuntimeSettings(
        enabled_bookmakers=enabled_bookmakers,
        enabled_sports=settings.enabled_sport_list,
        scrape_market_scope=settings.scrape_market_scope,
        analysis_markets=list(
            normalize_analysis_markets(
                settings.analysis_markets,
                legacy_scrape_market_scope=settings.scrape_market_scope,
            )
        ),
        scrape_lookahead_hours=settings.scrape_lookahead_hours,
        scrape_interval_minutes=settings.scrape_interval_minutes,
        max_middle_opportunities_per_market=settings.max_middle_opportunities_per_market,
        enable_fitted_middles=settings.enable_fitted_middles,
        min_fitted_middle_ev_percent=settings.min_fitted_middle_ev_percent,
        rate_limit_per_second=settings.rate_limit_per_second,
        meridian_rate_limit_per_second=settings.meridian_rate_limit_per_second,
        soccerbet_detail_mode=settings.soccerbet_detail_mode,
        merkurxtip_detail_mode=settings.merkurxtip_detail_mode,
        pinnbet_detail_mode=settings.pinnbet_detail_mode,
        betole_detail_mode=settings.betole_detail_mode,
        notification_gap_threshold=settings.notification_gap_threshold,
        persist_inapp_notifications=settings.persist_inapp_notifications,
    )


def _settings_json(values: ScrapeRuntimeSettings) -> str:
    payload = values.model_dump()
    payload[_SETTINGS_ROLLOUT_VERSION_KEY] = _SETTINGS_ROLLOUT_VERSION
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _settings_from_json(raw: str) -> ScrapeRuntimeSettings:
    try:
        payload = json.loads(raw)
        apply_enabled_sports_rollout = (
            isinstance(payload, dict)
            and payload.get(_SETTINGS_ROLLOUT_VERSION_KEY) != _SETTINGS_ROLLOUT_VERSION
        )
        if isinstance(payload, dict) and "analysis_markets" not in payload:
            payload["analysis_markets"] = list(
                legacy_analysis_markets_for_scope(
                    payload.get("scrape_market_scope", "all")
                )
            )
        values = ScrapeRuntimeSettings.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValidationError):
        logger.warning("Ignoring invalid persisted scrape runtime settings")
        return default_scrape_runtime_settings()
    values = _sanitize_persisted_scrape_runtime_settings(values)
    if apply_enabled_sports_rollout:
        values = _with_rollout_enabled_sports(values)
    return values


def _unique_ordered(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _with_rollout_enabled_sports(
    values: ScrapeRuntimeSettings,
) -> ScrapeRuntimeSettings:
    allowed_sports = set(_available_sports())
    enabled_sports = _unique_ordered(
        [
            *values.enabled_sports,
            *(sport for sport in _ROLLOUT_ENABLED_SPORTS if sport in allowed_sports),
        ]
    )
    return values.model_copy(update={"enabled_sports": enabled_sports})


def _registered_bookmaker_names() -> dict[str, str]:
    return {
        scraper.get_bookmaker_id(): scraper.get_bookmaker_name()
        for scraper in registry.get_all()
    }


def available_bookmaker_ids() -> list[str]:
    return sorted(set(settings.bookmaker_list) | set(registry.get_ids()))


def _available_sports() -> list[str]:
    sports = set(_SUPPORTED_SPORTS)
    sports.update(settings.enabled_sport_list)
    for scraper in registry.get_all():
        for capability in scraper.get_scraper_capabilities():
            sports.add(capability.sport)
    return sorted(sports)


def validate_scrape_runtime_settings(
    values: ScrapeRuntimeSettings,
) -> ScrapeRuntimeSettings:
    normalized = values.model_copy(
        update={
            "enabled_bookmakers": _unique_ordered(values.enabled_bookmakers),
            "enabled_sports": _unique_ordered(values.enabled_sports),
        }
    )
    try:
        analysis_markets = list(
            normalize_analysis_markets(
                normalized.analysis_markets,
                legacy_scrape_market_scope=normalized.scrape_market_scope,
            )
        )
    except MarketAllowlistError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized = normalized.model_copy(update={"analysis_markets": analysis_markets})
    allowed_bookmakers = set(available_bookmaker_ids())
    unknown_bookmakers = sorted(set(normalized.enabled_bookmakers) - allowed_bookmakers)
    if unknown_bookmakers:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown bookmaker ids: {', '.join(unknown_bookmakers)}",
        )

    allowed_sports = set(_available_sports())
    unknown_sports = sorted(set(normalized.enabled_sports) - allowed_sports)
    if unknown_sports:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported sports: {', '.join(unknown_sports)}",
        )
    analysis_market_sports = {
        token.split(":", 1)[0]
        for token in normalized.analysis_markets
        if token != DEFAULT_ANALYSIS_MARKETS[0]
    }
    unknown_analysis_market_sports = sorted(
        analysis_market_sports - allowed_sports - {"*"}
    )
    if unknown_analysis_market_sports:
        raise HTTPException(
            status_code=422,
            detail=(
                "Unsupported analysis market sports: "
                + ", ".join(unknown_analysis_market_sports)
            ),
        )

    _validate_range(
        "scrape_interval_minutes",
        normalized.scrape_interval_minutes,
        minimum=1,
        maximum=_SCRAPE_INTERVAL_MINUTES_MAX,
    )
    _validate_range(
        "scrape_lookahead_hours",
        normalized.scrape_lookahead_hours,
        minimum=0,
        maximum=_SCRAPE_LOOKAHEAD_HOURS_MAX,
    )
    _validate_range(
        "max_middle_opportunities_per_market",
        normalized.max_middle_opportunities_per_market,
        minimum=1,
        maximum=_MAX_MIDDLE_OPPORTUNITIES_PER_MARKET_MAX,
    )
    _validate_range(
        "min_fitted_middle_ev_percent",
        normalized.min_fitted_middle_ev_percent,
        minimum=0,
        maximum=_MIN_FITTED_MIDDLE_EV_PERCENT_MAX,
    )
    _validate_range(
        "rate_limit_per_second",
        normalized.rate_limit_per_second,
        minimum=0,
        maximum=_RATE_LIMIT_PER_SECOND_MAX,
    )
    _validate_range(
        "meridian_rate_limit_per_second",
        normalized.meridian_rate_limit_per_second,
        minimum=0,
        maximum=_RATE_LIMIT_PER_SECOND_MAX,
    )
    return normalized


def _sanitize_persisted_scrape_runtime_settings(
    values: ScrapeRuntimeSettings,
) -> ScrapeRuntimeSettings:
    allowed_bookmakers = set(available_bookmaker_ids())
    enabled_bookmakers = [
        bookmaker_id
        for bookmaker_id in _unique_ordered(values.enabled_bookmakers)
        if bookmaker_id in allowed_bookmakers
    ]

    allowed_sports = set(_available_sports())
    enabled_sports = [
        sport for sport in _unique_ordered(values.enabled_sports) if sport in allowed_sports
    ]
    try:
        analysis_markets = list(
            normalize_analysis_markets(
                values.analysis_markets,
                legacy_scrape_market_scope=values.scrape_market_scope,
            )
        )
    except MarketAllowlistError:
        logger.warning("Ignoring invalid persisted analysis market filters")
        analysis_markets = list(DEFAULT_ANALYSIS_MARKETS)

    return values.model_copy(
        update={
            "enabled_bookmakers": enabled_bookmakers,
            "enabled_sports": enabled_sports,
            "analysis_markets": analysis_markets,
            "scrape_interval_minutes": _clamp(
                values.scrape_interval_minutes,
                minimum=1,
                maximum=_SCRAPE_INTERVAL_MINUTES_MAX,
            ),
            "scrape_lookahead_hours": _clamp(
                values.scrape_lookahead_hours,
                minimum=0,
                maximum=_SCRAPE_LOOKAHEAD_HOURS_MAX,
            ),
            "max_middle_opportunities_per_market": _clamp(
                values.max_middle_opportunities_per_market,
                minimum=1,
                maximum=_MAX_MIDDLE_OPPORTUNITIES_PER_MARKET_MAX,
            ),
            "min_fitted_middle_ev_percent": _clamp(
                values.min_fitted_middle_ev_percent,
                minimum=0,
                maximum=_MIN_FITTED_MIDDLE_EV_PERCENT_MAX,
            ),
            "rate_limit_per_second": _clamp(
                values.rate_limit_per_second,
                minimum=0,
                maximum=_RATE_LIMIT_PER_SECOND_MAX,
            ),
            "meridian_rate_limit_per_second": _clamp(
                values.meridian_rate_limit_per_second,
                minimum=0,
                maximum=_RATE_LIMIT_PER_SECOND_MAX,
            ),
        }
    )


def _clamp(
    value: int | float,
    *,
    minimum: int | float,
    maximum: int | float,
) -> int | float:
    return max(minimum, min(value, maximum))


def _validate_range(
    field_name: str,
    value: int | float,
    *,
    minimum: int | float,
    maximum: int | float,
) -> None:
    if value < minimum or value > maximum:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be between {minimum} and {maximum}",
        )


async def _ensure_settings_row_unlocked() -> None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id FROM runtime_scrape_settings WHERE id = ?",
        (_SETTINGS_ROW_ID,),
    )
    if rows:
        return
    defaults = validate_scrape_runtime_settings(default_scrape_runtime_settings())
    await db.execute(
        """INSERT INTO runtime_scrape_settings (
               id,
               applied_config,
               applied_at,
               updated_at
           ) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        (_SETTINGS_ROW_ID, _settings_json(defaults)),
    )
    await db.commit()


async def _ensure_settings_row() -> None:
    async with _get_settings_lock():
        await _ensure_settings_row_unlocked()


async def ensure_scrape_settings_seeded() -> None:
    await _ensure_settings_row()


async def _load_settings_row_unlocked() -> tuple[
    ScrapeRuntimeSettings,
    ScrapeRuntimeSettings | None,
    str | None,
    str | None,
]:
    await _ensure_settings_row_unlocked()
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT applied_config, pending_config, applied_at, pending_at
           FROM runtime_scrape_settings
           WHERE id = ?""",
        (_SETTINGS_ROW_ID,),
    )
    row = rows[0]
    applied = _settings_from_json(row["applied_config"])
    pending = _settings_from_json(row["pending_config"]) if row["pending_config"] else None
    return applied, pending, row["applied_at"], row["pending_at"]


async def _load_settings_row() -> tuple[
    ScrapeRuntimeSettings,
    ScrapeRuntimeSettings | None,
    str | None,
    str | None,
]:
    async with _get_settings_lock():
        return await _load_settings_row_unlocked()


async def get_applied_scrape_settings() -> ScrapeRuntimeSettings:
    applied, _, _, _ = await _load_settings_row()
    return applied


async def get_scrape_settings_response(
    *,
    applied_immediately: bool = False,
) -> ScrapeSettingsResponse:
    applied, pending, applied_at, pending_at = await _load_settings_row()
    return _settings_response(
        applied=applied,
        pending=pending,
        applied_at=applied_at,
        pending_at=pending_at,
        applied_immediately=applied_immediately,
    )


async def update_scrape_settings(
    patch: ScrapeRuntimeSettingsUpdate,
    *,
    apply_immediately: bool,
) -> ScrapeSettingsResponse:
    async with _get_settings_lock():
        applied, pending, _, _ = await _load_settings_row_unlocked()
        base = pending or applied
        update_data = patch.model_dump(exclude_unset=True, exclude_none=True)
        if "scrape_market_scope" in update_data and "analysis_markets" not in update_data:
            update_data["analysis_markets"] = list(
                legacy_analysis_markets_for_scope(update_data["scrape_market_scope"])
            )
        candidate = validate_scrape_runtime_settings(base.model_copy(update=update_data))

        db = await get_db()
        if apply_immediately:
            await db.execute(
                """UPDATE runtime_scrape_settings
                   SET applied_config = ?,
                       pending_config = NULL,
                       applied_at = CURRENT_TIMESTAMP,
                       pending_at = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (_settings_json(candidate), _SETTINGS_ROW_ID),
            )
        else:
            await db.execute(
                """UPDATE runtime_scrape_settings
                   SET pending_config = ?,
                       pending_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (_settings_json(candidate), _SETTINGS_ROW_ID),
            )
        await db.commit()
    return await get_scrape_settings_response(applied_immediately=apply_immediately)


async def promote_pending_scrape_settings() -> ScrapeRuntimeSettings:
    async with _get_settings_lock():
        await _ensure_settings_row_unlocked()
        db = await get_db()
        rows = await db.execute_fetchall(
            """SELECT applied_config, pending_config
               FROM runtime_scrape_settings
               WHERE id = ?""",
            (_SETTINGS_ROW_ID,),
        )
        row = rows[0]
        applied = _settings_from_json(row["applied_config"])
        pending_raw = row["pending_config"]
        if not pending_raw:
            return applied

        pending = _settings_from_json(pending_raw)
        pending_json = _settings_json(pending)
        await db.execute(
            """UPDATE runtime_scrape_settings
               SET applied_config = ?,
                   pending_config = NULL,
                   applied_at = CURRENT_TIMESTAMP,
                   pending_at = NULL,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?
                 AND pending_config = ?""",
            (pending_json, _SETTINGS_ROW_ID, pending_raw),
        )
        await db.commit()
        return pending


def _settings_response(
    *,
    applied: ScrapeRuntimeSettings,
    pending: ScrapeRuntimeSettings | None,
    applied_at: str | None,
    pending_at: str | None,
    applied_immediately: bool,
) -> ScrapeSettingsResponse:
    defaults = _with_rollout_enabled_sports(
        default_scrape_runtime_settings(prefer_registered_bookmakers=False)
    )
    return ScrapeSettingsResponse(
        applied=applied,
        pending=pending,
        defaults=validate_scrape_runtime_settings(defaults),
        has_pending_changes=pending is not None,
        applied_at=applied_at,
        pending_at=pending_at,
        applied_immediately=applied_immediately,
        options=_settings_options(applied=pending or applied),
    )


def _settings_options(*, applied: ScrapeRuntimeSettings) -> ScrapeSettingsOptions:
    names = _registered_bookmaker_names()
    enabled = set(applied.enabled_bookmakers)
    bookmakers = [
        ScrapeSettingsBookmakerOption(
            id=bookmaker_id,
            name=names.get(bookmaker_id, bookmaker_id),
            enabled=bookmaker_id in enabled,
        )
        for bookmaker_id in available_bookmaker_ids()
    ]
    bookmakers.sort(key=lambda item: (item.name.lower(), item.id))
    return ScrapeSettingsOptions(
        bookmakers=bookmakers,
        sports=_available_sports(),
        analysis_market_options=[
            ScrapeSettingsMarketOption(
                token=option.token,
                label=option.label,
                sport=option.sport,
            )
            for option in ANALYSIS_MARKET_OPTIONS
        ],
        scrape_interval_minutes_max=_SCRAPE_INTERVAL_MINUTES_MAX,
        scrape_lookahead_hours_max=_SCRAPE_LOOKAHEAD_HOURS_MAX,
        max_middle_opportunities_per_market_max=_MAX_MIDDLE_OPPORTUNITIES_PER_MARKET_MAX,
        min_fitted_middle_ev_percent_max=_MIN_FITTED_MIDDLE_EV_PERCENT_MAX,
        rate_limit_per_second_max=_RATE_LIMIT_PER_SECOND_MAX,
    )
