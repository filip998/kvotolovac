from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData, RawOutcomeOffer
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)


# ── Endpoints ──────────────────────────────────────────────
_BASE_URL = "https://starbet.rs"
_SOURCE_URL = f"{_BASE_URL}/Bet"
_SPORT_TREE_URL = f"{_BASE_URL}/Oblozuvanje.aspx/GetSportoviSoLigi"
_GET_LIGA_URL = f"{_BASE_URL}/Oblozuvanje.aspx/GetLiga"

_BOOKMAKER_ID = "starbet"
_BOOKMAKER_NAME = "StarBet"

_FOOTBALL_SID = 0
_BASKETBALL_SID = 22
_TENNIS_SID = 37


_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": _BASE_URL,
    "Referer": _SOURCE_URL,
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


# ── Tip IDs we care about ──────────────────────────────────
# Football
_FOOTBALL_RESULT_TIDS: dict[int, tuple[str, str]] = {
    1: ("home", "1"),
    2: ("draw", "X"),
    10: ("away", "2"),
}
_FOOTBALL_DOUBLE_CHANCE_TIDS: dict[int, tuple[str, str]] = {
    83: ("home_or_draw", "1X"),
    84: ("home_or_away", "12"),
    85: ("draw_or_away", "X2"),
}
# Total Goals — preview format uses TID 70 ("0-2 golova") and TID 74 ("3+").
# These are equivalent to Over/Under 2.5 in the canonical taxonomy.
_FOOTBALL_TOTAL_UNDER_TID = 70
_FOOTBALL_TOTAL_OVER_TID = 74
_FOOTBALL_TOTAL_GOALS_LINE = 2.5
_FOOTBALL_TOTAL_UNDER_LABEL = "0-2"
_FOOTBALL_TOTAL_OVER_LABEL = "3+"

# Tennis
_TENNIS_MATCH_WINNER_TIDS: dict[int, tuple[str, str]] = {
    1: ("home", "1"),
    10: ("away", "2"),
}

# Basketball — Ukupno Poena (Total Points) preview rows.
# TID 103 = under, TID 105 = over. The `G` field carries the line value;
# the row repeats once with isG=true representing the "GR" border column —
# the parser must skip those duplicates.
_BASKETBALL_TOTAL_UNDER_TID = 103
_BASKETBALL_TOTAL_OVER_TID = 105


# ── Doubles / canonical leagues ────────────────────────────
_TENNIS_DOUBLES_SEPARATORS = ("/", "／", "\\")

# Tennis "Winner" / outright leagues are disguised as head-to-head events
# whose away_team is a placeholder like "Winner Roland-Garros 2026".  They
# share SID=37 with real singles matches, so we filter them by their tokenised
# league name AND by the away-team prefix as a belt-and-braces guard.
# Serbian latinised equivalents (`pobednik`, `pobjednik`) are bundled in too
# — StarBet localises some labels and other scrapers in this repo already use
# the same vocabulary (see `mozzart_scraper._TENNIS_MATCH_WINNER_GROUP_NAMES`
# and `admiralbet_scraper`'s "pobednik" guard).
_TENNIS_OUTRIGHT_LEAGUE_TOKENS = frozenset(
    {"winner", "outright", "futures", "pobednik", "pobjednik", "specijal", "specijali"}
)
_TENNIS_OUTRIGHT_TEAM_PREFIX_TOKENS = frozenset(
    {"winner", "pobednik", "pobjednik"}
)

_CANONICAL_LEAGUES: dict[str, str] = {
    "nba": "nba",
    "basketball nba": "nba",
    "basketball nba play offs": "nba",
    "basketball nba playoffs": "nba",
    "nba play offs": "nba",
    "nba play off": "nba",
    "nba playoffs": "nba",
    "nba players inc over time": "nba",
    "basketball euroleague": "euroleague",
    "basketball euroleague m": "euroleague",
    "euroleague": "euroleague",
    "euroleague m": "euroleague",
    "basketball euroleague m final four athens": "euroleague",
    "evroliga": "euroleague",
    "aba league": "aba_liga",
    "aba liga": "aba_liga",
    "basketball aba league": "aba_liga",
    "basketball aba league play off": "aba_liga",
    "basketball spain acb": "spain_acb",
    "spain acb": "spain_acb",
}


