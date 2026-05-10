from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..services.scrape_window import (
    current_utc_time,
    format_utc_naive_seconds,
    lookahead_cutoff,
)
from ..services.text_normalizer import normalize_identity_text
from ..models.schemas import RawOddsData, RawOutcomeOffer

logger = logging.getLogger(__name__)

_LIST_URL = "https://srboffer.admiralbet.rs/api/offer/getWebEventsSelections"

_PLAYER_DEFAULT_PARAMS = {
    "pageId": "3",
    "sportId": "123",
    "isLive": "false",
    "dateFrom": "",  # filled at scrape time
    "dateTo": "",  # filled at scrape time
    "eventMappingTypes": ["1", "2", "3", "4", "5"],
}

_GAME_TOTAL_DEFAULT_PARAMS = {
    "pageId": "3",
    "sportId": "2",
    "isLive": "false",
    "dateFrom": "",  # filled at scrape time
    "dateTo": "",  # filled at scrape time
    "eventMappingTypes": ["1", "2", "3", "4", "5"],
}

# Football outcome lane uses pageId=14 (smallest payload that still carries
# all three target markets: Konacan ishod, Dupla sansa, Ukupno golova).
_FOOTBALL_OUTCOME_PARAMS = {
    "pageId": "14",
    "sportId": "1",
    "isLive": "false",
    "dateFrom": "",  # filled at scrape time
    "dateTo": "",  # filled at scrape time
    "eventMappingTypes": ["1", "2", "3", "4", "5"],
}

_TENNIS_OUTCOME_PARAMS = {
    "pageId": "3",
    "sportId": "3",
    "isLive": "false",
    "dateFrom": "",  # filled at scrape time
    "dateTo": "",  # filled at scrape time
    "eventMappingTypes": ["1", "2", "3", "4", "5"],
}

_DEFAULT_HEADERS = {
    "accept": "application/utf8+json, application/json;q=0.9",
    "language": "sr-Latn",
    "officeid": "138",
    "origin": "https://admiralbet.rs",
    "referer": "https://admiralbet.rs/",
}

# betTypeId constants
_BET_POINTS_MILESTONES = 1683  # "Postiže poena" — milestone outcomes (5+, 10+, …)
_BET_GAME_TOTAL_OT = 213  # "Ukupno (+OT)"
_BET_HANDICAP_OT = 191  # "Hendikep (+OT)" — signed sBV is team1's Asian handicap.

# Football betTypeId constants (anchored on numeric IDs so localized name
# drift cannot break classification).
_BET_FOOTBALL_RESULT = 135  # "Konacan ishod"
_BET_FOOTBALL_DOUBLE_CHANCE = 152  # "Dupla sansa"
_BET_FOOTBALL_TOTAL_GOALS = 137  # "Ukupno golova"
_BET_TENNIS_MATCH_WINNER = 1723  # "Pobednik"
_TENNIS_PAGE_URL = "https://admiralbet.rs/sport-prematch?sport=Tenis"

# Only the 2.5 line is in scope for football_total_goals.
_FOOTBALL_TARGET_TOTAL_LINE = 2.5

# Outcome maps for football. Keys are matched after stripping & uppercasing
# the feed-supplied outcome.name to be robust against whitespace/case drift.
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
# (SoccerBet/MerkurXTip/etc.) so the unified pipeline groups them.
_FOOTBALL_TOTAL_SIDES: dict[str, tuple[str, str]] = {
    "manje": ("under", "0-2"),
    "vise": ("over", "3+"),
}

# Mapping of betTypeId → canonical market_type for player over/under props.
_BET_OVER_UNDER_MAP: dict[int, str] = {
    1598: "player_points",                  # Ukupno poena
    1599: "player_assists",                 # Ukupno asistencija
    1600: "player_rebounds",                # Ukupno skokova
    300: "player_3points",                  # Ukupno pogodjenih trojki
    1601: "player_points_assists",          # Ukupno poena+asistencija
    1602: "player_points_rebounds",          # Ukupno poena+skokova
    1603: "player_rebounds_assists",         # Ukupno asistencija+skokova
    1604: "player_points_rebounds_assists",  # Ukupno poena+asistencija+skokova
}

