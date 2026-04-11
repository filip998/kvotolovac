from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_LIST_URL = "https://sports-sm-distribution-api.de-2.nsoftcdn.com/api/v1/events"
_DETAIL_URL = "https://sports-sm-distribution-api.de-2.nsoftcdn.com/api/v1/events/{event_id}"

_COMPANY_UUID = "4f54c6aa-82a9-475d-bf0e-dc02ded89225"
_PLAYER_POINTS_MARKET_ID = 2402

_LIST_DATA_FORMAT = '{"default":"object","events":"array","outcomes":"array"}'
_LIST_LANGUAGE = (
    '{"default":"sr-Latn","events":"sr-Latn","sport":"sr-Latn",'
    '"category":"sr-Latn","tournament":"sr-Latn","team":"sr-Latn","market":"sr-Latn"}'
)
_DETAIL_DATA_FORMAT = '{"default":"array","markets":"array","events":"array"}'

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://sports-sm-web.7platform.net",
    "Referer": "https://sports-sm-web.7platform.net/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}

_DEFAULT_PARAMS = {
    "deliveryPlatformId": "3",
    "companyUuid": _COMPANY_UUID,
    "filter[sportId]": "273",
    "sort": "categoryPosition,categoryName,tournamentPosition,tournamentName,startsAt",
    "offerTemplate": "WEB_OVERVIEW",
    "shortProps": "1",
    "dataFormat": _LIST_DATA_FORMAT,
    "language": _LIST_LANGUAGE,
    "timezone": "Europe/Belgrade",
}

_UNLIMITED_DETAIL_CONCURRENCY = 10
_MIN_DETAIL_CONCURRENCY = 2
_REQUEST_TIMEZONE = ZoneInfo("Europe/Belgrade")

_PLAYER_NAME_RE = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*$")


def _format_filter_from(dt: datetime | None = None) -> str:
    """Return BalkanBet's accepted naive Belgrade-local timestamp format."""
    if dt is None:
        dt = datetime.now(tz=timezone.utc)
    return dt.astimezone(_REQUEST_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_player_name(name: str) -> tuple[str, str | None]:
    """Split 'A.Plummer (Bosna)' into ('A.Plummer', 'Bosna')."""
    if not name:
        return (name, None)
    m = _PLAYER_NAME_RE.match(name)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return (name.strip(), None)


def _get_event_ids(data: dict) -> list[int]:
    """Extract event IDs that have a player-points market from list response."""
    events = data.get("data", {}).get("events", [])
    ids: list[int] = []
    for ev in events:
        markets = ev.get("o") or {}
        has_player_points = any(
            (mk.get("b") == _PLAYER_POINTS_MARKET_ID)
            for mk in (markets.values() if isinstance(markets, dict) else markets)
        )
        if not has_player_points:
            continue
        event_id = ev.get("a")
        if event_id:
            ids.append(int(event_id))
    return ids


def _normalize_start_time(raw: str | None) -> str | None:
    """Convert an ISO-8601 timestamp to the canonical ``+00:00`` format.

    The BalkanBet API returns ``2026-04-11T16:00:00.000Z`` but other scrapers
    produce ``2026-04-11T16:00:00+00:00``.  The normalizer compares start
    times as strings, so the format must match.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.isoformat()
    except (ValueError, TypeError):
        return raw


def _parse_event_detail(data: dict) -> list[RawOddsData]:
    """Parse a single event detail response into RawOddsData entries."""
    results: list[RawOddsData] = []

    detail = data.get("data", data)
    if not detail:
        return results

    raw_name = detail.get("name", "")
    player_name, team = _parse_player_name(raw_name)
    start_time = _normalize_start_time(detail.get("startsAt"))

    markets = detail.get("markets") or []
    for mkt in markets:
        if mkt.get("marketId") != _PLAYER_POINTS_MARKET_ID:
            continue

        special_values = mkt.get("specialValues") or []
        if not special_values:
            continue

        try:
            threshold = float(special_values[0])
        except (ValueError, TypeError, IndexError):
            continue

        outcomes = mkt.get("outcomes") or []
        over_odds: float | None = None
        under_odds: float | None = None
        for oc in outcomes:
            oc_name = (oc.get("name") or "").lower()
            if oc_name == "više":
                over_odds = oc.get("odds")
            elif oc_name == "manje":
                under_odds = oc.get("odds")

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="balkanbet",
                league_id="basketball",
                home_team=team or "",
                away_team=player_name,
                market_type="player_points",
                player_name=player_name,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=start_time,
            )
        )

    return results


def _get_detail_fetch_concurrency(http_client: HttpClient, event_count: int) -> int:
    if event_count <= 0:
        return 0
    if http_client.rate_limit_per_second <= 0:
        return min(event_count, _UNLIMITED_DETAIL_CONCURRENCY)
    return min(
        event_count,
        max(_MIN_DETAIL_CONCURRENCY, math.ceil(http_client.rate_limit_per_second)),
    )


class BalkanBetScraper(BaseScraper):
    """Scraper for BalkanBet basketball player points."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "balkanbet"

    def get_bookmaker_name(self) -> str:
        return "BalkanBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_event_detail(
        self,
        event_id: int,
        semaphore: asyncio.Semaphore,
    ) -> list[RawOddsData]:
        async with semaphore:
            try:
                detail = await self._http.get_json(
                    _DETAIL_URL.format(event_id=event_id),
                    params={
                        "companyUuid": _COMPANY_UUID,
                        "id": str(event_id),
                        "language": _LIST_LANGUAGE,
                        "timezone": "Europe/Belgrade",
                        "dataFormat": _DETAIL_DATA_FORMAT,
                    },
                    headers=_DEFAULT_HEADERS,
                )
            except Exception:
                logger.warning("BalkanBet: failed to fetch detail for event %s", event_id)
                return []

        return _parse_event_detail(detail)

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        now_iso = _format_filter_from()
        list_params = {**_DEFAULT_PARAMS, "filter[from]": now_iso}

        try:
            data = await self._http.get_json(
                _LIST_URL,
                params=list_params,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("BalkanBet list scrape failed")
            return []

        event_ids = _get_event_ids(data)
        if not event_ids:
            logger.warning("BalkanBet: no player-points events found")
            return []

        concurrency = _get_detail_fetch_concurrency(self._http, len(event_ids))
        semaphore = asyncio.Semaphore(concurrency)
        detail_results = await asyncio.gather(
            *(self._fetch_event_detail(eid, semaphore) for eid in event_ids)
        )
        results = [item for batch in detail_results for item in batch]

        logger.info(
            "BalkanBet scraped %d player odds from %d events (detail concurrency=%d)",
            len(results), len(event_ids), concurrency,
        )
        return results
