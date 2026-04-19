from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)


# ── Per-sport spec ──────────────────────────────────────────────────────
#
# Each SportSpec captures everything that is sport-specific about MaxBet's
# offer, so adding a new sport (football, tennis…) is a single dict entry.
# The bulk-fetch + parse pipeline lives in MaxBetScraper and is sport-agnostic.


@dataclass(frozen=True)
class ThresholdLine:
    over_code: str
    under_code: str
    param_key: str
    market_type: str


@dataclass(frozen=True)
class FixedMilestone:
    over_code: str
    threshold: float
    market_type: str = "player_points_milestones"


@dataclass(frozen=True)
class GameTotalLine:
    over_code: str
    under_code: str
    param_key: str


@dataclass(frozen=True)
class SportSpec:
    sport: str
    # Lists fetched concurrently. The "player" list provides player-prop matches
    # (we only need their IDs + filter params); the "totals" list provides
    # game-total matches whose odds we read directly from the list response.
    player_list_url: str
    totals_list_url: str | None
    # Bulk-detail endpoint accepting a comma-joined `matchIdsToken=` query param.
    # Returns a JSON list of fully-populated match objects (parity with
    # /restapi/offer/sr/match/{id}).
    bulk_detail_url: str
    # League-name prefix used to identify player-prop matches in the player list.
    player_league_prefix: str
    # League-name prefix used to identify game-total matches in the totals list.
    # When empty (""), every list match that is not a player-prop match qualifies.
    totals_league_prefix: str
    threshold_lines: tuple[ThresholdLine, ...]
    fixed_milestones: tuple[FixedMilestone, ...]
    game_total_lines: tuple[GameTotalLine, ...]
    game_total_ot_lines: tuple[GameTotalLine, ...]
    game_total_market_type: str
    game_total_ot_market_type: str
    canonical_leagues: dict[str, str] = field(default_factory=dict)


# ── Basketball spec ─────────────────────────────────────────────────────

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

# Tip type codes from MaxBet's ttg_lang endpoint (shared iBet platform).
_BASKETBALL_THRESHOLD_LINES: tuple[ThresholdLine, ...] = (
    ThresholdLine("51679", "51681", "ouPlPoints", "player_points"),
    ThresholdLine("55253", "55255", "ouPlP2", "player_points"),
    ThresholdLine("55256", "55258", "ouPlP3", "player_points"),
    ThresholdLine("51685", "51687", "ouPlRebounds", "player_rebounds"),
    ThresholdLine("51682", "51684", "ouPlAssists", "player_assists"),
    ThresholdLine("51688", "51690", "ouPl3Points", "player_3points"),
    ThresholdLine("55672", "55674", "ouPlSt", "player_steals"),
    ThresholdLine("55681", "55683", "ouPlB", "player_blocks"),
    ThresholdLine("55244", "55246", "ouPlTPR", "player_points_rebounds"),
    ThresholdLine("55247", "55249", "ouPlTPA", "player_points_assists"),
    ThresholdLine("55250", "55252", "ouPlTRA", "player_rebounds_assists"),
    ThresholdLine("55215", "55217", "ouPlTPRA", "player_points_rebounds_assists"),
)

_BASKETBALL_FIXED_MILESTONES: tuple[FixedMilestone, ...] = (
    FixedMilestone("54096", 4.5),
    FixedMilestone("54101", 9.5),
    FixedMilestone("54106", 14.5),
    FixedMilestone("54111", 19.5),
    FixedMilestone("54116", 24.5),
    FixedMilestone("54121", 29.5),
    FixedMilestone("54126", 34.5),
    FixedMilestone("54131", 39.5),
    FixedMilestone("54136", 44.5),
    FixedMilestone("54141", 49.5),
    FixedMilestone("57454", 59.5),
)

_BASKETBALL_GAME_TOTAL_LINES: tuple[GameTotalLine, ...] = (
    GameTotalLine("227", "228", "overUnder"),
    GameTotalLine("429", "427", "overUnder2"),
)

# OT-inclusive full-game totals confirmed from the live basketball list feed.
_BASKETBALL_GAME_TOTAL_OT_LINES: tuple[GameTotalLine, ...] = (
    GameTotalLine("50445", "50444", "overUnderOvertime"),
    GameTotalLine("50447", "50446", "overUnderOvertime2"),
    GameTotalLine("50449", "50448", "overUnderOvertime3"),
    GameTotalLine("50451", "50450", "overUnderOvertime4"),
    GameTotalLine("50453", "50452", "overUnderOvertime5"),
    GameTotalLine("50455", "50454", "overUnderOvertime6"),
    GameTotalLine("50457", "50456", "overUnderOvertime7"),
)

