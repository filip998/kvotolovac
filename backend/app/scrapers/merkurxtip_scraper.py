from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_LEAGUE_URL = "https://www.merkurxtip.rs/restapi/offer/sr/sport/SK/league/{league_id}/mob"
_MATCH_URL = "https://www.merkurxtip.rs/restapi/offer/sr/match/{match_id}"

_DEFAULT_PARAMS = {
    "annex": "0",
    "mobileVersion": "1.16.2.34",
    "locale": "sr",
}

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.merkurxtip.rs/",
}

# Tip type codes — same white-label platform as MaxBet / OktagonBet
_OVER_CODE = "51679"
_UNDER_CODE = "51681"
_ALT1_OVER = "55253"
_ALT1_UNDER = "55255"
_ALT2_OVER = "55256"
_ALT2_UNDER = "55258"
_REB_OVER = "51685"
_REB_UNDER = "51687"
_AST_OVER = "51682"
_AST_UNDER = "51684"
_THREES_OVER = "51688"
_THREES_UNDER = "51690"
_STEALS_OVER = "55672"
_STEALS_UNDER = "55674"
_BLOCKS_OVER = "55681"
_BLOCKS_UNDER = "55683"
_PR_OVER = "55244"
_PR_UNDER = "55246"
_PA_OVER = "55247"
_PA_UNDER = "55249"
_RA_OVER = "55250"
_RA_UNDER = "55252"
_PRA_OVER = "55215"
_PRA_UNDER = "55217"

# Mapping: (over_code, under_code, param_key, market_type)
_THRESHOLD_LINES = [
    (_OVER_CODE, _UNDER_CODE, "ouPlPoints", "player_points"),
    (_ALT1_OVER, _ALT1_UNDER, "ouPlP2", "player_points"),
    (_ALT2_OVER, _ALT2_UNDER, "ouPlP3", "player_points"),
    (_REB_OVER, _REB_UNDER, "ouPlRebounds", "player_rebounds"),
    (_AST_OVER, _AST_UNDER, "ouPlAssists", "player_assists"),
    (_THREES_OVER, _THREES_UNDER, "ouPl3Points", "player_3points"),
    (_STEALS_OVER, _STEALS_UNDER, "ouPlSt", "player_steals"),
    (_BLOCKS_OVER, _BLOCKS_UNDER, "ouPlB", "player_blocks"),
    (_PR_OVER, _PR_UNDER, "ouPlTPR", "player_points_rebounds"),
    (_PA_OVER, _PA_UNDER, "ouPlTPA", "player_points_assists"),
    (_RA_OVER, _RA_UNDER, "ouPlTRA", "player_rebounds_assists"),
    (_PRA_OVER, _PRA_UNDER, "ouPlTPRA", "player_points_rebounds_assists"),
]

_LIST_MATCH_PARAM_KEYS = {
    "ouPlPoints",
    "ouPlRebounds",
    "ouPlAssists",
    "ouPl3Points",
    "ouPlSt",
    "ouPlB",
    "ouPlTPR",
    "ouPlTPA",
    "ouPlTRA",
    "ouPlTPRA",
}

