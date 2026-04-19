from __future__ import annotations

import asyncio
import hashlib
import logging
import re
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
_ALL_GAME_GROUP_ID = "all"

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
_PRA_PLAYER_MARKET_RE = re.compile(
    r"^(?P<player>.+?)\s+\([^)]+\)\s+Points,\s*Assists\s+and\s+Rebounds\s*$",
    re.IGNORECASE,
)


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


def _extract_player_name(raw_name: str) -> str | None:
    name = raw_name.strip()
    if not name:
        return None

    pra_match = _PRA_PLAYER_MARKET_RE.match(name)
    if pra_match:
        return pra_match.group("player").strip()

    if "," in name:
        return _parse_player_name(name)

    return None


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
    """Return True if the market name can be resolved to a player prop name."""
    return _extract_player_name(name) is not None


def _is_game_total_ot_group(name: str) -> bool:
    normalized = "".join(name.casefold().split())
    return normalized.startswith("ukupno(uklj.ot)")


def _normalize_group_name(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def _matches_supported_player_group(name: str, normalized: str, suffix: str) -> bool:
    return normalized.startswith(suffix) or ("," in name and normalized.endswith(suffix))


def _classify_supported_market_group(name: str) -> str | None:
    normalized = _normalize_group_name(name)
    if _matches_supported_player_group(name, normalized, "ukupnopoenaukljot"):
        return "player_points"
    if _matches_supported_player_group(name, normalized, "ukupnoskokovaukljot"):
        return "player_rebounds"
    if _matches_supported_player_group(name, normalized, "ukupnoasistencijaukljot"):
        return "player_assists"
    if _matches_supported_player_group(name, normalized, "ukupnopostignutihtrojkiukljot"):
        return "player_3points"
    if _PRA_PLAYER_MARKET_RE.match(name.strip()):
        return "player_points_rebounds_assists"
    if normalized.startswith("ukupnoukljot"):
        return "game_total_ot"
    return None


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

    Only markets whose ``name`` can be resolved to a player are treated as
    player props. This filters out fallback team totals and ladder/milestone
    shapes that Meridian sometimes returns alongside supported O/U markets.
    """
    results: list[RawOddsData] = []

    for group in markets_payload:
        for market in group.get("markets", []):
            if market.get("state") != "ACTIVE":
                continue

            threshold = market.get("overUnder")
            if threshold is None:
                continue

            raw_player_name = str(market.get("name", ""))
            player_name = _extract_player_name(raw_player_name)
            if player_name is None:
                continue

            over_odds, under_odds = _extract_over_under_odds(market.get("selections", []))

            if over_odds is None and under_odds is None:
                continue

            results.append(
                RawOddsData(
                    bookmaker_id="meridian",
                    league_id=league_id,
                    sport="basketball",
                    home_team=home_team,
                    away_team=away_team,
                    market_type=market_type,
                    player_name=player_name,
                    threshold=threshold,
                    over_odds=over_odds,
                    under_odds=under_odds,
                    start_time=start_time,
                )
            )

    return results


def _parse_game_total_ot_markets(
    markets_payload: list[dict],
    *,
    home_team: str,
    away_team: str,
    league_id: str,
    start_time: str | None,
) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for group in markets_payload:
        if not _is_game_total_ot_group(str(group.get("marketName", ""))):
            continue

        for market in group.get("markets", []):
            if market.get("state") != "ACTIVE":
                continue

            threshold = market.get("overUnder")
            if threshold is None:
                continue

            over_odds, under_odds = _extract_over_under_odds(market.get("selections", []))
            if over_odds is None and under_odds is None:
                continue

            results.append(
                RawOddsData(
                    bookmaker_id="meridian",
                    league_id=league_id,
                    sport="basketball",
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


def _parse_supported_markets(
    markets_payload: list[dict],
    *,
    event_id: int,
    home_team: str,
    away_team: str,
    league_id: str,
    start_time: str | None,
) -> list[RawOddsData]:
    grouped_payloads: dict[str, list[dict]] = {
        "player_points": [],
        "player_rebounds": [],
        "player_assists": [],
        "player_3points": [],
        "player_points_rebounds_assists": [],
    }
    game_total_groups: list[dict] = []

    for group in markets_payload:
        market_type = _classify_supported_market_group(str(group.get("marketName", "")))
        if market_type is None:
            continue
        if market_type == "game_total_ot":
            game_total_groups.append(group)
            continue
        grouped_payloads[market_type].append(group)

    results: list[RawOddsData] = []
    for market_type, payload in grouped_payloads.items():
        if not payload:
            continue
        results.extend(
            _parse_markets(
                payload,
                event_id=event_id,
                home_team=home_team,
                away_team=away_team,
                league_id=league_id,
                start_time=start_time,
                market_type=market_type,
            )
        )

    results.extend(
        _parse_game_total_ot_markets(
            game_total_groups,
            home_team=home_team,
            away_team=away_team,
            league_id=league_id,
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
        game_group_id: str = _ALL_GAME_GROUP_ID,
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

    async def _fetch_event_markets(
        self,
        token: str,
        event_context: dict[str, object],
    ) -> list[RawOddsData]:
        markets_payload = await self._fetch_markets(
            token,
            int(event_context["event_id"]),
        )
        if not markets_payload:
            return []

        return _parse_supported_markets(
            markets_payload,
            event_id=int(event_context["event_id"]),
            home_team=str(event_context["home_team"]),
            away_team=str(event_context["away_team"]),
            league_id=str(event_context["league_id"]),
            start_time=event_context["start_time"],
        )

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

        event_results = await asyncio.gather(
            *(self._fetch_event_markets(token, event_context) for event_context in event_contexts)
        )
        results = [item for batch in event_results for item in batch]

        logger.info(
            "Meridian scraped %d odds from %d listed events (%d candidates) via %s",
            len(results),
            len(events),
            len(event_contexts),
            _ALL_GAME_GROUP_ID,
        )
        return results
