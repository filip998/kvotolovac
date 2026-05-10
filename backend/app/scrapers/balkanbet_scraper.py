from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData, RawOutcomeOffer
from ..services.scrape_window import current_utc_time, lookahead_cutoff

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
_FOOTBALL_OUTCOME_SPORT_ID = "18"
_TENNIS_OUTCOME_SPORT_ID = "78"
_TENNIS_MATCH_WINNER_MARKET_ID = 1955
_TENNIS_PAGE_URL = "https://www.balkanbet.rs/sportsko-kladjenje/tenis"
_FOOTBALL_OUTCOME_MARKETS = {
    6: {
        "1": ("football_result", "home", None),
        "X": ("football_result", "draw", None),
        "2": ("football_result", "away", None),
    },
    368: {
        "1X": ("football_double_chance", "home_or_draw", None),
        "12": ("football_double_chance", "home_or_away", None),
        "X2": ("football_double_chance", "draw_or_away", None),
    },
    443: {
        "0-2": ("football_total_goals", "under", 2.5),
        "2+": ("football_total_goals", "over", 1.5),
        "3+": ("football_total_goals", "over", 2.5),
        "4+": ("football_total_goals", "over", 3.5),
    },
}
_TENNIS_OUTCOME_LABELS = {
    "1": ("home", "1"),
    "2": ("away", "2"),
}


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
    # Mapping of NSoft `marketId` → canonical market_type for player props.
    # Covers points, assists, rebounds, 3-pointers, combo markets, and milestones.
    player_market_map: dict[int, str]
    # Set of NSoft `marketId` values that represent game totals incl. OT.
    game_total_ot_market_ids: frozenset[int]
    # Set of NSoft `marketId` values that represent the game-level Asian
    # handicap incl. OT.  Outcomes are labelled ``H1 <line>`` / ``H2 -<line>``
    # where ``H1`` is the home (= team1) side and ``g[0]`` carries the home
    # team's signed Asian handicap.
    game_handicap_ot_market_ids: frozenset[int] = frozenset()
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
    player_market_map={
        2402: "player_points",
        2403: "player_rebounds",
        2406: "player_assists",
        3087: "player_3points",
        3123: "player_points_rebounds",
        3126: "player_points_assists",
        3138: "player_points_rebounds_assists",
        5091: "player_points_milestones",
    },
    game_total_ot_market_ids=frozenset({530}),
    game_handicap_ot_market_ids=frozenset({524}),
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
    """Split 'A.Plummer (Bosna)' into ('A.Plummer', 'Bosna').

    NSoft occasionally appends trailing noise such as `` -`` after the
    parenthesised team name (e.g. ``N.Jokić (Denver) -``).  Strip it
    before matching so the team is still extracted.
    """
    if not name:
        return (name, None)
    cleaned = re.sub(r"\)\s*-\s*$", ")", name)
    m = _PLAYER_NAME_RE.match(cleaned)
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
            try:
                odds = float(value)
            except (TypeError, ValueError):
                return None
            return odds if odds > 0 else None
    return None


