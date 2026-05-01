from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData
from ..services.scrape_window import current_utc_time, lookahead_cutoff
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_BASE_URL = "https://sportdataproviderv3-volcanors.xtreme.bet/api/public"
_OFFER_BASE_URL = f"{_BASE_URL}/offer/GetOfferBase"
_FIXTURES_URL = f"{_BASE_URL}/offer/GetFixtures"
_EVENT_MARKETS_URL = f"{_BASE_URL}/Offer/GetEventMarkets"
_SOURCE_URL = "https://www.volcanobet.rs/sport-v2/prematch/events"

_BOOKMAKER_ID = "volcanobet"
_LANG = "sr"
_CLIENT_TYPE = "WebConsumer"
_DATA_PROVIDER_ID = "3c2ce91e-5abe-46df-8120-17007b544ff1-V3"
_FALLBACK_BASKETBALL_SPORT_ID = "3"

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.volcanobet.rs/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}

_EVENT_BATCH_SIZE = 12
_EVENT_BATCH_CONCURRENCY = 4

_PLAYER_POINTS_MARKET_NAME = "broj poena igraca uklj prod"
_GAME_TOTAL_OT_MARKET_NAME = "zbir poena uklj prod"
_GAME_HANDICAP_OT_MARKET_NAME = "hendikep uklj prod"
_FALLBACK_PLAYER_POINTS_MARKET_IDS = ("921",)
_FALLBACK_GAME_TOTAL_OT_MARKET_IDS = ("225",)
_FALLBACK_GAME_HANDICAP_OT_MARKET_IDS = ("223",)

_CANONICAL_LEAGUES: dict[str, str] = {
    "nba": "nba",
    "nba plej of": "nba",
    "nba play off": "nba",
    "nba play offs": "nba",
    "wnba": "wnba",
    "evroliga": "euroleague",
    "euroleague": "euroleague",
    "eurocup": "eurocup",
    "eurokup": "eurocup",
    "aba liga": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "srbija 1": "kls",
    "argentina 1": "argentina_1",
    "portoriko 1": "portoriko_1",
}


@dataclass(frozen=True)
class OfferBaseLookup:
    basketball_sport_id: str
    league_names: dict[str, str]
    player_points_market_ids: tuple[str, ...]
    game_total_ot_market_ids: tuple[str, ...]
    game_handicap_ot_market_ids: tuple[str, ...] = _FALLBACK_GAME_HANDICAP_OT_MARKET_IDS


@dataclass(frozen=True)
class FixtureContext:
    event_id: str
    league_id: str
    home_team: str
    away_team: str
    start_time: str | None
    source_url: str | None


@dataclass(frozen=True)
class ParsedSelection:
    market_type: str
    player_name: str | None
    participant_id: str | None
    threshold: float
    side: str
    odd_value: float


def _normalize_league_key(raw_name: str | None) -> str:
    return normalize_identity_text(raw_name)


def _extract_league_id(raw_name: str | None) -> str:
    normalized = _normalize_league_key(raw_name)
    if not normalized:
        return "basketball"
    return _CANONICAL_LEAGUES.get(normalized, normalized.replace(" ", "_"))


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.replace("\xa0", " ").split())
    return cleaned or None


def _parse_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed


def _parse_threshold(value: object) -> float | None:
    threshold = _parse_float(value)
    if threshold is None or threshold <= 0:
        return None
    return threshold


def _parse_odd_value(value: object) -> float | None:
    odd_value = _parse_float(value)
    if odd_value is None or odd_value <= 1.0:
        return None
    return odd_value


def _parse_start_time(value: object) -> tuple[str | None, datetime | None]:
    if not isinstance(value, str) or not value.strip():
        return None, None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_dt = parsed.astimezone(timezone.utc)
    return utc_dt.isoformat(), utc_dt


