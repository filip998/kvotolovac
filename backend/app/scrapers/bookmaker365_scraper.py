from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData, RawOutcomeOffer
from ..services.scrape_window import (
    configured_lookahead_hours,
    current_utc_time,
    lookahead_cutoff,
)
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_REGULAR_LEAGUES_URL = "https://ibet2.365.rs/restapi/offer/sr/categories/sport/B/l"
_PLAYER_LEAGUES_URL = "https://ibet2.365.rs/restapi/offer/sr/categories/sport/SK/l"
_REGULAR_LEAGUE_PREVIEW_URL = (
    "https://ibet2.365.rs/restapi/offer/sr/sport/B/league/{league_id}/mob"
)
_REGULAR_BULK_URL = "https://ibet2.365.rs/restapi/offer/sr/sport/B/mob"
_PLAYER_LEAGUE_PREVIEW_URL = (
    "https://ibet2.365.rs/restapi/offer/sr/sport/SK/league/{league_id}/mob"
)
_PLAYER_BULK_URL = "https://ibet2.365.rs/restapi/offer/sr/sport/SK/mob"
_FOOTBALL_LEAGUES_URL = "https://ibet2.365.rs/restapi/offer/sr/categories/sport/S/l"
_FOOTBALL_LEAGUE_PREVIEW_URL = (
    "https://ibet2.365.rs/restapi/offer/sr/sport/S/league/{league_id}/mob"
)
_FOOTBALL_BULK_URL = "https://ibet2.365.rs/restapi/offer/sr/sport/S/mob"

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Origin": "https://www.365.rs",
    "Referer": "https://www.365.rs/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}
_DEFAULT_PARAMS: dict[str, str] = {
    "annex": "0",
    "mobileVersion": "2.32.10.5",
    "locale": "sr",
}

_BOOKMAKER_ID = "365"
_FETCH_CONCURRENCY = 8


@dataclass(frozen=True)
class ThresholdLine:
    over_code: str
    under_code: str
    param_key: str
    market_type: str


@dataclass(frozen=True)
class LeagueCategory:
    league_id: str
    league_name: str
    match_count: int


@dataclass(frozen=True)
class MatchContext:
    league_id: str
    home_team: str
    away_team: str
    start_time: str | None


@dataclass(frozen=True)
class MatchupIndex:
    by_match_code: dict[int, MatchContext]
    by_team_slot: dict[tuple[str, int], MatchContext]


@dataclass(frozen=True)
class BasketballParseResult:
    rows: list[RawOddsData]
    regular_rows: list[RawOddsData]
    player_rows: list[RawOddsData]
    handicap_rows: list[RawOddsData]
    unmatched_player_matches: int


_PLAYER_THRESHOLD_LINES: tuple[ThresholdLine, ...] = (
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
    ThresholdLine("56169", "56171", "ouPlTo", "player_turnovers"),
)
_GAME_TOTAL_LINES: tuple[ThresholdLine, ...] = (
    ThresholdLine("227", "228", "overUnder", "game_total"),
    ThresholdLine("429", "427", "overUnder2", "game_total"),
)
_GAME_TOTAL_OT_LINES: tuple[ThresholdLine, ...] = (
    ThresholdLine("50445", "50444", "overUnderOvertime", "game_total_ot"),
    ThresholdLine("50447", "50446", "overUnderOvertime2", "game_total_ot"),
    ThresholdLine("50449", "50448", "overUnderOvertime3", "game_total_ot"),
    ThresholdLine("50451", "50450", "overUnderOvertime4", "game_total_ot"),
    ThresholdLine("50453", "50452", "overUnderOvertime5", "game_total_ot"),
    ThresholdLine("50455", "50454", "overUnderOvertime6", "game_total_ot"),
    ThresholdLine("50457", "50456", "overUnderOvertime7", "game_total_ot"),
)

