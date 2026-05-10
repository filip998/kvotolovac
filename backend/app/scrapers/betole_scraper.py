from __future__ import annotations

import asyncio
import math
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from .base import BaseScraper
from .http_client import HttpClient
from .outcome_team_recovery import recover_matchup_from_payload
from ..config import settings
from ..models.schemas import RawOddsData, RawOutcomeOffer
from ..services.market_allowlist import analysis_market_allowlist
from ..services.scrape_window import current_utc_time, lookahead_cutoff
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_REGULAR_FEED_URL = "https://www.betole.com/restapi/offer/sr/sport/B/mob"
_PLAYER_FEED_URL = "https://www.betole.com/restapi/offer/sr/sport/SK/mob"
_FOOTBALL_LIST_URL = "https://www.betole.com/restapi/offer/sr/sport/S/mob"
_TENNIS_LIST_URL = "https://www.betole.com/restapi/offer/sr/sport/T/mob"
_TENNIS_PAGE_URL = "https://www.betole.com/sr/sportsko-kladjenje/tenis/T"
_FOOTBALL_DETAIL_URL_TEMPLATE = "https://www.betole.com/restapi/offer/sr/match/{match_id}"

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Referer": "https://www.betole.com/home",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}
_DEFAULT_PARAMS: dict[str, str] = {
    "annex": "0",
    "desktopVersion": "2.46.6.3",
    "locale": "sr",
}

_FOOTBALL_DETAIL_PARAMS: dict[str, str] = {
    "annex": "0",
}


def _list_params() -> dict[str, str]:
    """List-feed params with the configured lookahead window.

    Used by the football outcome listing — the regular basketball feed
    keeps the legacy unfiltered params for backward compatibility.
    """
    return {
        **_DEFAULT_PARAMS,
        "hours": str(settings.scrape_lookahead_hours),
    }


_BOOKMAKER_ID = "betole"
_TENNIS_LIVE_START_BUFFER_MS = 5 * 60 * 1000


@dataclass(frozen=True)
class ThresholdLine:
    over_code: str
    under_code: str
    param_key: str
    market_type: str


@dataclass(frozen=True)
class MatchContext:
    match_id: int
    match_code: int
    league_id: str
    home_team: str
    away_team: str
    start_time: str | None


@dataclass(frozen=True)
class MatchupIndex:
    by_match_code: dict[int, MatchContext]
    by_league_team_slot: dict[tuple[str, str, int], MatchContext]
    by_team_slot: dict[tuple[str, int], MatchContext]


_PLAYER_THRESHOLD_LINES: tuple[ThresholdLine, ...] = (
    ThresholdLine("51679", "51681", "ouPlPoints", "player_points"),
    ThresholdLine("51682", "51684", "ouPlAssists", "player_assists"),
    ThresholdLine("51685", "51687", "ouPlRebounds", "player_rebounds"),
)
_GAME_TOTAL_OT_LINE = ThresholdLine("50445", "50444", "overUnderOvertime", "game_total_ot")

# OT-inclusive Asian handicap (full game). BetOle's existing
# /restapi/offer/sr/sport/B/mob basketball list call already returns
# params["handicapOvertime"] and odds codes 50430 ("2" = home covers) /
# 50431 ("1" = away covers) on the same response — no new HTTP needed.
# Each match exposes one main line per match (no ladder).
#
# BetOle is on the same Tipster white-label platform as MerkurXTip /
# OktagonBet / 365 / SoccerBet, and they all share the same
# *home-perspective signed* convention: ``handicapOvertime`` is the home
# team's signed Asian-handicap line (negative = home favourite, positive
# = home underdog — same as Mozzart's ``Hendikep -X`` UI).  Our analyzer
# expects ``threshold`` to be the home expected margin (positive = home
# favoured), so we negate the parsed line in `_parse_handicap_match`.
# The odds codes are *also* opposite of historical comments: 50430 (``"2"``)
# pays when the home team covers and 50431 (``"1"``) when the away team
# covers — verified live by checking the ladder direction (home cover
# odds fall as the line grows in favour of the favourite).
_HANDICAP_OT_LINE = ThresholdLine(
    "50430", "50431", "handicapOvertime", "home_handicap_ot",
)

