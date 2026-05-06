from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData, RawOutcomeOffer
from ..services.scrape_window import current_utc_time, lookahead_cutoff
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_LOCALE = "sr-Latn-RS"
_BASE_URL = "https://production-superbet-offer-rs.freetls.fastly.net"
_STRUCTURE_URL = f"{_BASE_URL}/sb-rs/api/v2/{_LOCALE}/struct"
_EVENTS_BY_DATE_URL = f"{_BASE_URL}/sb-rs/api/v2/{_LOCALE}/events/by-date"
_MARKET_GROUPS_URL = (
    f"{_BASE_URL}/sb-rs/api/v2/{_LOCALE}/sport/{{sport_id}}/phase/prematch/market-groups"
)
_EVENT_SUBSCRIPTION_URL = f"{_BASE_URL}/sb-rs/api/v3/subscription/{_LOCALE}/events"

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Referer": "https://superbet.rs/sportske-opklade/kosarka/danas",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}
_SSE_HEADERS: dict[str, str] = {
    **_DEFAULT_HEADERS,
    "Accept": "text/event-stream",
}

_BOOKMAKER_ID = "superbet"
_DETAIL_BATCH_SIZE = 8
_DETAIL_CONCURRENCY = 4
_MATCH_NAME_RE = re.compile(r"\s*·\s*|\s+vs\s+|\s+v\s+|\s+-\s+|\s+—\s+", re.IGNORECASE)

_CANONICAL_LEAGUES: dict[str, str] = {
    "nba": "nba",
    "nba play off": "nba",
    "nba play offs": "nba",
    "nba playoff": "nba",
    "nba plej of": "nba",
    "evroliga": "euroleague",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba liga plej of": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
    "germany bbl": "germany",
    "nemacka bbl": "germany",
    "puerto rico": "portoriko_1",
    "puerto rico bsn": "portoriko_1",
    "portoriko 1": "portoriko_1",
}

_GAME_TOTAL_MARKET_NAMES = {
    "ukupno poena uklj produzetke",
}

# OT-inclusive Asian handicap (full game). SuperBet exposes this market under
# the human-readable name ``Hendikep poena (uklj. produžetke)``. Each odd's
# ``metadata.specifiers["hcp"]`` carries the signed line value (team1's
# Asian handicap; **negative = team1 favoured**), and ``metadata.code`` is
# ``"1"`` (team1=home covers) or ``"2"`` (team2=away covers). Each market
# emits a full ladder of lines.
#
# IMPORTANT: SuperBet uses the MaxBet/AdmiralBet/PinnBet/Mozzart sign
# convention (not the Tipster one), so the parser FLIPS the sign:
# ``threshold = -hcp`` (positive threshold = home favoured per analyzer
# canonical convention).
_HANDICAP_OT_MARKET_NAMES = {
    "hendikep poena uklj produzetke",
}

_PLAYER_POINTS_MARKET_NAMES = {
    "ukupno poena igraca uklj produzetke",
}

_PLAYER_ASSISTS_MARKET_NAMES = {
    "ukupno asistencija igraca uklj produzetke",
}

_PLAYER_REBOUNDS_MARKET_NAMES = {
    "ukupno skokova igraca uklj produzetke",
}

_PLAYER_3POINTS_MARKET_NAMES = {
    "ukupno pogodaka za 3 poena uklj produzetke",
    "3 poena igraca uklj produzetke",
}

_PLAYER_POINTS_ASSISTS_MARKET_NAMES = {
    "poeni igraca asistencije uklj produzetke",
}

_PLAYER_POINTS_REBOUNDS_MARKET_NAMES = {
    "poeni igraca skokovi uklj produzetke",
}

_PLAYER_REBOUNDS_ASSISTS_MARKET_NAMES = {
    "skokovi igraca asistencije uklj produzetke",
}

_PLAYER_POINTS_REBOUNDS_ASSISTS_MARKET_NAMES = {
    "poeni igraca skokovi asistencije uklj produzetke",
}

_PLAYER_BLOCKS_MARKET_NAMES = {
    "ukupno blokada igraca uklj produzetke",
}

_PLAYER_STEALS_MARKET_NAMES = {
    "ukupno ukradenih lopti igraca uklj produzetke",
}

_PLAYER_TURNOVERS_MARKET_NAMES = {
    "ukupno izgubljenih lopti igraca uklj produzetke",
}


