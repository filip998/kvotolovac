from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_LIST_URL = "https://www.oktagonbet.com/restapi/offer/sr/sport/SK/mob"
_MATCH_URL = "https://www.oktagonbet.com/restapi/offer/sr/match/{match_id}"

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
_THREES_OVER = "51688"  # 3-pointers over
_THREES_UNDER = "51690"  # 3-pointers under
_STEALS_OVER = "55672"  # steals over
_STEALS_UNDER = "55674"  # steals under
_BLOCKS_OVER = "55681"  # blocks over
_BLOCKS_UNDER = "55683"  # blocks under
_POINTS_REBOUNDS_OVER = "55244"  # points + rebounds over
_POINTS_REBOUNDS_UNDER = "55246"  # points + rebounds under
_POINTS_ASSISTS_OVER = "55247"  # points + assists over
_POINTS_ASSISTS_UNDER = "55249"  # points + assists under
_REBOUNDS_ASSISTS_OVER = "55250"  # rebounds + assists over
_REBOUNDS_ASSISTS_UNDER = "55252"  # rebounds + assists under
_PRA_OVER = "55215"  # points + rebounds + assists over
_PRA_UNDER = "55217"  # points + rebounds + assists under

# Mapping: (over_code, under_code, param_key, market_type)
_THRESHOLD_LINES = [
    (_OVER_CODE, _UNDER_CODE, "ouPlPoints", "player_points"),
    (_REB_OVER, _REB_UNDER, "ouPlRebounds", "player_rebounds"),
    (_AST_OVER, _AST_UNDER, "ouPlAssists", "player_assists"),
    (_THREES_OVER, _THREES_UNDER, "ouPl3Points", "player_3points"),
    (_STEALS_OVER, _STEALS_UNDER, "ouPlSt", "player_steals"),
    (_BLOCKS_OVER, _BLOCKS_UNDER, "ouPlB", "player_blocks"),
    (_POINTS_REBOUNDS_OVER, _POINTS_REBOUNDS_UNDER, "ouPlTPR", "player_points_rebounds"),
    (_POINTS_ASSISTS_OVER, _POINTS_ASSISTS_UNDER, "ouPlTPA", "player_points_assists"),
    (_REBOUNDS_ASSISTS_OVER, _REBOUNDS_ASSISTS_UNDER, "ouPlTRA", "player_rebounds_assists"),
    (_PRA_OVER, _PRA_UNDER, "ouPlTPRA", "player_points_rebounds_assists"),
]

_FIXED_POINT_LADDERS = [
    ("54096", 4.5),
    ("54101", 9.5),
    ("54106", 14.5),
    ("54111", 19.5),
    ("54116", 24.5),
    ("54121", 29.5),
    ("54126", 34.5),
    ("54131", 39.5),
    ("54136", 44.5),
    ("54141", 49.5),
    ("57454", 59.5),
]

_LEAGUE_PREFIX = "igrači ~"
_UNLIMITED_DETAIL_CONCURRENCY = 10

# Map OktagonBet league suffixes to canonical IDs used by other scrapers,
# so cross-bookmaker match comparison works (match_id includes league_id).
_CANONICAL_LEAGUES = {
    "usa nba": "nba",
}


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _parse_threshold(threshold_str: str | None) -> float | None:
    if not threshold_str:
        return None
    try:
        return float(threshold_str)
    except (ValueError, TypeError):
        return None


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


def _build_raw_odds(
    match: dict,
    *,
    market_type: str,
    threshold: float,
    over_odds: float | None,
    under_odds: float | None,
) -> RawOddsData:
    player_name = match.get("home", "")
    team = match.get("away", "")
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(match.get("leagueName", ""))

    return RawOddsData(
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


def _parse_match(match: dict) -> list[RawOddsData]:
    """Parse a single bulk-listing match into RawOddsData entries."""
    if not _is_player_market(match):
        return []

    params = match.get("params", {})
    odds = match.get("odds", {})

    results: list[RawOddsData] = []
    for over_code, under_code, param_key, market_type in _THRESHOLD_LINES:
        threshold = _parse_threshold(params.get(param_key))
        if threshold is None:
            continue

        over_odds = odds.get(over_code)
        under_odds = odds.get(under_code)
        if over_odds is None and under_odds is None:
            continue

        results.append(
            _build_raw_odds(
                match,
                market_type=market_type,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
            )
        )

    return results


def _parse_match_detail(match: dict) -> list[RawOddsData]:
    """Parse fixed-threshold player points ladders from a match detail response."""
    if not _is_player_market(match):
        return []

    odds = match.get("odds", {})
    results: list[RawOddsData] = []
    for over_code, threshold in _FIXED_POINT_LADDERS:
        over_odds = odds.get(over_code)
        if over_odds is None:
            continue
        results.append(
            _build_raw_odds(
                match,
                market_type="player_points_milestones",
                threshold=threshold,
                over_odds=over_odds,
                under_odds=None,
            )
        )

    return results


def _get_player_match_ids(matches: list[dict]) -> list[int]:
    """Extract bulk-listing match IDs for player-market detail fetches."""
    return [
        match_id
        for match in matches
        if _is_player_market(match) and (match_id := match.get("id"))
    ]


def _get_detail_fetch_concurrency(http_client: HttpClient, match_count: int) -> int:
    """Derive safe detail-fetch concurrency from the client's configured rate limit."""
    if match_count <= 0:
        return 0

    if http_client.rate_limit_per_second <= 0:
        return min(match_count, _UNLIMITED_DETAIL_CONCURRENCY)

    return min(match_count, max(1, math.ceil(http_client.rate_limit_per_second)))


class OktagonBetScraper(BaseScraper):
    """Scraper for OktagonBet player over/under odds.

    OktagonBet exposes core over/under markets in the bulk listing and
    fixed-threshold player points ladders in the per-match detail endpoint.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "oktagonbet"

    def get_bookmaker_name(self) -> str:
        return "OktagonBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_detail_markets(self, match_ids: list[int]) -> list[RawOddsData]:
        if not match_ids:
            return []

        semaphore = asyncio.Semaphore(
            _get_detail_fetch_concurrency(self._http, len(match_ids))
        )

        async def fetch(match_id: int) -> list[RawOddsData]:
            async with semaphore:
                try:
                    detail = await self._http.get_json(
                        _MATCH_URL.format(match_id=match_id),
                        params=_DEFAULT_PARAMS,
                        headers=_DEFAULT_HEADERS,
                    )
                except Exception:
                    logger.warning("OktagonBet: failed to fetch detail for match %s", match_id)
                    return []
                return _parse_match_detail(detail)

        detail_results = await asyncio.gather(*(fetch(match_id) for match_id in match_ids))
        return [item for result in detail_results for item in result]

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

        match_ids = _get_player_match_ids(matches)
        results.extend(await self._fetch_detail_markets(match_ids))

        logger.info(
            "OktagonBet scraped %d player odds from %d matches",
            len(results),
            len(matches),
        )
        return results