# ── ISO-datetime parsing ──────────────────────────────────
# StarBet emits 7-digit fractional seconds (e.g. `2026-06-11T21:00:00.0000000+02:00`).
# Python's `datetime.fromisoformat` only accepts up to 6 fractional digits and
# raises `ValueError` for 7+ digits on every Python the repo targets.
_FRACTIONAL_SECONDS_TRIM_RE = re.compile(r"(\.\d{6})\d+")


def _parse_starbet_dt(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    candidate = value.strip().replace("Z", "+00:00")
    # Collapse any fractional-seconds run longer than 6 digits to exactly 6.
    candidate = _FRACTIONAL_SECONDS_TRIM_RE.sub(lambda m: m.group(1), candidate)

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


# ── Internal types ─────────────────────────────────────────


@dataclass(frozen=True)
class _LeagueDescriptor:
    """A single basketball/football/tennis league surfaced by GetSportoviSoLigi."""

    lid: int
    name: str
    sport_id: int
    country: str | None


@dataclass(frozen=True)
class _SportTree:
    leagues_by_sport: dict[int, list[_LeagueDescriptor]]


@dataclass(frozen=True)
class _Fixture:
    """A basketball regular game (host of player_points joins)."""

    pid: int
    home_team: str
    away_team: str
    league_id: str
    league_name: str
    start_time_utc: datetime
    start_time_iso: str
    raw_league_name: str


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.replace("\xa0", " ").split())
    return cleaned or None


def _split_pair_name(pair_name: str | None) -> tuple[str, str] | None:
    if not pair_name or " : " not in pair_name:
        return None
    home, _, away = pair_name.partition(" : ")
    home = home.strip()
    away = away.strip()
    if not home or not away:
        return None
    return home, away


def _league_key(raw_name: str | None, sport: str) -> str:
    normalized = normalize_identity_text(raw_name)
    if not normalized:
        return sport
    canonical = _CANONICAL_LEAGUES.get(normalized)
    if canonical is not None:
        return canonical
    return normalized.replace(" ", "_")


def _is_nba_league(raw_name: str | None) -> bool:
    normalized = normalize_identity_text(raw_name)
    if not normalized:
        return False
    return "nba" in normalized.split()


def _looks_like_player_special(raw_name: str | None) -> bool:
    """League heuristic: the special's display name contains 'players'."""

    normalized = normalize_identity_text(raw_name)
    if not normalized:
        return False
    return "players" in normalized.split()


def _infer_target_league_id(special_league_name: str | None) -> str | None:
    """Derive the regular-league id that a player-special league points at.

    The platform names player-prop specials ``"<League> Players (...)"``,
    e.g. ``"NBA Players (Inc. Over Time)"`` for NBA players.  We strip the
    ``players`` token plus any trailing parenthesised qualifier and run the
    remainder through ``_league_key`` so the result matches the canonical
    league_id we emit for the corresponding regular fixture.  Returns ``None``
    when the name doesn't carry a usable target prefix (e.g. just ``"Players"``).
    """

    if not special_league_name:
        return None
    # Drop any "(...)" suffix and the "Players" sentinel token, keeping the
    # league prefix intact for the canonical-mapping lookup.
    bare = re.sub(r"\s*\([^)]*\)\s*", " ", special_league_name)
    tokens = [t for t in normalize_identity_text(bare).split() if t != "players"]
    if not tokens:
        return None
    return _league_key(" ".join(tokens), "basketball")


def _is_tennis_outright_league(raw_name: str | None) -> bool:
    normalized = normalize_identity_text(raw_name)
    if not normalized:
        return False
    tokens = set(normalized.split())
    return bool(tokens & _TENNIS_OUTRIGHT_LEAGUE_TOKENS)


def _is_tennis_outright_pair(home_team: str, away_team: str) -> bool:
    for team in (home_team, away_team):
        tokens = normalize_identity_text(team).split()
        if tokens and tokens[0] in _TENNIS_OUTRIGHT_TEAM_PREFIX_TOKENS:
            return True
    return False


