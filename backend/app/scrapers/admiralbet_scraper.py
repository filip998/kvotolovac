from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_LIST_URL = "https://srboffer.admiralbet.rs/api/offer/getWebEventsSelections"

_PLAYER_DEFAULT_PARAMS = {
    "pageId": "3",
    "sportId": "123",
    "isLive": "false",
    "dateFrom": "",  # filled at scrape time
    "dateTo": "2031-12-31T00:00:00",
    "eventMappingTypes": ["1", "2", "3", "4", "5"],
}

_GAME_TOTAL_DEFAULT_PARAMS = {
    "pageId": "3",
    "sportId": "2",
    "isLive": "false",
    "dateFrom": "",  # filled at scrape time
    "dateTo": "2031-12-31T00:00:00",
    "eventMappingTypes": ["1", "2", "3", "4", "5"],
}

_DEFAULT_HEADERS = {
    "accept": "application/utf8+json, application/json;q=0.9",
    "language": "sr-Latn",
    "officeid": "138",
    "origin": "https://admiralbet.rs",
    "referer": "https://admiralbet.rs/",
}

# betTypeId constants
_BET_POINTS_OVER_UNDER = 1598  # "Ukupno poena" — threshold in sBV
_BET_POINTS_MILESTONES = 1683  # "Postiže poena" — milestone outcomes (5+, 10+, …)
_BET_GAME_TOTAL_OT = 213  # "Ukupno (+OT)"

# Map competitionName values to canonical league IDs used by other scrapers.
# When NBA competitionId/name is discovered, add it here.
_COMPETITION_LEAGUE_MAP: dict[str, str] = {
    "nba": "nba",
    "usa nba": "nba",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
}


def _normalize_league_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(competition_name: str | None) -> str:
    """Map an AdmiralBet competitionName to a canonical league ID.

    Falls back to a lowercased slug of the competition name, which keeps
    different competitions separated even without explicit mapping.
    """
    if not competition_name:
        return "basketball"
    raw = competition_name.strip().lower()
    normalized = _normalize_league_key(raw)
    if normalized in _COMPETITION_LEAGUE_MAP:
        return _COMPETITION_LEAGUE_MAP[normalized]
    return raw or "basketball"

# Milestone outcome thresholds — "5+" means 5 or more, equivalent to over 4.5
_MILESTONE_THRESHOLDS: dict[str, float] = {
    "5+": 4.5,
    "10+": 9.5,
    "15+": 14.5,
    "20+": 19.5,
    "25+": 24.5,
    "30+": 29.5,
    "35+": 34.5,
    "40+": 39.5,
    "45+": 44.5,
    "50+": 49.5,
}

def _parse_event_name(name: str) -> tuple[str, str]:
    """Parse 'Player Name - Team Name' into (player, team)."""
    if " - " in name:
        player, team = name.split(" - ", 1)
        return player.strip(), team.strip()
    return name.strip(), ""


def _parse_start_time(dt_str: str | None) -> str | None:
    """Parse AdmiralBet datetimes to the canonical ``+00:00`` format.

    AdmiralBet returns naive values such as ``2026-04-15T15:30:00`` for
    basketball events. In practice those values already line up with the UTC
    timestamps from the other bookmakers we merge against, so treat them as UTC
    instead of shifting them by the local Belgrade offset.
    """
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _parse_over_under_bets(event: dict, player_name: str, team: str,
                           start_time: str | None, league_id: str) -> list[RawOddsData]:
    """Extract over/under lines from betTypeId 1598 bets."""
    results: list[RawOddsData] = []
    for bet in event.get("bets", []):
        if bet.get("betTypeId") != _BET_POINTS_OVER_UNDER:
            continue
        if not bet.get("isPlayable"):
            continue

        sbv = bet.get("sBV")
        if sbv is None:
            continue
        try:
            threshold = float(sbv)
        except (ValueError, TypeError):
            continue

        over_odds = None
        under_odds = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            name = (outcome.get("name") or "").lower()
            if name == "vise":
                over_odds = outcome.get("odd")
            elif name == "manje":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(RawOddsData(
            bookmaker_id="admiralbet",
            league_id=league_id,
            home_team=team,
            away_team=player_name,
            market_type="player_points",
            player_name=player_name,
            threshold=threshold,
            over_odds=over_odds,
            under_odds=under_odds,
            start_time=start_time,
        ))

    return results