_CANONICAL_LEAGUES: dict[str, str] = {
    "usa nba play offs": "nba",
    "usa nba play in": "nba",
    "europe euroleague play in": "euroleague",
    "europe euroleague play offs": "euroleague",
    "europe aba league losers stage": "aba_liga",
    "europe aba league winners stage": "aba_liga",
    "germany bbl": "germany",
    "greece basket league": "greece",
    "turkey super ligi": "turkey",
    "argentina la liga": "argentina_1",
    "brazil nbb play offs": "brazil_nbb",
}


# ── Football outcome offers ──
#
# BetOle is on the same Tipster/iBet white-label as MerkurXTip /
# OktagonBet / SoccerBet / 365 — it shares the numeric tip-type
# convention.  The football list feed (``/sport/S/mob``) returns the
# whole-game result (codes 1/2/3) and the 2.5 totals (22 = 0-2 under,
# 24 = 3+ over) directly in ``odds``, but does NOT include the double
# chance picks (codes 7/8/9).  Those only show up in the per-match
# detail feed (``/restapi/offer/sr/match/{id}``) — the bulk PUT
# ``/ibet/offer/prematchesByIds.html`` that OktagonBet uses returns an
# empty body for BetOle even with a session, so the per-match GET is
# the only available source.
_FOOTBALL_LIST_OUTCOME_CODES: dict[str, tuple[str, str, float | None, str]] = {
    "1": ("football_result", "home", None, "1"),
    "2": ("football_result", "draw", None, "X"),
    "3": ("football_result", "away", None, "2"),
    "22": ("football_total_goals", "under", 2.5, "0-2"),
    "24": ("football_total_goals", "over", 2.5, "3+"),
}

_FOOTBALL_DETAIL_DOUBLE_CHANCE_CODES: dict[str, tuple[str, str]] = {
    "7": ("home_or_draw", "1X"),
    "8": ("home_or_away", "12"),
    "9": ("draw_or_away", "X2"),
}
_TENNIS_OUTCOME_CODES: dict[str, tuple[str, str]] = {
    "1": ("home", "1"),
    "3": ("away", "2"),
}


