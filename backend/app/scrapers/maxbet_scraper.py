from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_LIST_URL = "https://www.maxbet.rs/restapi/offer/sr/sport/SK/mob"
_MATCH_URL = "https://www.maxbet.rs/restapi/offer/sr/match/{match_id}"

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
_OVER_CODE = "51679"    # "+" — player scores MORE points (primary threshold)
_UNDER_CODE = "51681"   # "-" — player scores LESS points (primary threshold)
_ALT1_OVER = "55253"    # "alt1 +" — alt threshold 1 over
_ALT1_UNDER = "55255"   # "alt1 -" — alt threshold 1 under
_ALT2_OVER = "55256"    # "alt2 +" — alt threshold 2 over
_ALT2_UNDER = "55258"   # "alt2 -" — alt threshold 2 under

# Mapping: (over_code, under_code) → param key for threshold
_THRESHOLD_LINES = [
    (_OVER_CODE, _UNDER_CODE, "ouPlPoints"),
    (_ALT1_OVER, _ALT1_UNDER, "ouPlP2"),
    (_ALT2_OVER, _ALT2_UNDER, "ouPlP3"),
]


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _parse_match_detail(match: dict) -> list[RawOddsData]:
    """Parse a single match detail response into RawOddsData for all threshold lines."""
    results: list[RawOddsData] = []

    league_name = match.get("leagueName", "")
    if "poeni igrača" not in league_name.lower():
        return results

    params = match.get("params", {})
    odds = match.get("odds", {})
    player_name = match.get("home", "")
    team = match.get("away", "")
    start_time = _parse_start_time(match.get("kickOffTime"))

    league_id = league_name.lower().replace("poeni igrača", "").strip()
    if not league_id:
        league_id = "basketball"

    for over_code, under_code, param_key in _THRESHOLD_LINES:
        threshold_str = params.get(param_key)
        if not threshold_str:
            continue
        try:
            threshold = float(threshold_str)
        except (ValueError, TypeError):
            continue

        over_odds = odds.get(over_code)
        under_odds = odds.get(under_code)
        if over_odds is None and under_odds is None:
            continue

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


def _get_player_match_ids(matches: list[dict]) -> list[int]:
    """Extract match IDs for player points markets from the bulk listing."""
    ids: list[int] = []
    for m in matches:
        if "poeni igrača" not in m.get("leagueName", "").lower():
            continue
        if not m.get("params", {}).get("ouPlPoints"):
            continue
        match_id = m.get("id")
        if match_id:
            ids.append(match_id)
    return ids


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

        # Step 1: Get bulk listing to find player points match IDs
        try:
            data = await self._http.get_json(
                _LIST_URL,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("MaxBet list scrape failed")
            return []

        matches = data.get("esMatches", [])
        if not matches:
            logger.warning("MaxBet returned 0 matches")
            return []

        match_ids = _get_player_match_ids(matches)
        if not match_ids:
            logger.warning("MaxBet: no player points matches found")
            return []

        # Step 2: Fetch detail for each player to get alt thresholds
        results: list[RawOddsData] = []
        for mid in match_ids:
            try:
                detail = await self._http.get_json(
                    _MATCH_URL.format(match_id=mid),
                    params=_DEFAULT_PARAMS,
                    headers=_DEFAULT_HEADERS,
                )
                results.extend(_parse_match_detail(detail))
            except Exception:
                logger.warning("MaxBet: failed to fetch detail for match %s", mid)

        logger.info(
            "MaxBet scraped %d player odds from %d players",
            len(results), len(match_ids),
        )
        return results
