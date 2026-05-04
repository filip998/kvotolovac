from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import logging
import re

from rapidfuzz import fuzz

from ..models.schemas import (
    NormalizedOutcomeOffer,
    RawOddsData,
    RawOutcomeOffer,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from .league_registry import resolve_league
from .normalizer import generate_match_id, normalize_odds_with_diagnostics, resolve_team_name
from .team_registry import create_canonical_team
from .text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_FOOTBALL_AUTO_MATCH_AVG_THRESHOLD = 78
_FOOTBALL_AUTO_MATCH_SIDE_THRESHOLD = 70
_FOOTBALL_AUTO_MATCH_STRONG_SIDE_THRESHOLD = 95
_FOOTBALL_AUTO_MATCH_WEAK_SIDE_THRESHOLD = 60
_FOOTBALL_AUTO_MATCH_MARGIN = 8
_LOW_SIGNAL_TEAM_TOKENS = {"bc", "bk", "kk", "fc", "fk", "club", "team", "sc", "cf", "cd", "ce"}
_TEAM_QUALIFIER_TOKENS = {
    "2",
    "ii",
    "b",
    "res",
    "reserve",
    "reserves",
    "u17",
    "u18",
    "u19",
    "u20",
    "u21",
    "u23",
    "w",
    "women",
    "youth",
}
# Cross-sport aliases for explicit women markers. Plain ASCII "z" is not in
# this set because it is a common location abbreviation in football; only
# explicit marker syntax such as "(Ž)" or "Ž/" is treated as women.
_WOMEN_QUALIFIER_ALIASES = frozenset({"w", "wom", "women"})
_AGGRESSIVE_MERGE_SPORTS = frozenset({"basketball"})
_EXPLICIT_Z_WOMEN_MARKER_RE = re.compile(
    r"(^|\s)ž(?=$|\s)|\(\s*[žz]\s*\)|^\s*[žz]\s*/",
    re.IGNORECASE,
)
_SAME_ORIENTATION = "same"
_REVERSED_ORIENTATION = "reversed"


@dataclass(frozen=True)
class _OutcomeEvent:
    bookmaker_id: str
    sport: str
    start_time: str
    home_team: str
    away_team: str


@dataclass(frozen=True)
class _OutcomeEventSlot:
    sport: str
    start_time: str
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str

    @property
    def key(self) -> tuple[str, str, int, int]:
        return (self.sport, self.start_time, self.home_team_id, self.away_team_id)

    @property
    def reversed_key(self) -> tuple[str, str, int, int]:
        return (self.sport, self.start_time, self.away_team_id, self.home_team_id)


@dataclass(frozen=True)
class _OutcomeEventResolution:
    slot: _OutcomeEventSlot
    orientation: str = _SAME_ORIENTATION


@dataclass(frozen=True)
class _OutcomeEventPair:
    left: _OutcomeEvent
    right: _OutcomeEvent
    home_score: float
    away_score: float
    orientation: str

    @property
    def score(self) -> float:
        return (self.home_score + self.away_score) / 2

    @property
    def strong_side_score(self) -> float:
        return max(self.home_score, self.away_score)

    @property
    def weak_side_score(self) -> float:
        return min(self.home_score, self.away_score)


def _significant_tokens(name: str) -> set[str]:
    return {
        token
        for token in normalize_identity_text(name).split()
        if token not in _LOW_SIGNAL_TEAM_TOKENS
    }


def _team_similarity(left: str, right: str) -> float:
    left_key = normalize_identity_text(left)
    right_key = normalize_identity_text(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 100.0
    left_tokens = _significant_tokens(left)
    right_tokens = _significant_tokens(right)
    if left_tokens and left_tokens == right_tokens:
        return 100.0
    return float(fuzz.token_sort_ratio(left_key, right_key))


def _team_qualifiers(name: str, *, sport: str | None = None) -> set[str]:
    tokens = normalize_identity_text(name).split()
    qualifiers: set[str] = set()
    youth_ages = {"17", "18", "19", "20", "21", "23"}
    active_qualifier_tokens = _TEAM_QUALIFIER_TOKENS | {"wom"}

    if _EXPLICIT_Z_WOMEN_MARKER_RE.search(name):
        qualifiers.add("women")

    def suffix_has_qualifier(start_index: int) -> bool:
        index = start_index
        while index < len(tokens):
            token = tokens[index]
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            if token == "team":
                index += 1
                continue
            if token == "u" and next_token in youth_ages:
                return True
            if token in active_qualifier_tokens:
                return True
            index += 1
        return False

    for index, token in enumerate(tokens):
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if token == "u" and next_token in youth_ages:
            qualifiers.add(f"u{next_token}")
            continue
        if token in {"b", "2", "ii"}:
            if index > 0 and (index == len(tokens) - 1 or next_token == "team" or suffix_has_qualifier(index + 1)):
                qualifiers.add(token)
            continue
        if token in _WOMEN_QUALIFIER_ALIASES:
            is_suffix = index > 0 and (
                index == len(tokens) - 1
                or next_token in {"team", "women"}
                or suffix_has_qualifier(index + 1)
            )
            if is_suffix:
                qualifiers.add("women")
            continue
        if token == "z":
            # Plain ASCII Z is intentionally not a universal women alias.
            # The explicit-marker regex above handles "(Ž)", "(Z)", "Ž/",
            # and "Z/" without breaking football abbreviations such as
            # "FK Borac Z" for Zvornik.
            continue
        if token not in active_qualifier_tokens:
            continue
        qualifiers.add(token)
    return qualifiers


def _strip_explicit_z_women_markers(name: str) -> str:
    without_parenthesized = re.sub(
        r"\(\s*[žz]\s*\)",
        " ",
        name,
        flags=re.IGNORECASE,
    )
    without_leading_slash = re.sub(
        r"^\s*[žz]\s*/",
        "",
        without_parenthesized,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"(^|\s)ž(?=$|\s)",
        r"\1",
        without_leading_slash,
        flags=re.IGNORECASE,
    )


def _same_team_context(left: str, right: str, *, sport: str | None = None) -> bool:
    return _team_qualifiers(left, sport=sport) == _team_qualifiers(right, sport=sport)


def _display_name_for_event(*names: str) -> str:
    return max(
        (name.strip() for name in names if name.strip()),
        key=lambda value: (len(_significant_tokens(value)), len(value), value),
    )


def _autocreate_cross_book_football_teams(raw_list: list[RawOutcomeOffer]) -> None:
    matchup_counts: dict[tuple[str, str, tuple[str, str]], set[str]] = defaultdict(set)
    display_names: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for raw in raw_list:
        if raw.start_time is None:
            continue
        home_key = normalize_identity_text(raw.home_team)
        away_key = normalize_identity_text(raw.away_team)
        if not home_key or not away_key or home_key == away_key:
            continue
        pair_key = (raw.sport, raw.start_time, tuple(sorted((home_key, away_key))))
        matchup_counts[pair_key].add(raw.bookmaker_id)
        display_names[(raw.sport, home_key)][raw.home_team.strip()] += 1
        display_names[(raw.sport, away_key)][raw.away_team.strip()] += 1

    for (sport, _start_time, team_keys), bookmaker_ids in matchup_counts.items():
        if len(bookmaker_ids) < 2:
            continue
        for team_key in team_keys:
            counter = display_names.get((sport, team_key))
            if not counter:
                continue
            display_name = max(counter.items(), key=lambda item: (item[1], len(item[0]), item[0]))[0]
            if resolve_team_name(display_name, sport=sport).team_id is None:
                create_canonical_team(display_name=display_name, sport=sport)


def _unique_events(raw_list: list[RawOutcomeOffer]) -> list[_OutcomeEvent]:
    seen: set[tuple[str, str, str, str, str]] = set()
    events: list[_OutcomeEvent] = []
    for raw in raw_list:
        if raw.sport != "football" or raw.start_time is None:
            continue
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            normalize_identity_text(raw.home_team),
            normalize_identity_text(raw.away_team),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(
            _OutcomeEvent(
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
                start_time=raw.start_time,
                home_team=raw.home_team,
                away_team=raw.away_team,
            )
        )
    return events


def _pair_candidates(left: _OutcomeEvent, right: _OutcomeEvent) -> _OutcomeEventPair | None:
    if left.bookmaker_id == right.bookmaker_id or left.sport != right.sport or left.start_time != right.start_time:
        return None
    candidates: list[_OutcomeEventPair] = []
    if _same_team_context(left.home_team, right.home_team, sport=left.sport) and _same_team_context(left.away_team, right.away_team, sport=left.sport):
        candidates.append(
            _OutcomeEventPair(
                left=left,
                right=right,
                home_score=_team_similarity(left.home_team, right.home_team),
                away_score=_team_similarity(left.away_team, right.away_team),
                orientation=_SAME_ORIENTATION,
            )
        )
    if _same_team_context(left.home_team, right.away_team, sport=left.sport) and _same_team_context(left.away_team, right.home_team, sport=left.sport):
        candidates.append(
            _OutcomeEventPair(
                left=left,
                right=right,
                home_score=_team_similarity(left.home_team, right.away_team),
                away_score=_team_similarity(left.away_team, right.home_team),
                orientation=_REVERSED_ORIENTATION,
            )
        )
    if not candidates:
        return None
    pair = max(candidates, key=lambda candidate: (candidate.score, candidate.orientation == _SAME_ORIENTATION))
    if not _is_auto_event_match_candidate(pair):
        return None
    return pair


def _is_auto_event_match_candidate(pair: _OutcomeEventPair) -> bool:
    if pair.score < _FOOTBALL_AUTO_MATCH_AVG_THRESHOLD:
        return False
    balanced_match = pair.weak_side_score >= _FOOTBALL_AUTO_MATCH_SIDE_THRESHOLD
    anchored_match = (
        pair.strong_side_score >= _FOOTBALL_AUTO_MATCH_STRONG_SIDE_THRESHOLD
        and pair.weak_side_score >= _FOOTBALL_AUTO_MATCH_WEAK_SIDE_THRESHOLD
    )
    return balanced_match or anchored_match


def _event_key(event: _OutcomeEvent) -> tuple[str, str, str, str, str]:
    return (
        event.bookmaker_id,
        event.sport,
        event.start_time,
        normalize_identity_text(event.home_team),
        normalize_identity_text(event.away_team),
    )


def _event_key_from_raw(raw: RawOutcomeOffer) -> tuple[str, str, str, str, str] | None:
    if raw.start_time is None:
        return None
    return (
        raw.bookmaker_id,
        raw.sport,
        raw.start_time,
        normalize_identity_text(raw.home_team),
        normalize_identity_text(raw.away_team),
    )


def _invert_orientation(orientation: str) -> str:
    return _REVERSED_ORIENTATION if orientation == _SAME_ORIENTATION else _SAME_ORIENTATION


def _orientation_from_pair(base_orientation: str, pair_orientation: str) -> str:
    if pair_orientation == _SAME_ORIENTATION:
        return base_orientation
    return _invert_orientation(base_orientation)


def _resolve_event_slot(event: _OutcomeEvent) -> _OutcomeEventSlot | None:
    home_resolution = resolve_team_name(
        event.home_team,
        bookmaker_id=event.bookmaker_id,
        sport=event.sport,
    )
    away_resolution = resolve_team_name(
        event.away_team,
        bookmaker_id=event.bookmaker_id,
        sport=event.sport,
    )
    if (
        home_resolution.team_id is None
        or away_resolution.team_id is None
        or home_resolution.team_id == away_resolution.team_id
    ):
        return None
    return _OutcomeEventSlot(
        sport=event.sport,
        start_time=event.start_time,
        home_team_id=home_resolution.team_id,
        away_team_id=away_resolution.team_id,
        home_team=home_resolution.team_name,
        away_team=away_resolution.team_name,
    )


def _create_event_slot(pair: _OutcomeEventPair) -> _OutcomeEventSlot | None:
    if pair.orientation == _SAME_ORIENTATION:
        home_name = _display_name_for_event(pair.left.home_team, pair.right.home_team)
        away_name = _display_name_for_event(pair.left.away_team, pair.right.away_team)
    else:
        home_name = _display_name_for_event(pair.left.home_team, pair.right.away_team)
        away_name = _display_name_for_event(pair.left.away_team, pair.right.home_team)

    home = create_canonical_team(display_name=home_name, sport=pair.left.sport)
    away = create_canonical_team(display_name=away_name, sport=pair.left.sport)
    if home.team_id == away.team_id:
        return None
    return _OutcomeEventSlot(
        sport=pair.left.sport,
        start_time=pair.left.start_time,
        home_team_id=home.team_id,
        away_team_id=away.team_id,
        home_team=home.team_name,
        away_team=away.team_name,
    )


def _same_slot(left: _OutcomeEventResolution, right: _OutcomeEventResolution) -> bool:
    return left.slot.key == right.slot.key


def _reversed_slots(left: _OutcomeEventResolution, right: _OutcomeEventResolution) -> bool:
    return left.slot.key == right.slot.reversed_key


def _rank_event_pairs(events: list[_OutcomeEvent]) -> list[_OutcomeEventPair]:
    events_by_slot: dict[tuple[str, str], list[_OutcomeEvent]] = defaultdict(list)
    for event in events:
        events_by_slot[(event.sport, event.start_time)].append(event)

    accepted: list[_OutcomeEventPair] = []
    for events in events_by_slot.values():
        candidates_by_event: dict[_OutcomeEvent, list[_OutcomeEventPair]] = defaultdict(list)
        for idx, left in enumerate(events):
            for right in events[idx + 1 :]:
                pair = _pair_candidates(left, right)
                if pair is None:
                    continue
                candidates_by_event[left].append(pair)
                candidates_by_event[right].append(pair)

        for event, candidates in candidates_by_event.items():
            ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
            if not ranked:
                continue
            best = ranked[0]
            if len(ranked) > 1 and best.score - ranked[1].score < _FOOTBALL_AUTO_MATCH_MARGIN:
                continue
            counterpart = best.right if best.left == event else best.left
            counterpart_ranked = sorted(
                candidates_by_event.get(counterpart, []),
                key=lambda item: item.score,
                reverse=True,
            )
            if not counterpart_ranked or counterpart_ranked[0] != best:
                continue
            if best not in accepted:
                accepted.append(best)

    return sorted(accepted, key=lambda item: item.score, reverse=True)


def _build_football_event_resolutions(
    raw_list: list[RawOutcomeOffer],
) -> dict[tuple[str, str, str, str, str], _OutcomeEventResolution]:
    events = _unique_events(raw_list)
    resolutions: dict[tuple[str, str, str, str, str], _OutcomeEventResolution] = {}
    for event in events:
        slot = _resolve_event_slot(event)
        if slot is None:
            continue
        resolutions[_event_key(event)] = _OutcomeEventResolution(slot=slot)

    for pair in _rank_event_pairs(events):
        left_key = _event_key(pair.left)
        right_key = _event_key(pair.right)
        left_resolution = resolutions.get(left_key)
        right_resolution = resolutions.get(right_key)

        if left_resolution is not None and right_resolution is not None:
            if _same_slot(left_resolution, right_resolution):
                continue
            if pair.orientation == _REVERSED_ORIENTATION and _reversed_slots(left_resolution, right_resolution):
                resolutions[right_key] = _OutcomeEventResolution(
                    slot=left_resolution.slot,
                    orientation=_orientation_from_pair(left_resolution.orientation, pair.orientation),
                )
            continue

        if left_resolution is not None:
            resolutions[right_key] = _OutcomeEventResolution(
                slot=left_resolution.slot,
                orientation=_orientation_from_pair(left_resolution.orientation, pair.orientation),
            )
            continue

        if right_resolution is not None:
            resolutions[left_key] = _OutcomeEventResolution(
                slot=right_resolution.slot,
                orientation=_orientation_from_pair(right_resolution.orientation, pair.orientation),
            )
            continue

        slot = _create_event_slot(pair)
        if slot is None:
            continue
        resolutions[left_key] = _OutcomeEventResolution(slot=slot)
        resolutions[right_key] = _OutcomeEventResolution(
            slot=slot,
            orientation=pair.orientation,
        )

    return resolutions


def _map_outcome_code_for_orientation(
    market_type: str,
    outcome_code: str,
    orientation: str,
) -> str | None:
    if orientation == _SAME_ORIENTATION:
        return outcome_code
    if market_type == "football_total_goals":
        return outcome_code
    if market_type == "football_result":
        return {
            "home": "away",
            "away": "home",
            "draw": "draw",
        }.get(outcome_code)
    if market_type == "football_double_chance":
        return {
            "home_or_draw": "draw_or_away",
            "draw_or_away": "home_or_draw",
            "home_or_away": "home_or_away",
        }.get(outcome_code)
    return None


def _team_review_proxy_rows(raw_list: list[RawOutcomeOffer]) -> list[RawOddsData]:
    rows: list[RawOddsData] = []
    seen: set[tuple[str, str, str | None, str, str]] = set()
    for raw in raw_list:
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            normalize_identity_text(raw.home_team),
            normalize_identity_text(raw.away_team),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                sport=raw.sport,
                home_team=raw.home_team,
                away_team=raw.away_team,
                source_url=raw.source_url,
                market_type=raw.market_type,
                player_name=None,
                threshold=raw.line or 0.0,
                over_odds=raw.odds,
                under_odds=None,
                start_time=raw.start_time,
            )
        )
    return rows


