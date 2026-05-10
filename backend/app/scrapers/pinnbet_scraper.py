from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Literal

from .base import BaseScraper
from .http_client import HttpClient
from ..config import settings
from ..services.scrape_window import (
    current_utc_time,
    format_utc_naive_seconds,
    lookahead_cutoff,
)
from ..models.schemas import RawOddsData, RawOutcomeOffer
from ..services.normalizer import normalize_team_name
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_BASE_LIST_URL = (
    "https://sportweb.pinnbet.rs/SportBookCacheWeb/api/offer/getWebEventsSelections"
)
_BASE_DETAIL_URL = (
    "https://sportweb.pinnbet.rs/SportBookCacheWeb/api/offer/betsAndGroups"
)

_PLAYER_SPORT_ID = 3
_GAME_TOTAL_SPORT_ID = 2
_FOOTBALL_SPORT_ID = 1
_TENNIS_SPORT_ID = 4
_OFFICE_ID = "6"
_LANGUAGE = "sr-Latn"
_PLAYER_PAGE_ID = 3
_GAME_TOTAL_PAGE_ID = 35
_FOOTBALL_PAGE_ID = 3
_TENNIS_PAGE_ID = 3

_MAPPING_TYPE_PLAYER = 5
_EVENT_MAPPING_TYPES = [1, 2, 3, 4, 5]
_BET_TYPE_GAME_TOTAL_OT = 167
_GAME_TOTAL_OT_BET_NAME = "ukupno poena (+ot)"
_BET_TYPE_HANDICAP_OT = 166
_HANDICAP_OT_BET_NAME = "hendikep (+ot)"

# Football betTypeId constants — anchored on numeric IDs so localized name
# drift on the bookmaker side cannot break classification.
_BET_FOOTBALL_RESULT = 1  # "Konacan ishod"
_BET_FOOTBALL_TOTAL_GOALS = 2  # "Ukupno golova"
_BET_FOOTBALL_DOUBLE_CHANCE = 3  # "Dupla sansa"
_BET_TENNIS_MATCH_WINNER = 257  # "Pobednik"
_TENNIS_PAGE_URL = "https://www.pinnbet.rs/sportsko-kladjenje/tenis"

# Only the 2.5 line is in scope for football_total_goals (matches the
# canonical line used by every other football outcome scraper).
_FOOTBALL_TARGET_TOTAL_LINE = 2.5

# Outcome name → (outcome_code, raw_label).  Keys are matched after a
# strip+upper pass so whitespace/case drift on the bookmaker side does
# not break classification.
_FOOTBALL_RESULT_OUTCOMES: dict[str, tuple[str, str]] = {
    "1": ("home", "1"),
    "X": ("draw", "X"),
    "2": ("away", "2"),
}
_FOOTBALL_DOUBLE_CHANCE_OUTCOMES: dict[str, tuple[str, str]] = {
    "1X": ("home_or_draw", "1X"),
    "12": ("home_or_away", "12"),
    "X2": ("draw_or_away", "X2"),
}
_TENNIS_MATCH_WINNER_OUTCOMES: dict[str, tuple[str, str]] = {
    "1": ("home", "1"),
    "2": ("away", "2"),
}
# Total-goals side classification keyed off accent/case-insensitive name.
# Maps to (outcome_code, canonical raw_label) — the canonical raw_label
# matches the convention used by the other football outcome scrapers
# (BetOle/AdmiralBet/etc.) so the unified pipeline groups them.
_FOOTBALL_TOTAL_SIDES: dict[str, tuple[str, str]] = {
    "manje": ("under", "0-2"),
    "vise": ("over", "3+"),
}

# Per-bookmaker concurrency caps for the per-event football detail
# fetches (full mode only).  The HttpClient enforces a global rate
# limit per bookmaker, so any concurrency above
# ``ceil(rate_limit_per_second)`` cannot speed things up — it just
# helps mask network latency between rate-limited slots.  When the
# rate limit is disabled (0), cap at 10 to stay polite.
_MIN_DETAIL_CONCURRENCY = 2
_UNLIMITED_DETAIL_CONCURRENCY = 10