# OT-inclusive Asian handicap (full game). Bookmaker365's existing
# /sport/B/league/{id}/mob preview already returns
# params['handicapOvertime'] through ['handicapOvertime13'] and the matching
# odds codes 50430–50443 (lines 1–7) plus 51624–51635 (lines 8–13). 365 is
# on the same Tipster white-label platform as MerkurXTip / OktagonBet /
# BetOle / SoccerBet and shares their convention: ``handicapOvertime`` is
# the home team's *signed* Asian-handicap line (negative = home favourite,
# positive = home underdog — same as Mozzart's ``Hendikep -X`` UI).  Our
# canonical analyzer convention is the *opposite* (positive threshold =
# home expected margin), so we negate the parsed value when storing.
#
# The bet codes are also *opposite* of historical comments: in each pair
# the *even* code (50430, 50432, ..., 50442, 51624, ...) holds the "2"
# outcome which pays when the **home team covers**, and the *odd* code
# (50431, 50433, ..., 50443, 51625, ...) pays when the **away team
# covers**.  Verified live by checking the ladder direction (home cover
# odds fall as the line grows in the favourite's favour).
_HANDICAP_OT_LINES: tuple[ThresholdLine, ...] = (
    ThresholdLine("50430", "50431", "handicapOvertime",  "home_handicap_ot"),
    ThresholdLine("50432", "50433", "handicapOvertime2", "home_handicap_ot"),
    ThresholdLine("50434", "50435", "handicapOvertime3", "home_handicap_ot"),
    ThresholdLine("50436", "50437", "handicapOvertime4", "home_handicap_ot"),
    ThresholdLine("50438", "50439", "handicapOvertime5", "home_handicap_ot"),
    ThresholdLine("50440", "50441", "handicapOvertime6", "home_handicap_ot"),
    ThresholdLine("50442", "50443", "handicapOvertime7", "home_handicap_ot"),
    ThresholdLine("51624", "51625", "handicapOvertime8", "home_handicap_ot"),
    ThresholdLine("51626", "51627", "handicapOvertime9", "home_handicap_ot"),
    ThresholdLine("51628", "51629", "handicapOvertime10", "home_handicap_ot"),
    ThresholdLine("51630", "51631", "handicapOvertime11", "home_handicap_ot"),
    ThresholdLine("51632", "51633", "handicapOvertime12", "home_handicap_ot"),
    ThresholdLine("51634", "51635", "handicapOvertime13", "home_handicap_ot"),
)

_SUPPORTED_PLAYER_PARAM_KEYS = {line.param_key for line in _PLAYER_THRESHOLD_LINES}
_PLAYER_LEAGUE_SUFFIXES = (
    " broj poena skokova asistencija",
    " muckalica igraci",
)

# Football outcome lane.  365's per-league preview surfaces the same
# Tipster-style codes that the SoccerBet/MerkurXTip/OktagonBet/BetOle
# siblings use directly in the match's ``odds`` map — no detail call
# needed.
_FOOTBALL_OUTCOME_CODES: dict[str, tuple[str, str, float | None, str]] = {
    "1": ("football_result", "home", None, "1"),
    "2": ("football_result", "draw", None, "X"),
    "3": ("football_result", "away", None, "2"),
    "7": ("football_double_chance", "home_or_draw", None, "1X"),
    "8": ("football_double_chance", "home_or_away", None, "12"),
    "9": ("football_double_chance", "draw_or_away", None, "X2"),
    "22": ("football_total_goals", "under", 2.5, "0-2"),
    "24": ("football_total_goals", "over", 2.5, "3+"),
}
_CANONICAL_LEAGUES: dict[str, str] = {
    "nba play off": "nba",
    "nba play in": "nba",
    "evroliga": "euroleague",
    "evroliga play in": "euroleague",
    "evroliga play off": "euroleague",
    "evroliga play offs": "euroleague",
    "turska": "turkey",
    "italija": "italy",
    "nemacka": "germany",
}