# Football outcome-offer markets are surfaced in the SuperBet event
# subscription payload as full-game match markets. Each is anchored on
# its **stable numeric market id** (preferred over the localized name,
# which can drift across releases). The codes inside each market come
# from ``odds[].metadata.code`` and are scoped per market — there is no
# cross-market collision because we look up the mapping only after we
# have already classified the market by id.
#
# - ``Konačan ishod`` (id 547): 1X2 result.
#   Codes: ``"1"`` = home, ``"0"`` = draw (X), ``"2"`` = away.
# - ``Dupla šansa`` (id 531): double chance.
#   Codes: ``"10"`` = 1X (home_or_draw), ``"12"`` = 12 (home_or_away),
#   ``"02"`` = X2 (draw_or_away).
# - ``Ukupno golova`` (id 200734): total goals ladder. Each over/under
#   pair is keyed by ``odds[].metadata.specifiers["total"]`` (e.g.
#   ``"2.5"``). The first MVP slice emits only the **2.5** line, with
#   side derived from the localized prefix on ``metadata.name`` —
#   ``Više`` = over, ``Manje`` = under. We compare the parsed float to
#   ``2.5`` rather than doing string equality so cosmetic variants like
#   ``"2.50"``/``" 2.5 "`` are handled.
_FOOTBALL_RESULT_MARKET_ID = 547
_FOOTBALL_DOUBLE_CHANCE_MARKET_ID = 531
_FOOTBALL_TOTAL_GOALS_MARKET_ID = 200734
_FOOTBALL_TARGET_TOTAL_LINE = 2.5

_FOOTBALL_RESULT_OUTCOMES: dict[str, tuple[str, str]] = {
    "1": ("home", "1"),
    "0": ("draw", "X"),
    "2": ("away", "2"),
}

_FOOTBALL_DOUBLE_CHANCE_OUTCOMES: dict[str, tuple[str, str]] = {
    "10": ("home_or_draw", "1X"),
    "12": ("home_or_away", "12"),
    "02": ("draw_or_away", "X2"),
}

# Side detection for ``Ukupno golova`` is based on the localized prefix
# of ``metadata.name`` (e.g. ``"Više 2.5"``/``"Manje 2.5"``). We
# normalize once via ``normalize_identity_text`` so accent/case shifts
# do not break the match — ``Više`` -> ``vise``, ``Manje`` -> ``manje``.
_FOOTBALL_TOTAL_GOALS_OVER_PREFIX = "vise"
_FOOTBALL_TOTAL_GOALS_UNDER_PREFIX = "manje"


@dataclass(frozen=True)
class SportSpec:
    scope_id: str
    sport_name: str
    sport_id: int
    route_slug: str
    offer_state: str = "prematch"
    detail_batch_size: int = _DETAIL_BATCH_SIZE


@dataclass(frozen=True)
class StructureLookup:
    tournament_names: dict[int, str]
    category_names: dict[int, str]


@dataclass(frozen=True)
class EventContext:
    event_id: int
    league_id: str
    home_team: str
    away_team: str
    start_time: str | None
    source_url: str | None


@dataclass(frozen=True)
class ParsedSelection:
    market_type: str
    player_name: str | None
    threshold: float
    side: str
    odd_value: float


_SPORT_SPECS: dict[str, SportSpec] = {
    "basketball": SportSpec(
        scope_id="basketball",
        sport_name="basketball",
        sport_id=4,
        route_slug="kosarka",
    ),
}