_BET_TYPE_MARKETS: dict[int, str] = {
    1200: "player_points",
    1201: "player_assists",
    1202: "player_rebounds",
    1203: "player_points_assists",
    1204: "player_points_rebounds",
    1205: "player_rebounds_assists",
    1206: "player_points_rebounds_assists",
    1191: "player_steals",
    1194: "player_blocks",
    1195: "player_3points",
}

_BET_TYPE_NAME_MARKETS: dict[str, str] = {
    "ukupno poena": "player_points",
    "ukupno asistencija": "player_assists",
    "ukupno skokova": "player_rebounds",
    "ukupno poena+asistencija": "player_points_assists",
    "ukupno poena+skokova": "player_points_rebounds",
    "ukupno asistencija+skokova": "player_rebounds_assists",
    "ukupno poena+asistencija+skokova": "player_points_rebounds_assists",
    "ukupno ukradenih lopti": "player_steals",
    "ukupno blokada": "player_blocks",
    "ukupno postignutih trojki": "player_3points",
}

_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": (
        "application/utf8+json, application/json;q=0.9, "
        "text/plain;q=0.8, */*;q=0.7"
    ),
    "Content-Type": "application/json",
    "Language": _LANGUAGE,
    "OfficeId": _OFFICE_ID,
    "Origin": "https://www.pinnbet.rs",
    "Referer": "https://www.pinnbet.rs/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}

_DEFAULT_PARAMS: dict[str, str] = {}

_COMPETITION_NAME_LEAGUE_MAP: dict[str, str] = {
    "nba": "nba",
    "usa nba": "nba",
    "nba plej of": "nba",
    "nba playoff": "nba",
    "euroleague": "euroleague",
    "evroliga": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
}
_COMPETITION_ID_LEAGUE_MAP: dict[int, str] = {
    3221: "nba",
    13981: "nba",
    22317: "aba_liga",
}

def _build_list_url(
    sport_id: int,
    page_id: int,
    region_id: int | None = None,
    competition_id: int | None = None,
) -> str:
    """Build list URL with repeated eventMappingTypes pre-encoded."""
    now_dt = current_utc_time()
    now = format_utc_naive_seconds(now_dt)
    date_to = format_utc_naive_seconds(lookahead_cutoff(now_dt))
    mapping_qs = "&".join(
        f"eventMappingTypes={t}" for t in _EVENT_MAPPING_TYPES
    )
    url = (
        f"{_BASE_LIST_URL}"
        f"?pageId={page_id}&sportId={sport_id}"
    )
    if region_id is not None:
        url += f"&regionId={region_id}"
    if competition_id is not None:
        url += f"&competitionId={competition_id}"
    url += (
        f"&isLive=false"
        f"&dateFrom={now}&dateTo={date_to}"
        f"&{mapping_qs}"
    )
    return url


