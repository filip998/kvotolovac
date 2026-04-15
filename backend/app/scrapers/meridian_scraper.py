from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_AUTH_URL = "https://auth.meridianbet.com/oauth/token"
_API_BASE = "https://online.meridianbet.com/betshop"
_EVENTS_URL = f"{_API_BASE}/api/v1/standard/sport/55/events"
_MARKETS_URL = f"{_API_BASE}/api/v2/events/{{event_id}}/markets"
_OFFER_LEAGUE_URL = f"{_API_BASE}/api/v1/offer/sport/55/league"

_CLIENT_NAME = "web-serbia"
_CLIENT_ID = "zF9zVU3LsdjvpHv"

# Universal game-group UUIDs for player prop markets
_GAME_GROUPS: dict[str, str] = {
    "player_points": "1ace0bb3-759d-41a1-8964-7dc8aac38cfe",
    "player_rebounds": "ce657e80-2e15-47b9-bbcb-871f6e597a22",
    "player_assists": "1d5c0101-d012-42dc-8d21-b3da1dfd1fd1",
}

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "sr",
    "Origin": "https://meridianbet.rs",
    "Referer": "https://meridianbet.rs/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}

_TOKEN_REFRESH_MARGIN_S = 60
_MAX_PAGES = 10
_MAX_DETAIL_CONCURRENCY = 8
_MIN_DETAIL_CONCURRENCY = 2
_MAX_OFFER_LEAGUE_IDS_PER_REQUEST = 20


def _build_basic_auth() -> str:
    """Build the rotating Basic auth header value for anonymous token request."""
    import base64

    now_utc = datetime.now(tz=timezone.utc)
    date_str = now_utc.strftime("%Y%m%d%H")
    raw = _CLIENT_ID + date_str
    hashed = hashlib.sha512(raw.encode()).hexdigest()
    creds = f"{_CLIENT_NAME}:{hashed}"
    return base64.b64encode(creds.encode()).decode()


def _parse_player_name(raw_name: str) -> str:
    """Convert 'LastName, FirstName' → 'FirstName LastName'."""
    if "," in raw_name:
        parts = raw_name.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return raw_name.strip()


def _extract_over_under_odds(selections: list[dict]) -> tuple[float | None, float | None]:
    over_odds: float | None = None
    under_odds: float | None = None

    for selection in selections:
        selection_name = str(selection.get("name", "")).lower()
        price = selection.get("price")
        if price is None:
            continue
        if "više" in selection_name or "vise" in selection_name:
            over_odds = price
        elif "manje" in selection_name:
            under_odds = price

    return over_odds, under_odds


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _is_player_market(name: str) -> bool:
    """Return True if the market name looks like a player ('LastName, FirstName')."""
    return "," in name


def _is_game_total_ot_group(name: str) -> bool:
    normalized = "".join(name.casefold().split())
    return normalized.startswith("ukupno(uklj.ot)")


def _chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _get_detail_fetch_concurrency(http_client: HttpClient, event_count: int) -> int:
    if event_count <= 0:
        return 0

    if http_client.rate_limit_per_second <= 0:
        return min(event_count, _MAX_DETAIL_CONCURRENCY)

    return min(
        event_count,
        _MAX_DETAIL_CONCURRENCY,
        max(_MIN_DETAIL_CONCURRENCY, math.ceil(http_client.rate_limit_per_second)),
    )


def _build_event_context(event: dict, *, now_epoch_ms: int) -> dict[str, object] | None:
    header = event.get("header", {})
    event_id = header.get("eventId")
    if not event_id:
        logger.debug("Meridian: skipping event without eventId")
        return None

    if header.get("state") != "ACTIVE":
        logger.debug(
            "Meridian: skipping event %s with state=%s",
            event_id,
            header.get("state"),
        )
        return None

    rivals = header.get("rivals", [])
    if len(rivals) < 2 or not rivals[0] or not rivals[1]:
        logger.debug("Meridian: skipping event %s with invalid rivals payload", event_id)
        return None

    start_epoch_ms = header.get("startTime")
    if start_epoch_ms and start_epoch_ms < now_epoch_ms:
        logger.debug(
            "Meridian: skipping past event %s with startTime=%s",
            event_id,
            start_epoch_ms,
        )
        return None

    league_info = header.get("league", {})
    return {
        "event_id": event_id,
        "home_team": rivals[0],
        "away_team": rivals[1],
        "start_time": _parse_start_time(start_epoch_ms),
        "league_id": league_info.get("slug", "basketball"),
        "league_numeric_id": league_info.get("leagueId"),
    }