def _parse_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result


def _parse_odds(value: object) -> float | None:
    parsed = _parse_float(value)
    if parsed is None or parsed <= 1.0:
        return None
    return parsed


def _parse_threshold(value: object) -> float | None:
    parsed = _parse_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


# ── Tip-row helpers ────────────────────────────────────────


def _iter_tip_rows(pair: dict) -> list[dict]:
    rows = pair.get("T")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and not row.get("isG", False)]


def _select_single_odd(rows: list[dict], tid: int) -> float | None:
    """Return odds for the first non-isG row with the given TID."""

    for row in rows:
        if row.get("TID") == tid:
            return _parse_odds(row.get("K"))
    return None


def _select_total_points_pair(rows: list[dict]) -> tuple[float, float, float] | None:
    """Return (line, over_odds, under_odds) for the first complete Ukupno Poena pair.

    Today's preview ships exactly one line per event, but the platform's
    detail endpoints already emit alternate ladders.  We defensively group
    rows by their line value (``G``) so a future preview that interleaves
    multiple lines still surfaces a usable headline rather than dropping the
    whole market because the first under/over pair don't share a line.
    """

    grouped: dict[float, dict[str, float | None]] = {}
    line_order: list[float] = []
    for row in rows:
        tid = row.get("TID")
        if tid not in (_BASKETBALL_TOTAL_UNDER_TID, _BASKETBALL_TOTAL_OVER_TID):
            continue
        line = _parse_threshold(row.get("G"))
        odds = _parse_odds(row.get("K"))
        if line is None or odds is None:
            continue
        bucket = grouped.get(line)
        if bucket is None:
            bucket = {"under": None, "over": None}
            grouped[line] = bucket
            line_order.append(line)
        side = "under" if tid == _BASKETBALL_TOTAL_UNDER_TID else "over"
        if bucket[side] is None:
            bucket[side] = odds

    for line in line_order:
        bucket = grouped[line]
        if bucket["under"] is not None and bucket["over"] is not None:
            return line, bucket["over"], bucket["under"]  # type: ignore[return-value]
    return None


# ── Sport-tree parsing ─────────────────────────────────────


def _parse_sport_tree(payload: object) -> _SportTree:
    leagues_by_sport: dict[int, list[_LeagueDescriptor]] = {}
    if not isinstance(payload, list):
        return _SportTree(leagues_by_sport=leagues_by_sport)

    for sport in payload:
        if not isinstance(sport, dict):
            continue
        sid = sport.get("SID")
        if not isinstance(sid, int) or sid not in {
            _FOOTBALL_SID,
            _BASKETBALL_SID,
            _TENNIS_SID,
        }:
            continue
        descriptors: list[_LeagueDescriptor] = []
        for league in sport.get("L") or []:
            if not isinstance(league, dict):
                continue
            lid = league.get("LID")
            ln = _clean_text(league.get("LN") or league.get("NW"))
            if not isinstance(lid, int) or ln is None:
                continue
            descriptors.append(
                _LeagueDescriptor(
                    lid=lid,
                    name=ln,
                    sport_id=sid,
                    country=_clean_text(league.get("NG")),
                )
            )
        if descriptors:
            leagues_by_sport[sid] = descriptors

    return _SportTree(leagues_by_sport=leagues_by_sport)


# ── Per-sport extraction ──────────────────────────────────


