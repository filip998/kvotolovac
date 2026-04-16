from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_PLAYER_LIST_URL = "https://www.maxbet.rs/restapi/offer/sr/sport/SK/mob"
_TOTALS_LIST_URL = "https://www.maxbet.rs/restapi/offer/sr/sport/B/mob"
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
_REB_OVER = "51685"     # "SK+" — rebounds over
_REB_UNDER = "51687"    # "SK-" — rebounds under
_AST_OVER = "51682"     # "AS+" — assists over
_AST_UNDER = "51684"    # "AS-" — assists under
_THREES_OVER = "51688"  # "3+" — three-pointers over
_THREES_UNDER = "51690"  # "3-" — three-pointers under
_STEALS_OVER = "55672"  # "UK+" — steals over
_STEALS_UNDER = "55674"  # "UK-" — steals under
_BLOCKS_OVER = "55681"  # "BL+" — blocks over
_BLOCKS_UNDER = "55683"  # "BL-" — blocks under
_PR_OVER = "55244"      # "P+R+" — points + rebounds over
_PR_UNDER = "55246"     # "P+R-" — points + rebounds under
_PA_OVER = "55247"      # "P+A+" — points + assists over
_PA_UNDER = "55249"     # "P+A-" — points + assists under
_RA_OVER = "55250"      # "R+A+" — rebounds + assists over
_RA_UNDER = "55252"     # "R+A-" — rebounds + assists under
_PRA_OVER = "55215"     # "P+R+A+" — points + rebounds + assists over
_PRA_UNDER = "55217"    # "P+R+A-" — points + rebounds + assists under

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

_GAME_TOTAL_LINES = [
    ("227", "228", "overUnder"),
    ("429", "427", "overUnder2"),
]

# OT-inclusive full-game totals confirmed from the live basketball list feed.
_GAME_TOTAL_OT_LINES = [
    ("50445", "50444", "overUnderOvertime"),
    ("50447", "50446", "overUnderOvertime2"),
    ("50449", "50448", "overUnderOvertime3"),
    ("50451", "50450", "overUnderOvertime4"),
    ("50453", "50452", "overUnderOvertime5"),
    ("50455", "50454", "overUnderOvertime6"),
    ("50457", "50456", "overUnderOvertime7"),
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

_UNLIMITED_DETAIL_CONCURRENCY = 10
_MIN_DETAIL_CONCURRENCY = 2
_PLAYER_LEAGUE_PREFIX = "poeni igrača"
_BASKETBALL_LEAGUE_PREFIX = "košarka"
_CANONICAL_LEAGUES = {
    "nba": "nba",
    "usa nba": "nba",
    "nba play offs": "nba",
    "nba promotion play offs": "nba",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "aba liga winners stage": "aba_liga",
    "aba liga losers stage": "aba_liga",
    "aba liga plej of": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
    "argentina": "argentina_1",
    "argentina 1": "argentina_1",
    "puerto rico": "portoriko_1",
    "portoriko 1": "portoriko_1",
}


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _normalize_league_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(league_name: str) -> str:
    raw = league_name.lower().strip()
    for prefix in (_PLAYER_LEAGUE_PREFIX, _BASKETBALL_LEAGUE_PREFIX):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :].strip(" ~-")
            break
    normalized = _normalize_league_key(raw)
    if not normalized:
        return "basketball"
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


def _parse_match_detail(match: dict) -> list[RawOddsData]:
    """Parse a single match detail response into RawOddsData for all threshold lines."""
    results: list[RawOddsData] = []

    league_name = match.get("leagueName", "")
    if _PLAYER_LEAGUE_PREFIX not in league_name.lower():
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
            bookmaker_id="maxbet",
            league_id=league_id,
            sport="basketball",
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


def _parse_game_total_match(match: dict) -> list[RawOddsData]:
    return _parse_game_total_lines(match, _GAME_TOTAL_LINES, "game_total")


def _parse_game_total_ot_match(match: dict) -> list[RawOddsData]:
    return _parse_game_total_lines(match, _GAME_TOTAL_OT_LINES, "game_total_ot")


def _parse_game_total_lines(
    match: dict,
    lines: list[tuple[str, str, str]],
    market_type: str,
) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    league_name = match.get("leagueName", "")
    if not league_name or _PLAYER_LEAGUE_PREFIX in league_name.lower():
        return results

    home_team = match.get("home", "").strip()
    away_team = match.get("away", "").strip()
    if not home_team or not away_team:
        return results

    params = match.get("params", {})
    odds = match.get("odds", {})
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(league_name)

    for over_code, under_code, param_key in lines:
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
                sport="basketball",
                home_team=home_team,
                away_team=away_team,
                market_type=market_type,
                player_name=None,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=start_time,
            )
        )

    return results


def _get_player_match_ids(matches: list[dict]) -> list[int]:
    """Extract supported player-props match IDs from the bulk listing."""
    ids: list[int] = []
    for m in matches:
        if _PLAYER_LEAGUE_PREFIX not in m.get("leagueName", "").lower():
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


class MaxBetScraper(BaseScraper):
    """Real scraper for MaxBet basketball player props and full-game totals."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "maxbet"

    def get_bookmaker_name(self) -> str:
        return "MaxBet"

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
                logger.warning("MaxBet: failed to fetch detail for match %s", match_id)
                return []

        return _parse_match_detail(detail)

    async def _fetch_list_matches(self, url: str, label: str) -> list[dict]:
        try:
            data = await self._http.get_json(
                url,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("MaxBet: failed to fetch %s list", label, exc_info=True)
            return []

        matches = data.get("esMatches", [])
        if not matches:
            logger.info("MaxBet: no %s matches found", label)
        return matches

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        player_matches, total_matches = await asyncio.gather(
            self._fetch_list_matches(_PLAYER_LIST_URL, "player props"),
            self._fetch_list_matches(_TOTALS_LIST_URL, "game totals"),
        )

        regular_total_results: list[RawOddsData] = []
        ot_total_results: list[RawOddsData] = []
        total_match_count = 0
        for match in total_matches:
            regular_parsed = _parse_game_total_match(match)
            ot_parsed = _parse_game_total_ot_match(match)
            if regular_parsed or ot_parsed:
                total_match_count += 1
            regular_total_results.extend(regular_parsed)
            ot_total_results.extend(ot_parsed)

        player_results: list[RawOddsData] = []
        match_ids = _get_player_match_ids(player_matches)
        concurrency = 0
        if match_ids:
            concurrency = _get_detail_fetch_concurrency(self._http, len(match_ids))
            semaphore = asyncio.Semaphore(concurrency)
            detail_results = await asyncio.gather(
                *(self._fetch_match_detail(mid, semaphore) for mid in match_ids)
            )
            player_results = [item for batch in detail_results for item in batch]
        else:
            logger.info("MaxBet: no player points matches found")

        total_results = regular_total_results + ot_total_results
        results = total_results + player_results
        logger.info(
            "MaxBet scraped %d odds (%d regular-time game totals, %d OT-inclusive game totals from %d matches, %d player odds from %d players, detail concurrency=%d)",
            len(results),
            len(regular_total_results),
            len(ot_total_results),
            total_match_count,
            len(player_results),
            len(match_ids),
            concurrency,
        )
        return results
