from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .base import BaseScraper
from .http_client import HttpClient
from ..config import settings
from ..models.schemas import RawOddsData
from ..services.scrape_window import current_utc_time, lookahead_cutoff
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_GROUPS_URL = "https://www.soccerbet.rs/restapi/offer/sr/categories/ext/sport/B/g"
_GROUP_LEAGUES_URL = (
    "https://www.soccerbet.rs/restapi/offer/sr/categories/ext/sport/B/group/{group_id}/l"
)
_LEAGUE_PREVIEW_URL = (
    "https://www.soccerbet.rs/restapi/offer/sr/sport/B/league/{league_id}/mob"
)
_PLAYER_PREVIEW_URL = (
    "https://www.soccerbet.rs/restapi/offer/sr/ext/sport/B/league/{league_id}/PL/mob"
)
_ALL_GAMES_URL = "https://www.soccerbet.rs/restapi/offer/sr/sport/B/mob"
_ALL_PLAYERS_URL = "https://www.soccerbet.rs/restapi/offer/sr/ext/sport/B/PL/mob"
_DETAIL_URL = "https://www.soccerbet.rs/restapi/offer/sr/match-by-code/{match_code}"

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Referer": "https://www.soccerbet.rs/sr/kladjenje",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}
_DEFAULT_PARAMS: dict[str, str] = {
    "annex": "0",
    "mobileVersion": "1.21.2",
    "locale": "sr",
}

_DISCOVERY_CONCURRENCY = 8
_DETAIL_CONCURRENCY = 5
_BOOKMAKER_ID = "soccerbet"
_ACTIVE_PICK_STATUS = "U"


@dataclass(frozen=True)
class ThresholdLine:
    over_code: int
    under_code: int
    market_type: str


@dataclass(frozen=True)
class FixedMilestone:
    tip_type_code: int
    threshold: float
    market_type: str = "player_points_milestones"


@dataclass(frozen=True)
class LeagueCategory:
    league_id: str
    league_name: str
    pm_count: int
    players_count: int


@dataclass(frozen=True)
class MatchContext:
    league_id: str
    home_team: str
    away_team: str
    start_time: str | None


# SoccerBet uses the same iBet tip-type family as MaxBet for the basketball
# markets we care about, so these codes map directly onto the existing market
# vocabulary used across the app.
_PLAYER_THRESHOLD_LINES: tuple[ThresholdLine, ...] = (
    ThresholdLine(51679, 51681, "player_points"),
    ThresholdLine(51682, 51684, "player_assists"),
    ThresholdLine(51685, 51687, "player_rebounds"),
    ThresholdLine(51688, 51690, "player_3points"),
    ThresholdLine(55244, 55246, "player_points_rebounds"),
    ThresholdLine(55247, 55249, "player_points_assists"),
    ThresholdLine(55250, 55252, "player_rebounds_assists"),
    ThresholdLine(55215, 55217, "player_points_rebounds_assists"),
    ThresholdLine(55672, 55674, "player_steals"),
    ThresholdLine(55681, 55683, "player_blocks"),
    ThresholdLine(56169, 56171, "player_turnovers"),
)

_PLAYER_POINTS_MILESTONES: tuple[FixedMilestone, ...] = (
    FixedMilestone(54096, 4.5),
    FixedMilestone(54101, 9.5),
    FixedMilestone(54106, 14.5),
    FixedMilestone(54111, 19.5),
    FixedMilestone(54116, 24.5),
    FixedMilestone(54121, 29.5),
    FixedMilestone(54126, 34.5),
    FixedMilestone(54131, 39.5),
    FixedMilestone(54136, 44.5),
    FixedMilestone(54141, 49.5),
    FixedMilestone(57454, 59.5),
)

_GAME_TOTAL_LINES: tuple[ThresholdLine, ...] = (
    ThresholdLine(227, 228, "game_total"),
    ThresholdLine(50445, 50444, "game_total_ot"),
)