# Football is exposed only through the outcome-offer lane
# (``scrape_outcome_offers``) and must NOT be advertised as a
# threshold-odds league via ``get_supported_leagues()`` — otherwise the
# unified pipeline would generate a bogus ``threshold_odds`` capability
# for ``(basketball, football)`` and call ``scrape_odds("football")``
# every cycle. Keep this lookup separate from ``_SPORT_SPECS``.
_OUTCOME_SPORT_SPECS: dict[str, SportSpec] = {
    "football": SportSpec(
        scope_id="football",
        sport_name="football",
        sport_id=5,
        route_slug="fudbal",
    ),
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


def _extract_localized_name(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, dict):
        return None

    preferred = value.get(_LOCALE)
    if isinstance(preferred, str) and preferred.strip():
        return preferred.strip()

    for candidate in value.values():
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _format_request_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_start_time(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_league_key(raw_name: str | None) -> str:
    return normalize_identity_text(raw_name)


def _extract_league_id(raw_name: str | None, *, default: str = "basketball") -> str:
    normalized = _normalize_league_key(raw_name)
    if not normalized:
        return default
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


def _slugify_segment(value: str) -> str:
    return normalize_identity_text(value, keep_hyphens=True).replace(" ", "-")


def _build_source_url(spec: SportSpec, event_id: int, home_team: str, away_team: str) -> str | None:
    home_slug = _slugify_segment(home_team)
    away_slug = _slugify_segment(away_team)
    if not home_slug or not away_slug:
        return None
    return f"https://superbet.rs/kvote/{spec.route_slug}/{home_slug}-vs-{away_slug}-{event_id}?mdt=o"


def _split_match_name(value: str | None) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    parts = _MATCH_NAME_RE.split(cleaned, maxsplit=1)
    if len(parts) != 2:
        return None
    home_team, away_team = (part.strip() for part in parts)
    if not home_team or not away_team:
        return None
    return home_team, away_team


def _normalize_player_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.replace("\xa0", " ").split())
    if not cleaned:
        return None
    if "," not in cleaned:
        return cleaned
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if len(parts) < 2:
        return cleaned
    return " ".join([*parts[1:], parts[0]]).strip()


def _extract_threshold(
    specifiers: dict[str, object],
    *,
    market_type: str | None = None,
) -> float | None:
    milestone = _parse_float(specifiers.get("milestone"))
    if milestone is not None:
        return max(milestone - 0.5, 0.5)
    if market_type == "home_handicap_ot":
        # SuperBet stores ``hcp`` as team1's Asian handicap (negative = team1
        # favoured). Canonicalise to home-perspective expected margin.
        hcp = _parse_float(specifiers.get("hcp"))
        if hcp is None:
            return None
        return -hcp
    return _parse_float(specifiers.get("total"))


def _extract_side(
    metadata: dict[str, object],
    specifiers: dict[str, object],
    *,
    market_type: str | None = None,
) -> str | None:
    if specifiers.get("milestone") is not None:
        return "over"

    code = str(metadata.get("code") or "").strip()
    if market_type == "home_handicap_ot":
        # "1" = team1=home covers → maps to over; "2" = team2=away covers → under
        if code == "1":
            return "over"
        if code == "2":
            return "under"
        return None

    if code == "+":
        return "over"
    if code == "-":
        return "under"

    normalized_name = normalize_identity_text(str(metadata.get("name") or ""))
    normalized_info = normalize_identity_text(str(metadata.get("info") or ""))
    if "vise od" in normalized_name or "vise od" in normalized_info:
        return "over"
    if "manje od" in normalized_name or "manje od" in normalized_info:
        return "under"
    return None


def _classify_market_type(
    market_name: str,
    group_name: str | None,
    specifiers: dict[str, object],
) -> str | None:
    normalized_name = normalize_identity_text(market_name)
    has_player = bool(_normalize_player_name(specifiers.get("player")))
    has_milestone = specifiers.get("milestone") is not None

    if not has_player and normalized_name in _GAME_TOTAL_MARKET_NAMES:
        return "game_total_ot"

    if not has_player and normalized_name in _HANDICAP_OT_MARKET_NAMES:
        return "home_handicap_ot"

    if normalized_name in _PLAYER_POINTS_REBOUNDS_ASSISTS_MARKET_NAMES:
        return "player_points_rebounds_assists" if has_player else None
    if normalized_name in _PLAYER_POINTS_ASSISTS_MARKET_NAMES:
        return "player_points_assists" if has_player else None
    if normalized_name in _PLAYER_POINTS_REBOUNDS_MARKET_NAMES:
        return "player_points_rebounds" if has_player else None
    if normalized_name in _PLAYER_REBOUNDS_ASSISTS_MARKET_NAMES:
        return "player_rebounds_assists" if has_player else None
    if normalized_name in _PLAYER_BLOCKS_MARKET_NAMES:
        return "player_blocks" if has_player else None
    if normalized_name in _PLAYER_STEALS_MARKET_NAMES:
        return "player_steals" if has_player else None
    if normalized_name in _PLAYER_TURNOVERS_MARKET_NAMES:
        return "player_turnovers" if has_player else None
    if normalized_name in _PLAYER_3POINTS_MARKET_NAMES:
        return "player_3points" if has_player else None
    if normalized_name in _PLAYER_ASSISTS_MARKET_NAMES:
        return "player_assists" if has_player else None
    if normalized_name in _PLAYER_REBOUNDS_MARKET_NAMES:
        return "player_rebounds" if has_player else None
    if normalized_name in _PLAYER_POINTS_MARKET_NAMES:
        if not has_player:
            return None
        return "player_points_milestones" if has_milestone else "player_points"
    return None


def _extract_group_name(market: dict, market_group_lookup: dict[int, str]) -> str | None:
    market_id = _parse_int(market.get("id"))
    if market_id is None:
        return None
    return market_group_lookup.get(market_id)


def _parse_selection(
    market: dict,
    odd: dict,
    *,
    group_name: str | None,
) -> ParsedSelection | None:
    if odd.get("display") is False:
        return None
    if odd.get("status") not in (1, "1", "active", "ACTIVE"):
        return None

    price = _parse_float(odd.get("price"))
    if price is None or price <= 1.0:
        return None

    metadata = odd.get("metadata")
    if not isinstance(metadata, dict):
        return None
    specifiers = metadata.get("specifiers")
    if not isinstance(specifiers, dict):
        specifiers = {}

    market_name = str(market.get("name") or "")
    market_type = _classify_market_type(market_name, group_name, specifiers)
    if market_type is None:
        return None

    threshold = _extract_threshold(specifiers, market_type=market_type)
    if threshold is None:
        return None

    player_name = _normalize_player_name(specifiers.get("player"))
    if market_type not in ("game_total_ot", "home_handicap_ot") and player_name is None:
        return None

    side = _extract_side(metadata, specifiers, market_type=market_type)
    if side is None:
        return None

    return ParsedSelection(
        market_type=market_type,
        player_name=player_name,
        threshold=threshold,
        side=side,
        odd_value=price,
    )


def _parse_event_payload(
    event_payload: dict,
    *,
    context: EventContext,
    market_group_lookup: dict[int, str],
) -> list[RawOddsData]:
    markets = event_payload.get("markets")
    if not isinstance(markets, list):
        return []

    grouped: dict[tuple[str, str | None, float], dict[str, float | None]] = {}

    for market in markets:
        if not isinstance(market, dict):
            continue
        group_name = _extract_group_name(market, market_group_lookup)
        odds = market.get("odds")
        if not isinstance(odds, list):
            continue
        for odd in odds:
            if not isinstance(odd, dict):
                continue
            parsed = _parse_selection(market, odd, group_name=group_name)
            if parsed is None:
                continue
            key = (parsed.market_type, parsed.player_name, parsed.threshold)
            line = grouped.setdefault(key, {"over": None, "under": None})
            line[parsed.side] = parsed.odd_value

    results: list[RawOddsData] = []
    for (market_type, player_name, threshold), odds in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1] or "", item[0][2]),
    ):
        if odds["over"] is None and odds["under"] is None:
            continue
        results.append(
            RawOddsData(
                bookmaker_id=_BOOKMAKER_ID,
                league_id=context.league_id,
                sport="basketball",
                home_team=context.home_team,
                away_team=context.away_team,
                source_url=context.source_url,
                market_type=market_type,
                player_name=player_name,
                threshold=threshold,
                over_odds=odds["over"],
                under_odds=odds["under"],
                start_time=context.start_time,
            )
        )
    return results