# Map competitionName values to canonical league IDs used by other scrapers.
# When NBA competitionId/name is discovered, add it here.
_COMPETITION_LEAGUE_MAP: dict[str, str] = {
    "nba": "nba",
    "usa nba": "nba",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
}


def _normalize_league_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(
    competition_name: str | None, *, default: str = "basketball"
) -> str:
    """Map an AdmiralBet competitionName to a canonical league ID.

    Falls back to a lowercased slug of the competition name, which keeps
    different competitions separated even without explicit mapping. When the
    competition name is missing, empty, or normalises to an empty string
    (e.g. ``"---"``), the supplied ``default`` is returned — pass
    ``default="football"`` from football helpers so degenerate league names do
    not silently land under the basketball default.
    """
    if not competition_name:
        return default
    raw = competition_name.strip().lower()
    normalized = _normalize_league_key(raw)
    if not normalized:
        return default
    if normalized in _COMPETITION_LEAGUE_MAP:
        return _COMPETITION_LEAGUE_MAP[normalized]
    return raw or default

# Milestone outcome thresholds — "5+" means 5 or more, equivalent to over 4.5
_MILESTONE_THRESHOLDS: dict[str, float] = {
    "5+": 4.5,
    "10+": 9.5,
    "15+": 14.5,
    "20+": 19.5,
    "25+": 24.5,
    "30+": 29.5,
    "35+": 34.5,
    "40+": 39.5,
    "45+": 44.5,
    "50+": 49.5,
}

def _parse_event_name(name: str) -> tuple[str, str]:
    """Parse 'Player Name - Team Name' into (player, team)."""
    if " - " in name:
        player, team = name.split(" - ", 1)
        return player.strip(), team.strip()
    return name.strip(), ""


def _parse_start_time(dt_str: str | None) -> str | None:
    """Parse AdmiralBet datetimes to the canonical ``+00:00`` format.

    AdmiralBet returns naive values such as ``2026-04-15T15:30:00`` for
    basketball events. In practice those values already line up with the UTC
    timestamps from the other bookmakers we merge against, so treat them as UTC
    instead of shifting them by the local Belgrade offset.
    """
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def _parse_over_under_bets(event: dict, player_name: str, team: str,
                           start_time: str | None, league_id: str) -> list[RawOddsData]:
    """Extract over/under lines from player prop bets."""
    results: list[RawOddsData] = []
    for bet in event.get("bets", []):
        market_type = _BET_OVER_UNDER_MAP.get(bet.get("betTypeId"))
        if market_type is None:
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

        over_odds = None
        under_odds = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            name = (outcome.get("name") or "").lower()
            if name == "vise":
                over_odds = outcome.get("odd")
            elif name == "manje":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(RawOddsData(
            bookmaker_id="admiralbet",
            league_id=league_id,
            sport="basketball",
            home_team=team,
            away_team=player_name,
            market_type=market_type,
            player_name=player_name,
            threshold=threshold,
            over_odds=over_odds,
            under_odds=under_odds,
            start_time=start_time,
        ))

    return results


def _parse_milestone_bets(event: dict, player_name: str, team: str,
                          start_time: str | None, league_id: str) -> list[RawOddsData]:
    """Extract milestone (5+, 10+, …) bets from betTypeId 1683."""
    results: list[RawOddsData] = []
    for bet in event.get("bets", []):
        if bet.get("betTypeId") != _BET_POINTS_MILESTONES:
            continue
        if not bet.get("isPlayable"):
            continue

        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            name = (outcome.get("name") or "").strip()
            threshold = _MILESTONE_THRESHOLDS.get(name)
            if threshold is None:
                continue

            over_odds = outcome.get("odd")
            if over_odds is None:
                continue

            results.append(RawOddsData(
                bookmaker_id="admiralbet",
                league_id=league_id,
                sport="basketball",
                home_team=team,
                away_team=player_name,
                market_type="player_points_milestones",
                player_name=player_name,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=None,
                start_time=start_time,
            ))

    return results