def _extract_football_offers(
    leagues_payload: object,
    descriptors: dict[int, _LeagueDescriptor],
) -> list[RawOutcomeOffer]:
    if not isinstance(leagues_payload, list):
        return []

    offers: list[RawOutcomeOffer] = []
    seen: set[tuple[int, str, str | None]] = set()

    for league in leagues_payload:
        if not isinstance(league, dict):
            continue
        lid = league.get("LID")
        if not isinstance(lid, int):
            continue
        descriptor = descriptors.get(lid)
        league_name = (
            descriptor.name
            if descriptor is not None
            else _clean_text(league.get("LN") or league.get("NW")) or ""
        )
        league_id = _league_key(league_name, "football")

        for pair in league.get("P") or []:
            if not isinstance(pair, dict):
                continue
            pid = pair.get("PID")
            if not isinstance(pid, int):
                continue
            teams = _split_pair_name(_clean_text(pair.get("PN")))
            if teams is None:
                continue
            home_team, away_team = teams
            start_dt = _parse_starbet_dt(_clean_text(pair.get("DI")))
            if start_dt is None:
                continue
            start_iso = _format_utc(start_dt)
            rows = _iter_tip_rows(pair)
            if not rows:
                continue

            def _emit(market_type: str, outcome_code: str, raw_label: str, odds: float, line: float | None = None) -> None:
                key = (pid, market_type, outcome_code)
                if key in seen:
                    return
                seen.add(key)
                offers.append(
                    RawOutcomeOffer(
                        bookmaker_id=_BOOKMAKER_ID,
                        league_id=league_id,
                        sport="football",
                        home_team=home_team,
                        away_team=away_team,
                        source_url=_SOURCE_URL,
                        market_type=market_type,
                        outcome_code=outcome_code,
                        odds=odds,
                        line=line,
                        raw_label=raw_label,
                        start_time=start_iso,
                    )
                )

            # Football result (1X2)
            for tid, (outcome_code, raw_label) in _FOOTBALL_RESULT_TIDS.items():
                odds = _select_single_odd(rows, tid)
                if odds is None:
                    continue
                _emit("football_result", outcome_code, raw_label, odds)

            # Double chance — preview ships any subset of {1X, 12, X2}
            for tid, (outcome_code, raw_label) in _FOOTBALL_DOUBLE_CHANCE_TIDS.items():
                odds = _select_single_odd(rows, tid)
                if odds is None:
                    continue
                _emit("football_double_chance", outcome_code, raw_label, odds)

            # Total Goals (line 2.5: under=0-2, over=3+)
            under_odds = _select_single_odd(rows, _FOOTBALL_TOTAL_UNDER_TID)
            over_odds = _select_single_odd(rows, _FOOTBALL_TOTAL_OVER_TID)
            if under_odds is not None:
                _emit(
                    "football_total_goals",
                    "under",
                    _FOOTBALL_TOTAL_UNDER_LABEL,
                    under_odds,
                    line=_FOOTBALL_TOTAL_GOALS_LINE,
                )
            if over_odds is not None:
                _emit(
                    "football_total_goals",
                    "over",
                    _FOOTBALL_TOTAL_OVER_LABEL,
                    over_odds,
                    line=_FOOTBALL_TOTAL_GOALS_LINE,
                )

    offers.sort(
        key=lambda row: (
            row.start_time or "",
            row.home_team,
            row.away_team,
            row.market_type,
            row.outcome_code,
        )
    )
    return offers


def _extract_tennis_offers(
    leagues_payload: object,
    descriptors: dict[int, _LeagueDescriptor],
) -> list[RawOutcomeOffer]:
    if not isinstance(leagues_payload, list):
        return []

    offers: list[RawOutcomeOffer] = []
    seen: set[tuple[int, str]] = set()

    for league in leagues_payload:
        if not isinstance(league, dict):
            continue
        lid = league.get("LID")
        if not isinstance(lid, int):
            continue
        descriptor = descriptors.get(lid)
        league_name = (
            descriptor.name
            if descriptor is not None
            else _clean_text(league.get("LN") or league.get("NW")) or ""
        )
        if _is_tennis_outright_league(league_name):
            continue
        league_id = _league_key(league_name, "tennis")

        for pair in league.get("P") or []:
            if not isinstance(pair, dict):
                continue
            pid = pair.get("PID")
            if not isinstance(pid, int):
                continue
            teams = _split_pair_name(_clean_text(pair.get("PN")))
            if teams is None:
                continue
            home, away = teams
            if any(sep in home or sep in away for sep in _TENNIS_DOUBLES_SEPARATORS):
                continue
            if _is_tennis_outright_pair(home, away):
                continue
            start_dt = _parse_starbet_dt(_clean_text(pair.get("DI")))
            if start_dt is None:
                continue
            start_iso = _format_utc(start_dt)
            rows = _iter_tip_rows(pair)
            if not rows:
                continue
            for tid, (outcome_code, raw_label) in _TENNIS_MATCH_WINNER_TIDS.items():
                odds = _select_single_odd(rows, tid)
                if odds is None:
                    continue
                key = (pid, outcome_code)
                if key in seen:
                    continue
                seen.add(key)
                offers.append(
                    RawOutcomeOffer(
                        bookmaker_id=_BOOKMAKER_ID,
                        league_id=league_id,
                        sport="tennis",
                        home_team=home,
                        away_team=away,
                        source_url=_SOURCE_URL,
                        market_type="tennis_match_winner",
                        outcome_code=outcome_code,
                        odds=odds,
                        line=None,
                        raw_label=raw_label,
                        start_time=start_iso,
                    )
                )

    offers.sort(
        key=lambda row: (
            row.start_time or "",
            row.home_team,
            row.away_team,
            row.outcome_code,
        )
    )
    return offers


