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
_BASKETBALL_SPORT_ID = "36"
_PLAYER_SPORT_ID = "273"
_GAME_TOTAL_OT_MARKET_ID = 530
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

_BASE_LIST_PARAMS = {
    "deliveryPlatformId": "3",
    "companyUuid": _COMPANY_UUID,
    "sort": "categoryPosition,categoryName,tournamentPosition,tournamentName,startsAt",
    "offerTemplate": "WEB_OVERVIEW",
    "shortProps": "1",
    "dataFormat": _LIST_DATA_FORMAT,
    "language": _LIST_LANGUAGE,
    "timezone": "Europe/Belgrade",
}
_PLAYER_LIST_PARAMS = {**_BASE_LIST_PARAMS, "filter[sportId]": _PLAYER_SPORT_ID}
_GAME_TOTAL_OT_LIST_PARAMS = {
    **_BASE_LIST_PARAMS,
    "filter[sportId]": _BASKETBALL_SPORT_ID,
}

_UNLIMITED_DETAIL_CONCURRENCY = 10
_MIN_DETAIL_CONCURRENCY = 2
_REQUEST_TIMEZONE = ZoneInfo("Europe/Belgrade")
_TOURNAMENT_LEAGUE_MAP: dict[int, str] = {
    252: "euroleague",
    29368: "aba_liga",
    30757: "turkey",
    31317: "italy",
    31353: "germany",
}

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


def _iter_list_markets(event: dict) -> list[dict]:
    markets = event.get("o") or {}
    if isinstance(markets, dict):
        return [market for market in markets.values() if isinstance(market, dict)]
    if isinstance(markets, list):
        return [market for market in markets if isinstance(market, dict)]
    return []


def _get_events_with_market_id(data: dict, market_id: int) -> list[dict]:
    events = data.get("data", {}).get("events", [])
    matching_events: list[dict] = []
    for event in events:
        if any(market.get("b") == market_id for market in _iter_list_markets(event)):
            matching_events.append(event)
    return matching_events


def _get_event_ids(data: dict) -> list[int]:
    """Extract event IDs that have a player-points market from list response."""
    ids: list[int] = []
    for ev in _get_events_with_market_id(data, _PLAYER_POINTS_MARKET_ID):
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


def _extract_league_id(category_id: int | None, tournament_id: int | None) -> str:
    if tournament_id in _TOURNAMENT_LEAGUE_MAP:
        return _TOURNAMENT_LEAGUE_MAP[tournament_id]
    if tournament_id is not None:
        return f"balkanbet_tournament_{tournament_id}"
    if category_id is not None:
        return f"balkanbet_category_{category_id}"
    return "basketball"


def _extract_outcome_price(outcome: dict) -> float | None:
    for key in ("odd", "odds", "g"):
        value = outcome.get(key)
        if value is not None:
            return value
    return None


def _extract_over_under_odds(outcomes: list[dict]) -> tuple[float | None, float | None]:
    over_odds: float | None = None
    under_odds: float | None = None
    for outcome in outcomes:
        outcome_name = (outcome.get("name") or outcome.get("e") or "").lower()
        outcome_price = _extract_outcome_price(outcome)
        if outcome_name.startswith("više"):
            over_odds = outcome_price
        elif outcome_name.startswith("manje"):
            under_odds = outcome_price
    return over_odds, under_odds


def _parse_event_detail(data: dict, league_id: str | None = None) -> list[RawOddsData]:
    """Parse a single event detail response into RawOddsData entries."""
    results: list[RawOddsData] = []

    detail = data.get("data", data)
    if not detail:
        return results

    raw_name = detail.get("name", "")
    player_name, team = _parse_player_name(raw_name)
    start_time = _normalize_start_time(detail.get("startsAt"))
    effective_league_id = league_id or _extract_league_id(
        detail.get("categoryId"),
        detail.get("tournamentId"),
    )

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
        over_odds, under_odds = _extract_over_under_odds(outcomes)

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="balkanbet",
                league_id=effective_league_id,
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


def _split_match_name(name: str) -> tuple[str, str] | None:
    home_team, separator, away_team = name.partition(" - ")
    if not separator:
        return None
    home_team = home_team.strip()
    away_team = away_team.strip()
    if not home_team or not away_team:
        return None
    return home_team, away_team


def _parse_game_total_ot_list(data: dict) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for event in _get_events_with_market_id(data, _GAME_TOTAL_OT_MARKET_ID):
        matchup = _split_match_name(event.get("j") or event.get("name") or "")
        if matchup is None:
            continue

        home_team, away_team = matchup
        start_time = _normalize_start_time(event.get("n") or event.get("startsAt"))
        league_id = _extract_league_id(event.get("c"), event.get("f"))

        for market in _iter_list_markets(event):
            if market.get("b") != _GAME_TOTAL_OT_MARKET_ID:
                continue

            special_values = market.get("g") or market.get("specialValues") or []
            if not special_values:
                continue

            try:
                threshold = float(special_values[0])
            except (ValueError, TypeError, IndexError):
                continue

            outcomes = market.get("h") or market.get("outcomes") or []
            over_odds, under_odds = _extract_over_under_odds(outcomes)
            if over_odds is None and under_odds is None:
                continue

            results.append(
                RawOddsData(
                    bookmaker_id="balkanbet",
                    league_id=league_id,
                    home_team=home_team,
                    away_team=away_team,
                    market_type="game_total_ot",
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
    """Scraper for BalkanBet basketball player points and OT-inclusive totals."""

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

    async def _fetch_list(self, params: dict, label: str) -> dict:
        try:
            return await self._http.get_json(
                _LIST_URL,
                params=params,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("BalkanBet: failed to fetch %s list", label, exc_info=True)
            return {}

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        now_iso = _format_filter_from()
        player_list_params = {**_PLAYER_LIST_PARAMS, "filter[from]": now_iso}
        total_list_params = {**_GAME_TOTAL_OT_LIST_PARAMS, "filter[from]": now_iso}

        player_data, total_data = await asyncio.gather(
            self._fetch_list(player_list_params, "player-points"),
            self._fetch_list(total_list_params, "game-total-ot"),
        )

        player_event_ids = _get_event_ids(player_data)
        player_results: list[RawOddsData] = []
        if player_event_ids:
            concurrency = _get_detail_fetch_concurrency(self._http, len(player_event_ids))
            semaphore = asyncio.Semaphore(concurrency)
            detail_results = await asyncio.gather(
                *(self._fetch_event_detail(eid, semaphore) for eid in player_event_ids)
            )
            player_results = [item for batch in detail_results for item in batch]
        else:
            concurrency = 0

        ot_total_events = _get_events_with_market_id(total_data, _GAME_TOTAL_OT_MARKET_ID)
        total_results = _parse_game_total_ot_list(total_data)
        results = [*player_results, *total_results]

        logger.info(
            (
                "BalkanBet scraped %d player odds from %d player events "
                "(detail concurrency=%d) and %d OT total odds from %d basketball events"
            ),
            len(player_results),
            len(player_event_ids),
            concurrency,
            len(total_results),
            len(ot_total_events),
        )
        return results