# Per-bookmaker concurrency caps for the per-match detail fetches.
# The HttpClient enforces a global rate limit per bookmaker, so any
# concurrency above ``ceil(rate_limit_per_second)`` cannot speed
# things up — it just helps mask network latency between rate-limited
# slots.  When the rate limit is disabled (0), cap at 10 to stay
# polite.
_MIN_DETAIL_CONCURRENCY = 2
_UNLIMITED_DETAIL_CONCURRENCY = 10


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
    for suffix in (" players duel", " players"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
            break
    return normalized


def _extract_league_id(raw_name: str | None, *, default: str = "basketball") -> str:
    normalized = _normalize_league_key(raw_name)
    if not normalized:
        return default
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


def _coerce_positive_odds(value: object) -> float | None:
    try:
        odds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if odds <= 0:
        return None
    return odds


def _within_lookahead(match: dict, cutoff_ms: int) -> bool:
    kickoff = _parse_int(match.get("kickOffTime"))
    return kickoff is None or kickoff <= cutoff_ms


def _build_source_url(match_id: int | None) -> str | None:
    if match_id is None:
        return None
    return f"https://www.betole.com/match-special/{match_id}"


def _build_matchup_index(matches: list[dict]) -> MatchupIndex:
    by_match_code: dict[int, MatchContext] = {}
    by_league_team_slot: dict[tuple[str, str, int], MatchContext] = {}
    by_team_slot: dict[tuple[str, int], MatchContext] = {}

    for match in matches:
        match_id = _parse_int(match.get("id"))
        match_code = _parse_int(match.get("matchCode"))
        kickoff = _parse_int(match.get("kickOffTime"))
        home_team = (match.get("home") or "").strip()
        away_team = (match.get("away") or "").strip()
        if (
            match_id is None
            or match_code is None
            or kickoff is None
            or not home_team
            or not away_team
        ):
            continue

        league_key = _normalize_league_key(match.get("leagueName"))
        context = MatchContext(
            match_id=match_id,
            match_code=match_code,
            league_id=_extract_league_id(match.get("leagueName")),
            home_team=home_team,
            away_team=away_team,
            start_time=_parse_start_time(kickoff),
        )
        by_match_code[match_code] = context
        home_slot = (normalize_identity_text(home_team), kickoff)
        away_slot = (normalize_identity_text(away_team), kickoff)
        if league_key:
            by_league_team_slot[(league_key, *home_slot)] = context
            by_league_team_slot[(league_key, *away_slot)] = context
        by_team_slot[home_slot] = context
        by_team_slot[away_slot] = context

    return MatchupIndex(
        by_match_code=by_match_code,
        by_league_team_slot=by_league_team_slot,
        by_team_slot=by_team_slot,
    )


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
    league_key = _normalize_league_key(match.get("leagueName"))
    if league_key:
        context = matchup_index.by_league_team_slot.get((league_key, team_name, kickoff))
        if context is not None:
            return context
    return matchup_index.by_team_slot.get((team_name, kickoff))


def _parse_regular_match(match: dict) -> list[RawOddsData]:
    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    if not home_team or not away_team:
        return []

    params = match.get("params") or {}
    odds = match.get("odds") or {}
    threshold = _parse_float(params.get(_GAME_TOTAL_OT_LINE.param_key))
    if threshold is None:
        return []

    over_odds = _parse_float(odds.get(_GAME_TOTAL_OT_LINE.over_code))
    under_odds = _parse_float(odds.get(_GAME_TOTAL_OT_LINE.under_code))
    if over_odds is None and under_odds is None:
        return []

    return [
        RawOddsData(
            bookmaker_id=_BOOKMAKER_ID,
            league_id=_extract_league_id(match.get("leagueName")),
            sport="basketball",
            home_team=home_team,
            away_team=away_team,
            source_url=_build_source_url(_parse_int(match.get("id"))),
            market_type=_GAME_TOTAL_OT_LINE.market_type,
            player_name=None,
            threshold=threshold,
            over_odds=over_odds,
            under_odds=under_odds,
            start_time=_parse_start_time(match.get("kickOffTime")),
        )
    ]


def _parse_handicap_match(match: dict) -> list[RawOddsData]:
    """Parse OT-inclusive Asian handicap rows from a regular-feed match.

    BetOle stores ``handicapOvertime`` as the home team's *signed*
    Asian-handicap line (negative = home favourite, positive = home
    underdog), matching Mozzart's ``Hendikep -X`` UI convention.  Our
    canonical analyzer convention is the opposite (positive = home
    expected margin, i.e., positive when home is favoured), so we negate
    the parsed value when storing the threshold.  Outcome ``"2"`` (code
    50430) pays when home covers; ``"1"`` (50431) pays when away covers.
    """
    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    if not home_team or not away_team:
        return []

    params = match.get("params") or {}
    odds = match.get("odds") or {}

    line_str = params.get(_HANDICAP_OT_LINE.param_key)
    if line_str is None or line_str == "":
        return []
    line = _parse_float(line_str)
    if line is None:
        return []
    threshold = -line

    over_odds = _parse_float(odds.get(_HANDICAP_OT_LINE.over_code))
    under_odds = _parse_float(odds.get(_HANDICAP_OT_LINE.under_code))
    if over_odds is None and under_odds is None:
        return []

    return [
        RawOddsData(
            bookmaker_id=_BOOKMAKER_ID,
            league_id=_extract_league_id(match.get("leagueName")),
            sport="basketball",
            home_team=home_team,
            away_team=away_team,
            source_url=_build_source_url(_parse_int(match.get("id"))),
            market_type=_HANDICAP_OT_LINE.market_type,
            player_name=None,
            threshold=threshold,
            over_odds=over_odds,
            under_odds=under_odds,
            start_time=_parse_start_time(match.get("kickOffTime")),
        )
    ]


def _parse_player_match(match: dict, matchup_index: MatchupIndex) -> list[RawOddsData]:
    player_name = (match.get("home") or "").strip()
    if not player_name:
        return []

    context = _resolve_matchup_context(match, matchup_index)
    if context is None:
        return []

    params = match.get("params") or {}
    odds = match.get("odds") or {}
    source_url = _build_source_url(context.match_id)

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
                source_url=source_url,
                market_type=line.market_type,
                player_name=player_name,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=context.start_time,
            )
        )

    return results


