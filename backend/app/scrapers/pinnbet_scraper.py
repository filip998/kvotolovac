from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_BASE_LIST_URL = (
    "https://sportweb.pinnbet.rs/SportBookCacheWeb/api/offer/getWebEventsSelections"
)
_DETAIL_URL = (
    "https://sportweb.pinnbet.rs/SportBookCacheWeb/api/offer/betsAndGroups"
    "/{sport_id}/{region_id}/{competition_id}/{event_id}"
)

_SPORT_ID = 3
_OFFICE_ID = "6"
_LANGUAGE = "sr-Latn"

_PLAYER_POINTS_BET_TYPE = 1200
_MAPPING_TYPE_PLAYER = 5
_EVENT_MAPPING_TYPES = [1, 2, 3, 4, 5]

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": (
        "application/utf8+json, application/json;q=0.9, "
        "text/plain;q=0.8, */*;q=0.7"
    ),
    "Content-Type": "application/json",
    "Language": _LANGUAGE,
    "OfficeId": _OFFICE_ID,
    "Origin": "https://www.pinnbet.rs",
    "Referer": "https://www.pinnbet.rs/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}

_DEFAULT_PARAMS: dict[str, str] = {}

# (sportId, regionId, competitionId, fallback league_id)
_KNOWN_COMPETITIONS: list[tuple[int, int, int, str]] = [
    (3, 462, 3221, "nba"),       # NBA when available
    (3, 464, 22317, "aba_liga"), # AdmiralBet ABA liga - plej of (current live fallback)
]

_COMPETITION_NAME_LEAGUE_MAP: dict[str, str] = {
    "nba": "nba",
    "usa nba": "nba",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
}
_COMPETITION_ID_LEAGUE_MAP: dict[int, str] = {
    3221: "nba",
    22317: "aba_liga",
}

_DETAIL_CONCURRENCY = 5


def _build_list_url(sport_id: int, region_id: int, competition_id: int) -> str:
    """Build list URL with repeated eventMappingTypes pre-encoded."""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    date_to = "2031-12-31T00:00:00"
    mapping_qs = "&".join(
        f"eventMappingTypes={t}" for t in _EVENT_MAPPING_TYPES
    )
    return (
        f"{_BASE_LIST_URL}"
        f"?pageId=3&sportId={sport_id}&regionId={region_id}"
        f"&competitionId={competition_id}&isLive=false"
        f"&dateFrom={now}&dateTo={date_to}"
        f"&{mapping_qs}"
    )


def _normalize_start_time(raw: str | None) -> str | None:
    """Convert a PinnBet datetime string to canonical ``+00:00`` format.

    PinnBet returns ``2026-04-11T16:00:00`` (no timezone).  Other scrapers
    produce ``2026-04-11T16:00:00+00:00``.  Treat naive values as UTC to
    match the normalizer's string-based comparison.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return raw


def _normalize_competition_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(event: dict, fallback_league_id: str = "basketball") -> str:
    competition_name = event.get("competitionName")
    key = _normalize_competition_key(
        competition_name if isinstance(competition_name, str) else None
    )
    if key in _COMPETITION_NAME_LEAGUE_MAP:
        return _COMPETITION_NAME_LEAGUE_MAP[key]

    competition_id = event.get("competitionId")
    try:
        competition_id_int = int(competition_id)
    except (TypeError, ValueError):
        competition_id_int = None

    if competition_id_int in _COMPETITION_ID_LEAGUE_MAP:
        return _COMPETITION_ID_LEAGUE_MAP[competition_id_int]
    if key:
        return key
    return fallback_league_id


def _parse_event_name(name: str) -> tuple[str, str | None]:
    """Split ``'Player Name - Team Name'`` into *(player, team)*.

    Returns ``(name, None)`` when no ``' - '`` separator is found.
    """
    if " - " in name:
        player, team = name.split(" - ", 1)
        return player.strip(), team.strip()
    return name.strip(), None


def _get_player_event_ids(events: list[dict]) -> list[dict]:
    """Return full event dicts whose ``mappingTypeId`` equals 5 (player specials)."""
    return [e for e in events if e.get("mappingTypeId") == _MAPPING_TYPE_PLAYER]


def _parse_event_detail(
    event: dict,
    bets_data: dict,
    league_id: str | None = None,
) -> list[RawOddsData]:
    """Combine a list-endpoint event with its detail bets into *RawOddsData*."""
    results: list[RawOddsData] = []

    name = event.get("name", "")
    player_name, team = _parse_event_name(name)
    if not player_name:
        return results

    start_time = _normalize_start_time(event.get("dateTime"))
    effective_league_id = league_id or _extract_league_id(event)

    for bet in bets_data.get("bets", []):
        if bet.get("betTypeId") != _PLAYER_POINTS_BET_TYPE:
            continue

        sbv = bet.get("sBV")
        if sbv is None:
            continue
        try:
            threshold = float(sbv)
        except (ValueError, TypeError):
            continue

        over_odds: float | None = None
        under_odds: float | None = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            outcome_name = (outcome.get("name") or "").lower()
            if outcome_name == "više":
                over_odds = outcome.get("odd")
            elif outcome_name == "manje":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="pinnbet",
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


class PinnBetScraper(BaseScraper):
    """Scraper for PinnBet basketball player points props."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "pinnbet"

    def get_bookmaker_name(self) -> str:
        return "PinnBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_event_detail(
        self,
        event: dict,
        semaphore: asyncio.Semaphore,
        league_id: str | None = None,
    ) -> list[RawOddsData]:
        sport_id = event.get("sportId", _SPORT_ID)
        region_id = event.get("regionId")
        competition_id = event.get("competitionId")
        event_id = event.get("id")

        url = _DETAIL_URL.format(
            sport_id=sport_id,
            region_id=region_id,
            competition_id=competition_id,
            event_id=event_id,
        )

        async with semaphore:
            try:
                detail = await self._http.get_json(
                    url,
                    headers=_DEFAULT_HEADERS,
                )
            except Exception:
                logger.warning(
                    "PinnBet: failed to fetch detail for event %s",
                    event_id,
                )
                return []

        return _parse_event_detail(event, detail, league_id=league_id)

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        all_results: list[RawOddsData] = []

        for sport_id, region_id, competition_id, comp_league_id in _KNOWN_COMPETITIONS:
            url = _build_list_url(sport_id, region_id, competition_id)

            try:
                data = await self._http.get_json(url, headers=_DEFAULT_HEADERS)
            except Exception:
                logger.warning(
                    "PinnBet: failed to fetch events for competition %s",
                    competition_id,
                )
                continue

            # The list endpoint returns a JSON array, not an object.
            if not isinstance(data, list):  # type: ignore[arg-type]
                logger.warning(
                    "PinnBet: unexpected response type %s for competition %s",
                    type(data).__name__,
                    competition_id,
                )
                continue

            player_events = _get_player_event_ids(data)
            if not player_events:
                logger.warning(
                    "PinnBet: no player events for competition %s",
                    competition_id,
                )
                continue

            semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)
            detail_results = await asyncio.gather(
                *(
                    self._fetch_event_detail(
                        ev,
                        semaphore,
                        league_id=_extract_league_id(ev, fallback_league_id=comp_league_id),
                    )
                    for ev in player_events
                )
            )
            for batch in detail_results:
                all_results.extend(batch)

        logger.info("PinnBet scraped %d player odds", len(all_results))
        return all_results