def _parse_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_start_time(epoch_ms: object) -> str | None:
    parsed = _parse_int(epoch_ms)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed / 1000, tz=timezone.utc).isoformat()


def _normalize_league_key(raw_name: str | None) -> str:
    normalized = normalize_identity_text(raw_name)
    for suffix in _PLAYER_LEAGUE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
            break
    return normalized


def _extract_league_id(raw_name: str | None, *, default: str = "basketball") -> str:
    normalized = _normalize_league_key(raw_name)
    if not normalized:
        return default
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


def _is_supported_player_league(raw_name: str | None) -> bool:
    normalized = normalize_identity_text(raw_name)
    return "broj poena" in normalized


def _within_lookahead(match: dict, cutoff_ms: int) -> bool:
    kickoff = _parse_int(match.get("kickOffTime"))
    return kickoff is None or kickoff <= cutoff_ms


def _bulk_params() -> dict[str, str]:
    return {
        **_DEFAULT_PARAMS,
        "hours": str(configured_lookahead_hours()),
    }


def _collect_leagues(data: dict, *, player_view: bool) -> list[LeagueCategory]:
    categories = data.get("categories") or []
    leagues: list[LeagueCategory] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        league_id = category.get("id")
        league_name = category.get("name")
        if not league_id or not isinstance(league_name, str) or not league_name.strip():
            continue
        if player_view and not _is_supported_player_league(league_name):
            continue
        match_count = _parse_int(category.get("count")) or 0
        if match_count <= 0:
            continue
        leagues.append(
            LeagueCategory(
                league_id=str(league_id),
                league_name=league_name.strip(),
                match_count=match_count,
            )
        )
    return leagues


def _select_regular_leagues(
    regular_leagues: list[LeagueCategory],
    player_leagues: list[LeagueCategory],
) -> list[LeagueCategory]:
    del player_leagues
    return regular_leagues


def _build_matchup_index(matches: list[dict]) -> MatchupIndex:
    by_match_code: dict[int, MatchContext] = {}
    by_team_slot: dict[tuple[str, int], MatchContext] = {}

    for match in matches:
        kickoff = _parse_int(match.get("kickOffTime"))
        home_team = (match.get("home") or "").strip()
        away_team = (match.get("away") or "").strip()
        if kickoff is None or not home_team or not away_team:
            continue

        context = MatchContext(
            league_id=_extract_league_id(match.get("leagueName")),
            home_team=home_team,
            away_team=away_team,
            start_time=_parse_start_time(kickoff),
        )

        match_code = _parse_int(match.get("matchCode"))
        if match_code is not None:
            by_match_code[match_code] = context

        by_team_slot[(normalize_identity_text(home_team), kickoff)] = context
        by_team_slot[(normalize_identity_text(away_team), kickoff)] = context

    return MatchupIndex(by_match_code=by_match_code, by_team_slot=by_team_slot)


def _resolve_matchup_context(match: dict, matchup_index: MatchupIndex) -> MatchContext | None:
    super_code = _parse_int(match.get("superCode"))
    if super_code is not None:
        context = matchup_index.by_match_code.get(super_code)
        if context is not None:
            return context

    kickoff = _parse_int(match.get("kickOffTime"))
    team_name = normalize_identity_text(match.get("away"))
    if kickoff is None or not team_name:
        return None
    return matchup_index.by_team_slot.get((team_name, kickoff))


def _parse_total_match(match: dict, lines: tuple[ThresholdLine, ...]) -> list[RawOddsData]:
    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    if not home_team or not away_team:
        return []

    params = match.get("params") or {}
    odds = match.get("odds") or {}
    if not isinstance(params, dict) or not isinstance(odds, dict):
        return []

    results: list[RawOddsData] = []
    for line in lines:
        threshold = _parse_float(params.get(line.param_key))
        if threshold is None:
            continue
        if line.market_type == "home_handicap_ot":
            # Source carries the home-perspective signed line (negative =
            # home favourite); analyzer expects positive = home favoured.
            threshold = -threshold

        over_odds = _parse_float(odds.get(line.over_code))
        under_odds = _parse_float(odds.get(line.under_code))
        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id=_BOOKMAKER_ID,
                league_id=_extract_league_id(match.get("leagueName")),
                sport="basketball",
                home_team=home_team,
                away_team=away_team,
                market_type=line.market_type,
                player_name=None,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=_parse_start_time(match.get("kickOffTime")),
            )
        )

    return results


