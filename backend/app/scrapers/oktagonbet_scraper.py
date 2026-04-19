from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData

logger = logging.getLogger(__name__)

_PLAYER_LIST_URL = "https://www.oktagonbet.com/restapi/offer/sr/sport/SK/mob"
_TOTALS_LIST_URL = "https://www.oktagonbet.com/restapi/offer/sr/sport/B/mob"
_BULK_URL = "https://www.oktagonbet.com/ibet/offer/prematchesByIds.html"
# Conservative chunk size — observed 124 IDs/PUT working with ~1.4s latency.
_BULK_CHUNK_SIZE = 150

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

_BULK_HEADERS = {
    **_DEFAULT_HEADERS,
    "Content-Type": "application/json",
}

# ── Tip-type code constants (numeric IDs are stable across restapi/ibet) ──
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

_GAME_TOTAL_OT_LINES = [
    ("50445", "50444", "overUnderOvertime"),
    ("50447", "50446", "overUnderOvertime2"),
    ("50449", "50448", "overUnderOvertime3"),
    ("50451", "50450", "overUnderOvertime4"),
    ("50453", "50452", "overUnderOvertime5"),
    ("50455", "50454", "overUnderOvertime6"),
    ("50457", "50456", "overUnderOvertime7"),
    ("51637", "51636", "overUnderOvertime8"),
    ("51639", "51638", "overUnderOvertime9"),
    ("51641", "51640", "overUnderOvertime10"),
    ("51643", "51642", "overUnderOvertime11"),
    ("51645", "51644", "overUnderOvertime12"),
    ("51647", "51646", "overUnderOvertime13"),
]

_LEAGUE_PREFIX = "igrači ~"

# Map OktagonBet league suffixes to canonical IDs used by other scrapers,
# so cross-bookmaker match comparison works (match_id includes league_id).
_CANONICAL_LEAGUES = {
    "usa nba": "nba",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "aba liga winners stage": "aba_liga",
    "aba liga losers stage": "aba_liga",
    "aba liga plej of": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
    "argentina liga a": "argentina_1",
    "new zealand nbl": "new_zealand",
    "puerto rico bsn": "portoriko_1",
    "south korea kbl": "south_korea_play_offs",
    "uruguay liga uruguaya": "uruguay_winners_stage",
}

# ── Sport-spec scaffolding (extensible to football/tennis later) ──

# Categories drive how home/away/player_name are populated on RawOddsData.
# "player" → home is the player, away is the team (matches OktagonBet SK shape).
# "game"   → home/away are teams, player_name is None.
_CATEGORY_PLAYER = "player"
_CATEGORY_GAME = "game"


@dataclass(frozen=True)
class TipTypeMapping:
    """How to interpret one numeric tipTypeId from a bulk PUT response."""

    market_type: str
    side: str  # "over" or "under" (extensible for 1X2 markets later)
    category: str  # _CATEGORY_PLAYER or _CATEGORY_GAME
    fixed_threshold: float | None = None  # if None, threshold comes from group's handicapParamValue


@dataclass(frozen=True)
class SportSpec:
    """Per-sport configuration for the bulk-API parsing pipeline."""

    sport_name: str  # value used in RawOddsData.sport (e.g. "basketball")
    list_endpoints: tuple[str, ...]  # restapi list URLs to discover match IDs from
    tip_type_map: dict[int, TipTypeMapping] = field(default_factory=dict)
    # Filter callback applied to each match dict from a list endpoint.
    # Returns True if this match should be queried via the bulk endpoint.
    match_filter: Callable[[dict], bool] = lambda _m: True


def _build_basketball_tip_type_map() -> dict[int, TipTypeMapping]:
    mapping: dict[int, TipTypeMapping] = {}
    for over_code, under_code, _, market_type in _THRESHOLD_LINES:
        mapping[int(over_code)] = TipTypeMapping(market_type, "over", _CATEGORY_PLAYER)
        mapping[int(under_code)] = TipTypeMapping(market_type, "under", _CATEGORY_PLAYER)
    for code, threshold in _FIXED_POINT_LADDERS:
        mapping[int(code)] = TipTypeMapping(
            "player_points_milestones", "over", _CATEGORY_PLAYER,
            fixed_threshold=threshold,
        )
    for over_code, under_code, _ in _GAME_TOTAL_OT_LINES:
        mapping[int(over_code)] = TipTypeMapping("game_total_ot", "over", _CATEGORY_GAME)
        mapping[int(under_code)] = TipTypeMapping("game_total_ot", "under", _CATEGORY_GAME)
    return mapping