_FIXED_POINTS_LADDERS = [
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

_KNOWN_LEAGUE_IDS: list[int] = [
    2314461,  # NBA Igrači
    2314422,  # ACB Igrači
]

_UNLIMITED_DETAIL_CONCURRENCY = 10
_MIN_DETAIL_CONCURRENCY = 2


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _extract_league_id(league_name: str) -> str:
    """Extract a canonical league ID from league name by stripping 'igrači' suffix.

    'ACB Igrači' → 'acb'
    'NBA Igrači' → 'nba'
    'Igrači'     → 'basketball'
    """
    lower = league_name.lower()
    idx = lower.find("igrači")
    if idx >= 0:
        raw = lower[:idx].strip()
    else:
        raw = lower.strip()
    return raw or "basketball"


def _parse_match_detail(match: dict) -> list[RawOddsData]:
    """Parse a single match detail response into RawOddsData for all threshold lines."""
    results: list[RawOddsData] = []

    league_name = match.get("leagueName", "")
    if "igrači" not in league_name.lower():
        return results

    params = match.get("params", {})
    odds = match.get("odds", {})
    player_name = match.get("home", "")
    team = match.get("away", "")
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(league_name)

    def build_raw_odds(
        *,
        market_type: str,
        threshold: float,
        over_odds: float | None,
        under_odds: float | None,
    ) -> RawOddsData:
        return RawOddsData(
            bookmaker_id="merkurxtip",
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
            build_raw_odds(
                market_type=market_type,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
            )
        )

    for over_code, threshold in _FIXED_POINTS_LADDERS:
        over_odds = odds.get(over_code)
        if over_odds is None:
            continue
        results.append(
            build_raw_odds(
                market_type="player_points_milestones",
                threshold=threshold,
                over_odds=over_odds,
                under_odds=None,
            )
        )

    return results


def _get_player_match_ids(matches: list[dict]) -> list[int]:
    """Extract supported player-props match IDs from a league listing."""
    ids: list[int] = []
    for m in matches:
        if "igrači" not in m.get("leagueName", "").lower():
            continue
        params = m.get("params", {})
        if not any(params.get(param_key) for param_key in _LIST_MATCH_PARAM_KEYS):
            continue
        match_id = m.get("id")
        if match_id:
            ids.append(match_id)
    return ids


def _get_detail_fetch_concurrency(http_client: HttpClient, match_count: int) -> int:
    if match_count <= 0:
        return 0
    if http_client.rate_limit_per_second <= 0:
        return min(match_count, _UNLIMITED_DETAIL_CONCURRENCY)
    return min(
        match_count,
        max(_MIN_DETAIL_CONCURRENCY, math.ceil(http_client.rate_limit_per_second)),
    )


class MerkurXTipScraper(BaseScraper):
    """Scraper for MERKUR X TIP basketball player props.

    Same white-label platform as MaxBet / OktagonBet, but uses
    league-specific listing endpoints instead of a bulk sport listing.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "merkurxtip"

    def get_bookmaker_name(self) -> str:
        return "MERKUR X TIP"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_match_detail(
        self,
        match_id: int,
        semaphore: asyncio.Semaphore,
    ) -> list[RawOddsData]:
        async with semaphore:
            try:
                detail = await self._http.get_json(
                    _MATCH_URL.format(match_id=match_id),
                    params=_DEFAULT_PARAMS,
                    headers=_DEFAULT_HEADERS,
                )
            except Exception:
                logger.warning("MerkurXTip: failed to fetch detail for match %s", match_id)
                return []

        return _parse_match_detail(detail)

    async def _fetch_league(self, league_id: int) -> list[int]:
        """Fetch a league listing and return player match IDs."""
        try:
            data = await self._http.get_json(
                _LEAGUE_URL.format(league_id=league_id),
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("MerkurXTip: failed to fetch league %s", league_id)
            return []

        matches = data.get("esMatches", [])
        return _get_player_match_ids(matches)

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        # Step 1: Fetch all known leagues to collect player match IDs
        all_match_ids: list[int] = []
        for known_league in _KNOWN_LEAGUE_IDS:
            match_ids = await self._fetch_league(known_league)
            all_match_ids.extend(match_ids)

        if not all_match_ids:
            logger.warning("MerkurXTip: no player matches found across %d leagues", len(_KNOWN_LEAGUE_IDS))
            return []

        # Step 2: Fan out detail fetches with concurrency limit
        concurrency = _get_detail_fetch_concurrency(self._http, len(all_match_ids))
        semaphore = asyncio.Semaphore(concurrency)
        detail_results = await asyncio.gather(
            *(self._fetch_match_detail(mid, semaphore) for mid in all_match_ids)
        )
        results = [item for batch in detail_results for item in batch]

        logger.info(
            "MerkurXTip scraped %d player odds from %d players (detail concurrency=%d)",
            len(results), len(all_match_ids), concurrency,
        )
        return results