def _index_basketball_fixtures(
    leagues_payload: object,
    descriptors: dict[int, _LeagueDescriptor],
) -> tuple[list[_Fixture], dict[tuple[str, str, str], _Fixture], dict[tuple[str, str], int]]:
    """Index regular basketball games for player-points join + game_total emission.

    Returns ``(fixtures, by_team_start_league, ambiguity_counts)`` where:

    * ``fixtures`` — every non-special basketball fixture, in feed order.
    * ``by_team_start_league`` — keyed by ``(normalized_team, utc_iso_start,
      league_id)`` so a player special that targets league ``L`` can only
      resolve against a regular fixture in that same league.  The dict keeps
      the first fixture per key (we never overwrite); collisions on the
      smaller 2-tuple are surfaced via ``ambiguity_counts`` for logging.
    * ``ambiguity_counts`` — ``(normalized_team, utc_iso_start) → distinct-league
      count``, populated whenever the same team plays at the same minute
      in two different basketball leagues. Used downstream to log a warning
      and skip the unsafe join.
    """

    if not isinstance(leagues_payload, list):
        return [], {}, {}

    fixtures: list[_Fixture] = []
    by_team_start_league: dict[tuple[str, str, str], _Fixture] = {}
    seen_leagues_per_team_start: dict[tuple[str, str], set[str]] = {}

    for league in leagues_payload:
        if not isinstance(league, dict):
            continue
        lid = league.get("LID")
        if not isinstance(lid, int):
            continue
        descriptor = descriptors.get(lid)
        league_name = (
            descriptor.name
            if descriptor is not None
            else _clean_text(league.get("LN") or league.get("NW")) or ""
        )
        if _looks_like_player_special(league_name):
            continue
        league_id = _league_key(league_name, "basketball")

        for pair in league.get("P") or []:
            if not isinstance(pair, dict):
                continue
            pid = pair.get("PID")
            if not isinstance(pid, int):
                continue
            teams = _split_pair_name(_clean_text(pair.get("PN")))
            if teams is None:
                continue
            home, away = teams
            start_dt = _parse_starbet_dt(_clean_text(pair.get("DI")))
            if start_dt is None:
                continue
            start_iso = _format_utc(start_dt) or ""
            if not start_iso:
                continue
            fixture = _Fixture(
                pid=pid,
                home_team=home,
                away_team=away,
                league_id=league_id,
                league_name=league_name,
                start_time_utc=start_dt,
                start_time_iso=start_iso,
                raw_league_name=league_name,
            )
            fixtures.append(fixture)
            for team in (home, away):
                normalized_team = normalize_identity_text(team)
                three_key = (normalized_team, start_iso, league_id)
                if three_key not in by_team_start_league:
                    by_team_start_league[three_key] = fixture
                two_key = (normalized_team, start_iso)
                seen_leagues_per_team_start.setdefault(two_key, set()).add(league_id)

    ambiguity_counts = {
        key: len(leagues)
        for key, leagues in seen_leagues_per_team_start.items()
        if len(leagues) > 1
    }
    return fixtures, by_team_start_league, ambiguity_counts