def _basketball_match_filter(match: dict) -> bool:
    """Accept SK player markets (excluding duels/specials) + B (game) matches."""
    sport = (match.get("sport") or "").upper()
    if sport == "SK":
        return _is_player_market(match)
    if sport == "B":
        return True
    # Fallback: if sport field missing (test fixtures), use leagueName heuristic.
    league_name = (match.get("leagueName") or "").lower()
    if league_name.startswith(_LEAGUE_PREFIX):
        return _is_player_market(match)
    return True


_SPORT_SPECS: dict[str, SportSpec] = {
    "basketball": SportSpec(
        sport_name="basketball",
        list_endpoints=(_PLAYER_LIST_URL, _TOTALS_LIST_URL),
        tip_type_map=_build_basketball_tip_type_map(),
        match_filter=_basketball_match_filter,
    ),
}


# ── Helpers (reused by both legacy parsers and bulk parser) ──


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

    normalized = " ".join(raw.replace("_", " ").replace("-", " ").replace("~", " ").split())
    if not normalized:
        return "basketball"
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


def _build_raw_odds(
    match: dict,
    *,
    market_type: str,
    threshold: float,
    over_odds: float | None,
    under_odds: float | None,
    sport_name: str = "basketball",
) -> RawOddsData:
    player_name = match.get("home", "")
    team = match.get("away", "")
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(match.get("leagueName", ""))

    return RawOddsData(
        bookmaker_id="oktagonbet",
        league_id=league_id,
        sport=sport_name,
        home_team=team,
        away_team=player_name,
        market_type=market_type,
        player_name=player_name,
        threshold=threshold,
        over_odds=over_odds,
        under_odds=under_odds,
        start_time=start_time,
    )


def _build_game_total_raw_odds(
    match: dict,
    *,
    market_type: str,
    threshold: float,
    over_odds: float | None,
    under_odds: float | None,
    sport_name: str = "basketball",
) -> RawOddsData:
    home_team = match.get("home", "").strip()
    away_team = match.get("away", "").strip()
    start_time = _parse_start_time(match.get("kickOffTime"))
    league_id = _extract_league_id(match.get("leagueName", ""))

    return RawOddsData(
        bookmaker_id="oktagonbet",
        league_id=league_id,
        sport=sport_name,
        home_team=home_team,
        away_team=away_team,
        market_type=market_type,
        player_name=None,
        threshold=threshold,
        over_odds=over_odds,
        under_odds=under_odds,
        start_time=start_time,
    )


# ── Legacy list-format parsers (still used by tests; cheap defense-in-depth) ──


def _parse_match(match: dict) -> list[RawOddsData]:
    """Parse a single bulk-listing match into RawOddsData entries (legacy params/odds shape)."""
    if not _is_player_market(match):
        return []

    params = match.get("params", {}) or {}
    odds = match.get("odds", {}) or {}

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


def _parse_game_total_ot_match(match: dict) -> list[RawOddsData]:
    if _is_player_market(match):
        return []

    home_team = match.get("home", "").strip()
    away_team = match.get("away", "").strip()
    if not home_team or not away_team:
        return []

    params = match.get("params", {}) or {}
    odds = match.get("odds", {}) or {}

    results: list[RawOddsData] = []
    for over_code, under_code, param_key in _GAME_TOTAL_OT_LINES:
        threshold = _parse_threshold(params.get(param_key))
        if threshold is None:
            continue

        over_odds = odds.get(over_code)
        under_odds = odds.get(under_code)
        if over_odds is None and under_odds is None:
            continue

        results.append(
            _build_game_total_raw_odds(
                match,
                market_type="game_total_ot",
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
            )
        )

    return results


def _parse_match_detail(match: dict) -> list[RawOddsData]:
    """Parse fixed-threshold player points ladders from a legacy detail-format match."""
    if not _is_player_market(match):
        return []

    odds = match.get("odds", {}) or {}
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


# ── New bulk-PUT parser (sport-spec driven) ──