def _extract_market_name_for_sport(market: dict, sport_id: str) -> str | None:
    for sport_text in market.get("st") or []:
        if not isinstance(sport_text, dict):
            continue
        if str(sport_text.get("s") or "") != sport_id:
            continue
        name = _clean_text(sport_text.get("n"))
        if name is not None:
            return name
    return _clean_text(market.get("n"))


def _extract_market_ids(
    markets: list[object],
    *,
    basketball_sport_id: str,
    normalized_name: str,
    expected_bet_options: frozenset[str] = frozenset({"under", "over"}),
) -> tuple[str, ...]:
    market_ids: list[str] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_id = _clean_text(market.get("i"))
        market_name = _extract_market_name_for_sport(market, basketball_sport_id)
        if market_id is None or market_name is None:
            continue

        normalized_market_name = normalize_identity_text(market_name)
        if normalized_market_name != normalized_name:
            continue

        bet_options = {
            option
            for bet in market.get("b") or []
            if isinstance(bet, dict)
            for option in [str(bet.get("p") or "").strip().lower()]
            if option
        }
        if bet_options != expected_bet_options:
            continue

        market_ids.append(market_id)

    return tuple(sorted(dict.fromkeys(market_ids)))


def _build_offer_base_lookup(data: object) -> OfferBaseLookup:
    payload = data.get("o") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        return OfferBaseLookup(
            basketball_sport_id=_FALLBACK_BASKETBALL_SPORT_ID,
            league_names={},
            player_points_market_ids=_FALLBACK_PLAYER_POINTS_MARKET_IDS,
            game_total_ot_market_ids=_FALLBACK_GAME_TOTAL_OT_MARKET_IDS,
            game_handicap_ot_market_ids=_FALLBACK_GAME_HANDICAP_OT_MARKET_IDS,
        )

    sports = payload.get("s") or []
    basketball_sport_id = _FALLBACK_BASKETBALL_SPORT_ID
    for sport in sports:
        if not isinstance(sport, dict):
            continue
        if _normalize_league_key(_clean_text(sport.get("n"))) != "kosarka":
            continue
        sport_id = _clean_text(sport.get("i"))
        if sport_id is not None:
            basketball_sport_id = sport_id
            break

    league_names: dict[str, str] = {}
    for league in payload.get("le") or []:
        if not isinstance(league, dict):
            continue
        if str(league.get("si") or "") != basketball_sport_id:
            continue
        league_id = _clean_text(league.get("i"))
        league_name = _clean_text(league.get("n"))
        if league_id is None or league_name is None:
            continue
        league_names[league_id] = league_name

    markets = payload.get("m") or []
    player_points_market_ids = _extract_market_ids(
        markets,
        basketball_sport_id=basketball_sport_id,
        normalized_name=_PLAYER_POINTS_MARKET_NAME,
    )
    game_total_ot_market_ids = _extract_market_ids(
        markets,
        basketball_sport_id=basketball_sport_id,
        normalized_name=_GAME_TOTAL_OT_MARKET_NAME,
    )
    # Asian-handicap market: outcome ``p`` strings are ``"1"`` (team-1
    # covers) and ``"2"`` (team-2 covers) instead of under/over.
    game_handicap_ot_market_ids = _extract_market_ids(
        markets,
        basketball_sport_id=basketball_sport_id,
        normalized_name=_GAME_HANDICAP_OT_MARKET_NAME,
        expected_bet_options=frozenset({"1", "2"}),
    )

    return OfferBaseLookup(
        basketball_sport_id=basketball_sport_id,
        league_names=league_names,
        player_points_market_ids=(
            player_points_market_ids or _FALLBACK_PLAYER_POINTS_MARKET_IDS
        ),
        game_total_ot_market_ids=(
            game_total_ot_market_ids or _FALLBACK_GAME_TOTAL_OT_MARKET_IDS
        ),
        game_handicap_ot_market_ids=(
            game_handicap_ot_market_ids or _FALLBACK_GAME_HANDICAP_OT_MARKET_IDS
        ),
    )


