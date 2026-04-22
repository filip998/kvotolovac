from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData
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


def _extract_league_id(raw_name: str | None) -> str:
    normalized = _normalize_league_key(raw_name)
    if not normalized:
        return "basketball"
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


def _extract_threshold(specifiers: dict[str, object]) -> float | None:
    milestone = _parse_float(specifiers.get("milestone"))
    if milestone is not None:
        return max(milestone - 0.5, 0.5)
    return _parse_float(specifiers.get("total"))


def _extract_side(metadata: dict[str, object], specifiers: dict[str, object]) -> str | None:
    if specifiers.get("milestone") is not None:
        return "over"

    code = str(metadata.get("code") or "").strip()
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
    normalized_group = normalize_identity_text(group_name)
    has_player = bool(_normalize_player_name(specifiers.get("player")))
    has_milestone = specifiers.get("milestone") is not None

    if not has_player and normalized_name == "ukupno poena uklj produzetke":
        return "game_total_ot"

    if "poeni igraca skokovi asistencije" in normalized_name:
        return "player_points_rebounds_assists" if has_player else None
    if "poeni igraca asistencije" in normalized_name:
        return "player_points_assists" if has_player else None
    if "poeni igraca skokovi" in normalized_name and "asistencije" not in normalized_name:
        return "player_points_rebounds" if has_player else None
    if "skokovi igraca asistencije" in normalized_name:
        return "player_rebounds_assists" if has_player else None
    if "ukupno blokada igraca" in normalized_name:
        return "player_blocks" if has_player else None
    if "ukupno ukradenih lopti igraca" in normalized_name:
        return "player_steals" if has_player else None
    if "ukupno izgubljenih lopti igraca" in normalized_name:
        return "player_turnovers" if has_player else None
    if (
        "ukupno pogodaka za 3 poena" in normalized_name
        or normalized_name.startswith("3 poena igraca")
    ):
        return "player_3points" if has_player else None
    if "suteva za 3 poena igraca" in normalized_name:
        return None
    if "ukupno asistencija igraca" in normalized_name:
        return "player_assists" if has_player else None
    if "ukupno skokova igraca" in normalized_name:
        return "player_rebounds" if has_player else None
    if "ukupno poena igraca" in normalized_name:
        if not has_player:
            return None
        return "player_points_milestones" if has_milestone else "player_points"

    if normalized_group == "poeni igraca" and has_player:
        return "player_points_milestones" if has_milestone else "player_points"
    if normalized_group == "asistencije" and has_player:
        return "player_assists"
    if normalized_group == "skokovi" and has_player:
        return "player_rebounds"
    if normalized_group == "3 poena igraca" and has_player:
        return "player_3points"
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

    threshold = _extract_threshold(specifiers)
    if threshold is None:
        return None

    player_name = _normalize_player_name(specifiers.get("player"))
    if market_type != "game_total_ot" and player_name is None:
        return None

    side = _extract_side(metadata, specifiers)
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

            league_id = _extract_league_id(league_name or spec.scope_id)
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