def _coerce_odd(value) -> float | None:
    """Bulk responses use 0/None to mean 'no offer'."""
    if value is None:
        return None
    try:
        odd = float(value)
    except (TypeError, ValueError):
        return None
    if odd <= 0:
        return None
    return odd


def _iter_pick_groups(match: dict) -> Iterable[dict]:
    groups = match.get("odBetPickGroups")
    if not isinstance(groups, list):
        return ()
    return groups


def _parse_bulk_match(match: dict, spec: SportSpec) -> list[RawOddsData]:
    """Parse one bulk-PUT match into RawOddsData using the sport's tip-type map.

    Walks ``odBetPickGroups[].tipTypes[]`` and emits one row per (market, threshold)
    by collecting the over/under sides of each tipTypeId mapped in ``spec.tip_type_map``.
    """
    tip_map = spec.tip_type_map
    if not tip_map:
        return []

    # Aggregate by (market_type, threshold, category) so over/under from the
    # same group land on a single row.
    @dataclass
    class _Bucket:
        market_type: str
        threshold: float
        category: str
        over: float | None = None
        under: float | None = None

    buckets: dict[tuple[str, float, str], _Bucket] = {}
    order: list[tuple[str, float, str]] = []

    for group in _iter_pick_groups(match):
        if not isinstance(group, dict):
            continue
        group_threshold_raw = group.get("handicapParamValue")
        try:
            group_threshold = (
                float(group_threshold_raw) if group_threshold_raw is not None else None
            )
        except (TypeError, ValueError):
            group_threshold = None

        for tip in group.get("tipTypes", []) or []:
            if not isinstance(tip, dict):
                continue
            try:
                tip_id = int(tip.get("tipTypeId"))
            except (TypeError, ValueError):
                continue
            mapping = tip_map.get(tip_id)
            if mapping is None:
                continue

            threshold = (
                mapping.fixed_threshold
                if mapping.fixed_threshold is not None
                else group_threshold
            )
            if threshold is None:
                continue

            odd = _coerce_odd(tip.get("value"))
            if odd is None:
                continue

            key = (mapping.market_type, threshold, mapping.category)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = _Bucket(mapping.market_type, threshold, mapping.category)
                buckets[key] = bucket
                order.append(key)

            if mapping.side == "over":
                if bucket.over is None:
                    bucket.over = odd
            elif mapping.side == "under":
                if bucket.under is None:
                    bucket.under = odd

    results: list[RawOddsData] = []
    for key in order:
        bucket = buckets[key]
        if bucket.over is None and bucket.under is None:
            continue
        if bucket.category == _CATEGORY_PLAYER:
            results.append(
                _build_raw_odds(
                    match,
                    market_type=bucket.market_type,
                    threshold=bucket.threshold,
                    over_odds=bucket.over,
                    under_odds=bucket.under,
                    sport_name=spec.sport_name,
                )
            )
        else:
            home_team = (match.get("home") or "").strip()
            away_team = (match.get("away") or "").strip()
            if not home_team or not away_team:
                continue
            results.append(
                _build_game_total_raw_odds(
                    match,
                    market_type=bucket.market_type,
                    threshold=bucket.threshold,
                    over_odds=bucket.over,
                    under_odds=bucket.under,
                    sport_name=spec.sport_name,
                )
            )

    return results