def _has_supported_player_param(match: dict) -> bool:
    params = match.get("params") or {}
    if not isinstance(params, dict):
        return False
    return any(params.get(param_key) for param_key in _SUPPORTED_PLAYER_PARAM_KEYS)


def _parse_player_match(match: dict, matchup_index: MatchupIndex) -> list[RawOddsData]:
    player_name = (match.get("home") or "").strip()
    if not player_name or not _has_supported_player_param(match):
        return []

    context = _resolve_matchup_context(match, matchup_index)
    if context is None:
        return []

    params = match.get("params") or {}
    odds = match.get("odds") or {}
    if not isinstance(params, dict) or not isinstance(odds, dict):
        return []

    results: list[RawOddsData] = []
    for line in _PLAYER_THRESHOLD_LINES:
        threshold = _parse_float(params.get(line.param_key))
        if threshold is None:
            continue

        over_odds = _parse_float(odds.get(line.over_code))
        under_odds = _parse_float(odds.get(line.under_code))
        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id=_BOOKMAKER_ID,
                league_id=context.league_id,
                sport="basketball",
                home_team=context.home_team,
                away_team=context.away_team,
                market_type=line.market_type,
                player_name=player_name,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=context.start_time,
            )
        )

    return results


def _coerce_positive_odds(value: object) -> float | None:
    parsed = _parse_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _parse_football_outcome_match(match: dict) -> list[RawOutcomeOffer]:
    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    if not home_team or not away_team:
        return []

    odds_map = match.get("odds") or {}
    if not isinstance(odds_map, dict):
        return []

    league_id = _extract_league_id(match.get("leagueName", ""), default="football")
    start_time = _parse_start_time(match.get("kickOffTime"))
    results: list[RawOutcomeOffer] = []

    for code, (market_type, outcome_code, line, raw_label) in _FOOTBALL_OUTCOME_CODES.items():
        odds = _coerce_positive_odds(odds_map.get(code))
        if odds is None:
            continue
        results.append(
            RawOutcomeOffer(
                bookmaker_id=_BOOKMAKER_ID,
                league_id=league_id,
                sport="football",
                home_team=home_team,
                away_team=away_team,
                market_type=market_type,
                outcome_code=outcome_code,
                odds=odds,
                line=line,
                raw_label=raw_label,
                start_time=start_time,
            )
        )

    return results


def _parse_basketball_matches(
    regular_matches: list[dict],
    player_matches: list[dict],
) -> BasketballParseResult:
    matchup_index = _build_matchup_index(regular_matches)

    regular_total_results: list[RawOddsData] = []
    ot_total_results: list[RawOddsData] = []
    handicap_results: list[RawOddsData] = []
    for match in regular_matches:
        regular_total_results.extend(_parse_total_match(match, _GAME_TOTAL_LINES))
        ot_total_results.extend(_parse_total_match(match, _GAME_TOTAL_OT_LINES))
        handicap_results.extend(_parse_total_match(match, _HANDICAP_OT_LINES))

    player_results: list[RawOddsData] = []
    unmatched_player_matches = 0
    for match in player_matches:
        parsed = _parse_player_match(match, matchup_index)
        if not parsed and _has_supported_player_param(match):
            unmatched_player_matches += 1
        player_results.extend(parsed)

    regular_rows = regular_total_results + ot_total_results
    rows = regular_rows + handicap_results + player_results
    return BasketballParseResult(
        rows=rows,
        regular_rows=regular_rows,
        player_rows=player_results,
        handicap_rows=handicap_results,
        unmatched_player_matches=unmatched_player_matches,
    )


