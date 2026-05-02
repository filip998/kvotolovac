from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData
from ..services.scrape_window import current_utc_time, lookahead_cutoff
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_REGULAR_FEED_URL = "https://www.betole.com/restapi/offer/sr/sport/B/mob"
_PLAYER_FEED_URL = "https://www.betole.com/restapi/offer/sr/sport/SK/mob"

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

_BOOKMAKER_ID = "betole"


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


def _extract_league_id(raw_name: str | None) -> str:
    normalized = _normalize_league_key(raw_name)
    if not normalized:
        return "basketball"
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


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


class BetOleScraper(BaseScraper):
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return _BOOKMAKER_ID

    def get_bookmaker_name(self) -> str:
        return "BetOle"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

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