def _is_active_flag(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    parsed = _coerce_int(value)
    if parsed is not None:
        return parsed != 0
    return True


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


_HANDICAP_OUTCOME_LABEL_RE = re.compile(
    r"^(?P<side>H[12]|P[12])\s+(?P<value>[+\-]?\d+(?:[.,]\d+)?)\s*$"
)


def _parse_handicap_outcome_label(label: str) -> tuple[str, float] | None:
    """Parse a NSoft handicap outcome label like ``H1 9.5`` / ``P2 -6.5``.

    Returns ``(side, signed_line)`` where ``side`` ∈ ``{"H1", "H2"}`` (``P1``
    is normalised to ``H1`` and ``P2`` to ``H2`` so callers can rely on a
    single label set) and ``signed_line`` is the numeric value parsed from
    the label.  Returns ``None`` for any non-handicap label.
    """
    if not label:
        return None
    match = _HANDICAP_OUTCOME_LABEL_RE.match(label.strip())
    if not match:
        return None
    raw_side = match.group("side")
    side = "H1" if raw_side in ("H1", "P1") else "H2"
    raw_value = match.group("value").replace(",", ".")
    try:
        signed = float(raw_value)
    except ValueError:
        return None
    return side, signed


def _extract_handicap_h1_h2_odds(
    outcomes: list[dict],
) -> tuple[float | None, float | None, float | None]:
    """Pick H1 / H2 outcome odds + the team-1 signed line from a NSoft handicap market.

    NSoft labels handicap outcomes with the team-1 line on the team-1 side
    (e.g. ``H1 9.5`` paired with ``H2 -9.5``, or ``P1 -6.5`` paired with
    ``P2 +6.5``).  The team-1 signed line is read directly from the
    ``H1`` / ``P1`` outcome label so the parser is independent of how the
    market's top-level ``g`` ladder value is signed.
    """
    h1_odds: float | None = None
    h2_odds: float | None = None
    h1_line: float | None = None
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        label = (outcome.get("e") or outcome.get("name") or "").strip()
        parsed = _parse_handicap_outcome_label(label)
        if parsed is None:
            continue
        side, signed_line = parsed
        price = _extract_outcome_price(outcome)
        if side == "H1":
            h1_odds = price
            h1_line = signed_line
        else:
            h2_odds = price
    return h1_odds, h2_odds, h1_line


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


def _parse_player_props_list(
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
            market_id = _coerce_int(market.get("b") or market.get("marketId"))
            market_type = spec.player_market_map.get(market_id)
            if market_type is None:
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
                    market_type=market_type,
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
            market_id = _coerce_int(market.get("b") or market.get("marketId"))
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


def _parse_game_handicap_ot_list(
    data: dict,
    spec: NSoftSportSpec,
) -> list[RawOddsData]:
    """Parse the NSoft game handicap (incl. OT) market for each event.

    Storage convention (matches Phase 1):
      * ``threshold`` is the home team's signed expected margin (positive ⇒
        home favoured).  NSoft's ``g[0]`` is the **home team's signed
        handicap line** (positive ⇒ home is the underdog and gets a head
        start), so the threshold is the negation of that line.
      * ``over_odds`` = home covers (``H1`` outcome).
      * ``under_odds`` = away covers (``H2`` outcome).
    """
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
            market_id = _coerce_int(market.get("b") or market.get("marketId"))
            if market_id not in spec.game_handicap_ot_market_ids:
                continue

            outcomes = market.get("h") or market.get("outcomes") or []
            over_odds, under_odds, h1_line = _extract_handicap_h1_h2_odds(outcomes)
            if h1_line is None:
                continue
            if over_odds is None and under_odds is None:
                continue
            threshold = -h1_line

            results.append(
                RawOddsData(
                    bookmaker_id="balkanbet",
                    league_id=league_id,
                    sport=spec.sport,
                    home_team=home_team,
                    away_team=away_team,
                    market_type="home_handicap_ot",
                    threshold=threshold,
                    over_odds=over_odds,
                    under_odds=under_odds,
                    start_time=start_time,
                )
            )

    return results


def _parse_football_outcome_list(data: dict) -> list[RawOutcomeOffer]:
    results: list[RawOutcomeOffer] = []
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
            {},
            default="football",
        )

        for market in _iter_list_markets(event):
            market_id = _coerce_int(market.get("b") or market.get("marketId"))
            outcome_map = _FOOTBALL_OUTCOME_MARKETS.get(market_id)
            if outcome_map is None:
                continue

            outcomes = market.get("h") or market.get("outcomes") or []
            for outcome in outcomes:
                raw_label = (outcome.get("e") or outcome.get("name") or "").strip()
                mapping = outcome_map.get(raw_label)
                if mapping is None:
                    continue
                odds = _extract_outcome_price(outcome)
                if odds is None:
                    continue
                market_type, outcome_code, line = mapping
                results.append(
                    RawOutcomeOffer(
                        bookmaker_id="balkanbet",
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


def _event_name(event: dict) -> str:
    return (event.get("j") or event.get("name") or "").strip()


def _is_tennis_doubles_event(event: dict) -> bool:
    name = _event_name(event)
    if "/" in name:
        return True

    matchup = _split_match_name(name)
    if matchup is not None:
        home_team, away_team = matchup
        if "/" in home_team or "/" in away_team:
            return True

    text_fields = (
        name,
        str(event.get("tournamentName") or ""),
        str(event.get("categoryName") or ""),
    )
    searchable = " ".join(text_fields).lower()
    return any(token in searchable for token in ("doubles", "double", "dublovi", "parovi"))


def _parse_tennis_outcome_list(data: dict) -> list[RawOutcomeOffer]:
    results: list[RawOutcomeOffer] = []
    events = data.get("data", {}).get("events", [])

    for event in events:
        if not isinstance(event, dict):
            continue
        if not _is_active_flag(event.get("l") if event.get("l") is not None else event.get("active")):
            continue
        if _is_tennis_doubles_event(event):
            continue

        matchup = _split_match_name(_event_name(event))
        if matchup is None:
            continue
        home_team, away_team = matchup
        start_time = _normalize_start_time(event.get("n") or event.get("startsAt"))
        league_id = _extract_league_id(
            event.get("c") if event.get("c") is not None else event.get("categoryId"),
            event.get("f") if event.get("f") is not None else event.get("tournamentId"),
            {},
            default="tennis",
        )

        for market in _iter_list_markets(event):
            market_id = _coerce_int(market.get("b") or market.get("marketId"))
            if market_id != _TENNIS_MATCH_WINNER_MARKET_ID:
                continue
            if not _is_active_flag(
                market.get("d") if market.get("d") is not None else market.get("active")
            ):
                continue

            outcomes = market.get("h") or market.get("outcomes") or []
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                if not _is_active_flag(
                    outcome.get("c") if outcome.get("c") is not None else outcome.get("active")
                ):
                    continue
                raw_label = (outcome.get("e") or outcome.get("name") or "").strip()
                mapping = _TENNIS_OUTCOME_LABELS.get(raw_label)
                if mapping is None:
                    continue
                odds = _extract_outcome_price(outcome)
                if odds is None:
                    continue
                outcome_code, display_label = mapping
                results.append(
                    RawOutcomeOffer(
                        bookmaker_id="balkanbet",
                        league_id=league_id,
                        sport="tennis",
                        home_team=home_team,
                        away_team=away_team,
                        market_type="tennis_match_winner",
                        outcome_code=outcome_code,
                        odds=odds,
                        line=None,
                        raw_label=display_label,
                        start_time=start_time,
                        source_url=_TENNIS_PAGE_URL,
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

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football", "tennis"]

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

        now = current_utc_time()
        now_iso = _format_filter_from(now)
        cutoff_iso = _format_filter_from(lookahead_cutoff(now))
        player_params = {
            **_BASE_LIST_PARAMS,
            "filter[sportId]": spec.player_sport_id,
            "filter[from]": now_iso,
            "filter[to]": cutoff_iso,
        }
        totals_params = {
            **_BASE_LIST_PARAMS,
            "filter[sportId]": spec.totals_sport_id,
            "filter[from]": now_iso,
            "filter[to]": cutoff_iso,
        }

        player_data, totals_data = await asyncio.gather(
            self._fetch_list(player_params, f"{spec.sport} player-points"),
            self._fetch_list(totals_params, f"{spec.sport} game-total-ot"),
        )

        player_results = _parse_player_props_list(player_data, spec)
        totals_results = _parse_game_total_ot_list(totals_data, spec)
        handicap_results = _parse_game_handicap_ot_list(totals_data, spec)
        results = [*player_results, *totals_results, *handicap_results]

        logger.info(
            "BalkanBet scraped %d %s player prop odds, %d %s OT total odds, "
            "and %d %s OT handicap odds",
            len(player_results),
            spec.sport,
            len(totals_results),
            spec.sport,
            len(handicap_results),
            spec.sport,
        )
        return results

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        if sport == "football":
            now = current_utc_time()
            params = {
                **_BASE_LIST_PARAMS,
                "filter[sportId]": _FOOTBALL_OUTCOME_SPORT_ID,
                "filter[from]": _format_filter_from(now),
                "filter[to]": _format_filter_from(lookahead_cutoff(now)),
            }
            data = await self._fetch_list(params, "football outcomes")
            results = _parse_football_outcome_list(data)
            logger.info(
                "BalkanBet scraped %d football outcome offers",
                len(results),
            )
            return results

        if sport != "tennis":
            return []

        now = current_utc_time()
        params = {
            **_BASE_LIST_PARAMS,
            "filter[sportId]": _TENNIS_OUTCOME_SPORT_ID,
            "filter[from]": _format_filter_from(now),
            "filter[to]": _format_filter_from(lookahead_cutoff(now)),
        }
        data = await self._fetch_list(params, "tennis outcomes")
        results = _parse_tennis_outcome_list(data)
        logger.info(
            "BalkanBet scraped %d tennis outcome offers",
            len(results),
        )
        return results