def _parse_game_total_ot_bets(
    event: dict,
    home_team: str,
    away_team: str,
    start_time: str | None,
    league_id: str,
) -> list[RawOddsData]:
    """Extract OT-inclusive match totals from basketball event listings."""
    results: list[RawOddsData] = []
    for bet in event.get("bets", []):
        bet_type_name = (bet.get("betTypeName") or "").strip().lower()
        if bet.get("betTypeId") != _BET_GAME_TOTAL_OT:
            continue
        if "+ot" not in bet_type_name:
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

        over_odds = None
        under_odds = None
        for outcome in bet.get("betOutcomes", []):
            if not outcome.get("isPlayable"):
                continue
            name = (outcome.get("name") or "").lower()
            if name == "vise":
                over_odds = outcome.get("odd")
            elif name == "manje":
                under_odds = outcome.get("odd")

        if over_odds is None and under_odds is None:
            continue

        results.append(
            RawOddsData(
                bookmaker_id="admiralbet",
                league_id=league_id,
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


def _parse_event(event: dict) -> list[RawOddsData]:
    """Parse a single AdmiralBet event into RawOddsData entries."""
    name = event.get("name", "")
    player_name, team = _parse_event_name(name)
    if not player_name or not team:
        return []

    start_time = _parse_start_time(event.get("dateTime"))
    league_id = _extract_league_id(event.get("competitionName"))

    results: list[RawOddsData] = []
    results.extend(_parse_over_under_bets(event, player_name, team, start_time, league_id))
    results.extend(_parse_milestone_bets(event, player_name, team, start_time, league_id))
    return results


def _parse_handicap_ot_bets(
    event: dict,
    home_team: str,
    away_team: str,
    start_time: str | None,
    league_id: str,
) -> list[RawOddsData]:
    """Extract OT-inclusive Asian handicap rows.

    AdmiralBet expresses the handicap line via a signed ``sBV`` interpreted as
    *team1's* Asian handicap (negative when team1 is the favorite). Outcome
    name ``"1"`` pays when team1 covers; ``"2"`` pays when team2 covers.
    The event's ``name`` is ``"home - away"``, so team1=home, team2=away. We
    canonicalise to a home-perspective threshold ``= -sBV`` so the analyzer can
    treat handicap exactly like total-points (over = home covers, under = away
    covers).
    """
    results: list[RawOddsData] = []
    for bet in event.get("bets", []):
        if bet.get("betTypeId") != _BET_HANDICAP_OT:
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
                bookmaker_id="admiralbet",
                league_id=league_id,
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


def _parse_game_total_ot_event(event: dict) -> list[RawOddsData]:
    """Parse a standard basketball match event into OT-inclusive game totals."""
    name = event.get("name", "")
    home_team, away_team = _parse_event_name(name)
    if not home_team or not away_team:
        return []

    start_time = _parse_start_time(event.get("dateTime"))
    league_id = _extract_league_id(event.get("competitionName"))
    return _parse_game_total_ot_bets(event, home_team, away_team, start_time, league_id)


def _parse_handicap_ot_event(event: dict) -> list[RawOddsData]:
    """Parse a basketball match event into OT-inclusive Asian handicap rows."""
    name = event.get("name", "")
    home_team, away_team = _parse_event_name(name)
    if not home_team or not away_team:
        return []

    start_time = _parse_start_time(event.get("dateTime"))
    league_id = _extract_league_id(event.get("competitionName"))
    return _parse_handicap_ot_bets(event, home_team, away_team, start_time, league_id)


def _parse_total_line(value: object) -> float | None:
    """Parse an AdmiralBet sBV/line value, tolerating ``"2.50"`` / ``" 2.5 "``."""
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

    Prefers the bet-level ``sBV`` (carried at the market level), but falls
    back to the outcome-level ``sBV``. Returns ``None`` when neither parses,
    or when the two are present and disagree (defense in depth — we'd rather
    drop a confusing offer than mis-classify it as 2.5).
    """
    bet_line = _parse_total_line(bet.get("sBV"))
    outcome_line = _parse_total_line(outcome.get("sBV"))
    if bet_line is not None and outcome_line is not None:
        if abs(bet_line - outcome_line) > 1e-9:
            return None
        return bet_line
    return bet_line if bet_line is not None else outcome_line


def _coerce_positive_odds(value: object) -> float | None:
    try:
        odds = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if odds is None or odds <= 0:
        return None
    return odds


def _parse_football_outcome_event(event: dict, *, source_url: str | None = None) -> list[RawOutcomeOffer]:
    """Parse a single AdmiralBet football event into RawOutcomeOffer rows.

    Emits up to:
      * 3 result offers (1/X/2)
      * 3 double-chance offers (1X/12/X2)
      * 2 totals offers (over/under) at the 2.5 line — other lines are skipped
    """
    name = event.get("name", "")
    home_team, away_team = _parse_event_name(name)
    if not home_team or not away_team:
        return []

    start_time = _parse_start_time(event.get("dateTime"))
    league_id = _extract_league_id(
        event.get("competitionName"), default="football"
    )

    results: list[RawOutcomeOffer] = []
    for bet in event.get("bets", []):
        if not bet.get("isPlayable"):
            continue
        bet_type_id = bet.get("betTypeId")
        if bet_type_id == _BET_FOOTBALL_RESULT:
            outcome_lookup = _FOOTBALL_RESULT_OUTCOMES
            market_type = "football_result"
            line: float | None = None
        elif bet_type_id == _BET_FOOTBALL_DOUBLE_CHANCE:
            outcome_lookup = _FOOTBALL_DOUBLE_CHANCE_OUTCOMES
            market_type = "football_double_chance"
            line = None
        elif bet_type_id == _BET_FOOTBALL_TOTAL_GOALS:
            # Cheap pre-filter: if the bet-level sBV resolves and is not the
            # target line, skip the whole bet without scanning outcomes.
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
            odds_raw = outcome.get("odd")
            try:
                odds = float(odds_raw) if odds_raw is not None else None
            except (TypeError, ValueError):
                odds = None
            if odds is None or odds <= 0:
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
                    bookmaker_id="admiralbet",
                    league_id=league_id,
                    sport="football",
                    home_team=home_team,
                    away_team=away_team,
                    source_url=source_url,
                    market_type=market_type,
                    outcome_code=outcome_code,
                    odds=odds,
                    line=line,
                    raw_label=raw_label,
                    start_time=start_time,
                )
            )

    return results


_TENNIS_DOUBLES_TOKENS = {"doubles", "double", "dublovi", "parovi"}


def _is_tennis_doubles_event(event: dict) -> bool:
    name = (event.get("name") or "").strip()
    competition_name = (event.get("competitionName") or "").strip()
    normalized_context = normalize_identity_text(f"{name} {competition_name}")
    if set(normalized_context.split()) & _TENNIS_DOUBLES_TOKENS:
        return True
    if name.count(" - ") > 1:
        return True
    home_team, away_team = _parse_event_name(name)
    if not home_team or not away_team:
        return False
    return "/" in home_team or "/" in away_team


def _parse_tennis_outcome_event(event: dict) -> list[RawOutcomeOffer]:
    if event.get("isLive") is True:
        return []
    if event.get("isPlayable") is False or event.get("isInOffer") is False:
        return []

    name = event.get("name", "")
    home_team, away_team = _parse_event_name(name)
    if not home_team or not away_team:
        return []
    if _is_tennis_doubles_event(event):
        return []

    start_time = _parse_start_time(event.get("dateTime"))
    league_id = _extract_league_id(event.get("competitionName"), default="tennis")
    results: list[RawOutcomeOffer] = []

    for bet in event.get("bets", []):
        if bet.get("betTypeId") != _BET_TENNIS_MATCH_WINNER:
            continue
        if normalize_identity_text(bet.get("betTypeName") or "") != "pobednik":
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
                    bookmaker_id="admiralbet",
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


class AdmiralBetScraper(BaseScraper):
    """Scraper for AdmiralBet player props and OT-inclusive game totals.

    AdmiralBet returns both player specials and standard basketball events in
    bulk listings, so no per-event detail fetches are needed.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "admiralbet"

    def get_bookmaker_name(self) -> str:
        return "AdmiralBet"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football", "tennis"]

    async def _fetch_list(self, params: dict, label: str) -> list[dict]:
        try:
            data = await self._http.get_json(
                _LIST_URL,
                params=params,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("AdmiralBet %s scrape failed", label)
            return []

        if not isinstance(data, list):
            logger.warning(
                "AdmiralBet: unexpected %s response type %s",
                label,
                type(data).__name__,
            )
            return []

        return data

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        now = current_utc_time()
        cutoff = lookahead_cutoff(now)
        player_params = {
            **_PLAYER_DEFAULT_PARAMS,
            "dateFrom": format_utc_naive_seconds(now),
            "dateTo": format_utc_naive_seconds(cutoff),
        }
        game_total_params = {
            **_GAME_TOTAL_DEFAULT_PARAMS,
            "dateFrom": format_utc_naive_seconds(now),
            "dateTo": format_utc_naive_seconds(cutoff),
        }

        player_data, basketball_events = await asyncio.gather(
            self._fetch_list(player_params, "player specials list"),
            self._fetch_list(game_total_params, "basketball events list"),
        )

        player_results: list[RawOddsData] = []
        for event in player_data:
            player_results.extend(_parse_event(event))

        total_results: list[RawOddsData] = []
        for event in basketball_events:
            total_results.extend(_parse_game_total_ot_event(event))
            total_results.extend(_parse_handicap_ot_event(event))

        results = [*player_results, *total_results]

        logger.info(
            (
                "AdmiralBet scraped %d player odds from %d special events "
                "and %d OT total/handicap odds from %d basketball events"
            ),
            len(player_results),
            len(player_data),
            len(total_results),
            len(basketball_events),
        )
        return results

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        if sport == "tennis":
            now = current_utc_time()
            cutoff = lookahead_cutoff(now)
            params = {
                **_TENNIS_OUTCOME_PARAMS,
                "dateFrom": format_utc_naive_seconds(now),
                "dateTo": format_utc_naive_seconds(cutoff),
            }

            events = await self._fetch_list(params, "tennis events list")

            results: list[RawOutcomeOffer] = []
            for event in events:
                results.extend(_parse_tennis_outcome_event(event))

            logger.info(
                "AdmiralBet scraped %d tennis outcome offers from %d events",
                len(results),
                len(events),
            )
            return results

        if sport != "football":
            return []

        now = current_utc_time()
        cutoff = lookahead_cutoff(now)
        params = {
            **_FOOTBALL_OUTCOME_PARAMS,
            "dateFrom": format_utc_naive_seconds(now),
            "dateTo": format_utc_naive_seconds(cutoff),
        }

        events = await self._fetch_list(params, "football events list")

        results: list[RawOutcomeOffer] = []
        for event in events:
            results.extend(_parse_football_outcome_event(event))

        logger.info(
            "AdmiralBet scraped %d football outcome offers from %d events",
            len(results),
            len(events),
        )
        return results