def _basketball_bulk_is_usable(
    regular_matches: list[dict],
    player_matches: list[dict],
    parsed: BasketballParseResult,
) -> bool:
    if not regular_matches:
        return False
    market_types = {row.market_type for row in parsed.regular_rows}
    required_regular_markets = {"game_total", "game_total_ot"}
    if not required_regular_markets.issubset(market_types):
        return False
    if not parsed.handicap_rows:
        return False
    if player_matches and (not parsed.player_rows or parsed.unmatched_player_matches):
        return False
    return True


def _football_bulk_is_usable(rows: list[RawOutcomeOffer]) -> bool:
    market_types = {row.market_type for row in rows}
    return {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }.issubset(market_types)


class Bookmaker365Scraper(BaseScraper):
    """365 basketball + football scraper backed by the public iBet-style offer API."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return _BOOKMAKER_ID

    def get_bookmaker_name(self) -> str:
        return "365"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_league_categories(
        self,
        url: str,
        *,
        label: str,
        player_view: bool = False,
    ) -> list[LeagueCategory]:
        try:
            data = await self._http.get_json(
                url,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("365: failed to fetch %s categories", label, exc_info=True)
            return []

        leagues = _collect_leagues(data, player_view=player_view)
        if not leagues:
            logger.info("365: no %s leagues found", label)
        return leagues

    async def _fetch_bulk_matches(
        self,
        url: str,
        *,
        label: str,
        cutoff_ms: int,
    ) -> list[dict] | None:
        try:
            data = await self._http.get_json(
                url,
                params=_bulk_params(),
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("365: failed to fetch %s bulk feed", label, exc_info=True)
            return None

        matches = data.get("esMatches") if isinstance(data, dict) else None
        if not isinstance(matches, list):
            logger.warning("365: %s bulk feed missing esMatches list", label)
            return None
        return [
            match
            for match in matches
            if isinstance(match, dict) and _within_lookahead(match, cutoff_ms)
        ]

    async def _fetch_league_preview(
        self,
        url_template: str,
        league: LeagueCategory,
        *,
        label: str,
        cutoff_ms: int,
    ) -> list[dict]:
        try:
            data = await self._http.get_json(
                url_template.format(league_id=league.league_id),
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning(
                "365: failed to fetch %s preview for league %s",
                label,
                league.league_id,
                exc_info=True,
            )
            return []

        matches = data.get("esMatches") if isinstance(data, dict) else None
        if not matches:
            return []
        return [match for match in matches if _within_lookahead(match, cutoff_ms)]

    async def _fetch_previews(
        self,
        leagues: list[LeagueCategory],
        *,
        url_template: str,
        label: str,
        cutoff_ms: int,
    ) -> list[dict]:
        if not leagues:
            return []

        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch_one(league: LeagueCategory) -> list[dict]:
            async with semaphore:
                return await self._fetch_league_preview(
                    url_template,
                    league,
                    label=label,
                    cutoff_ms=cutoff_ms,
                )

        results = await asyncio.gather(*(fetch_one(league) for league in leagues))
        return [match for preview in results for match in preview]

    async def _scrape_odds_bulk(self, cutoff_ms: int) -> BasketballParseResult | None:
        regular_matches, player_matches = await asyncio.gather(
            self._fetch_bulk_matches(
                _REGULAR_BULK_URL,
                label="regular basketball",
                cutoff_ms=cutoff_ms,
            ),
            self._fetch_bulk_matches(
                _PLAYER_BULK_URL,
                label="basketball player props",
                cutoff_ms=cutoff_ms,
            ),
        )
        if regular_matches is None or player_matches is None:
            return None

        parsed = _parse_basketball_matches(regular_matches, player_matches)
        if not _basketball_bulk_is_usable(regular_matches, player_matches, parsed):
            logger.warning(
                (
                    "365: basketball bulk feed incomplete "
                    "(regular matches=%d, player matches=%d, parsed rows=%d); falling back"
                ),
                len(regular_matches),
                len(player_matches),
                len(parsed.rows),
            )
            return None
        logger.info(
            (
                "365: using basketball bulk feeds (%d regular matches, "
                "%d player matches, %d rows, unmatched player rows=%d)"
            ),
            len(regular_matches),
            len(player_matches),
            len(parsed.rows),
            parsed.unmatched_player_matches,
        )
        return parsed

    async def _scrape_odds_per_league(self, cutoff_ms: int) -> BasketballParseResult:
        regular_leagues_task = self._fetch_league_categories(
            _REGULAR_LEAGUES_URL,
            label="regular basketball",
        )
        player_leagues_task = self._fetch_league_categories(
            _PLAYER_LEAGUES_URL,
            label="basketball player props",
            player_view=True,
        )
        regular_leagues, player_leagues = await asyncio.gather(
            regular_leagues_task,
            player_leagues_task,
        )
        selected_regular_leagues = _select_regular_leagues(regular_leagues, player_leagues)

        regular_matches_task = self._fetch_previews(
            selected_regular_leagues,
            url_template=_REGULAR_LEAGUE_PREVIEW_URL,
            label="regular basketball",
            cutoff_ms=cutoff_ms,
        )
        player_matches_task = self._fetch_previews(
            player_leagues,
            url_template=_PLAYER_LEAGUE_PREVIEW_URL,
            label="basketball player props",
            cutoff_ms=cutoff_ms,
        )
        regular_matches, player_matches = await asyncio.gather(
            regular_matches_task,
            player_matches_task,
        )

        parsed = _parse_basketball_matches(regular_matches, player_matches)
        logger.info(
            (
                "365 scraped %d basketball odds via per-league fallback "
                "(%d regular leagues, %d player leagues, %d regular matches, "
                "%d player matches; %d handicap rows)"
            ),
            len(parsed.rows),
            len(selected_regular_leagues),
            len(player_leagues),
            len(regular_matches),
            len(player_matches),
            len(parsed.handicap_rows),
        )
        return parsed

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        cutoff_ms = int(lookahead_cutoff(current_utc_time()).timestamp() * 1000)
        parsed = await self._scrape_odds_bulk(cutoff_ms)
        if parsed is None:
            parsed = await self._scrape_odds_per_league(cutoff_ms)
        return parsed.rows

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football"]

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        if sport != "football":
            return []

        cutoff_ms = int(lookahead_cutoff(current_utc_time()).timestamp() * 1000)
        bulk_matches = await self._fetch_bulk_matches(
            _FOOTBALL_BULK_URL,
            label="football",
            cutoff_ms=cutoff_ms,
        )
        if bulk_matches is not None:
            bulk_results = [
                result
                for match in bulk_matches
                for result in _parse_football_outcome_match(match)
            ]
            if _football_bulk_is_usable(bulk_results):
                logger.info(
                    "365: using football bulk feed (%d offers from %d matches)",
                    len(bulk_results),
                    len(bulk_matches),
                )
                return bulk_results
            logger.warning(
                (
                    "365: football bulk feed incomplete "
                    "(matches=%d, parsed offers=%d); falling back"
                ),
                len(bulk_matches),
                len(bulk_results),
            )

        leagues = await self._fetch_league_categories(
            _FOOTBALL_LEAGUES_URL,
            label="football",
        )
        if not leagues:
            return []

        matches = await self._fetch_previews(
            leagues,
            url_template=_FOOTBALL_LEAGUE_PREVIEW_URL,
            label="football",
            cutoff_ms=cutoff_ms,
        )

        results: list[RawOutcomeOffer] = []
        for match in matches:
            results.extend(_parse_football_outcome_match(match))

        logger.info(
            "365 scraped %d football outcome offers from %d matches across %d leagues",
            len(results),
            len(matches),
            len(leagues),
        )
        return results
