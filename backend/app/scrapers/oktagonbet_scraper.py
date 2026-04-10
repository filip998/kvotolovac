from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_LIST_URL = "https://www.oktagonbet.com/restapi/offer/sr/sport/SK/mob"

_DEFAULT_PARAMS = {
    "annex": "1",
    "hours": "12",
    "mobileVersion": "2.44.5.6",
    "locale": "sr",
}

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.oktagonbet.com/",
}

# Tip type codes — same platform as MaxBet
_OVER_CODE = "51679"    # player scores MORE points
_UNDER_CODE = "51681"   # player scores LESS points
_REB_OVER = "51685"     # rebounds over
_REB_UNDER = "51687"    # rebounds under
_AST_OVER = "51682"     # assists over
_AST_UNDER = "51684"    # assists under

# Mapping: (over_code, under_code, param_key, market_type)
_THRESHOLD_LINES = [
    (_OVER_CODE, _UNDER_CODE, "ouPlPoints", "player_points"),
    (_REB_OVER, _REB_UNDER, "ouPlRebounds", "player_rebounds"),
    (_AST_OVER, _AST_UNDER, "ouPlAssists", "player_assists"),
]

_LEAGUE_PREFIX = "igrači ~"

# Map OktagonBet league suffixes to canonical IDs used by other scrapers,
# so cross-bookmaker match comparison works (match_id includes league_id).
_CANONICAL_LEAGUES = {
    "usa nba": "nba",
}


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _is_player_market(match: dict) -> bool:
    """Return True if the match is a player over/under market (not duels or specials)."""
    league_name = match.get("leagueName", "").lower()
    return (
        league_name.startswith(_LEAGUE_PREFIX)
        and "dueli" not in league_name
        and match.get("leagueCategory") == "PL"
    )


def _extract_league_id(league_name: str) -> str:
    """Extract a canonical league ID from the league name.

    'Igrači ~ USA NBA' → 'nba'  (via canonical mapping)
    'Igrači ~ Euroleague' → 'euroleague'
    """
    lower = league_name.lower()
    prefix = "igrači"
    if lower.startswith(prefix):
        raw = lower[len(prefix) :].lstrip().lstrip("~").strip()
    else:
        raw = lower.strip()

    return _CANONICAL_LEAGUES.get(raw, raw) or "basketball"


def _parse_match(match: dict) -> list[RawOddsData]:
    """Parse a single bulk-listing match into RawOddsData entries."""
    if not _is_player_market(match):
        return []

    params = match.get("params", {})
    odds = match.get("odds", {})
    player_name = match.get("home", "")
    team = match.get("away", "")
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(match.get("leagueName", ""))

    results: list[RawOddsData] = []
    for over_code, under_code, param_key, market_type in _THRESHOLD_LINES:
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
                bookmaker_id="oktagonbet",
                league_id=league_id,
                home_team=team,
                away_team=player_name,
                market_type=market_type,
                player_name=player_name,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=start_time,
            )
        )

    return results


class OktagonBetScraper(BaseScraper):
    """Scraper for OktagonBet player over/under odds.

    OktagonBet runs on the same white-label platform as MaxBet but exposes
    all market data in the bulk listing, so no per-match detail fetch is needed.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "oktagonbet"

    def get_bookmaker_name(self) -> str:
        return "OktagonBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        try:
            data = await self._http.get_json(
                _LIST_URL,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("OktagonBet list scrape failed")
            return []

        matches = data.get("esMatches", [])
        if not matches:
            logger.warning("OktagonBet returned 0 matches")
            return []

        results: list[RawOddsData] = []
        for match in matches:
            results.extend(_parse_match(match))

        logger.info(
            "OktagonBet scraped %d player odds from %d matches",
            len(results),
            len(matches),
        )
        return results
