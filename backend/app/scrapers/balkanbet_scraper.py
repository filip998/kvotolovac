from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

# ── NSoft platform constants ────────────────────────────────────────────
#
# BalkanBet runs on NSoft 7platform.  All offer data is served by a single
# REST endpoint that returns events with their markets and outcomes embedded
# inline (no per-event detail call needed) when shortProps=1 is requested.

_LIST_URL = "https://sports-sm-distribution-api.de-2.nsoftcdn.com/api/v1/events"
_COMPANY_UUID = "4f54c6aa-82a9-475d-bf0e-dc02ded89225"

_LIST_DATA_FORMAT = '{"default":"object","events":"array","outcomes":"array"}'
_LIST_LANGUAGE = (
    '{"default":"sr-Latn","events":"sr-Latn","sport":"sr-Latn",'
    '"category":"sr-Latn","tournament":"sr-Latn","team":"sr-Latn","market":"sr-Latn"}'
)

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://sports-sm-web.7platform.net",
    "Referer": "https://sports-sm-web.7platform.net/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}

_BASE_LIST_PARAMS = {
    "deliveryPlatformId": "3",
    "companyUuid": _COMPANY_UUID,
    "sort": "categoryPosition,categoryName,tournamentPosition,tournamentName,startsAt",
    "offerTemplate": "WEB_OVERVIEW",
    "shortProps": "1",
    "dataFormat": _LIST_DATA_FORMAT,
    "language": _LIST_LANGUAGE,
    "timezone": "Europe/Belgrade",
}

_REQUEST_TIMEZONE = ZoneInfo("Europe/Belgrade")
_PLAYER_NAME_RE = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*$")


# ── Per-sport spec ──────────────────────────────────────────────────────
#
# Each NSoftSportSpec captures everything sport-specific so adding a new sport
# (football, tennis, hockey…) is a single dict entry.  The list-fetch + parse
# pipeline lives in BalkanBetScraper and is sport-agnostic.


@dataclass(frozen=True)
class NSoftSportSpec:
    """Per-sport configuration for the NSoft (BalkanBet) offer endpoint."""

    sport: str
    # NSoft splits player props onto a dedicated sport ID separate from the
    # game-level sport ID. Both are stringified because that is how the API
    # accepts them in `filter[sportId]`.
    player_sport_id: str
    totals_sport_id: str
    # Set of NSoft `marketId` values that represent player point totals.
    # We currently model them all as a single `player_points` market type
    # (matches existing storage shape and other scrapers' output).
    player_points_market_ids: frozenset[int]
    # Set of NSoft `marketId` values that represent game totals incl. OT.
    game_total_ot_market_ids: frozenset[int]
    # tournamentId → canonical league slug.  Anything not found falls back to
    # `balkanbet_tournament_<id>` / `balkanbet_category_<id>`.
    tournament_league_map: dict[int, str] = field(default_factory=dict)


_BASKETBALL_TOURNAMENT_LEAGUE_MAP: dict[int, str] = {
    252: "euroleague",
    29368: "aba_liga",
    30757: "turkey",
    31317: "italy",
    31353: "germany",
}

_BASKETBALL_SPEC = NSoftSportSpec(
    sport="basketball",
    player_sport_id="273",
    totals_sport_id="36",
    player_points_market_ids=frozenset({2402}),
    game_total_ot_market_ids=frozenset({530}),
    tournament_league_map=_BASKETBALL_TOURNAMENT_LEAGUE_MAP,
)

_SPORT_SPECS: dict[str, NSoftSportSpec] = {
    _BASKETBALL_SPEC.sport: _BASKETBALL_SPEC,
}


# ── Generic helpers ─────────────────────────────────────────────────────