def _parse_markets(
    markets_payload: list[dict],
    event_id: int,
    home_team: str,
    away_team: str,
    league_id: str,
    start_time: str | None,
    market_type: str,
) -> list[RawOddsData]:
    """Parse Meridian markets response into RawOddsData.

    Only markets whose ``name`` contains a comma (i.e. "LastName, FirstName"
    format) are treated as player props.  This filters out fallback team-total
    markets like "Ukupno (uklj.OT)" that Meridian sometimes returns when a
    game-group has no player-level data.
    """
    results: list[RawOddsData] = []

    for group in markets_payload:
        for market in group.get("markets", []):
            if market.get("state") != "ACTIVE":
                continue

            threshold = market.get("overUnder")
            if threshold is None:
                continue

            player_name = market.get("name", "")
            if not player_name or not _is_player_market(player_name):
                continue

            over_odds, under_odds = _extract_over_under_odds(market.get("selections", []))

            if over_odds is None and under_odds is None:
                continue

            results.append(
                RawOddsData(
                    bookmaker_id="meridian",
                    league_id=league_id,
                    home_team=home_team,
                    away_team=away_team,
                    market_type=market_type,
                    player_name=_parse_player_name(player_name),
                    threshold=threshold,
                    over_odds=over_odds,
                    under_odds=under_odds,
                    start_time=start_time,
                )
            )

    return results


def _parse_game_total_ot_events(
    leagues_payload: list[dict],
    *,
    now_epoch_ms: int,
) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for league in leagues_payload:
        for event in league.get("events", []):
            event_context = _build_event_context(event, now_epoch_ms=now_epoch_ms)
            if event_context is None:
                continue

            for position in event.get("positions", []):
                for group in position.get("groups", []):
                    if not _is_game_total_ot_group(str(group.get("name", ""))):
                        continue

                    threshold = group.get("overUnder")
                    if threshold is None:
                        continue

                    over_odds, under_odds = _extract_over_under_odds(group.get("selections", []))
                    if over_odds is None and under_odds is None:
                        continue

                    results.append(
                        RawOddsData(
                            bookmaker_id="meridian",
                            league_id=str(event_context["league_id"]),
                            home_team=str(event_context["home_team"]),
                            away_team=str(event_context["away_team"]),
                            market_type="game_total_ot",
                            threshold=threshold,
                            over_odds=over_odds,
                            under_odds=under_odds,
                            start_time=event_context["start_time"],
                        )
                    )

    return results


