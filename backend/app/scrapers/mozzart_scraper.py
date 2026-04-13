from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_API_URL = "https://www.mozzartbet.com/betting/specialMatches"

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Cookie": "i18next=sr",
    "medium": "PREMATCH_MOBILE",
}

# "Broj poena B.Saraf" → ("B.Saraf", "player_points")
# "Broj skokova B.Saraf" → ("B.Saraf", "player_rebounds")
# "Broj asistencija B.Saraf" → ("B.Saraf", "player_assists")
_MARKET_PATTERNS = [
    (re.compile(r"^Broj poena\s+(.+)$", re.IGNORECASE), "player_points"),
    (re.compile(r"^Broj skokova\s+(.+)$", re.IGNORECASE), "player_rebounds"),
    (re.compile(r"^Broj asistencija\s+(.+)$", re.IGNORECASE), "player_assists"),
]

# Group names that contain player markets
_PLAYER_GROUP_KEYWORDS = [
    "poena igrača", "poena igra",
    "skokova igrača", "skokova igra",
    "asistencija igrača", "asistencija igra",
]

_BASKETBALL_SPORT_ID = 2
_SPECIALS_MATCH_TYPE = 2
_CANONICAL_LEAGUES = {
    "nba": "nba",
    "usa nba": "nba",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
}


def _build_request_body(
    competition_ids: list[int] | None = None,
    page_size: int = 50,
) -> dict:
    body_inner: dict = {
        "sportIds": [_BASKETBALL_SPORT_ID],
        "pageSize": page_size,
        "currentPage": 0,
        "matchTypeId": _SPECIALS_MATCH_TYPE,
        "orderType": "BY_COMPETITION",
        "loadPriorityTemplateGamesOnly": True,
        "loadAllTemplateGames": False,
        "packGamesGroupBySport": False,
        "medium": "ANDROID",
        "loadExtendedOffer": False,
        "packGroupsInMatch": True,
        "sportsLoad": True,
        "uberOffer": True,
    }
    if competition_ids:
        body_inner["competitionIds"] = competition_ids

    return {
        "currentPage": 0,
        "pageSize": page_size,
        "body": body_inner,
        "uri": "/matches",
    }


def _extract_player_and_market(game_name: str) -> tuple[str | None, str]:
    """Extract player name and market type from game name."""
    for pattern, market_type in _MARKET_PATTERNS:
        m = pattern.match(game_name.strip())
        if m:
            return m.group(1).strip(), market_type
    return None, "player_points"


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _normalize_league_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(competition_name: str | None) -> str:
    if not competition_name:
        return "basketball"

    raw = competition_name.strip().lower()
    normalized = _normalize_league_key(raw)
    if normalized in _CANONICAL_LEAGUES:
        return _CANONICAL_LEAGUES[normalized]
    return raw.replace(" ", "_") or "basketball"


def _parse_items(items: list[dict]) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for match in items:
        home = match.get("home", {}).get("name", "")
        visitor = match.get("visitor", {}).get("name", "")
        competition = match.get("competition", {}).get("name", "")
        start_time = _parse_start_time(match.get("startTime"))
        league_id = _extract_league_id(competition)

        # Aggregate by (player_name, threshold, market_type) to handle any odds ordering
        aggregated: dict[tuple[str, float, str], dict] = {}

        for group in match.get("oddsGroup", []):
            group_name = group.get("groupName", "").lower()
            if not any(kw in group_name for kw in _PLAYER_GROUP_KEYWORDS):
                continue

            for odd in group.get("odds", []):
                if odd.get("oddStatus") != "ACTIVE":
                    continue

                game_name = odd.get("game", {}).get("name", "")
                extracted_name, market_type = _extract_player_and_market(game_name)
                subgame_name = odd.get("subgame", {}).get("name", "").lower()

                try:
                    sov = float(odd.get("specialOddValue", "0"))
                except (ValueError, TypeError):
                    continue

                value = odd.get("value")
                if not extracted_name or sov <= 0 or value is None:
                    continue

                key = (extracted_name, sov, market_type)
                if key not in aggregated:
                    aggregated[key] = {"over": None, "under": None}

                if "više" in subgame_name or "vise" in subgame_name:
                    aggregated[key]["over"] = value
                elif "manje" in subgame_name:
                    aggregated[key]["under"] = value

        for (player_name, threshold, market_type), odds in aggregated.items():
            results.append(
                RawOddsData(
                    bookmaker_id="mozzart",
                    league_id=league_id,
                    home_team=home,
                    away_team=visitor,
                    market_type=market_type,
                    player_name=player_name,
                    threshold=threshold,
                    over_odds=odds["over"],
                    under_odds=odds["under"],
                    start_time=start_time,
                )
            )

    return results


class MozzartScraper(BaseScraper):
    """Real scraper for Mozzart bookmaker player points over/under odds."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "mozzart"

    def get_bookmaker_name(self) -> str:
        return "Mozzart"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        body = _build_request_body()

        try:
            data = await self._http.post_json(
                _API_URL,
                json_body=body,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("Mozzart scrape failed")
            return []

        items = data.get("items", [])
        if not items:
            logger.warning("Mozzart returned 0 items")
            return []

        results = _parse_items(items)
        logger.info("Mozzart scraped %d player odds from %d matches", len(results), len(items))
        return results