def _chunked(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class OktagonBetScraper(BaseScraper):
    """Scraper for OktagonBet odds.

    Uses a single bulk PUT (``ibet/offer/prematchesByIds.html``) that returns
    every market and odd for every requested match in one round-trip — replacing
    the previous N+1 per-match detail loop. List endpoints are still used to
    discover match IDs and metadata.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "oktagonbet"

    def get_bookmaker_name(self) -> str:
        return "OktagonBet"

    def get_supported_leagues(self) -> list[str]:
        return list(_SPORT_SPECS.keys())

    async def _fetch_list(self, url: str, label: str) -> dict:
        try:
            return await self._http.get_json(
                url,
                params=_DEFAULT_PARAMS,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("OktagonBet: failed to fetch %s list", label, exc_info=True)
            return {}

    async def _fetch_bulk_chunk(self, match_ids: list[int]) -> dict:
        if not match_ids:
            return {}
        try:
            payload = await self._http.put_json(
                _BULK_URL,
                json_body=match_ids,
                headers=_BULK_HEADERS,
            )
        except Exception:
            logger.warning(
                "OktagonBet: bulk PUT failed for %d match IDs",
                len(match_ids),
                exc_info=True,
            )
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    async def _fetch_bulk(self, match_ids: list[int]) -> dict:
        if not match_ids:
            return {}
        chunks = list(_chunked(match_ids, _BULK_CHUNK_SIZE))
        chunk_results = await asyncio.gather(
            *(self._fetch_bulk_chunk(chunk) for chunk in chunks)
        )
        merged: dict = {}
        for result in chunk_results:
            merged.update(result)
        return merged

    @staticmethod
    def _dedupe_raw_odds(rows: list[RawOddsData]) -> list[RawOddsData]:
        """Merge rows that share an identity key, combining over/under coverage.

        Two rows from different sources (legacy LIST parse + new bulk parse) may
        carry one side each — naive last-write-wins would silently drop the
        other side. Instead we merge: keep the first non-null value per side,
        and prefer the latest metadata fields (start_time etc.) since those
        come from the bulk path which is generally fresher.
        """
        deduped: dict[tuple, RawOddsData] = {}
        order: list[tuple] = []
        for row in rows:
            key = (
                row.bookmaker_id,
                row.league_id,
                row.home_team,
                row.away_team,
                row.player_name,
                row.market_type,
                row.threshold,
            )
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = row
                order.append(key)
                continue

            merged = RawOddsData(
                bookmaker_id=row.bookmaker_id,
                league_id=row.league_id,
                sport=row.sport or existing.sport,
                home_team=row.home_team,
                away_team=row.away_team,
                market_type=row.market_type,
                player_name=row.player_name,
                threshold=row.threshold,
                over_odds=existing.over_odds if existing.over_odds is not None else row.over_odds,
                under_odds=existing.under_odds if existing.under_odds is not None else row.under_odds,
                start_time=row.start_time or existing.start_time,
            )
            deduped[key] = merged
        return [deduped[key] for key in order]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        spec = _SPORT_SPECS.get(league_id)
        if spec is None:
            return []

        list_payloads = await asyncio.gather(
            *(self._fetch_list(url, league_id) for url in spec.list_endpoints)
        )

        # Index list-side metadata (home/away/kickoff/leagueName) by match id.
        # The bulk endpoint returns these too but we trust list-side fields and
        # fall back to bulk fields if missing.
        list_matches: dict[int, dict] = {}
        for payload in list_payloads:
            for match in payload.get("esMatches", []) or []:
                if not spec.match_filter(match):
                    continue
                match_id = match.get("id")
                if not isinstance(match_id, int):
                    continue
                list_matches[match_id] = match

        if not list_matches:
            logger.info("OktagonBet: no matches discovered for league %s", league_id)
            return []

        bulk_by_id = await self._fetch_bulk(list(list_matches.keys()))

        missing_bulk_ids = [mid for mid in list_matches if mid not in bulk_by_id and str(mid) not in bulk_by_id]
        if missing_bulk_ids:
            logger.warning(
                "OktagonBet: bulk PUT returned no data for %d/%d matches (milestones/extended OT will be missing for those)",
                len(missing_bulk_ids), len(list_matches),
            )

        # Legacy parsers still run on the LIST responses — they cost nothing
        # extra and provide a safety net if the bulk PUT returns partial data.
        results: list[RawOddsData] = []
        for match_id, list_match in list_matches.items():
            results.extend(_parse_match(list_match))
            results.extend(_parse_game_total_ot_match(list_match))

            bulk_match = bulk_by_id.get(match_id) or bulk_by_id.get(str(match_id))
            if bulk_match:
                # Merge metadata from list into bulk so the parser sees the
                # canonical leagueName/leagueCategory used by filters.
                merged = {**bulk_match, **{
                    k: list_match[k]
                    for k in ("home", "away", "kickOffTime", "leagueName", "leagueCategory")
                    if list_match.get(k) is not None
                }}
                results.extend(_parse_bulk_match(merged, spec))

        results = self._dedupe_raw_odds(results)

        logger.info(
            "OktagonBet scraped %d odds from %d matches (bulk PUTs: %d)",
            len(results),
            len(list_matches),
            (len(list_matches) + _BULK_CHUNK_SIZE - 1) // _BULK_CHUNK_SIZE,
        )
        return results