def _normalize_start_time(raw: str | None) -> str | None:
    """Convert a PinnBet datetime string to canonical ``+00:00`` format.

    PinnBet returns ``2026-04-11T16:00:00`` (no timezone).  Other scrapers
    produce ``2026-04-11T16:00:00+00:00``.  Treat naive values as UTC to
    match the normalizer's string-based comparison.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return raw


def _normalize_competition_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(event: dict, fallback_league_id: str = "basketball") -> str:
    competition_name = event.get("competitionName")
    key = _normalize_competition_key(
        competition_name if isinstance(competition_name, str) else None
    )
    if key in _COMPETITION_NAME_LEAGUE_MAP:
        return _COMPETITION_NAME_LEAGUE_MAP[key]

    competition_id = event.get("competitionId")
    try:
        competition_id_int = int(competition_id)
    except (TypeError, ValueError):
        competition_id_int = None

    if competition_id_int in _COMPETITION_ID_LEAGUE_MAP:
        return _COMPETITION_ID_LEAGUE_MAP[competition_id_int]
    if key:
        return key
    return fallback_league_id


def _parse_event_name(name: str) -> tuple[str, str | None]:
    """Split ``'Player Name - Team Name'`` into *(player, team)*.

    Returns ``(name, None)`` when no ``' - '`` separator is found.
    """
    if " - " in name:
        player, team = name.split(" - ", 1)
        return player.strip(), team.strip()
    return name.strip(), None


def _normalize_bet_type_key(raw: str | None) -> str:
    if not raw:
        return ""
    lowered = " ".join(raw.strip().lower().split())
    return lowered.replace(" + ", "+").replace("+ ", "+").replace(" +", "+")


def _resolve_market_type(bet: dict) -> str | None:
    bet_type_id = bet.get("betTypeId")
    if isinstance(bet_type_id, int) and bet_type_id in _BET_TYPE_MARKETS:
        return _BET_TYPE_MARKETS[bet_type_id]
    if isinstance(bet_type_id, str):
        try:
            bet_type_id_int = int(bet_type_id)
        except ValueError:
            bet_type_id_int = None
        if bet_type_id_int is not None and bet_type_id_int in _BET_TYPE_MARKETS:
            return _BET_TYPE_MARKETS[bet_type_id_int]
    return _BET_TYPE_NAME_MARKETS.get(_normalize_bet_type_key(bet.get("betTypeName")))


def _resolve_matchup_from_short_name(
    short_name: str | None,
    event_team: str | None,
    league_id: str,
) -> tuple[str, str] | None:
    if not short_name or not event_team:
        return None

    normalized_event_team = normalize_team_name(event_team, league_id)
    best_match: tuple[str, str] | None = None
    best_match_length = -1
    for idx, char in enumerate(short_name):
        if char != "-":
            continue
        home_team = short_name[:idx].strip(" -")
        away_team = short_name[idx + 1 :].strip(" -")
        if not home_team or not away_team:
            continue
        matched_side_length = -1
        if normalize_team_name(home_team, league_id) == normalized_event_team:
            matched_side_length = len(home_team)
        if normalize_team_name(away_team, league_id) == normalized_event_team:
            matched_side_length = max(matched_side_length, len(away_team))
        if matched_side_length > best_match_length:
            best_match = (home_team, away_team)
            best_match_length = matched_side_length
    return best_match


def _matchup_contains_player(matchup: tuple[str, str] | None, player_name: str) -> bool:
    if matchup is None:
        return False
    player_key = normalize_identity_text(player_name)
    if not player_key:
        return False
    return any(normalize_identity_text(team_name) == player_key for team_name in matchup)


def _get_player_event_ids(events: list[dict]) -> list[dict]:
    """Return full event dicts whose ``mappingTypeId`` equals 5 (player specials)."""
    return [e for e in events if e.get("mappingTypeId") == _MAPPING_TYPE_PLAYER]


def _parse_game_total_ot_event(
    event: dict,
    league_id: str | None = None,
) -> list[RawOddsData]:
    """Parse OT-inclusive match totals from the prematch list feed."""
    results: list[RawOddsData] = []
    home_team, away_team = _parse_event_name(event.get("name", ""))
    if not home_team or not away_team:
        return results

    start_time = _normalize_start_time(event.get("dateTime"))
    effective_league_id = league_id or _extract_league_id(event)

    for bet in event.get("bets", []):
        bet_type_key = _normalize_bet_type_key(bet.get("betTypeName"))
        if (
            bet.get("betTypeId") != _BET_TYPE_GAME_TOTAL_OT
            and bet_type_key != _GAME_TOTAL_OT_BET_NAME
        ):
            continue

        if not bet.get("isPlayable"):
            continue

        sbv = bet.get("sBV")
        if sbv is None:
            continue
        try:
            threshold = float(sbv)
        except (ValueError, TypeError):
            continue

        over_odds: float | None = None
        under_odds: float | None = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            outcome_name = (outcome.get("name") or "").lower()
            if outcome_name == "više":
                over_odds = outcome.get("odd")
            elif outcome_name == "manje":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="pinnbet",
                league_id=effective_league_id,
                sport="basketball",
                home_team=home_team,
                away_team=away_team,
                market_type="game_total_ot",
                player_name=None,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=start_time,
            )
        )

    return results


def _parse_handicap_ot_event(
    event: dict,
    league_id: str | None = None,
) -> list[RawOddsData]:
    """Parse OT-inclusive Asian handicap rows from the prematch list feed.

    PinnBet expresses the line via a signed ``sBV`` interpreted as team1's
    Asian handicap (negative when team1 is favored). Outcome name ``"1"``
    pays when team1 covers; ``"2"`` pays when team2 covers. The event's
    ``name`` is ``"home - away"`` (team1=home), so we canonicalise to a
    home-perspective threshold ``= -sBV`` so the analyzer treats handicap
    exactly like total-points (over=home covers, under=away covers).
    """
    results: list[RawOddsData] = []
    home_team, away_team = _parse_event_name(event.get("name", ""))
    if not home_team or not away_team:
        return results

    start_time = _normalize_start_time(event.get("dateTime"))
    effective_league_id = league_id or _extract_league_id(event)

    for bet in event.get("bets", []):
        bet_type_key = _normalize_bet_type_key(bet.get("betTypeName"))
        if (
            bet.get("betTypeId") != _BET_TYPE_HANDICAP_OT
            and bet_type_key != _HANDICAP_OT_BET_NAME
        ):
            continue

        if not bet.get("isPlayable"):
            continue

        sbv = bet.get("sBV")
        if sbv is None:
            continue
        try:
            sbv_value = float(sbv)
        except (ValueError, TypeError):
            continue

        threshold = -sbv_value

        over_odds: float | None = None
        under_odds: float | None = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            name = (outcome.get("name") or "").strip()
            if name == "1":
                over_odds = outcome.get("odd")
            elif name == "2":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="pinnbet",
                league_id=effective_league_id,
                sport="basketball",
                home_team=home_team,
                away_team=away_team,
                market_type="home_handicap_ot",
                player_name=None,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=start_time,
            )
        )

    return results


def _parse_event_detail(
    event: dict,
    bets_data: dict,
    league_id: str | None = None,
) -> list[RawOddsData]:
    """Parse a player-prop event and its embedded or detail-shaped bets."""
    results: list[RawOddsData] = []

    name = event.get("name", "")
    player_name, team = _parse_event_name(name)
    if not player_name:
        return results

    start_time = _normalize_start_time(event.get("dateTime"))
    effective_league_id = league_id or _extract_league_id(event)
    resolved_matchup = _resolve_matchup_from_short_name(
        event.get("shortName"),
        team,
        effective_league_id,
    )
    if _matchup_contains_player(resolved_matchup, player_name):
        resolved_matchup = None
    home_team = resolved_matchup[0] if resolved_matchup else (team or "")
    away_team = resolved_matchup[1] if resolved_matchup else player_name

    for bet in bets_data.get("bets", []):
        market_type = _resolve_market_type(bet)
        if market_type is None:
            continue

        sbv = bet.get("sBV")
        if sbv is None:
            continue
        try:
            threshold = float(sbv)
        except (ValueError, TypeError):
            continue

        over_odds: float | None = None
        under_odds: float | None = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            outcome_name = (outcome.get("name") or "").lower()
            if outcome_name == "više":
                over_odds = outcome.get("odd")
            elif outcome_name == "manje":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="pinnbet",
                league_id=effective_league_id,
                sport="basketball",
                home_team=home_team,
                away_team=away_team,
                market_type=market_type,
                player_name=player_name,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=start_time,
            )
        )

    return results


def _parse_total_line(value: object) -> float | None:
    """Parse a PinnBet sBV/line value, tolerating ``"2.50"`` / ``" 2.5 "`` / ``2.5``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _resolve_total_line(bet: dict, outcome: dict) -> float | None:
    """Resolve the totals line for an Ukupno golova bet.

    Prefers the bet-level ``sBV`` (carried at the market level), but
    falls back to the outcome-level ``sBV``. Returns ``None`` when
    neither parses, or when the two are present and disagree (defense
    in depth — we'd rather drop a confusing offer than mis-classify
    it as 2.5).
    """
    bet_line = _parse_total_line(bet.get("sBV"))
    outcome_line = _parse_total_line(outcome.get("sBV"))
    if bet_line is not None and outcome_line is not None:
        if abs(bet_line - outcome_line) > 1e-9:
            return None
        return bet_line
    return bet_line if bet_line is not None else outcome_line


