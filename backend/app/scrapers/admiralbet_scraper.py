from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_LIST_URL = "https://srboffer.admiralbet.rs/api/offer/getWebEventsSelections"

_DEFAULT_PARAMS = {
    "pageId": "3",
    "sportId": "123",
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

_BELGRADE_TZ = ZoneInfo("Europe/Belgrade")


def _parse_event_name(name: str) -> tuple[str, str]:
    """Parse 'Player Name - Team Name' into (player, team)."""
    if " - " in name:
        player, team = name.split(" - ", 1)
        return player.strip(), team.strip()
    return name.strip(), ""


def _parse_start_time(dt_str: str | None) -> str | None:
    """Parse a naive Belgrade-local datetime string to UTC ISO format."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_BELGRADE_TZ)
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


class AdmiralBetScraper(BaseScraper):
    """Scraper for AdmiralBet player over/under and milestone odds.

    AdmiralBet returns all player prop data in a single bulk listing,
    so no per-event detail fetches are needed.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "admiralbet"

    def get_bookmaker_name(self) -> str:
        return "AdmiralBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        params = {**_DEFAULT_PARAMS, "dateFrom": now}

        try:
            data = await self._http.get_json(
                _LIST_URL,
                params=params,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("AdmiralBet list scrape failed")
            return []

        if not isinstance(data, list):
            logger.warning("AdmiralBet: unexpected response type %s", type(data).__name__)
            return []

        results: list[RawOddsData] = []
        for event in data:
            results.extend(_parse_event(event))

        logger.info(
            "AdmiralBet scraped %d player odds from %d events",
            len(results),
            len(data),
        )
        return results