_BASKETBALL_CANONICAL_LEAGUES = {
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

_BASKETBALL_SPEC = SportSpec(
    sport="basketball",
    player_list_url="https://www.maxbet.rs/restapi/offer/sr/sport/SK/mob",
    totals_list_url="https://www.maxbet.rs/restapi/offer/sr/sport/B/mob",
    bulk_detail_url="https://www.maxbet.rs/restapi/offer/sr/matches/by-ids",
    player_league_prefix="poeni igrača",
    totals_league_prefix="košarka",
    threshold_lines=_BASKETBALL_THRESHOLD_LINES,
    fixed_milestones=_BASKETBALL_FIXED_MILESTONES,
    game_total_lines=_BASKETBALL_GAME_TOTAL_LINES,
    game_total_ot_lines=_BASKETBALL_GAME_TOTAL_OT_LINES,
    game_total_market_type="game_total",
    game_total_ot_market_type="game_total_ot",
    canonical_leagues=_BASKETBALL_CANONICAL_LEAGUES,
)

_SPORT_SPECS: dict[str, SportSpec] = {
    "basketball": _BASKETBALL_SPEC,
}


# ── Backward-compatible module-level constants (kept for existing tests) ──

_PLAYER_LIST_URL = _BASKETBALL_SPEC.player_list_url
_TOTALS_LIST_URL = _BASKETBALL_SPEC.totals_list_url
_BULK_DETAIL_URL = _BASKETBALL_SPEC.bulk_detail_url

_PLAYER_LEAGUE_PREFIX = _BASKETBALL_SPEC.player_league_prefix
_BASKETBALL_LEAGUE_PREFIX = _BASKETBALL_SPEC.totals_league_prefix
_CANONICAL_LEAGUES = _BASKETBALL_CANONICAL_LEAGUES

_THRESHOLD_LINES = [
    (line.over_code, line.under_code, line.param_key, line.market_type)
    for line in _BASKETBALL_THRESHOLD_LINES
]
_GAME_TOTAL_LINES = [
    (line.over_code, line.under_code, line.param_key)
    for line in _BASKETBALL_GAME_TOTAL_LINES
]
_GAME_TOTAL_OT_LINES = [
    (line.over_code, line.under_code, line.param_key)
    for line in _BASKETBALL_GAME_TOTAL_OT_LINES
]
_FIXED_POINTS_LADDERS = [(m.over_code, m.threshold) for m in _BASKETBALL_FIXED_MILESTONES]
_LIST_MATCH_PARAM_KEYS = {line.param_key for line in _BASKETBALL_THRESHOLD_LINES}


# ── Helpers ─────────────────────────────────────────────────────────────


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _normalize_league_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(league_name: str, spec: SportSpec = _BASKETBALL_SPEC) -> str:
    raw = league_name.lower().strip()
    for prefix in (spec.player_league_prefix, spec.totals_league_prefix):
        if prefix and raw.startswith(prefix):
            raw = raw[len(prefix):].strip(" ~-")
            break
    normalized = _normalize_league_key(raw)
    if not normalized:
        return spec.sport
    return spec.canonical_leagues.get(normalized, normalized.replace(" ", "_"))


def _is_player_match(match: dict, spec: SportSpec) -> bool:
    league_name = (match.get("leagueName") or "").lower()
    return spec.player_league_prefix in league_name


def _has_supported_player_param(match: dict, spec: SportSpec) -> bool:
    params = match.get("params") or {}
    return any(params.get(line.param_key) for line in spec.threshold_lines)


def _parse_player_match(match: dict, spec: SportSpec) -> list[RawOddsData]:
    """Parse a single player-prop match (from the bulk-detail response)."""
    if not _is_player_match(match, spec):
        return []

    params = match.get("params") or {}
    odds = match.get("odds") or {}
    player_name = match.get("home", "")
    team = match.get("away", "")
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(match.get("leagueName", ""), spec)

    results: list[RawOddsData] = []

    def emit(market_type: str, threshold: float, over: float | None, under: float | None) -> None:
        results.append(
            RawOddsData(
                bookmaker_id="maxbet",
                league_id=league_id,
                sport=spec.sport,
                home_team=team,
                away_team=player_name,
                market_type=market_type,
                player_name=player_name,
                threshold=threshold,
                over_odds=over,
                under_odds=under,
                start_time=start_time,
            )
        )

    for line in spec.threshold_lines:
        threshold_str = params.get(line.param_key)
        if not threshold_str:
            continue
        try:
            threshold = float(threshold_str)
        except (ValueError, TypeError):
            continue

        over_odds = odds.get(line.over_code)
        under_odds = odds.get(line.under_code)
        if over_odds is None and under_odds is None:
            continue

        emit(line.market_type, threshold, over_odds, under_odds)

    for milestone in spec.fixed_milestones:
        over_odds = odds.get(milestone.over_code)
        if over_odds is None:
            continue
        emit(milestone.market_type, milestone.threshold, over_odds, None)

    return results


def _parse_game_total_lines_for_spec(
    match: dict,
    spec: SportSpec,
    lines: tuple[GameTotalLine, ...],
    market_type: str,
) -> list[RawOddsData]:
    league_name = match.get("leagueName") or ""
    if not league_name:
        return []
    if _is_player_match(match, spec):
        return []

    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    if not home_team or not away_team:
        return []

    params = match.get("params") or {}
    odds = match.get("odds") or {}
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(league_name, spec)

    results: list[RawOddsData] = []
    for line in lines:
        threshold_str = params.get(line.param_key)
        if not threshold_str:
            continue
        try:
            threshold = float(threshold_str)
        except (ValueError, TypeError):
            continue

        over_odds = odds.get(line.over_code)
        under_odds = odds.get(line.under_code)
        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="maxbet",
                league_id=league_id,
                sport=spec.sport,
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


def _get_player_match_ids_for_spec(matches: list[dict], spec: SportSpec) -> list[int]:
    ids: list[int] = []
    for m in matches:
        if not _is_player_match(m, spec):
            continue
        if not _has_supported_player_param(m, spec):
            continue
        match_id = m.get("id")
        if match_id is not None:
            ids.append(match_id)
    return ids


# ── Backward-compatible module-level wrappers (basketball-only) ──────────


def _parse_match_detail(match: dict) -> list[RawOddsData]:
    return _parse_player_match(match, _BASKETBALL_SPEC)


def _parse_game_total_match(match: dict) -> list[RawOddsData]:
    return _parse_game_total_lines_for_spec(
        match,
        _BASKETBALL_SPEC,
        _BASKETBALL_SPEC.game_total_lines,
        _BASKETBALL_SPEC.game_total_market_type,
    )


def _parse_game_total_ot_match(match: dict) -> list[RawOddsData]:
    return _parse_game_total_lines_for_spec(
        match,
        _BASKETBALL_SPEC,
        _BASKETBALL_SPEC.game_total_ot_lines,
        _BASKETBALL_SPEC.game_total_ot_market_type,
    )


def _get_player_match_ids(matches: list[dict]) -> list[int]:
    return _get_player_match_ids_for_spec(matches, _BASKETBALL_SPEC)


# ── Scraper ─────────────────────────────────────────────────────────────


class MaxBetScraper(BaseScraper):
    """Real scraper for MaxBet, currently shipping basketball.

    Adding a new sport means adding a new SportSpec entry to _SPORT_SPECS;
    the per-sport pipeline is identical."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "maxbet"

    def get_bookmaker_name(self) -> str:
        return "MaxBet"

    def get_supported_leagues(self) -> list[str]:
        return list(_SPORT_SPECS.keys())

    async def _fetch_list(self, url: str, label: str) -> list[dict]:
        try:
            data = await self._http.get_json(
                url, params=_DEFAULT_PARAMS, headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("MaxBet: failed to fetch %s list", label, exc_info=True)
            return []

        matches = data.get("esMatches") if isinstance(data, dict) else None
        if not matches:
            logger.info("MaxBet: no %s matches found", label)
            return []
        return matches

    async def _fetch_bulk_details(
        self,
        bulk_url: str,
        match_ids: list[int],
    ) -> list[dict]:
        if not match_ids:
            return []
        params = {**_DEFAULT_PARAMS, "matchIdsToken": ",".join(str(i) for i in match_ids)}
        try:
            data = await self._http.get_json(
                bulk_url, params=params, headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning(
                "MaxBet: bulk detail fetch failed for %d ids", len(match_ids), exc_info=True,
            )
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Defensive: some iBet endpoints return {id: match}; normalize.
            return [v for v in data.values() if isinstance(v, dict)]
        return []

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        spec = _SPORT_SPECS.get(league_id)
        if spec is None:
            return []

        list_tasks: list = [
            self._fetch_list(spec.player_list_url, f"{spec.sport} player props")
        ]
        if spec.totals_list_url:
            list_tasks.append(
                self._fetch_list(spec.totals_list_url, f"{spec.sport} game totals")
            )

        list_results = await asyncio.gather(*list_tasks)
        player_matches = list_results[0]
        total_matches: list[dict] = list_results[1] if len(list_results) > 1 else []

        # Game totals are read directly from the totals list (no detail fetch needed).
        regular_total_results: list[RawOddsData] = []
        ot_total_results: list[RawOddsData] = []
        total_match_count = 0
        for match in total_matches:
            regular = _parse_game_total_lines_for_spec(
                match, spec, spec.game_total_lines, spec.game_total_market_type,
            )
            ot = _parse_game_total_lines_for_spec(
                match, spec, spec.game_total_ot_lines, spec.game_total_ot_market_type,
            )
            if regular or ot:
                total_match_count += 1
            regular_total_results.extend(regular)
            ot_total_results.extend(ot)

        # Player props: single bulk-detail GET replaces the previous N+1 loop.
        player_results: list[RawOddsData] = []
        match_ids = _get_player_match_ids_for_spec(player_matches, spec)
        if match_ids:
            details = await self._fetch_bulk_details(spec.bulk_detail_url, match_ids)
            for detail in details:
                player_results.extend(_parse_player_match(detail, spec))
        else:
            logger.info("MaxBet: no %s player-prop matches found", spec.sport)

        results = regular_total_results + ot_total_results + player_results
        logger.info(
            "MaxBet scraped %d %s odds (%d regular game totals, %d OT game totals from %d matches, "
            "%d player odds from %d players via bulk-detail)",
            len(results),
            spec.sport,
            len(regular_total_results),
            len(ot_total_results),
            total_match_count,
            len(player_results),
            len(match_ids),
        )
        return results
