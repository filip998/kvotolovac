from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_API_URL = "https://www.maxbet.rs/restapi/offer/sr/sport/SK/mob"

_DEFAULT_PARAMS = {
    "annex": "3",
    "mobileVersion": "1.17.1.25",
    "locale": "sr",
}

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.maxbet.rs/",
}

# Tip type codes from MaxBet's ttg_lang endpoint
_OVER_CODE = "51679"   # "+" — player scores MORE points
_UNDER_CODE = "51681"  # "-" — player scores LESS points


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _parse_matches(matches: list[dict]) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for match in matches:
        league_name = match.get("leagueName", "")
        if "poeni igrača" not in league_name.lower():
            continue

        params = match.get("params", {})
        threshold_str = params.get("ouPlPoints")
        if not threshold_str:
            continue

        try:
            threshold = float(threshold_str)
        except (ValueError, TypeError):
            continue

        odds = match.get("odds", {})
        over_odds = odds.get(_OVER_CODE)
        under_odds = odds.get(_UNDER_CODE)

        if over_odds is None and under_odds is None:
            continue

        player_name = match.get("home", "")
        team = match.get("away", "")
        start_time = _parse_start_time(match.get("kickOffTime"))

        # Derive league_id from leagueName: "Poeni igrača NBA" → "nba"
        league_id = league_name.lower().replace("poeni igrača", "").strip()
        if not league_id:
            league_id = "basketball"

        results.append(
            RawOddsData(
                bookmaker_id="maxbet",
                league_id=league_id,
                home_team=team,
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


class MaxBetScraper(BaseScraper):
    """Real scraper for MaxBet bookmaker player points over/under odds."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "maxbet"

    def get_bookmaker_name(self) -> str:
        return "MaxBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        try:
            data = await self._http.get_json(
                _API_URL,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("MaxBet scrape failed")
            return []

        matches = data.get("esMatches", [])
        if not matches:
            logger.warning("MaxBet returned 0 matches")
            return []

        results = _parse_matches(matches)
        logger.info("MaxBet scraped %d player odds from %d matches", len(results), len(matches))
        return results