def _extract_basketball_game_totals(
    leagues_payload: object,
    descriptors: dict[int, _LeagueDescriptor],
    fixtures: list[_Fixture],
) -> list[RawOddsData]:
    """Emit `game_total` (or `game_total_ot` for NBA) per regular basketball event."""

    if not fixtures:
        return []

    fixtures_by_pid = {fixture.pid: fixture for fixture in fixtures}
    rows: list[RawOddsData] = []

    if not isinstance(leagues_payload, list):
        return []

    for league in leagues_payload:
        if not isinstance(league, dict):
            continue
        lid = league.get("LID")
        if not isinstance(lid, int):
            continue
        descriptor = descriptors.get(lid)
        league_name = (
            descriptor.name
            if descriptor is not None
            else _clean_text(league.get("LN") or league.get("NW")) or ""
        )
        if _looks_like_player_special(league_name):
            continue
        is_nba = _is_nba_league(league_name)
        market_type = "game_total_ot" if is_nba else "game_total"

        for pair in league.get("P") or []:
            if not isinstance(pair, dict):
                continue
            pid = pair.get("PID")
            fixture = fixtures_by_pid.get(pid) if isinstance(pid, int) else None
            if fixture is None:
                continue
            tip_rows = _iter_tip_rows(pair)
            totals = _select_total_points_pair(tip_rows)
            if totals is None:
                continue
            line, over_odds, under_odds = totals
            rows.append(
                RawOddsData(
                    bookmaker_id=_BOOKMAKER_ID,
                    league_id=fixture.league_id,
                    sport="basketball",
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
                    source_url=_SOURCE_URL,
                    market_type=market_type,
                    player_name=None,
                    threshold=line,
                    over_odds=over_odds,
                    under_odds=under_odds,
                    start_time=fixture.start_time_iso,
                )
            )

    rows.sort(
        key=lambda row: (
            row.start_time or "",
            row.home_team,
            row.away_team,
        )
    )
    return rows


@dataclass
class _PlayerPointsExtraction:
    rows: list[RawOddsData] = field(default_factory=list)
    unresolved_count: int = 0
    unresolved_samples: list[tuple[str, str, str]] = field(default_factory=list)
    ambiguous_count: int = 0
    ambiguous_samples: list[tuple[str, str, str]] = field(default_factory=list)


def _extract_basketball_player_points(
    leagues_payload: object,
    descriptors: dict[int, _LeagueDescriptor],
    by_team_start_league: dict[tuple[str, str, str], _Fixture],
    ambiguity_counts: dict[tuple[str, str], int] | None = None,
) -> _PlayerPointsExtraction:
    extraction = _PlayerPointsExtraction()
    ambiguity_counts = ambiguity_counts or {}

    if not isinstance(leagues_payload, list):
        return extraction

    for league in leagues_payload:
        if not isinstance(league, dict):
            continue
        lid = league.get("LID")
        if not isinstance(lid, int):
            continue
        descriptor = descriptors.get(lid)
        league_name = (
            descriptor.name
            if descriptor is not None
            else _clean_text(league.get("LN") or league.get("NW")) or ""
        )
        if not _looks_like_player_special(league_name):
            continue
        target_league_id = _infer_target_league_id(league_name)
        if not target_league_id:
            # The special's name carried no recognisable league prefix;
            # we cannot safely attach its players to any fixture.
            continue

        for pair in league.get("P") or []:
            if not isinstance(pair, dict):
                continue
            split = _split_pair_name(_clean_text(pair.get("PN")))
            if split is None:
                continue
            player_name, team_name = split
            start_dt = _parse_starbet_dt(_clean_text(pair.get("DI")))
            if start_dt is None:
                continue
            start_iso = _format_utc(start_dt) or ""
            if not start_iso:
                continue

            normalized_team = normalize_identity_text(team_name)
            fixture = by_team_start_league.get(
                (normalized_team, start_iso, target_league_id)
            )
            if fixture is None:
                # No fixture in the target league for this team at this minute.
                # Surface the ambiguity (same team+time in another league) as
                # a separate signal so it cannot be confused with "team simply
                # not playing" failures.
                two_key = (normalized_team, start_iso)
                if ambiguity_counts.get(two_key, 0) > 1:
                    extraction.ambiguous_count += 1
                    if len(extraction.ambiguous_samples) < 5:
                        extraction.ambiguous_samples.append(
                            (player_name, team_name, target_league_id)
                        )
                else:
                    extraction.unresolved_count += 1
                    if len(extraction.unresolved_samples) < 5:
                        extraction.unresolved_samples.append(
                            (player_name, team_name, start_iso)
                        )
                continue

            tip_rows = _iter_tip_rows(pair)
            totals = _select_total_points_pair(tip_rows)
            if totals is None:
                continue
            line, over_odds, under_odds = totals
            extraction.rows.append(
                RawOddsData(
                    bookmaker_id=_BOOKMAKER_ID,
                    league_id=fixture.league_id,
                    sport="basketball",
                    home_team=fixture.home_team,
                    away_team=fixture.away_team,
                    source_url=_SOURCE_URL,
                    market_type="player_points",
                    player_name=player_name,
                    threshold=line,
                    over_odds=over_odds,
                    under_odds=under_odds,
                    start_time=fixture.start_time_iso,
                )
            )

    extraction.rows.sort(
        key=lambda row: (
            row.start_time or "",
            row.home_team,
            row.away_team,
            row.player_name or "",
            row.threshold,
        )
    )
    return extraction


