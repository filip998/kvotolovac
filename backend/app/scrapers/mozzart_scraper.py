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

# "Broj poena B.Saraf" → "B.Saraf"
_PLAYER_NAME_RE = re.compile(r"^Broj poena\s+(.+)$", re.IGNORECASE)

_BASKETBALL_SPORT_ID = 2
_SPECIALS_MATCH_TYPE = 2


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


def _extract_player_name(game_name: str) -> str | None:
    m = _PLAYER_NAME_RE.match(game_name.strip())
    return m.group(1).strip() if m else None


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _parse_items(items: list[dict]) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for match in items:
        home = match.get("home", {}).get("name", "")
        visitor = match.get("visitor", {}).get("name", "")
        competition = match.get("competition", {}).get("name", "")
        start_time = _parse_start_time(match.get("startTime"))
        league_id = competition.lower().replace(" ", "_") if competition else "basketball"

        # Aggregate by (player_name, threshold) to handle any odds ordering
        aggregated: dict[tuple[str, float], dict] = {}

        for group in match.get("oddsGroup", []):
            group_name = group.get("groupName", "")
            if "poena igrača" not in group_name.lower() and "poena igra" not in group_name.lower():
                continue

            for odd in group.get("odds", []):
                if odd.get("oddStatus") != "ACTIVE":
                    continue

                game_name = odd.get("game", {}).get("name", "")
                extracted_name = _extract_player_name(game_name)
                subgame_name = odd.get("subgame", {}).get("name", "").lower()

                try:
                    sov = float(odd.get("specialOddValue", "0"))
                except (ValueError, TypeError):
                    continue

                value = odd.get("value")
                if not extracted_name or sov <= 0 or value is None:
                    continue

                key = (extracted_name, sov)
                if key not in aggregated:
                    aggregated[key] = {"over": None, "under": None}

                if "više" in subgame_name or "vise" in subgame_name:
                    aggregated[key]["over"] = value
                elif "manje" in subgame_name:
                    aggregated[key]["under"] = value

        for (player_name, threshold), odds in aggregated.items():
            results.append(
                RawOddsData(
                    bookmaker_id="mozzart",
                    league_id=league_id,
                    home_team=home,
                    away_team=visitor,
                    market_type="player_points",
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