def _extract_wrapped_event_id(value: dict) -> int | None:
    event_id = _parse_int(value.get("event_id"))
    if event_id is not None:
        return event_id
    fixture = value.get("fixture")
    if not isinstance(fixture, dict):
        return None
    return _parse_int(fixture.get("event_id") or fixture.get("offer_id"))


def _odd_is_displayable(odd: dict) -> bool:
    if odd.get("display") is False:
        return False
    if odd.get("status") not in (1, "1", "active", "ACTIVE"):
        return False
    return True


def _football_total_side_from_name(name: str | None) -> str | None:
    if not isinstance(name, str):
        return None
    normalized = normalize_identity_text(name)
    if not normalized:
        return None
    head = normalized.split(" ", 1)[0]
    if head == _FOOTBALL_TOTAL_GOALS_OVER_PREFIX:
        return "over"
    if head == _FOOTBALL_TOTAL_GOALS_UNDER_PREFIX:
        return "under"
    return None


def _parse_football_event_payload(
    event_payload: dict,
    *,
    context: EventContext,
    bookmaker_id: str = _BOOKMAKER_ID,
) -> list[RawOutcomeOffer]:
    """Emit Superbet football outcome offers from a subscription payload.

    Identity fields (home/away/league/start_time) are taken from
    ``context`` (built off ``events-by-date``) and never from the per-
    event detail payload, so the same logical event always normalizes
    consistently across batches.
    """

    markets = event_payload.get("markets")
    if not isinstance(markets, list):
        return []

    results: list[RawOutcomeOffer] = []

    for market in markets:
        if not isinstance(market, dict):
            continue
        market_id = _parse_int(market.get("id"))
        if market_id is None:
            continue
        if market_id not in (
            _FOOTBALL_RESULT_MARKET_ID,
            _FOOTBALL_DOUBLE_CHANCE_MARKET_ID,
            _FOOTBALL_TOTAL_GOALS_MARKET_ID,
        ):
            continue

        odds = market.get("odds")
        if not isinstance(odds, list):
            continue

        for odd in odds:
            if not isinstance(odd, dict) or not _odd_is_displayable(odd):
                continue
            price = _parse_float(odd.get("price"))
            if price is None or price <= 1.0:
                continue
            metadata = odd.get("metadata")
            if not isinstance(metadata, dict):
                continue

            offer = _parse_football_odd(
                market_id=market_id,
                metadata=metadata,
                price=price,
                context=context,
                bookmaker_id=bookmaker_id,
            )
            if offer is not None:
                results.append(offer)

    return results