def _parse_football_outcome_match(match: dict) -> list[RawOutcomeOffer]:
    """Emit list-derived football offers (result + 2.5 totals)."""

    matchup = recover_matchup_from_payload(match)
    home_team = matchup.home_team
    away_team = matchup.away_team
    if not home_team or not away_team:
        return []
    if matchup.recovered:
        logger.debug(
            "BetOle: recovered football matchup from %s for match %s",
            matchup.source,
            match.get("id") or match.get("matchCode"),
        )

    odds_map = match.get("odds") or {}
    if not isinstance(odds_map, dict):
        return []

    league_id = _extract_league_id(match.get("leagueName", ""), default="football")
    start_time = _parse_start_time(match.get("kickOffTime"))
    results: list[RawOutcomeOffer] = []
    for code, (market_type, outcome_code, line, raw_label) in _FOOTBALL_LIST_OUTCOME_CODES.items():
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


def _is_tennis_doubles_match(match: dict) -> bool:
    league_name = str(match.get("leagueName") or "")
    home_team = str(match.get("home") or "")
    away_team = str(match.get("away") or "")
    league_key = _normalize_league_key(league_name)
    if any(token in league_key.split() for token in ("doubles", "double", "parovi")):
        return True
    return "/" in home_team or "/" in away_team


def _tennis_live_start_skip_reason(
    match: dict,
    *,
    now: datetime | None = None,
) -> str | None:
    if match.get("live") is not True:
        return None

    raw_kickoff = match.get("kickOffTime")
    if raw_kickoff in (None, ""):
        return "missing_start_time"

    kickoff_ms = _parse_int(raw_kickoff)
    if kickoff_ms is None:
        return "invalid_start_time"

    now_ms = int((now or current_utc_time()).timestamp() * 1000)
    if kickoff_ms <= now_ms + _TENNIS_LIVE_START_BUFFER_MS:
        return "live_near_or_past_start"

    return None


def _tennis_skip_reason(match: dict, *, now: datetime | None = None) -> str | None:
    if match.get("blocked") is True:
        return "blocked"
    if _is_tennis_doubles_match(match):
        return "doubles"

    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    if not home_team or not away_team:
        return "missing_competitor"

    raw_odds_map = match.get("odds")
    if raw_odds_map is not None and not isinstance(raw_odds_map, dict):
        return "invalid_odds_map"

    live_start_reason = _tennis_live_start_skip_reason(match, now=now)
    if live_start_reason:
        return live_start_reason

    return None


def _parse_tennis_outcome_match(
    match: dict,
    *,
    now: datetime | None = None,
) -> list[RawOutcomeOffer]:
    if _tennis_skip_reason(match, now=now):
        return []

    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    odds_map = match.get("odds") or {}
    league_id = _extract_league_id(match.get("leagueName"), default="tennis")
    start_time = _parse_start_time(match.get("kickOffTime"))
    results: list[RawOutcomeOffer] = []
    for code, (outcome_code, raw_label) in _TENNIS_OUTCOME_CODES.items():
        odds = _coerce_positive_odds(odds_map.get(code))
        if odds is None:
            continue
        results.append(
            RawOutcomeOffer(
                bookmaker_id=_BOOKMAKER_ID,
                league_id=league_id,
                sport="tennis",
                home_team=home_team,
                away_team=away_team,
                source_url=_TENNIS_PAGE_URL,
                market_type="tennis_match_winner",
                outcome_code=outcome_code,
                odds=odds,
                line=None,
                raw_label=raw_label,
                start_time=start_time,
            )
        )
    return results


def _parse_football_double_chance_detail_match(match: dict) -> list[RawOutcomeOffer]:
    """Emit detail-derived football double-chance offers (codes 7/8/9).

    Per-match detail responses also expose the result and totals, but
    those are already emitted from the cheaper list endpoint — this
    parser intentionally only mines codes 7/8/9 to avoid emitting two
    rows per (match, market, outcome) tuple.
    """

    matchup = recover_matchup_from_payload(match)
    home_team = matchup.home_team
    away_team = matchup.away_team
    if not home_team or not away_team:
        return []
    if matchup.recovered:
        logger.debug(
            "BetOle: recovered football detail matchup from %s for match %s",
            matchup.source,
            match.get("id") or match.get("matchCode"),
        )

    odds_map = match.get("odds") or {}
    if not isinstance(odds_map, dict):
        return []

    league_id = _extract_league_id(match.get("leagueName", ""), default="football")
    start_time = _parse_start_time(match.get("kickOffTime"))
    results: list[RawOutcomeOffer] = []
    for code, (outcome_code, raw_label) in _FOOTBALL_DETAIL_DOUBLE_CHANCE_CODES.items():
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
                market_type="football_double_chance",
                outcome_code=outcome_code,
                odds=odds,
                line=None,
                raw_label=raw_label,
                start_time=start_time,
            )
        )
    return results