def _format_filter_from(dt: datetime | None = None) -> str:
    """Return BalkanBet's accepted naive Belgrade-local timestamp format."""
    if dt is None:
        dt = datetime.now(tz=timezone.utc)
    return dt.astimezone(_REQUEST_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_player_name(name: str) -> tuple[str, str | None]:
    """Split 'A.Plummer (Bosna)' into ('A.Plummer', 'Bosna')."""
    if not name:
        return (name, None)
    m = _PLAYER_NAME_RE.match(name)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return (name.strip(), None)


def _iter_list_markets(event: dict) -> list[dict]:
    """Iterate over an event's markets, accepting both shortProps (`o`)
    and long-key (`markets`) container shapes."""
    markets = event.get("o")
    if markets is None:
        markets = event.get("markets")
    if markets is None:
        return []
    if isinstance(markets, dict):
        return [market for market in markets.values() if isinstance(market, dict)]
    if isinstance(markets, list):
        return [market for market in markets if isinstance(market, dict)]
    return []


def _normalize_start_time(raw: str | None) -> str | None:
    """Convert an ISO-8601 timestamp to the canonical ``+00:00`` format.

    BalkanBet returns ``2026-04-11T16:00:00.000Z``; the normalizer compares
    start times as strings so we must match other scrapers' canonical
    ``+00:00`` form exactly.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.isoformat()
    except (ValueError, TypeError):
        return raw


def _coerce_int(value) -> int | None:
    """Coerce numeric-looking values (incl. numeric strings) to int.

    NSoft has been observed to send IDs as either ``int`` or numeric ``str``
    depending on shortProps/dataFormat options.  We canonicalize on int so
    league-map lookups are stable.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def _extract_league_id(
    category_id,
    tournament_id,
    tournament_league_map: dict[int, str],
    default: str = "basketball",
) -> str:
    tournament_int = _coerce_int(tournament_id)
    category_int = _coerce_int(category_id)
    if tournament_int is not None and tournament_int in tournament_league_map:
        return tournament_league_map[tournament_int]
    if tournament_int is not None:
        return f"balkanbet_tournament_{tournament_int}"
    if category_int is not None:
        return f"balkanbet_category_{category_int}"
    return default


def _extract_outcome_price(outcome: dict) -> float | None:
    for key in ("odd", "odds", "g"):
        value = outcome.get(key)
        if value is not None:
            return value
    return None


def _extract_over_under_odds(outcomes: list[dict]) -> tuple[float | None, float | None]:
    over_odds: float | None = None
    under_odds: float | None = None
    for outcome in outcomes:
        outcome_name = (outcome.get("name") or outcome.get("e") or "").lower()
        outcome_price = _extract_outcome_price(outcome)
        if outcome_name.startswith("više"):
            over_odds = outcome_price
        elif outcome_name.startswith("manje"):
            under_odds = outcome_price
    return over_odds, under_odds


def _extract_threshold(market: dict) -> float | None:
    special_values = market.get("g") or market.get("specialValues") or []
    if not special_values:
        return None
    try:
        return float(special_values[0])
    except (ValueError, TypeError, IndexError):
        return None


# ── List parsers ────────────────────────────────────────────────────────


def _split_match_name(name: str) -> tuple[str, str] | None:
    home_team, separator, away_team = name.partition(" - ")
    if not separator:
        return None
    home_team = home_team.strip()
    away_team = away_team.strip()
    if not home_team or not away_team:
        return None
    return home_team, away_team


def _parse_player_points_list(
    data: dict,
    spec: NSoftSportSpec,
) -> list[RawOddsData]:
    """Parse the player-prop sport list response into RawOddsData entries.

    NSoft's ``WEB_OVERVIEW`` template returns markets and outcomes inline on
    each event when ``shortProps=1`` is set, so a single list call replaces
    the previous N+1 per-event detail fetches.
    """
    results: list[RawOddsData] = []
    events = data.get("data", {}).get("events", [])

    for event in events:
        raw_name = event.get("j") or event.get("name") or ""
        player_name, team = _parse_player_name(raw_name)
        if not player_name:
            continue

        start_time = _normalize_start_time(event.get("n") or event.get("startsAt"))
        league_id = _extract_league_id(
            event.get("c") if event.get("c") is not None else event.get("categoryId"),
            event.get("f") if event.get("f") is not None else event.get("tournamentId"),
            spec.tournament_league_map,
            default=spec.sport,
        )

        for market in _iter_list_markets(event):
            market_id = market.get("b") or market.get("marketId")
            if market_id not in spec.player_points_market_ids:
                continue

            threshold = _extract_threshold(market)
            if threshold is None:
                continue

            outcomes = market.get("h") or market.get("outcomes") or []
            over_odds, under_odds = _extract_over_under_odds(outcomes)
            if over_odds is None and under_odds is None:
                continue

            results.append(
                RawOddsData(
                    bookmaker_id="balkanbet",
                    league_id=league_id,
                    sport=spec.sport,
                    home_team=team or "",
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


def _parse_game_total_ot_list(
    data: dict,
    spec: NSoftSportSpec,
) -> list[RawOddsData]:
    results: list[RawOddsData] = []
    events = data.get("data", {}).get("events", [])

    for event in events:
        matchup = _split_match_name(event.get("j") or event.get("name") or "")
        if matchup is None:
            continue

        home_team, away_team = matchup
        start_time = _normalize_start_time(event.get("n") or event.get("startsAt"))
        league_id = _extract_league_id(
            event.get("c") if event.get("c") is not None else event.get("categoryId"),
            event.get("f") if event.get("f") is not None else event.get("tournamentId"),
            spec.tournament_league_map,
            default=spec.sport,
        )

        for market in _iter_list_markets(event):
            market_id = market.get("b") or market.get("marketId")
            if market_id not in spec.game_total_ot_market_ids:
                continue

            threshold = _extract_threshold(market)
            if threshold is None:
                continue

            outcomes = market.get("h") or market.get("outcomes") or []
            over_odds, under_odds = _extract_over_under_odds(outcomes)
            if over_odds is None and under_odds is None:
                continue

            results.append(
                RawOddsData(
                    bookmaker_id="balkanbet",
                    league_id=league_id,
                    sport=spec.sport,
                    home_team=home_team,
                    away_team=away_team,
                    market_type="game_total_ot",
                    threshold=threshold,
                    over_odds=over_odds,
                    under_odds=under_odds,
                    start_time=start_time,
                )
            )

    return results


# ── Scraper ─────────────────────────────────────────────────────────────


class BalkanBetScraper(BaseScraper):
    """Scraper for BalkanBet (NSoft 7platform) basketball player points and OT-inclusive totals.

    Issues two list calls per scrape (player-props sport + game-totals sport)
    and parses markets/outcomes directly from the inline ``WEB_OVERVIEW``
    response.  No per-event detail calls are made.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "balkanbet"

    def get_bookmaker_name(self) -> str:
        return "BalkanBet"

    def get_supported_leagues(self) -> list[str]:
        return list(_SPORT_SPECS.keys())

    async def _fetch_list(self, params: dict, label: str) -> dict:
        try:
            return await self._http.get_json(
                _LIST_URL,
                params=params,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("BalkanBet: failed to fetch %s list", label, exc_info=True)
            return {}

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        spec = _SPORT_SPECS.get(league_id)
        if spec is None:
            return []

        now_iso = _format_filter_from()
        player_params = {
            **_BASE_LIST_PARAMS,
            "filter[sportId]": spec.player_sport_id,
            "filter[from]": now_iso,
        }
        totals_params = {
            **_BASE_LIST_PARAMS,
            "filter[sportId]": spec.totals_sport_id,
            "filter[from]": now_iso,
        }

        player_data, totals_data = await asyncio.gather(
            self._fetch_list(player_params, f"{spec.sport} player-points"),
            self._fetch_list(totals_params, f"{spec.sport} game-total-ot"),
        )

        player_results = _parse_player_points_list(player_data, spec)
        totals_results = _parse_game_total_ot_list(totals_data, spec)
        results = [*player_results, *totals_results]

        logger.info(
            "BalkanBet scraped %d %s player odds and %d %s OT total odds",
            len(player_results),
            spec.sport,
            len(totals_results),
            spec.sport,
        )
        return results