def _extract_teams(participants: object) -> tuple[str, str] | None:
    if not isinstance(participants, list):
        return None

    ordered: list[tuple[int, int, str]] = []
    for index, participant in enumerate(participants):
        if not isinstance(participant, dict):
            continue
        name = _clean_text(participant.get("n"))
        if name is None:
            continue
        try:
            order = int(str(participant.get("p") or index + 1))
        except ValueError:
            order = index + 1
        ordered.append((order, index, name))

    ordered.sort()
    if len(ordered) < 2:
        return None
    return ordered[0][2], ordered[1][2]


def _extract_fixture_contexts(
    data: object,
    *,
    lookup: OfferBaseLookup,
    now: datetime | None = None,
) -> list[FixtureContext]:
    payload = data.get("f") if isinstance(data, dict) else None
    if not isinstance(payload, list):
        return []

    current_time = (now or current_utc_time()).astimezone(timezone.utc)
    cutoff = lookahead_cutoff(current_time)
    contexts: list[FixtureContext] = []

    for fixture in payload:
        if not isinstance(fixture, dict):
            continue
        if str(fixture.get("s") or "") != "NSY":
            continue
        if str(fixture.get("si") or "") != lookup.basketball_sport_id:
            continue

        event_id = _clean_text(fixture.get("ai"))
        if event_id is None:
            continue

        start_time, start_dt = _parse_start_time(fixture.get("sd"))
        if start_time is None or start_dt is None:
            continue
        if start_dt < current_time or start_dt > cutoff:
            continue

        teams = _extract_teams(fixture.get("p"))
        if teams is None:
            continue
        home_team, away_team = teams

        league_name = lookup.league_names.get(str(fixture.get("lei") or ""))
        contexts.append(
            FixtureContext(
                event_id=event_id,
                league_id=_extract_league_id(league_name),
                home_team=home_team,
                away_team=away_team,
                start_time=start_time,
                source_url=_SOURCE_URL,
            )
        )

    contexts.sort(key=lambda item: (item.start_time or "", item.home_team, item.away_team))
    return contexts