def _get_detail_fetch_concurrency(http_client: HttpClient, match_count: int) -> int:
    if match_count <= 0:
        return 0
    if http_client.rate_limit_per_second <= 0:
        return min(match_count, _UNLIMITED_DETAIL_CONCURRENCY)
    return min(
        match_count,
        max(_MIN_DETAIL_CONCURRENCY, math.ceil(http_client.rate_limit_per_second)),
    )


class BetOleScraper(BaseScraper):
    def __init__(
        self,
        http_client: HttpClient | None = None,
        detail_mode: Literal["partial", "full"] | None = None,
        analysis_markets: Iterable[str] | None = None,
        scrape_market_scope: str | None = None,
    ) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)
        self._detail_mode = detail_mode or settings.betole_detail_mode
        self._analysis_markets = (
            list(analysis_markets)
            if analysis_markets is not None
            else list(
                analysis_market_allowlist(
                    settings.analysis_markets,
                    legacy_scrape_market_scope=settings.scrape_market_scope,
                ).tokens
            )
        )
        self._scrape_market_scope = scrape_market_scope or settings.scrape_market_scope

    def get_bookmaker_id(self) -> str:
        return _BOOKMAKER_ID

    def get_bookmaker_name(self) -> str:
        return "BetOle"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football", "tennis"]

    async def _fetch_feed_rows(self, url: str, *, label: str) -> list[dict]:
        try:
            data = await self._http.get_json(
                url,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("BetOle: failed to fetch %s feed", label)
            return []

        rows = data.get("esMatches") or []
        if not isinstance(rows, list):
            return []

        cutoff_ms = int(lookahead_cutoff(current_utc_time()).timestamp() * 1000)
        return [
            row
            for row in rows
            if isinstance(row, dict) and _within_lookahead(row, cutoff_ms)
        ]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        regular_matches, player_matches = await asyncio.gather(
            self._fetch_feed_rows(_REGULAR_FEED_URL, label="regular basketball"),
            self._fetch_feed_rows(_PLAYER_FEED_URL, label="player basketball"),
        )
        if not regular_matches:
            logger.warning("BetOle: no regular basketball rows discovered")
            return []
        if not player_matches:
            logger.warning("BetOle: no player basketball rows discovered; scraping regular rows only")

        matchup_index = _build_matchup_index(regular_matches)

        regular_results = [
            result
            for match in regular_matches
            for result in _parse_regular_match(match)
        ]
        handicap_results = [
            result
            for match in regular_matches
            for result in _parse_handicap_match(match)
        ]

        player_results: list[RawOddsData] = []
        unmatched_player_rows = 0
        for match in player_matches:
            parsed = _parse_player_match(match, matchup_index)
            if not parsed:
                unmatched_player_rows += 1
                continue
            player_results.extend(parsed)

        results = [*regular_results, *handicap_results, *player_results]
        logger.info(
            (
                "BetOle scraped %d player odds from %d player rows "
                "and %d OT total + %d OT handicap odds from %d regular rows "
                "(unmatched player rows=%d)"
            ),
            len(player_results),
            len(player_matches),
            len(regular_results),
            len(handicap_results),
            len(regular_matches),
            unmatched_player_rows,
        )
        return results

    async def _fetch_football_list(self) -> list[dict]:
        try:
            data = await self._http.get_json(
                _FOOTBALL_LIST_URL,
                params=_list_params(),
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("BetOle: failed to fetch football listing", exc_info=True)
            return []

        rows = data.get("esMatches") or []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _fetch_tennis_list(self) -> list[dict]:
        try:
            data = await self._http.get_json(
                _TENNIS_LIST_URL,
                params=_list_params(),
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("BetOle: failed to fetch tennis listing", exc_info=True)
            return []

        rows = data.get("esMatches") or []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _fetch_football_detail(
        self,
        match_id: int,
        semaphore: asyncio.Semaphore,
    ) -> dict | None:
        async with semaphore:
            try:
                detail = await self._http.get_json(
                    _FOOTBALL_DETAIL_URL_TEMPLATE.format(match_id=match_id),
                    params=_FOOTBALL_DETAIL_PARAMS,
                    headers=_DEFAULT_HEADERS,
                )
            except Exception:
                logger.warning(
                    "BetOle: failed to fetch football match detail %s",
                    match_id,
                    exc_info=True,
                )
                return None
        if not isinstance(detail, dict):
            return None
        return detail

    def _should_fetch_football_details(self) -> bool:
        if self._detail_mode != "full":
            return False
        allowlist = analysis_market_allowlist(
            self._analysis_markets,
            legacy_scrape_market_scope=self._scrape_market_scope,
        )
        return allowlist.allows(sport="football", market_type="football_double_chance")

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        """Scrape outcome offers for supported sports.

        BetOle's bulk PUT endpoint that the OktagonBet sibling uses
        (``/ibet/offer/prematchesByIds.html``) returns an empty body
        for anonymous and cookie-bearing requests alike, so full mode
        enriches each football list match with a per-match GET to pick up the
        double chance picks (codes 7/8/9) that are not present in the
        list payload. Result and totals are always parsed from the
        cheaper list response.
        """

        if sport == "tennis":
            tennis_matches = await self._fetch_tennis_list()
            scrape_now = current_utc_time()
            results: list[RawOutcomeOffer] = []
            skipped: dict[str, int] = {}
            for match in tennis_matches:
                parsed = _parse_tennis_outcome_match(match, now=scrape_now)
                if not parsed:
                    reason = _tennis_skip_reason(match, now=scrape_now) or "no_match_winner"
                    skipped[reason] = skipped.get(reason, 0) + 1
                    continue
                results.extend(parsed)

            logger.info(
                (
                    "BetOle scraped %d tennis outcome offers from %d matches "
                    "with skipped=%s"
                ),
                len(results),
                len(tennis_matches),
                skipped,
            )
            return results

        if sport != "football":
            return []

        list_matches = await self._fetch_football_list()
        list_by_id: dict[int, dict] = {}
        for match in list_matches:
            match_id = _parse_int(match.get("id"))
            if match_id is None:
                continue
            list_by_id[match_id] = match

        if not list_by_id:
            logger.info("BetOle: no football matches discovered")
            return []

        results: list[RawOutcomeOffer] = []
        for list_match in list_by_id.values():
            results.extend(_parse_football_outcome_match(list_match))

        if not self._should_fetch_football_details():
            logger.info(
                (
                    "BetOle scraped %d football outcome offers from %d matches "
                    "(detail mode: %s, detail fetches skipped)"
                ),
                len(results),
                len(list_by_id),
                self._detail_mode,
            )
            return results

        match_ids = list(list_by_id.keys())
        concurrency = _get_detail_fetch_concurrency(self._http, len(match_ids))
        semaphore = asyncio.Semaphore(max(1, concurrency))
        details = await asyncio.gather(
            *(self._fetch_football_detail(mid, semaphore) for mid in match_ids)
        )

        missing_detail_ids = 0
        for match_id, detail in zip(match_ids, details):
            if detail is None:
                missing_detail_ids += 1
                continue
            list_match = list_by_id[match_id]
            # Always emit detail-derived offers using the LIST match's
            # team names, kickoff and league so they share the same
            # normalized event key as the list-derived result/totals
            # offers above.  The detail payload's metadata can drift
            # (whitespace, league capitalization) and would silently
            # land double-chance offers in a different normalized
            # event.  We override unconditionally so that even when the
            # list is missing a field (e.g., leagueName=None), both
            # parsers fall back to the same default rather than the
            # detail leaking its own value.
            merged = {
                **detail,
                "home": list_match.get("home"),
                "away": list_match.get("away"),
                "kickOffTime": list_match.get("kickOffTime"),
                "leagueName": list_match.get("leagueName"),
            }
            results.extend(_parse_football_double_chance_detail_match(merged))

        if missing_detail_ids:
            logger.warning(
                "BetOle: %d/%d football detail fetches returned no data; "
                "double chance will be missing for those matches",
                missing_detail_ids,
                len(match_ids),
            )

        logger.info(
            "BetOle scraped %d football outcome offers from %d matches "
            "(detail fetches: %d, concurrency: %d)",
            len(results),
            len(match_ids),
            len(match_ids) - missing_detail_ids,
            concurrency,
        )
        return results