def _parse_football_odd(
    *,
    market_id: int,
    metadata: dict,
    price: float,
    context: EventContext,
    bookmaker_id: str,
) -> RawOutcomeOffer | None:
    if market_id == _FOOTBALL_RESULT_MARKET_ID:
        code = metadata.get("code")
        mapping = _FOOTBALL_RESULT_OUTCOMES.get(str(code) if code is not None else "")
        if mapping is None:
            return None
        outcome_code, raw_label = mapping
        return RawOutcomeOffer(
            bookmaker_id=bookmaker_id,
            league_id=context.league_id,
            sport="football",
            home_team=context.home_team,
            away_team=context.away_team,
            source_url=context.source_url,
            market_type="football_result",
            outcome_code=outcome_code,
            odds=price,
            line=None,
            raw_label=raw_label,
            start_time=context.start_time,
        )

    if market_id == _FOOTBALL_DOUBLE_CHANCE_MARKET_ID:
        code = metadata.get("code")
        mapping = _FOOTBALL_DOUBLE_CHANCE_OUTCOMES.get(str(code) if code is not None else "")
        if mapping is None:
            return None
        outcome_code, raw_label = mapping
        return RawOutcomeOffer(
            bookmaker_id=bookmaker_id,
            league_id=context.league_id,
            sport="football",
            home_team=context.home_team,
            away_team=context.away_team,
            source_url=context.source_url,
            market_type="football_double_chance",
            outcome_code=outcome_code,
            odds=price,
            line=None,
            raw_label=raw_label,
            start_time=context.start_time,
        )

    if market_id == _FOOTBALL_TOTAL_GOALS_MARKET_ID:
        specifiers = metadata.get("specifiers")
        if not isinstance(specifiers, dict):
            return None
        line_value = _parse_float(specifiers.get("total"))
        if line_value is None or line_value != _FOOTBALL_TARGET_TOTAL_LINE:
            return None
        side = _football_total_side_from_name(metadata.get("name"))
        if side is None:
            return None
        raw_label = "3+" if side == "over" else "0-2"
        return RawOutcomeOffer(
            bookmaker_id=bookmaker_id,
            league_id=context.league_id,
            sport="football",
            home_team=context.home_team,
            away_team=context.away_team,
            source_url=context.source_url,
            market_type="football_total_goals",
            outcome_code=side,
            odds=price,
            line=_FOOTBALL_TARGET_TOTAL_LINE,
            raw_label=raw_label,
            start_time=context.start_time,
        )

    return None


def _iter_event_payloads(messages: list[object]) -> list[dict]:
    payloads: list[dict] = []
    for message in messages:
        if isinstance(message, list):
            payloads.extend(item for item in message if isinstance(item, dict))
            continue
        if isinstance(message, dict):
            payloads.append(message)
    return payloads


def _chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