def _coerce_positive_odds(value: object) -> float | None:
    if value is None:
        return None
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    if odds <= 0:
        return None
    return odds


_FOOTBALL_BET_TYPES_FROM_LIST: frozenset[int] = frozenset({
    _BET_FOOTBALL_RESULT,
    _BET_FOOTBALL_TOTAL_GOALS,
})


def _emit_football_offers_from_bets(
    *,
    home_team: str,
    away_team: str,
    league_id: str,
    start_time: str | None,
    bets: list[dict],
    bet_type_filter: frozenset[int],
) -> list[RawOutcomeOffer]:
    """Emit football outcome offers from a list/detail bets array.

    ``bet_type_filter`` constrains which bet types we consider, which
    lets the same parser walk both list bets (result + totals only) and
    detail bets (double chance only) without duplicating logic.
    """
    results: list[RawOutcomeOffer] = []
    for bet in bets:
        bet_type_id = bet.get("betTypeId")
        if not isinstance(bet_type_id, int) or bet_type_id not in bet_type_filter:
            continue
        if not bet.get("isPlayable"):
            continue

        if bet_type_id == _BET_FOOTBALL_RESULT:
            outcome_lookup = _FOOTBALL_RESULT_OUTCOMES
            market_type = "football_result"
            line: float | None = None
        elif bet_type_id == _BET_FOOTBALL_DOUBLE_CHANCE:
            outcome_lookup = _FOOTBALL_DOUBLE_CHANCE_OUTCOMES
            market_type = "football_double_chance"
            line = None
        elif bet_type_id == _BET_FOOTBALL_TOTAL_GOALS:
            # Cheap pre-filter: if the bet-level sBV resolves and is not
            # the target line, skip the whole bet without scanning outcomes.
            bet_line = _parse_total_line(bet.get("sBV"))
            if bet_line is not None and abs(bet_line - _FOOTBALL_TARGET_TOTAL_LINE) > 1e-9:
                continue
            outcome_lookup = None
            market_type = "football_total_goals"
            line = _FOOTBALL_TARGET_TOTAL_LINE
        else:
            continue

        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            odds = _coerce_positive_odds(outcome.get("odd"))
            if odds is None:
                continue

            raw_name = (outcome.get("name") or "").strip()
            if bet_type_id == _BET_FOOTBALL_TOTAL_GOALS:
                resolved_line = _resolve_total_line(bet, outcome)
                if resolved_line is None:
                    continue
                if abs(resolved_line - _FOOTBALL_TARGET_TOTAL_LINE) > 1e-9:
                    continue
                normalized_side = normalize_identity_text(raw_name)
                mapping = _FOOTBALL_TOTAL_SIDES.get(normalized_side)
                if mapping is None:
                    continue
                outcome_code, raw_label = mapping
            else:
                assert outcome_lookup is not None  # narrow for type checkers
                key = raw_name.upper()
                mapping = outcome_lookup.get(key)
                if mapping is None:
                    continue
                outcome_code, raw_label = mapping

            results.append(
                RawOutcomeOffer(
                    bookmaker_id="pinnbet",
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


def _football_event_identity(event: dict) -> tuple[str, str, str | None]:
    """Resolve (home, away, start_time) for a PinnBet football list event.

    Returns ``("", "", None)`` when the event name is unparseable.
    """
    home_team, away_team = _parse_event_name(event.get("name", ""))
    if not home_team or not away_team:
        return "", "", None
    start_time = _normalize_start_time(event.get("dateTime"))
    return home_team, away_team, start_time


def _parse_football_outcome_event(event: dict) -> list[RawOutcomeOffer]:
    """Parse a PinnBet football list event into result + 2.5 totals offers.

    Double chance is intentionally NOT emitted here — the list endpoint
    does not expose betTypeId=3 for football.  The detail endpoint is
    the only source for double chance and is only fetched in ``full``
    mode.
    """
    home_team, away_team, start_time = _football_event_identity(event)
    if not home_team or not away_team:
        return []
    league_id = _extract_league_id(event, fallback_league_id="football")
    return _emit_football_offers_from_bets(
        home_team=home_team,
        away_team=away_team,
        league_id=league_id,
        start_time=start_time,
        bets=event.get("bets", []),
        bet_type_filter=_FOOTBALL_BET_TYPES_FROM_LIST,
    )


def _parse_football_double_chance_detail(
    list_event: dict, detail_payload: dict
) -> list[RawOutcomeOffer]:
    """Emit football_double_chance offers from a per-event detail payload.

    Identity (home/away/start_time/league_id) is taken UNCONDITIONALLY
    from the list event so list-derived and detail-derived offers
    share the same normalized event key.  The detail payload only
    contributes its ``bets`` array.
    """
    home_team, away_team, start_time = _football_event_identity(list_event)
    if not home_team or not away_team:
        return []
    league_id = _extract_league_id(list_event, fallback_league_id="football")
    bets = detail_payload.get("bets") or []
    if not isinstance(bets, list):
        return []
    return _emit_football_offers_from_bets(
        home_team=home_team,
        away_team=away_team,
        league_id=league_id,
        start_time=start_time,
        bets=bets,
        bet_type_filter=frozenset({_BET_FOOTBALL_DOUBLE_CHANCE}),
    )


def _is_tennis_doubles_event(event: dict) -> bool:
    home_team, away_team = _parse_event_name(event.get("name", ""))
    if not home_team or not away_team:
        return False
    return "/" in home_team or "/" in away_team


def _parse_tennis_outcome_event(event: dict) -> list[RawOutcomeOffer]:
    if event.get("isLive") is True:
        return []
    if event.get("isPlayable") is False or event.get("isInOffer") is False:
        return []

    home_team, away_team = _parse_event_name(event.get("name", ""))
    if not home_team or not away_team:
        return []
    if _is_tennis_doubles_event(event):
        return []

    league_id = _extract_league_id(event, fallback_league_id="tennis")
    start_time = _normalize_start_time(event.get("dateTime"))
    results: list[RawOutcomeOffer] = []

    for bet in event.get("bets", []):
        if bet.get("betTypeId") != _BET_TENNIS_MATCH_WINNER:
            continue
        if bet.get("isPlayable") is False or bet.get("isInOffer") is False:
            continue

        for outcome in bet.get("betOutcomes", []):
            if outcome.get("isPlayable") is False or outcome.get("isInOffer") is False:
                continue
            odds = _coerce_positive_odds(outcome.get("odd"))
            if odds is None:
                continue

            raw_name = (outcome.get("name") or "").strip()
            mapping = _TENNIS_MATCH_WINNER_OUTCOMES.get(raw_name)
            if mapping is None:
                continue
            outcome_code, raw_label = mapping
            results.append(
                RawOutcomeOffer(
                    bookmaker_id="pinnbet",
                    league_id=league_id,
                    sport="tennis",
                    home_team=home_team,
                    away_team=away_team,
                    market_type="tennis_match_winner",
                    outcome_code=outcome_code,
                    odds=odds,
                    line=None,
                    raw_label=raw_label,
                    start_time=start_time,
                    source_url=_TENNIS_PAGE_URL,
                )
            )

    return results


def _football_detail_identity(event: dict) -> tuple[int, int, int, int] | None:
    """Resolve the (sportId, regionId, competitionId, eventId) tuple needed
    to build the detail URL.  Returns ``None`` when any field is missing.
    """
    try:
        return (
            int(event["sportId"]),
            int(event["regionId"]),
            int(event["competitionId"]),
            int(event["id"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _football_event_completeness_score(event: dict) -> int:
    """Score a list event by how usable it is for downstream parsing.

    Higher is better.  We prefer rows that:
      * have a parseable detail-id tuple (detail-eligible)
      * have a ``bets`` array (so list-derived offers are emitted)
      * carry a competition name (so league_id is non-degenerate)

    This drives "best row wins" dedupe when the list happens to repeat
    the same eventId across mapping types.
    """
    score = 0
    if _football_detail_identity(event) is not None:
        score += 4
    bets = event.get("bets")
    if isinstance(bets, list) and bets:
        score += 2
    if event.get("competitionName"):
        score += 1
    return score


def _dedupe_football_events(events: list[dict]) -> dict[int, dict]:
    """Collapse duplicate list rows by event id, keeping the most usable.

    ``eventMappingTypes=1..5`` can return the same eventId multiple
    times; without dedupe we'd silently emit duplicate list-derived
    offers and (in full mode) issue redundant detail fetches.
    """
    by_id: dict[int, dict] = {}
    for event in events:
        try:
            event_id = int(event["id"])
        except (KeyError, TypeError, ValueError):
            continue
        existing = by_id.get(event_id)
        if existing is None or (
            _football_event_completeness_score(event)
            > _football_event_completeness_score(existing)
        ):
            by_id[event_id] = event
    return by_id


def _dedupe_tennis_events(events: list[dict]) -> dict[int, dict]:
    by_id: dict[int, dict] = {}
    for event in events:
        try:
            event_id = int(event["id"])
        except (KeyError, TypeError, ValueError):
            continue
        existing = by_id.get(event_id)
        if existing is None or len(event.get("bets") or []) > len(existing.get("bets") or []):
            by_id[event_id] = event
    return by_id


def _get_detail_fetch_concurrency(http_client: HttpClient, match_count: int) -> int:
    if match_count <= 0:
        return 0
    if http_client.rate_limit_per_second <= 0:
        return min(match_count, _UNLIMITED_DETAIL_CONCURRENCY)
    return min(
        match_count,
        max(_MIN_DETAIL_CONCURRENCY, math.ceil(http_client.rate_limit_per_second)),
    )


class PinnBetScraper(BaseScraper):
    """Scraper for PinnBet basketball player props and OT-inclusive game totals.

    Also emits football outcome offers (result, totals @ 2.5, and — in
    ``full`` detail mode only — double chance via per-event detail
    fetch).  Default ``partial`` mode skips the per-event fetch and
    therefore cannot emit double chance, which the user explicitly
    accepted as a trade-off for cycle speed.
    """

    def __init__(
        self,
        http_client: HttpClient | None = None,
        *,
        detail_mode: Literal["partial", "full"] | None = None,
    ) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)
        self._detail_mode = detail_mode or settings.pinnbet_detail_mode

    def get_bookmaker_id(self) -> str:
        return "pinnbet"

    def get_bookmaker_name(self) -> str:
        return "PinnBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football", "tennis"]

    async def _fetch_game_total_events(self) -> list[dict]:
        url = _build_list_url(
            _GAME_TOTAL_SPORT_ID,
            page_id=_GAME_TOTAL_PAGE_ID,
        )

        try:
            data = await self._http.get_json(url, headers=_DEFAULT_HEADERS)
        except Exception:
            logger.warning("PinnBet: failed to fetch basketball prematch events")
            return []

        if not isinstance(data, list):  # type: ignore[arg-type]
            logger.warning(
                "PinnBet: unexpected response type %s for basketball prematch events",
                type(data).__name__,
            )
            return []

        return data

    async def _fetch_player_events(self) -> list[dict]:
        url = _build_list_url(
            _PLAYER_SPORT_ID,
            page_id=_PLAYER_PAGE_ID,
        )

        try:
            data = await self._http.get_json(url, headers=_DEFAULT_HEADERS)
        except Exception:
            logger.warning("PinnBet: failed to fetch basketball player events")
            return []

        if not isinstance(data, list):  # type: ignore[arg-type]
            logger.warning(
                "PinnBet: unexpected response type %s for basketball player events",
                type(data).__name__,
            )
            return []

        player_events = _get_player_event_ids(data)
        if not player_events:
            logger.warning("PinnBet: no player events in basketball player feed")

        return player_events

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        player_results: list[RawOddsData] = []
        total_results: list[RawOddsData] = []
        player_events = await self._fetch_player_events()
        for event in player_events:
            player_results.extend(
                _parse_event_detail(
                    event,
                    event,
                    league_id=_extract_league_id(event),
                )
            )

        basketball_events = await self._fetch_game_total_events()
        for event in basketball_events:
            total_results.extend(_parse_game_total_ot_event(event))
            total_results.extend(_parse_handicap_ot_event(event))

        all_results = [*player_results, *total_results]

        logger.info(
            (
                "PinnBet scraped %d player odds from %d player feed events "
                "and %d OT total/handicap odds from %d basketball prematch events"
            ),
            len(player_results),
            len(player_events),
            len(total_results),
            len(basketball_events),
        )
        return all_results

    async def _fetch_football_events(self) -> list[dict]:
        url = _build_list_url(
            _FOOTBALL_SPORT_ID,
            page_id=_FOOTBALL_PAGE_ID,
        )

        try:
            data = await self._http.get_json(url, headers=_DEFAULT_HEADERS)
        except Exception:
            logger.warning("PinnBet: failed to fetch football prematch events")
            return []

        if not isinstance(data, list):  # type: ignore[arg-type]
            logger.warning(
                "PinnBet: unexpected response type %s for football prematch events",
                type(data).__name__,
            )
            return []

        return [event for event in data if isinstance(event, dict)]

    async def _fetch_tennis_events(self) -> list[dict]:
        url = _build_list_url(
            _TENNIS_SPORT_ID,
            page_id=_TENNIS_PAGE_ID,
        )

        try:
            data = await self._http.get_json(url, headers=_DEFAULT_HEADERS)
        except Exception:
            logger.warning("PinnBet: failed to fetch tennis prematch events")
            return []

        if not isinstance(data, list):  # type: ignore[arg-type]
            logger.warning(
                "PinnBet: unexpected response type %s for tennis prematch events",
                type(data).__name__,
            )
            return []

        return [event for event in data if isinstance(event, dict)]

    async def _fetch_football_detail(
        self,
        identity: tuple[int, int, int, int],
        semaphore: asyncio.Semaphore,
    ) -> dict | None:
        sport_id, region_id, competition_id, event_id = identity
        url = (
            f"{_BASE_DETAIL_URL}/{sport_id}/{region_id}/{competition_id}/{event_id}"
        )
        async with semaphore:
            try:
                detail = await self._http.get_json(url, headers=_DEFAULT_HEADERS)
            except Exception:
                logger.warning(
                    "PinnBet: failed to fetch football match detail %s/%s/%s/%s",
                    sport_id,
                    region_id,
                    competition_id,
                    event_id,
                    exc_info=True,
                )
                return None
        if not isinstance(detail, dict):
            return None
        return detail

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        """Scrape PinnBet football outcome offers.

        ``partial`` mode (default): fetch the football list endpoint
        once and emit football_result + football_total_goals @ 2.5.
        Double chance is NOT emitted because PinnBet's list endpoint
        does not expose betTypeId=3.

        ``full`` mode: also fan out per-event detail fetches (rate-
        limited via semaphore) and emit football_double_chance from
        the detail bets, using identity fields from the LIST event so
        list-derived and detail-derived offers share the same
        normalized event.
        """
        if sport == "tennis":
            tennis_events = await self._fetch_tennis_events()
            events_by_id = _dedupe_tennis_events(tennis_events)
            results: list[RawOutcomeOffer] = []
            for event in events_by_id.values():
                results.extend(_parse_tennis_outcome_event(event))
            logger.info(
                "PinnBet scraped %d tennis outcome offers from %d events",
                len(results),
                len(events_by_id),
            )
            return results

        if sport != "football":
            return []

        list_events = await self._fetch_football_events()
        events_by_id = _dedupe_football_events(list_events)
        if not events_by_id:
            logger.info("PinnBet: no football matches discovered")
            return []

        results: list[RawOutcomeOffer] = []
        for event in events_by_id.values():
            results.extend(_parse_football_outcome_event(event))

        detail_attempts = 0
        detail_misses = 0
        concurrency = 0
        if self._detail_mode == "full":
            detail_targets: list[tuple[int, tuple[int, int, int, int]]] = []
            skipped_for_missing_ids = 0
            for event_id, event in events_by_id.items():
                identity = _football_detail_identity(event)
                if identity is None:
                    skipped_for_missing_ids += 1
                    continue
                detail_targets.append((event_id, identity))

            if skipped_for_missing_ids:
                logger.warning(
                    "PinnBet: skipping detail fetch for %d football events with missing IDs",
                    skipped_for_missing_ids,
                )

            if detail_targets:
                concurrency = _get_detail_fetch_concurrency(
                    self._http, len(detail_targets)
                )
                semaphore = asyncio.Semaphore(max(1, concurrency))
                details = await asyncio.gather(
                    *(
                        self._fetch_football_detail(identity, semaphore)
                        for _, identity in detail_targets
                    )
                )
                detail_attempts = len(detail_targets)
                for (event_id, _), detail in zip(detail_targets, details):
                    if detail is None:
                        detail_misses += 1
                        continue
                    list_event = events_by_id[event_id]
                    results.extend(
                        _parse_football_double_chance_detail(list_event, detail)
                    )
                if detail_misses:
                    logger.warning(
                        "PinnBet: %d/%d football detail fetches returned no data; "
                        "double chance will be missing for those matches",
                        detail_misses,
                        detail_attempts,
                    )

        logger.info(
            (
                "PinnBet scraped %d football outcome offers from %d events "
                "(detail mode=%s, attempts=%d, misses=%d, concurrency=%d)"
            ),
            len(results),
            len(events_by_id),
            self._detail_mode,
            detail_attempts,
            detail_misses,
            concurrency,
        )
        return results