def _unresolved_team_diagnostic(
    raw: RawOutcomeOffer,
    *,
    raw_team_name: str,
    reason_code: str,
) -> UnresolvedOddsDiagnostic:
    direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
    return UnresolvedOddsDiagnostic(
        bookmaker_id=raw.bookmaker_id,
        raw_league_id=raw.league_id,
        league_id=direct_league.league_id,
        sport=raw.sport,
        market_type=raw.market_type,
        player_name=None,
        raw_team_name=raw_team_name,
        normalized_team_name=resolve_team_name(
            raw_team_name,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        ).team_name,
        start_time=raw.start_time,
        threshold=raw.line or 0.0,
        over_odds=raw.odds,
        under_odds=None,
        reason_code=reason_code,
    )


def _normalized_offer_from_resolution(
    raw: RawOutcomeOffer,
    *,
    league_id: str,
    resolution: _OutcomeEventResolution,
) -> NormalizedOutcomeOffer | None:
    outcome_code = _map_outcome_code_for_orientation(
        raw.market_type,
        raw.outcome_code,
        resolution.orientation,
    )
    if outcome_code is None:
        logger.warning(
            "Skipping reversed football outcome %s/%s for bookmaker %s",
            raw.market_type,
            raw.outcome_code,
            raw.bookmaker_id,
        )
        return None

    match_id = generate_match_id(
        resolution.slot.home_team_id,
        resolution.slot.away_team_id,
        raw.start_time,
        raw.sport,
    )
    return NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=raw.bookmaker_id,
        league_id=league_id,
        sport=raw.sport,
        home_team_id=resolution.slot.home_team_id,
        away_team_id=resolution.slot.away_team_id,
        home_team=resolution.slot.home_team,
        away_team=resolution.slot.away_team,
        source_url=raw.source_url,
        market_type=raw.market_type,
        outcome_code=outcome_code,
        odds=raw.odds,
        line=raw.line,
        raw_label=raw.raw_label,
        start_time=raw.start_time,
    )