class SuperbetScraper(BaseScraper):
    """Public Superbet basketball scraper built from observed list + SSE detail feeds."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)
        self._structure_cache: dict[str, StructureLookup] = {}
        self._market_group_cache: dict[str, dict[int, str]] = {}

    def get_bookmaker_id(self) -> str:
        return _BOOKMAKER_ID

    def get_bookmaker_name(self) -> str:
        return "Superbet"

    def get_supported_leagues(self) -> list[str]:
        return sorted(_SPORT_SPECS)

    async def _get_structure_lookup(self, spec: SportSpec) -> StructureLookup:
        cached = self._structure_cache.get(spec.scope_id)
        if cached is not None:
            return cached

        try:
            data = await self._http.get_json(
                _STRUCTURE_URL,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("Superbet: failed to fetch structure catalog")
            return StructureLookup(tournament_names={}, category_names={})

        payload = data.get("data") or {}
        tournaments = payload.get("tournaments") or []
        categories = payload.get("categories") or []

        tournament_names: dict[int, str] = {}
        for tournament in tournaments:
            if not isinstance(tournament, dict):
                continue
            tournament_id = _parse_int(tournament.get("id"))
            tournament_name = _extract_localized_name(tournament.get("localNames"))
            if tournament_id is None or tournament_name is None:
                continue
            tournament_names[tournament_id] = tournament_name

        category_names: dict[int, str] = {}
        for category in categories:
            if not isinstance(category, dict):
                continue
            category_id = _parse_int(category.get("id"))
            category_name = _extract_localized_name(category.get("localNames"))
            if category_id is None or category_name is None:
                continue
            category_names[category_id] = category_name

        lookup = StructureLookup(
            tournament_names=tournament_names,
            category_names=category_names,
        )
        if tournament_names or category_names:
            self._structure_cache[spec.scope_id] = lookup
        return lookup

    async def _get_market_group_lookup(self, spec: SportSpec) -> dict[int, str]:
        cached = self._market_group_cache.get(spec.scope_id)
        if cached is not None:
            return cached

        try:
            data = await self._http.get_json(
                _MARKET_GROUPS_URL.format(sport_id=spec.sport_id),
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("Superbet: failed to fetch market groups for %s", spec.scope_id)
            return {}

        groups = data.get("data") or []
        market_lookup: dict[int, str] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = _extract_localized_name(group.get("localNames"))
            if group_name is None:
                continue
            for market_id in group.get("markets") or []:
                parsed_market_id = _parse_int(market_id)
                if parsed_market_id is None:
                    continue
                market_lookup[parsed_market_id] = group_name

        if market_lookup:
            self._market_group_cache[spec.scope_id] = market_lookup
        return market_lookup

    async def _fetch_events_by_date(self, spec: SportSpec) -> list[dict]:
        now = current_utc_time()
        cutoff = lookahead_cutoff(now)
        params = {
            "offerState": spec.offer_state,
            "sportId": str(spec.sport_id),
            "startDate": _format_request_datetime(now),
            "endDate": _format_request_datetime(cutoff),
        }

        try:
            data = await self._http.get_json(
                _EVENTS_BY_DATE_URL,
                params=params,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.warning("Superbet: failed to fetch %s discovery feed", spec.scope_id)
            return []

        rows = data.get("data") or []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _build_event_contexts(
        self,
        spec: SportSpec,
        events: list[dict],
        *,
        structure_lookup: StructureLookup,
    ) -> dict[int, EventContext]:
        contexts: dict[int, EventContext] = {}

        for event in events:
            event_id = _parse_int(event.get("eventId"))
            sport_id = _parse_int(event.get("sportId"))
            if event_id is None or sport_id != spec.sport_id:
                continue

            matchup = _split_match_name(event.get("matchName"))
            if matchup is None:
                logger.debug("Superbet: skipping event %s without parsable match name", event_id)
                continue

            home_team, away_team = matchup
            tournament_id = _parse_int(event.get("tournamentId"))
            category_id = _parse_int(event.get("categoryId"))

            league_name = None
            if tournament_id is not None:
                league_name = structure_lookup.tournament_names.get(tournament_id)
            if league_name is None and category_id is not None:
                league_name = structure_lookup.category_names.get(category_id)

            league_id = _extract_league_id(
                league_name or spec.scope_id,
                default=spec.scope_id,
            )
            start_time = _parse_start_time(event.get("utcDate"))
            contexts[event_id] = EventContext(
                event_id=event_id,
                league_id=league_id,
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                source_url=_build_source_url(spec, event_id, home_team, away_team),
            )

        return contexts

    async def _request_event_batch(self, event_ids: list[int]) -> list[dict]:
        messages = await self._http.get_sse_json(
            _EVENT_SUBSCRIPTION_URL,
            params={"events": ",".join(str(event_id) for event_id in event_ids)},
            headers=_SSE_HEADERS,
            max_messages=1,
            read_timeout=10.0,
        )
        return _iter_event_payloads(messages)

    async def _fetch_event_batch_payloads(self, event_ids: list[int]) -> dict[int, dict]:
        try:
            payloads = await self._request_event_batch(event_ids)
        except Exception:
            logger.warning("Superbet: failed to fetch detail batch for events %s", event_ids)
            return {}

        return {
            event_id: payload
            for payload in payloads
            if isinstance(payload, dict)
            and (event_id := _extract_wrapped_event_id(payload)) is not None
        }

    async def _fetch_event_batch(self, event_ids: list[int], *, semaphore: asyncio.Semaphore) -> dict[int, dict]:
        async with semaphore:
            by_event = await self._fetch_event_batch_payloads(event_ids)

        missing_event_ids = [event_id for event_id in event_ids if event_id not in by_event]
        if not missing_event_ids or len(event_ids) == 1:
            return by_event

        logger.warning(
            "Superbet: batch detail missed %d/%d events; retrying singly",
            len(missing_event_ids),
            len(event_ids),
        )
        single_batches = await asyncio.gather(
            *(self._fetch_event_batch([event_id], semaphore=semaphore) for event_id in missing_event_ids)
        )
        for batch in single_batches:
            by_event.update(batch)
        return by_event

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        spec = _SPORT_SPECS.get(league_id)
        if spec is None:
            return []

        structure_lookup, market_group_lookup, raw_events = await asyncio.gather(
            self._get_structure_lookup(spec),
            self._get_market_group_lookup(spec),
            self._fetch_events_by_date(spec),
        )

        contexts = self._build_event_contexts(
            spec,
            raw_events,
            structure_lookup=structure_lookup,
        )
        if not contexts:
            logger.warning("Superbet: no %s events discovered", spec.scope_id)
            return []

        event_ids = sorted(contexts)
        detail_semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        detail_batches = await asyncio.gather(
            *(
                self._fetch_event_batch(batch, semaphore=detail_semaphore)
                for batch in _chunked(event_ids, spec.detail_batch_size)
            )
        )

        detail_by_event: dict[int, dict] = {}
        for batch in detail_batches:
            detail_by_event.update(batch)

        results: list[RawOddsData] = []
        for event_id in event_ids:
            payload = detail_by_event.get(event_id)
            context = contexts.get(event_id)
            if payload is None or context is None:
                continue
            results.extend(
                _parse_event_payload(
                    payload,
                    context=context,
                    market_group_lookup=market_group_lookup,
                )
            )

        return results

    def get_supported_outcome_sports(self) -> list[str]:
        return sorted(_OUTCOME_SPORT_SPECS)

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        spec = _OUTCOME_SPORT_SPECS.get(sport)
        if spec is None:
            return []

        # Football classification is anchored on stable numeric market
        # ids — no market-groups lookup is needed, so we save one HTTP
        # call per cycle versus the basketball flow.
        structure_lookup, raw_events = await asyncio.gather(
            self._get_structure_lookup(spec),
            self._fetch_events_by_date(spec),
        )

        contexts = self._build_event_contexts(
            spec,
            raw_events,
            structure_lookup=structure_lookup,
        )
        if not contexts:
            logger.warning("Superbet: no %s events discovered", spec.scope_id)
            return []

        event_ids = sorted(contexts)
        detail_semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        detail_batches = await asyncio.gather(
            *(
                self._fetch_event_batch(batch, semaphore=detail_semaphore)
                for batch in _chunked(event_ids, spec.detail_batch_size)
            )
        )

        detail_by_event: dict[int, dict] = {}
        for batch in detail_batches:
            detail_by_event.update(batch)

        offers: list[RawOutcomeOffer] = []
        for event_id in event_ids:
            payload = detail_by_event.get(event_id)
            context = contexts.get(event_id)
            if payload is None or context is None:
                continue
            offers.extend(
                _parse_football_event_payload(
                    payload,
                    context=context,
                    bookmaker_id=_BOOKMAKER_ID,
                )
            )

        return offers