# ── Scraper class ──────────────────────────────────────────


class StarBetScraper(BaseScraper):
    """StarBet (starbet.rs) prematch scraper.

    The platform exposes everything we need through three POST endpoints:
    `GetSportoviSoLigi` (sport tree), `GetLiga` (bulk league fetch with the
    preview tip set per event), and `GetTipoviV2` (per-event detail).  v1
    sticks to the first two so the entire prematch offer for football +
    basketball + tennis ships in **4 HTTP calls per cycle**.
    """

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)
        self._sport_tree_task: asyncio.Task[_SportTree] | None = None
        self._sport_tree_lock: asyncio.Lock | None = None

    def get_bookmaker_id(self) -> str:
        return _BOOKMAKER_ID

    def get_bookmaker_name(self) -> str:
        return _BOOKMAKER_NAME

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football", "tennis"]

    # ── Sport-tree fetch (per-cycle in-flight dedupe, no lifetime cache) ──

    def _get_sport_tree_lock(self) -> asyncio.Lock:
        if self._sport_tree_lock is None:
            self._sport_tree_lock = asyncio.Lock()
        return self._sport_tree_lock

    async def _load_sport_tree(self) -> _SportTree:
        current = asyncio.current_task()
        try:
            payload = await self._http.post_json(
                _SPORT_TREE_URL,
                json_body={"filter": "0", "activeStyle": "img/sports"},
                headers=_DEFAULT_HEADERS,
            )
            return _parse_sport_tree(payload)
        finally:
            async with self._get_sport_tree_lock():
                if self._sport_tree_task is current:
                    # Drop the cached task so the next cycle refetches.
                    self._sport_tree_task = None

    async def _fetch_sport_tree(self) -> _SportTree:
        async with self._get_sport_tree_lock():
            task = self._sport_tree_task
            if task is None:
                task = asyncio.create_task(self._load_sport_tree())
                self._sport_tree_task = task

        return await asyncio.shield(task)

    async def _fetch_leagues(self, league_ids: list[int]) -> Any:
        if not league_ids:
            return []
        body = {
            "LigaID": list(league_ids),
            "filter": "0",
            "parId": 0,
        }
        try:
            payload = await self._http.post_json(
                _GET_LIGA_URL,
                json_body=body,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception(
                "StarBet GetLiga failed for %d league(s)",
                len(league_ids),
            )
            return []
        return payload

    async def _fetch_sport_leagues(self, sport_id: int) -> tuple[
        Any,
        dict[int, _LeagueDescriptor],
    ]:
        tree = await self._fetch_sport_tree()
        descriptors_list = tree.leagues_by_sport.get(sport_id, [])
        descriptors = {desc.lid: desc for desc in descriptors_list}
        if not descriptors:
            logger.info(
                "StarBet: sport_id=%d has no leagues in the sport tree", sport_id
            )
            return [], {}
        league_ids = [desc.lid for desc in descriptors_list]
        payload = await self._fetch_leagues(league_ids)
        return payload, descriptors

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        payload, descriptors = await self._fetch_sport_leagues(_BASKETBALL_SID)
        fixtures, by_team_start_league, ambiguity_counts = (
            _index_basketball_fixtures(payload, descriptors)
        )
        if not fixtures:
            logger.info("StarBet: no basketball fixtures in current cycle")
            return []

        game_total_rows = _extract_basketball_game_totals(
            payload, descriptors, fixtures
        )
        player_extraction = _extract_basketball_player_points(
            payload, descriptors, by_team_start_league, ambiguity_counts
        )

        if player_extraction.unresolved_count:
            logger.warning(
                "StarBet: %d player special(s) skipped (no matching basketball "
                "fixture by team+start in target league). Samples: %s",
                player_extraction.unresolved_count,
                player_extraction.unresolved_samples,
            )
        if player_extraction.ambiguous_count:
            logger.warning(
                "StarBet: %d player special(s) skipped because the same team "
                "plays at the same minute in multiple basketball leagues "
                "and we cannot safely pick the right fixture. Samples "
                "(player, team, target_league_id): %s",
                player_extraction.ambiguous_count,
                player_extraction.ambiguous_samples,
            )

        logger.info(
            "StarBet scraped %d basketball rows (%d game_total* across %d fixtures, "
            "%d player_points across %d players resolved out of %d candidates "
            "[%d unresolved, %d ambiguous])",
            len(game_total_rows) + len(player_extraction.rows),
            len(game_total_rows),
            len({row.start_time for row in game_total_rows}),
            len(player_extraction.rows),
            len({row.player_name for row in player_extraction.rows if row.player_name}),
            len(player_extraction.rows)
            + player_extraction.unresolved_count
            + player_extraction.ambiguous_count,
            player_extraction.unresolved_count,
            player_extraction.ambiguous_count,
        )
        return game_total_rows + player_extraction.rows

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        if sport == "football":
            payload, descriptors = await self._fetch_sport_leagues(_FOOTBALL_SID)
            offers = _extract_football_offers(payload, descriptors)
            logger.info(
                "StarBet scraped %d football outcome offers across %d events",
                len(offers),
                len({(row.home_team, row.away_team, row.start_time) for row in offers}),
            )
            return offers

        if sport == "tennis":
            payload, descriptors = await self._fetch_sport_leagues(_TENNIS_SID)
            offers = _extract_tennis_offers(payload, descriptors)
            logger.info(
                "StarBet scraped %d tennis outcome offers across %d singles events",
                len(offers),
                len({(row.home_team, row.away_team, row.start_time) for row in offers}),
            )
            return offers

        return []


__all__ = [
    "StarBetScraper",
    "_BOOKMAKER_ID",
    "_BOOKMAKER_NAME",
    "_SOURCE_URL",
    "_SPORT_TREE_URL",
    "_GET_LIGA_URL",
    "_DEFAULT_HEADERS",
    "_LeagueDescriptor",
    "_Fixture",
    "_parse_starbet_dt",
    "_parse_sport_tree",
    "_extract_football_offers",
    "_extract_tennis_offers",
    "_index_basketball_fixtures",
    "_extract_basketball_game_totals",
    "_extract_basketball_player_points",
    "_infer_target_league_id",
    "_looks_like_player_special",
    "_is_nba_league",
    "_is_tennis_outright_league",
    "_is_tennis_outright_pair",
    "_select_total_points_pair",
    "_iter_tip_rows",
    "_split_pair_name",
    "_league_key",
    "_CANONICAL_LEAGUES",
    "_TENNIS_DOUBLES_SEPARATORS",
    "_FOOTBALL_TOTAL_GOALS_LINE",
    "_PlayerPointsExtraction",
]

# Silence unused-cast warning kept for IDE inference clarity above.
_ = cast