def normalize_outcome_offers_with_diagnostics(
    raw_list: list[RawOutcomeOffer],
) -> tuple[
    list[NormalizedOutcomeOffer],
    list[UnresolvedOddsDiagnostic],
    list[TeamReviewDiagnostic],
]:
    _autocreate_cross_book_football_teams(raw_list)
    event_resolutions = _build_football_event_resolutions(raw_list)
    unresolved_event_rows = [
        raw
        for raw in raw_list
        if (event_key := _event_key_from_raw(raw)) is None
        or event_key not in event_resolutions
    ]
    _, _, team_review_cases = normalize_odds_with_diagnostics(
        _team_review_proxy_rows(unresolved_event_rows),
        log_unresolved_shared_platform=False,
    )

    normalized: list[NormalizedOutcomeOffer] = []
    unresolved: list[UnresolvedOddsDiagnostic] = []
    seen_unresolved: set[tuple[str, str, str, str | None, str]] = set()

    for raw in raw_list:
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        if raw.start_time is None:
            key = (raw.bookmaker_id, raw.raw_label or raw.market_type, raw.home_team, raw.start_time, "missing_start_time")
            if key not in seen_unresolved:
                seen_unresolved.add(key)
                unresolved.append(
                    _unresolved_team_diagnostic(
                        raw,
                        raw_team_name=raw.home_team,
                        reason_code="missing_start_time",
                    )
                )
            continue

        event_key = _event_key_from_raw(raw)
        event_resolution = event_resolutions.get(event_key) if event_key is not None else None
        if event_resolution is not None:
            normalized_offer = _normalized_offer_from_resolution(
                raw,
                league_id=direct_league.league_id,
                resolution=event_resolution,
            )
            if normalized_offer is not None:
                normalized.append(normalized_offer)
            continue

        home_resolution = resolve_team_name(raw.home_team, bookmaker_id=raw.bookmaker_id, sport=raw.sport)
        away_resolution = resolve_team_name(raw.away_team, bookmaker_id=raw.bookmaker_id, sport=raw.sport)

        if home_resolution.team_id is None or away_resolution.team_id is None:
            if home_resolution.team_id is None:
                key = (raw.bookmaker_id, raw.market_type, raw.home_team, raw.start_time, "unresolved_home_team")
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved.append(
                        _unresolved_team_diagnostic(
                            raw,
                            raw_team_name=raw.home_team,
                            reason_code="unresolved_home_team",
                        )
                    )
            if away_resolution.team_id is None:
                key = (raw.bookmaker_id, raw.market_type, raw.away_team, raw.start_time, "unresolved_away_team")
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved.append(
                        _unresolved_team_diagnostic(
                            raw,
                            raw_team_name=raw.away_team,
                            reason_code="unresolved_away_team",
                        )
                    )
            continue

        match_id = generate_match_id(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        normalized.append(
            NormalizedOutcomeOffer(
                match_id=match_id,
                bookmaker_id=raw.bookmaker_id,
                league_id=direct_league.league_id,
                sport=raw.sport,
                home_team_id=home_resolution.team_id,
                away_team_id=away_resolution.team_id,
                home_team=home_resolution.team_name,
                away_team=away_resolution.team_name,
                source_url=raw.source_url,
                market_type=raw.market_type,
                outcome_code=raw.outcome_code,
                odds=raw.odds,
                line=raw.line,
                raw_label=raw.raw_label,
                start_time=raw.start_time,
            )
        )

    return normalized, unresolved, team_review_cases
