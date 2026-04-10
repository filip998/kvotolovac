from __future__ import annotations

import hashlib
import logging
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


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _is_player_market(name: str) -> bool:
    """Return True if the market name looks like a player ('LastName, FirstName')."""
    return "," in name


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

            over_odds: float | None = None
            under_odds: float | None = None

            for sel in market.get("selections", []):
                sel_name = sel.get("name", "").lower()
                price = sel.get("price")
                if "više" in sel_name or "vise" in sel_name:
                    over_odds = price
                elif "manje" in sel_name:
                    under_odds = price

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

        results: list[RawOddsData] = []

        for event in events:
            header = event.get("header", {})
            event_id = header.get("eventId")
            if not event_id:
                continue

            rivals = header.get("rivals", [])
            home_team = rivals[0] if len(rivals) > 0 else ""
            away_team = rivals[1] if len(rivals) > 1 else ""
            start_time = _parse_start_time(header.get("startTime"))

            league_info = header.get("league", {})
            event_league = league_info.get("slug", "basketball")

            # Fetch player_points first; if the event has no player props
            # at all, skip rebounds/assists to avoid wasted requests.
            has_player_props = False

            for market_type, game_group_id in _GAME_GROUPS.items():
                if not has_player_props and market_type != "player_points":
                    continue

                markets_payload = await self._fetch_markets(token, event_id, game_group_id)
                if not markets_payload:
                    continue

                parsed = _parse_markets(
                    markets_payload,
                    event_id,
                    home_team,
                    away_team,
                    event_league,
                    start_time,
                    market_type,
                )

                if parsed and market_type == "player_points":
                    has_player_props = True

                results.extend(parsed)

        logger.info(
            "Meridian scraped %d player odds from %d events",
            len(results), len(events),
        )
        return results