def _extract_side(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized == "under":
        return "under"
    if normalized == "over":
        return "over"
    return None


def _parse_player_points_bet(bet: dict) -> ParsedSelection | None:
    player_name = _clean_text(bet.get("pn"))
    participant_id = _clean_text(bet.get("pid"))
    threshold = _parse_threshold(bet.get("bl"))
    odd_value = _parse_odd_value(bet.get("od"))
    side = _extract_side(bet.get("id"))

    if player_name is None or participant_id is None:
        return None
    if threshold is None or odd_value is None or side is None:
        return None

    return ParsedSelection(
        market_type="player_points",
        player_name=player_name,
        participant_id=participant_id,
        threshold=threshold,
        side=side,
        odd_value=odd_value,
    )


def _parse_game_total_ot_bet(bet: dict) -> ParsedSelection | None:
    threshold = _parse_threshold(bet.get("bl"))
    odd_value = _parse_odd_value(bet.get("od"))
    side = _extract_side(bet.get("id"))

    if threshold is None or odd_value is None or side is None:
        return None

    return ParsedSelection(
        market_type="game_total_ot",
        player_name=None,
        participant_id=None,
        threshold=threshold,
        side=side,
        odd_value=odd_value,
    )


def _extract_handicap_side(value: object) -> str | None:
    """Map Xtreme handicap outcome ``id`` to our ``over`` / ``under`` sides.

    Each handicap selection in the GetEventMarkets payload has ``id`` of
    ``"1"`` (team-1 covers ⇒ home covers ⇒ stored as ``over_odds``) or
    ``"2"`` (team-2 covers ⇒ away covers ⇒ stored as ``under_odds``).
    """
    normalized = str(value or "").strip()
    if normalized == "1":
        return "over"
    if normalized == "2":
        return "under"
    return None


def _parse_game_handicap_ot_bet(bet: dict) -> ParsedSelection | None:
    """Parse a single handicap (incl. OT) selection.

    Storage convention (matches Phase 1): ``threshold`` is the home team's
    expected margin (positive ⇒ home favoured).  Xtreme's ``bl`` is the
    team-1 = home signed Asian-handicap line — when it's negative the home
    team is favoured (must give back points), so the threshold is the
    negation.

    Unlike totals, the line is signed and may be negative or zero, so we
    parse it through ``_parse_float`` rather than ``_parse_threshold``
    (which rejects non-positive values for the totals' over/under
    semantics).
    """
    line = _parse_float(bet.get("bl"))
    odd_value = _parse_odd_value(bet.get("od"))
    side = _extract_handicap_side(bet.get("id"))

    if line is None or odd_value is None or side is None:
        return None

    return ParsedSelection(
        market_type="home_handicap_ot",
        player_name=None,
        participant_id=None,
        threshold=-line,
        side=side,
        odd_value=odd_value,
    )


def _parse_event_markets(
    item: object,
    *,
    context: FixtureContext,
    player_points_market_ids: set[str],
    game_total_ot_market_ids: set[str],
    game_handicap_ot_market_ids: set[str] | None = None,
) -> list[RawOddsData]:
    if not isinstance(item, dict):
        return []

    if game_handicap_ot_market_ids is None:
        game_handicap_ot_market_ids = set()

    markets = item.get("m")
    if not isinstance(markets, list):
        return []

    grouped: dict[tuple[str, str | None, float, str | None], dict[str, float | None]] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_id = _clean_text(market.get("id"))
        if market_id is None:
            continue
        bets = market.get("b")
        if not isinstance(bets, list):
            continue

        for bet in bets:
            if not isinstance(bet, dict):
                continue
            parsed: ParsedSelection | None = None
            if market_id in player_points_market_ids:
                parsed = _parse_player_points_bet(bet)
            elif market_id in game_total_ot_market_ids:
                parsed = _parse_game_total_ot_bet(bet)
            elif market_id in game_handicap_ot_market_ids:
                parsed = _parse_game_handicap_ot_bet(bet)
            if parsed is None:
                continue

            key = (
                parsed.market_type,
                parsed.player_name,
                parsed.threshold,
                parsed.participant_id,
            )
            odds = grouped.setdefault(key, {"over": None, "under": None})
            odds[parsed.side] = parsed.odd_value

    buckets: dict[tuple[str, str | None, float], list[tuple[str | None, dict[str, float | None]]]] = {}
    for (market_type, player_name, threshold, participant_id), odds in grouped.items():
        buckets.setdefault((market_type, player_name, threshold), []).append(
            (participant_id, odds)
        )

    results: list[RawOddsData] = []
    for (market_type, player_name, threshold), entries in sorted(
        buckets.items(),
        key=lambda item: (item[0][0], item[0][1] or "", item[0][2]),
    ):
        participant_ids = {participant_id for participant_id, _ in entries}
        if len(participant_ids) > 1:
            logger.warning(
                "VolcanoBet: skipping ambiguous player label %s at %s for %s vs %s",
                player_name,
                threshold,
                context.home_team,
                context.away_team,
            )
            continue

        odds = entries[0][1]
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


def _chunked(values: list[FixtureContext], size: int) -> list[list[FixtureContext]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


class VolcanoBetScraper(BaseScraper):
    """VolcanoBet basketball scraper built from the public Xtreme offer API."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)
        self._offer_base_cache: OfferBaseLookup | None = None

    def get_bookmaker_id(self) -> str:
        return _BOOKMAKER_ID

    def get_bookmaker_name(self) -> str:
        return "VolcanoBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _get_offer_base_lookup(self) -> OfferBaseLookup:
        if self._offer_base_cache is not None:
            return self._offer_base_cache

        data = await self._http.get_json(
            _OFFER_BASE_URL,
            params={
                "dpId": _DATA_PROVIDER_ID,
                "lang": _LANG,
                "clientType": _CLIENT_TYPE,
            },
            headers=_DEFAULT_HEADERS,
        )
        self._offer_base_cache = _build_offer_base_lookup(data)
        return self._offer_base_cache

    async def _fetch_fixtures(self) -> object:
        return await self._http.get_json(
            _FIXTURES_URL,
            params={
                "dp": _DATA_PROVIDER_ID,
                "lang": _LANG,
                "clientType": _CLIENT_TYPE,
            },
            headers=_DEFAULT_HEADERS,
        )

    async def _fetch_event_markets_batch(
        self,
        event_ids: list[str],
        market_ids: tuple[str, ...],
        *,
        semaphore: asyncio.Semaphore,
    ) -> list[dict]:
        if not event_ids or not market_ids:
            return []

        params: list[tuple[str, str]] = [
            ("clientType", _CLIENT_TYPE),
            ("dpId", _DATA_PROVIDER_ID),
            ("lang", _LANG),
        ]
        params.extend(("eventIds", event_id) for event_id in event_ids)
        params.extend(("marketIds", market_id) for market_id in market_ids)
        url = f"{_EVENT_MARKETS_URL}?{urlencode(params)}"

        async with semaphore:
            data = await self._http.get_json(url, headers=_DEFAULT_HEADERS)

        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        lookup = await self._get_offer_base_lookup()
        fixtures_data = await self._fetch_fixtures()
        fixture_contexts = _extract_fixture_contexts(fixtures_data, lookup=lookup)
        if not fixture_contexts:
            logger.info("VolcanoBet: no basketball fixtures within the current lookahead window")
            return []

        market_ids = tuple(
            dict.fromkeys(
                [
                    *lookup.player_points_market_ids,
                    *lookup.game_total_ot_market_ids,
                    *lookup.game_handicap_ot_market_ids,
                ]
            )
        )
        if not market_ids:
            logger.info("VolcanoBet: no supported basketball market ids discovered")
            return []

        semaphore = asyncio.Semaphore(_EVENT_BATCH_CONCURRENCY)
        batches = _chunked(fixture_contexts, _EVENT_BATCH_SIZE)
        batch_results = await asyncio.gather(
            *(
                self._fetch_event_markets_batch(
                    [context.event_id for context in batch],
                    market_ids,
                    semaphore=semaphore,
                )
                for batch in batches
            )
        )

        context_by_event_id = {
            context.event_id: context
            for context in fixture_contexts
        }
        player_point_market_ids = set(lookup.player_points_market_ids)
        game_total_ot_market_ids = set(lookup.game_total_ot_market_ids)
        game_handicap_ot_market_ids = set(lookup.game_handicap_ot_market_ids)

        results: list[RawOddsData] = []
        player_point_events: set[str] = set()
        game_total_events: set[str] = set()
        game_handicap_events: set[str] = set()
        for batch in batch_results:
            for item in batch:
                event_id = _clean_text(item.get("e"))
                if event_id is None:
                    continue
                context = context_by_event_id.get(event_id)
                if context is None:
                    continue

                parsed = _parse_event_markets(
                    item,
                    context=context,
                    player_points_market_ids=player_point_market_ids,
                    game_total_ot_market_ids=game_total_ot_market_ids,
                    game_handicap_ot_market_ids=game_handicap_ot_market_ids,
                )
                if not parsed:
                    continue

                results.extend(parsed)
                if any(row.market_type == "player_points" for row in parsed):
                    player_point_events.add(event_id)
                if any(row.market_type == "game_total_ot" for row in parsed):
                    game_total_events.add(event_id)
                if any(row.market_type == "home_handicap_ot" for row in parsed):
                    game_handicap_events.add(event_id)

        logger.info(
            "VolcanoBet scraped %d basketball odds (%d player points from %d events, "
            "%d OT totals from %d events, %d OT handicaps from %d events across %d fixtures)",
            len(results),
            sum(row.market_type == "player_points" for row in results),
            len(player_point_events),
            sum(row.market_type == "game_total_ot" for row in results),
            len(game_total_events),
            sum(row.market_type == "home_handicap_ot" for row in results),
            len(game_handicap_events),
            len(fixture_contexts),
        )
        return results