class MeridianScraper(BaseScraper):
    """Real scraper for Meridian bookmaker player prop odds."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def get_bookmaker_id(self) -> str:
        return "meridian"

    def get_bookmaker_name(self) -> str:
        return "Meridian"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _ensure_token(self) -> str:
        """Get a valid anonymous token, refreshing if needed."""
        now = time.time()
        if self._token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN_S:
            return self._token

        basic_auth = _build_basic_auth()
        headers = {
            **_DEFAULT_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic_auth}",
        }

        data = await self._http.post_json(
            _AUTH_URL,
            json_body=None,
            headers=headers,
            form_data="grant_type=general&username=&password=&locale=sr",
        )

        self._token = data["access_token"]
        expires_at_ms = data.get("expires_at", 0)
        self._token_expires_at = expires_at_ms / 1000 if expires_at_ms else now + 3500
        logger.info("Meridian: obtained anonymous token (expires in %ds)",
                     int(self._token_expires_at - now))
        return self._token

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {**_DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}

    async def _list_events(self, token: str) -> list[dict]:
        """Paginate through all basketball events."""
        all_events: list[dict] = []

        for page in range(_MAX_PAGES):
            try:
                data = await self._http.get_json(
                    _EVENTS_URL,
                    params={"page": str(page)},
                    headers=self._auth_headers(token),
                )
            except Exception:
                logger.warning("Meridian: failed to fetch events page %d", page)
                break

            events = data.get("payload", {}).get("events", [])
            if not events:
                break

            all_events.extend(events)

        return all_events

    async def _fetch_markets(
        self,
        token: str,
        event_id: int,
        game_group_id: str,
    ) -> list[dict]:
        """Fetch markets for a single event and game group."""
        url = _MARKETS_URL.format(event_id=event_id)
        try:
            data = await self._http.get_json(
                url,
                params={"gameGroupId": game_group_id},
                headers=self._auth_headers(token),
            )
            return data.get("payload", [])
        except Exception:
            logger.debug("Meridian: no markets for event %s group %s", event_id, game_group_id)
            return []

    async def _fetch_market_group(
        self,
        token: str,
        event_context: dict[str, object],
        market_type: str,
        game_group_id: str,
        semaphore: asyncio.Semaphore,
    ) -> list[RawOddsData]:
        async with semaphore:
            markets_payload = await self._fetch_markets(
                token,
                int(event_context["event_id"]),
                game_group_id,
            )

        if not markets_payload:
            return []

        return _parse_markets(
            markets_payload,
            event_id=int(event_context["event_id"]),
            home_team=str(event_context["home_team"]),
            away_team=str(event_context["away_team"]),
            league_id=str(event_context["league_id"]),
            start_time=event_context["start_time"],
            market_type=market_type,
        )

    async def _fetch_event_markets(
        self,
        token: str,
        event_context: dict[str, object],
        semaphore: asyncio.Semaphore,
    ) -> list[RawOddsData]:
        player_points = await self._fetch_market_group(
            token,
            event_context,
            "player_points",
            _GAME_GROUPS["player_points"],
            semaphore,
        )
        if not player_points:
            return []

        secondary_batches = await asyncio.gather(
            *(
                self._fetch_market_group(
                    token,
                    event_context,
                    market_type,
                    game_group_id,
                    semaphore,
                )
                for market_type, game_group_id in _GAME_GROUPS.items()
                if market_type != "player_points"
            )
        )

        results = list(player_points)
        for batch in secondary_batches:
            results.extend(batch)
        return results

    async def _fetch_game_total_ot_odds(
        self,
        token: str,
        league_ids: list[int],
        *,
        now_epoch_ms: int,
    ) -> list[RawOddsData]:
        if not league_ids:
            return []

        results: list[RawOddsData] = []

        for league_batch in _chunked(league_ids, _MAX_OFFER_LEAGUE_IDS_PER_REQUEST):
            try:
                data = await self._http.get_json(
                    _OFFER_LEAGUE_URL,
                    params={
                        "page": "0",
                        "time": "ONE_DAY",
                        "leagues": ",".join(str(league_id) for league_id in league_batch),
                    },
                    headers=self._auth_headers(token),
                )
            except Exception:
                logger.warning(
                    "Meridian: failed to fetch OT total offer batch for %d leagues",
                    len(league_batch),
                )
                continue

            results.extend(
                _parse_game_total_ot_events(
                    data.get("payload", {}).get("leagues", []),
                    now_epoch_ms=now_epoch_ms,
                )
            )

        deduped: dict[tuple[str, str, str, float], RawOddsData] = {}
        for result in results:
            deduped[
                (
                    result.league_id,
                    result.home_team,
                    result.away_team,
                    result.threshold,
                )
            ] = result
        return list(deduped.values())

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        try:
            token = await self._ensure_token()
        except Exception:
            logger.exception("Meridian: auth failed")
            return []

        events = await self._list_events(token)
        if not events:
            logger.warning("Meridian: no basketball events found")
            return []

        now_epoch_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        event_contexts = [
            context
            for context in (
                _build_event_context(event, now_epoch_ms=now_epoch_ms) for event in events
            )
            if context is not None
        ]
        if not event_contexts:
            logger.warning("Meridian: 0 candidate events after filtering %d listed events", len(events))
            return []

        offer_league_ids = sorted(
            {
                int(event_context["league_numeric_id"])
                for event_context in event_contexts
                if event_context.get("league_numeric_id") is not None
            }
        )
        game_total_ot_results = await self._fetch_game_total_ot_odds(
            token,
            offer_league_ids,
            now_epoch_ms=now_epoch_ms,
        )

        concurrency = _get_detail_fetch_concurrency(self._http, len(event_contexts))
        semaphore = asyncio.Semaphore(concurrency)
        event_results = await asyncio.gather(
            *(self._fetch_event_markets(token, event_context, semaphore) for event_context in event_contexts)
        )
        results = [item for batch in event_results for item in batch]
        results.extend(game_total_ot_results)

        logger.info(
            "Meridian scraped %d odds (%d OT totals) from %d listed events (%d candidates, detail concurrency=%d)",
            len(results),
            len(game_total_ot_results),
            len(events),
            len(event_contexts),
            concurrency,
        )
        return results