_CANONICAL_LEAGUES: dict[str, str] = {
    "nba": "nba",
    "nba play off": "nba",
    "nba playoff": "nba",
    "nba plej of": "nba",
    "euroleague": "euroleague",
    "evroliga": "euroleague",
    "aba liga": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
    "aba liga plej of": "aba_liga",
    "aba league": "aba_liga",
    "argentina": "argentina_1",
    "argentina 1": "argentina_1",
    "puerto rico": "portoriko_1",
    "portoriko 1": "portoriko_1",
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


def _parse_total_spec(specifier: object) -> float | None:
    if not isinstance(specifier, str):
        return None
    if not specifier.startswith("total="):
        return None
    return _parse_float(specifier.removeprefix("total="))


def _normalize_league_key(raw_league_name: str | None) -> str:
    normalized = normalize_identity_text(raw_league_name)
    if normalized.endswith(" igraci"):
        normalized = normalized[: -len(" igraci")].strip()
    return normalized


def _extract_league_id(raw_league_name: str | None) -> str:
    normalized = _normalize_league_key(raw_league_name)
    if not normalized:
        return "basketball"
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


def _within_lookahead(match: dict, cutoff_ms: int) -> bool:
    kickoff = _parse_int(match.get("kickOffTime"))
    return kickoff is None or kickoff <= cutoff_ms


def _iter_group_entries(bet_map: dict, tip_type_code: int) -> list[dict]:
    group = bet_map.get(str(tip_type_code))
    if not isinstance(group, dict):
        return []
    return [
        entry
        for entry in group.values()
        if isinstance(entry, dict) and entry.get("s") == _ACTIVE_PICK_STATUS
    ]


def _collect_threshold_odds(
    bet_map: dict,
    line: ThresholdLine,
) -> list[tuple[float, float | None, float | None]]:
    over_odds_by_threshold: dict[float, float] = {}
    under_odds_by_threshold: dict[float, float] = {}

    for entry in _iter_group_entries(bet_map, line.over_code):
        threshold = _parse_total_spec(entry.get("sv"))
        odd = _parse_float(entry.get("ov"))
        if threshold is not None and odd is not None:
            over_odds_by_threshold[threshold] = odd

    for entry in _iter_group_entries(bet_map, line.under_code):
        threshold = _parse_total_spec(entry.get("sv"))
        odd = _parse_float(entry.get("ov"))
        if threshold is not None and odd is not None:
            under_odds_by_threshold[threshold] = odd

    thresholds = sorted(set(over_odds_by_threshold) | set(under_odds_by_threshold))
    return [
        (
            threshold,
            over_odds_by_threshold.get(threshold),
            under_odds_by_threshold.get(threshold),
        )
        for threshold in thresholds
    ]


def _extract_fixed_odd(bet_map: dict, milestone: FixedMilestone) -> float | None:
    entries = _iter_group_entries(bet_map, milestone.tip_type_code)
    if not entries:
        return None
    return _parse_float(entries[0].get("ov"))


def _build_matchup_index(matches: list[dict]) -> dict[int, MatchContext]:
    matchups: dict[int, MatchContext] = {}
    for match in matches:
        match_code = _parse_int(match.get("matchCode"))
        home_team = (match.get("home") or "").strip()
        away_team = (match.get("away") or "").strip()
        if match_code is None or not home_team or not away_team:
            continue
        matchups[match_code] = MatchContext(
            league_id=_extract_league_id(match.get("leagueName")),
            home_team=home_team,
            away_team=away_team,
            start_time=_parse_start_time(match.get("kickOffTime")),
        )
    return matchups


def _parse_regular_match(match: dict) -> list[RawOddsData]:
    home_team = (match.get("home") or "").strip()
    away_team = (match.get("away") or "").strip()
    if not home_team or not away_team:
        return []

    bet_map = match.get("betMap") or {}
    if not isinstance(bet_map, dict):
        return []

    results: list[RawOddsData] = []
    league_id = _extract_league_id(match.get("leagueName"))
    start_time = _parse_start_time(match.get("kickOffTime"))

    for line in _GAME_TOTAL_LINES:
        for threshold, over_odds, under_odds in _collect_threshold_odds(bet_map, line):
            if over_odds is None and under_odds is None:
                continue
            results.append(
                RawOddsData(
                    bookmaker_id=_BOOKMAKER_ID,
                    league_id=league_id,
                    sport="basketball",
                    home_team=home_team,
                    away_team=away_team,
                    market_type=line.market_type,
                    player_name=None,
                    threshold=threshold,
                    over_odds=over_odds,
                    under_odds=under_odds,
                    start_time=start_time,
                )
            )

    return results


def _parse_player_match(
    match: dict,
    matchup_by_super_code: dict[int, MatchContext],
) -> list[RawOddsData]:
    player_name = (match.get("home") or "").strip()
    if not player_name:
        return []

    super_code = _parse_int(match.get("superCode"))
    if super_code is None:
        return []

    context = matchup_by_super_code.get(super_code)
    if context is None:
        return []

    bet_map = match.get("betMap") or {}
    if not isinstance(bet_map, dict):
        return []

    results: list[RawOddsData] = []

    for line in _PLAYER_THRESHOLD_LINES:
        for threshold, over_odds, under_odds in _collect_threshold_odds(bet_map, line):
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

    for milestone in _PLAYER_POINTS_MILESTONES:
        over_odds = _extract_fixed_odd(bet_map, milestone)
        if over_odds is None:
            continue
        results.append(
            RawOddsData(
                bookmaker_id=_BOOKMAKER_ID,
                league_id=context.league_id,
                sport="basketball",
                home_team=context.home_team,
                away_team=context.away_team,
                market_type=milestone.market_type,
                player_name=player_name,
                threshold=milestone.threshold,
                over_odds=over_odds,
                under_odds=None,
                start_time=context.start_time,
            )
        )

    return results


class SoccerBetScraper(BaseScraper):
    """SoccerBet basketball scraper with configurable preview/detail depth."""

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        detail_mode: Literal["partial", "full"] | None = None,
    ) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)
        self._detail_mode = detail_mode or settings.soccerbet_detail_mode

    def get_bookmaker_id(self) -> str:
        return _BOOKMAKER_ID

    def get_bookmaker_name(self) -> str:
        return "SoccerBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_groups(self) -> list[str]:
        try:
            data = await self._http.get_json(
                _GROUPS_URL,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("SoccerBet: failed to fetch basketball groups")
            return []

        categories = data.get("categories") or []
        group_ids: list[str] = []
        for category in categories:
            if not isinstance(category, dict):
                continue
            group_id = category.get("id")
            if group_id:
                group_ids.append(str(group_id))
        return group_ids

    async def _fetch_group_leagues(self, group_id: str, semaphore: asyncio.Semaphore) -> list[LeagueCategory]:
        async with semaphore:
            try:
                data = await self._http.get_json(
                    _GROUP_LEAGUES_URL.format(group_id=group_id),
                    params=_DEFAULT_PARAMS,
                    headers=_DEFAULT_HEADERS,
                )
            except Exception:
                logger.warning("SoccerBet: failed to fetch leagues for group %s", group_id)
                return []

        categories = data.get("categories") or []
        leagues: list[LeagueCategory] = []
        for category in categories:
            if not isinstance(category, dict):
                continue
            league_id = category.get("id")
            league_name = category.get("name")
            if not league_id or not isinstance(league_name, str) or not league_name.strip():
                continue
            leagues.append(
                LeagueCategory(
                    league_id=str(league_id),
                    league_name=league_name.strip(),
                    pm_count=_parse_int(category.get("pmCount")) or 0,
                    players_count=_parse_int(category.get("playersCount")) or 0,
                )
            )
        return leagues

    async def _discover_leagues(self) -> list[LeagueCategory]:
        group_ids = await self._fetch_groups()
        if not group_ids:
            return []

        semaphore = asyncio.Semaphore(_DISCOVERY_CONCURRENCY)
        discovered = await asyncio.gather(
            *(self._fetch_group_leagues(group_id, semaphore) for group_id in group_ids)
        )

        league_map: dict[str, LeagueCategory] = {}
        for league in [item for batch in discovered for item in batch]:
            existing = league_map.get(league.league_id)
            if existing is None:
                league_map[league.league_id] = league
                continue
            league_map[league.league_id] = LeagueCategory(
                league_id=league.league_id,
                league_name=existing.league_name or league.league_name,
                pm_count=max(existing.pm_count, league.pm_count),
                players_count=max(existing.players_count, league.players_count),
            )

        return sorted(league_map.values(), key=lambda league: league.league_id)

    async def _fetch_preview_rows(self, url: str) -> list[dict]:
        try:
            data = await self._http.get_json(
                url,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("SoccerBet: failed to fetch preview %s", url)
            return []

        rows = data.get("esMatches") or []
        if not isinstance(rows, list):
            return []
        cutoff_ms = int(lookahead_cutoff(current_utc_time()).timestamp() * 1000)
        return [row for row in rows if isinstance(row, dict) and _within_lookahead(row, cutoff_ms)]

    async def _fetch_regular_preview(self, league: LeagueCategory) -> list[dict]:
        return await self._fetch_preview_rows(
            _LEAGUE_PREVIEW_URL.format(league_id=league.league_id)
        )

    async def _fetch_player_preview(self, league: LeagueCategory) -> list[dict]:
        if league.players_count <= 0:
            return []
        return await self._fetch_preview_rows(
            _PLAYER_PREVIEW_URL.format(league_id=league.league_id)
        )

    async def _fetch_all_regular_preview(self) -> list[dict]:
        return await self._fetch_preview_rows(_ALL_GAMES_URL)

    async def _fetch_all_player_preview(self) -> list[dict]:
        return await self._fetch_preview_rows(_ALL_PLAYERS_URL)

    async def _fetch_detail(self, match_code: int, semaphore: asyncio.Semaphore) -> dict | None:
        async with semaphore:
            try:
                return await self._http.get_json(
                    _DETAIL_URL.format(match_code=match_code),
                    params=_DEFAULT_PARAMS,
                    headers=_DEFAULT_HEADERS,
                )
            except Exception:
                logger.warning(
                    "SoccerBet: failed to fetch detail for match code %s",
                    match_code,
                )
                return None

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        regular_results: list[RawOddsData] = []
        player_results: list[RawOddsData] = []

        if self._detail_mode == "partial":
            regular_matches, player_matches = await asyncio.gather(
                self._fetch_all_regular_preview(),
                self._fetch_all_player_preview(),
            )
            matchup_by_super_code = _build_matchup_index(regular_matches)
            for match in regular_matches:
                regular_results.extend(_parse_regular_match(match))
            for match in player_matches:
                parsed = _parse_player_match(match, matchup_by_super_code)
                if not parsed:
                    logger.warning(
                        "SoccerBet: dropped partial-mode player row for match code %s",
                        match.get("matchCode"),
                    )
                    continue
                player_results.extend(parsed)
        else:
            leagues = await self._discover_leagues()
            if not leagues:
                logger.warning("SoccerBet: no basketball leagues discovered")
                return []

            regular_batches, player_batches = await asyncio.gather(
                asyncio.gather(*(self._fetch_regular_preview(league) for league in leagues)),
                asyncio.gather(*(self._fetch_player_preview(league) for league in leagues)),
            )

            regular_matches = [match for batch in regular_batches for match in batch]
            player_matches = [match for batch in player_batches for match in batch]
            matchup_by_super_code = _build_matchup_index(regular_matches)
            detail_semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)
            regular_detail_targets = [
                match_code
                for match_code in (_parse_int(match.get("matchCode")) for match in regular_matches)
                if match_code is not None
            ]

            regular_details = await asyncio.gather(
                *(
                    self._fetch_detail(match_code, detail_semaphore)
                    for match_code in regular_detail_targets
                )
            )
            for detail in regular_details:
                if not detail:
                    continue
                regular_results.extend(_parse_regular_match(detail))

            player_detail_targets = [
                (match, match_code)
                for match in player_matches
                if (match_code := _parse_int(match.get("matchCode"))) is not None
            ]
            player_details = await asyncio.gather(
                *(
                    self._fetch_detail(match_code, detail_semaphore)
                    for _, match_code in player_detail_targets
                )
            )
            for (preview_match, _), detail in zip(player_detail_targets, player_details):
                if not detail:
                    continue
                detail_with_matchup = detail
                if (
                    isinstance(detail, dict)
                    and detail.get("superCode") is None
                    and preview_match.get("superCode") is not None
                ):
                    detail_with_matchup = {
                        **detail,
                        "superCode": preview_match["superCode"],
                    }
                parsed = _parse_player_match(detail_with_matchup, matchup_by_super_code)
                if not parsed:
                    logger.warning(
                        "SoccerBet: dropped full-mode player row for match code %s",
                        preview_match.get("matchCode"),
                    )
                    continue
                player_results.extend(parsed)

        all_results = [*player_results, *regular_results]

        logger.info(
            (
                "SoccerBet scraped %d odds from %d regular previews and %d player previews "
                "in %s mode"
            ),
            len(all_results),
            len(regular_matches),
            len(player_matches),
            self._detail_mode,
        )
        return all_results