def _parse_milestone_bets(event: dict, player_name: str, team: str,
                          start_time: str | None, league_id: str) -> list[RawOddsData]:
    """Extract milestone (5+, 10+, …) bets from betTypeId 1683."""
    results: list[RawOddsData] = []
    for bet in event.get("bets", []):
        if bet.get("betTypeId") != _BET_POINTS_MILESTONES:
            continue
        if not bet.get("isPlayable"):
            continue

        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            name = (outcome.get("name") or "").strip()
            threshold = _MILESTONE_THRESHOLDS.get(name)
            if threshold is None:
                continue

            over_odds = outcome.get("odd")
            if over_odds is None:
                continue

            results.append(RawOddsData(
                bookmaker_id="admiralbet",
                league_id=league_id,
                home_team=team,
                away_team=player_name,
                market_type="player_points_milestones",
                player_name=player_name,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=None,
                start_time=start_time,
            ))

    return results


def _parse_game_total_ot_bets(
    event: dict,
    home_team: str,
    away_team: str,
    start_time: str | None,
    league_id: str,
) -> list[RawOddsData]:
    """Extract OT-inclusive match totals from basketball event listings."""
    results: list[RawOddsData] = []
    for bet in event.get("bets", []):
        bet_type_name = (bet.get("betTypeName") or "").strip().lower()
        if bet.get("betTypeId") != _BET_GAME_TOTAL_OT:
            continue
        if "+ot" not in bet_type_name:
            continue
        if not bet.get("isPlayable"):
            continue

        sbv = bet.get("sBV")
        if sbv is None:
            continue
        try:
            threshold = float(sbv)
        except (ValueError, TypeError):
            continue

        over_odds = None
        under_odds = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            name = (outcome.get("name") or "").lower()
            if name == "vise":
                over_odds = outcome.get("odd")
            elif name == "manje":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="admiralbet",
                league_id=league_id,
                home_team=home_team,
                away_team=away_team,
                market_type="game_total_ot",
                player_name=None,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=start_time,
            )
        )

    return results


def _parse_event(event: dict) -> list[RawOddsData]:
    """Parse a single AdmiralBet event into RawOddsData entries."""
    name = event.get("name", "")
    player_name, team = _parse_event_name(name)
    if not player_name or not team:
        return []

    start_time = _parse_start_time(event.get("dateTime"))
    league_id = _extract_league_id(event.get("competitionName"))

    results: list[RawOddsData] = []
    results.extend(_parse_over_under_bets(event, player_name, team, start_time, league_id))
    results.extend(_parse_milestone_bets(event, player_name, team, start_time, league_id))
    return results


def _parse_game_total_ot_event(event: dict) -> list[RawOddsData]:
    """Parse a standard basketball match event into OT-inclusive game totals."""
    name = event.get("name", "")
    home_team, away_team = _parse_event_name(name)
    if not home_team or not away_team:
        return []

    start_time = _parse_start_time(event.get("dateTime"))
    league_id = _extract_league_id(event.get("competitionName"))
    return _parse_game_total_ot_bets(event, home_team, away_team, start_time, league_id)


class AdmiralBetScraper(BaseScraper):
    """Scraper for AdmiralBet player props and OT-inclusive game totals.

    AdmiralBet returns both player specials and standard basketball events in
    bulk listings, so no per-event detail fetches are needed.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "admiralbet"

    def get_bookmaker_name(self) -> str:
        return "AdmiralBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_list(self, params: dict, label: str) -> list[dict]:
        try:
            data = await self._http.get_json(
                _LIST_URL,
                params=params,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("AdmiralBet %s scrape failed", label)
            return []

        if not isinstance(data, list):
            logger.warning(
                "AdmiralBet: unexpected %s response type %s",
                label,
                type(data).__name__,
            )
            return []

        return data

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        player_params = {**_PLAYER_DEFAULT_PARAMS, "dateFrom": now}
        game_total_params = {**_GAME_TOTAL_DEFAULT_PARAMS, "dateFrom": now}

        player_data, basketball_events = await asyncio.gather(
            self._fetch_list(player_params, "player specials list"),
            self._fetch_list(game_total_params, "basketball events list"),
        )

        player_results: list[RawOddsData] = []
        for event in player_data:
            player_results.extend(_parse_event(event))

        total_results: list[RawOddsData] = []
        for event in basketball_events:
            total_results.extend(_parse_game_total_ot_event(event))

        results = [*player_results, *total_results]

        logger.info(
            (
                "AdmiralBet scraped %d player odds from %d special events "
                "and %d OT total odds from %d basketball events"
            ),
            len(player_results),
            len(player_data),
            len(total_results),
            len(basketball_events),
        )
        return results
