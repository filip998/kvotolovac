from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
import re
from collections import Counter, defaultdict
from functools import lru_cache

from rapidfuzz import fuzz

from ..models.schemas import (
    NormalizedOdds,
    RawOddsData,
    TeamReviewCandidate,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from .league_registry import resolve_league
from .team_registry import (
    DEFAULT_SPORT,
    create_canonical_team,
    resolve_team_alias,
    search_canonical_team_candidates,
)
from .text_normalizer import (
    compact_identity_text,
    normalize_identity_text,
    tokenize_identity_text,
)

logger = logging.getLogger(__name__)

# Known player canonical names — last name is the key
_CANONICAL_PLAYERS: dict[str, str] = {
    "vezenkov": "Sasha Vezenkov",
    "campazzo": "Facundo Campazzo",
    "sloukas": "Kostas Sloukas",
    "tavares": "Walter Tavares",
    "hayes-davis": "Nigel Hayes-Davis",
    "calathes": "Nick Calathes",
    "mirotic": "Nikola Mirotic",
    "lundberg": "Iffe Lundberg",
    "jovic": "Nikola Jovic",
    "petrusev": "Filip Petrusev",
    "lessort": "Mathias Lessort",
    "blossomgame": "Jaron Blossomgame",
    "lucic": "Vladimir Lucic",
    "lee": "Saben Lee",
    "durant": "Kevin Durant",
}

FUZZY_THRESHOLD = 75
TEAM_REVIEW_CANDIDATE_THRESHOLD = 76
TEAM_REVIEW_MAX_CANDIDATES = 3

_MARKET_TYPE_MAPPING: dict[str, str] = {
    "player_points": "player_points",
    "player points": "player_points",
    "points": "player_points",
    "player_rebounds": "player_rebounds",
    "player rebounds": "player_rebounds",
    "rebounds": "player_rebounds",
    "player_assists": "player_assists",
    "player assists": "player_assists",
    "assists": "player_assists",
    "player_3points": "player_3points",
    "player 3points": "player_3points",
    "player 3-points": "player_3points",
    "player 3 points": "player_3points",
    "3points": "player_3points",
    "3-points": "player_3points",
    "3 points": "player_3points",
    "player_steals": "player_steals",
    "player steals": "player_steals",
    "steals": "player_steals",
    "player_blocks": "player_blocks",
    "player blocks": "player_blocks",
    "blocks": "player_blocks",
    "player_points_rebounds": "player_points_rebounds",
    "player points rebounds": "player_points_rebounds",
    "player points + rebounds": "player_points_rebounds",
    "points rebounds": "player_points_rebounds",
    "points + rebounds": "player_points_rebounds",
    "player_points_assists": "player_points_assists",
    "player points assists": "player_points_assists",
    "player points + assists": "player_points_assists",
    "points assists": "player_points_assists",
    "points + assists": "player_points_assists",
    "player_rebounds_assists": "player_rebounds_assists",
    "player rebounds assists": "player_rebounds_assists",
    "player rebounds + assists": "player_rebounds_assists",
    "rebounds assists": "player_rebounds_assists",
    "rebounds + assists": "player_rebounds_assists",
    "player_points_rebounds_assists": "player_points_rebounds_assists",
    "player points rebounds assists": "player_points_rebounds_assists",
    "player points + rebounds + assists": "player_points_rebounds_assists",
    "points rebounds assists": "player_points_rebounds_assists",
    "points + rebounds + assists": "player_points_rebounds_assists",
    "pra": "player_points_rebounds_assists",
    "player pra": "player_points_rebounds_assists",
    "player_points_milestones": "player_points_milestones",
    "player points milestones": "player_points_milestones",
    "player points milestone": "player_points_milestones",
    "player points ladder": "player_points_milestones",
    "player_points_ladder": "player_points_milestones",
    "game_total": "game_total",
    "game total": "game_total",
    "total": "game_total",
    "game_total_ot": "game_total_ot",
    "game total ot": "game_total_ot",
}


def _normalize_team_key(raw_name: str) -> str:
    return normalize_identity_text(raw_name)


@dataclass(frozen=True)
class TeamNameResolution:
    team_id: int | None
    team_name: str
    source: str
    confidence: str
    score: float | None = None


def resolve_team_name(
    raw_name: str,
    league_id: str | None = None,
    bookmaker_id: str | None = None,
    *,
    sport: str = DEFAULT_SPORT,
) -> TeamNameResolution:
    alias_resolution = resolve_team_alias(
        raw_name,
        bookmaker_id=bookmaker_id,
        sport=sport,
    )
    if alias_resolution is not None:
        return TeamNameResolution(
            team_id=alias_resolution.team_id,
            team_name=alias_resolution.team_name,
            source=alias_resolution.source,
            confidence="high",
        )

    del league_id

    return TeamNameResolution(
        team_id=None,
        team_name=raw_name.strip(),
        source="raw",
        confidence="low",
        score=None,
    )


def normalize_team_name(
    raw_name: str,
    league_id: str | None = None,
    bookmaker_id: str | None = None,
    *,
    sport: str = DEFAULT_SPORT,
) -> str:
    return resolve_team_name(
        raw_name,
        league_id=league_id,
        bookmaker_id=bookmaker_id,
        sport=sport,
    ).team_name


def normalize_player_name(raw_name: str | None) -> str | None:
    if not raw_name:
        return None
    name = raw_name.strip()
    name_lower = name.lower()

    # Check if any canonical last name appears in the raw name
    for last_name, canonical in _CANONICAL_PLAYERS.items():
        if last_name in name_lower:
            return canonical

    # Fuzzy match against all canonical full names
    best_score = 0
    best_match = name
    for canonical in _CANONICAL_PLAYERS.values():
        score = fuzz.token_sort_ratio(name_lower, canonical.lower())
        if score > best_score and score >= FUZZY_THRESHOLD:
            best_score = score
            best_match = canonical
    return best_match


def _normalize_person_tokens(name: str) -> list[str]:
    return tokenize_identity_text(name, keep_hyphens=True)


def _compact_person_name(name: str) -> str:
    return compact_identity_text(name)


_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


def _strip_trailing_suffixes(tokens: list[str]) -> list[str]:
    """Remove name suffixes (Jr, Sr, II, etc.) only from the end."""
    while tokens and tokens[-1] in _NAME_SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def _player_name_parts(name: str) -> tuple[list[str], str] | None:
    tokens = _normalize_person_tokens(name)
    tokens = _strip_trailing_suffixes(tokens)
    if len(tokens) < 2:
        return None
    return tokens[:-1], tokens[-1]


def _player_name_completeness(first_tokens: list[str]) -> int:
    return sum(len(token) for token in first_tokens if len(token) > 1)


def _has_multiple_initials(first_tokens: list[str]) -> bool:
    return len(first_tokens) > 1 and all(len(token) == 1 for token in first_tokens)


def _collapse_first_name_variants(first_names: set[str]) -> set[str]:
    collapsed: list[str] = []
    for name in sorted(first_names, key=lambda value: (len(value), value), reverse=True):
        if any(existing.startswith(name) or name.startswith(existing) for existing in collapsed):
            continue
        collapsed.append(name)
    return set(collapsed)


def _name_surface_richness(name: str) -> tuple[int, int, int]:
    stripped = name.strip()
    return (
        sum(1 for ch in stripped if not ch.isascii()),
        stripped.count("-"),
        len(stripped),
    )


def _surface_person_tokens(name: str) -> list[str]:
    tokens = [part.strip() for part in name.split() if part.strip()]
    while tokens and normalize_identity_text(tokens[-1]) in _NAME_SUFFIXES:
        tokens.pop()
    return tokens


def _is_abbreviated_surface_token(token: str) -> bool:
    compact = re.sub(r"[^A-Za-zÀ-ž]+", "", token)
    return bool(compact) and ("." in token or (len(compact) <= 2 and compact.isupper()))


def _check_first_name_match(
    raw_first_tokens: list[str],
    candidate_first_tokens: list[str],
    raw_last_name: str,
    candidate_last_name: str,
) -> bool:
    if (
        not raw_first_tokens
        or not candidate_first_tokens
        or raw_last_name != candidate_last_name
        or _has_multiple_initials(raw_first_tokens)
        or (len(raw_first_tokens) == 1 and len(raw_first_tokens[0]) == 1 and _has_multiple_initials(candidate_first_tokens))
    ):
        return False

    raw_first = raw_first_tokens[0]
    candidate_first = candidate_first_tokens[0]
    if raw_first == candidate_first:
        return True
    if len(raw_first) == 1:
        return candidate_first.startswith(raw_first)
    if candidate_first.startswith(raw_first) or raw_first.startswith(candidate_first):
        return True
    return raw_first[0] == candidate_first[0] and fuzz.ratio(raw_first, candidate_first) >= 80


def _is_contextual_player_match(raw_name: str, candidate_name: str) -> bool:
    raw_parts = _player_name_parts(raw_name)
    candidate_parts = _player_name_parts(candidate_name)
    if not raw_parts or not candidate_parts:
        return False

    raw_first_tokens, raw_last_name = raw_parts
    candidate_first_tokens, candidate_last_name = candidate_parts
    raw_surface_tokens = _surface_person_tokens(raw_name)
    candidate_surface_tokens = _surface_person_tokens(candidate_name)

    # Normal order match
    if _check_first_name_match(raw_first_tokens, candidate_first_tokens, raw_last_name, candidate_last_name):
        return True

    # Reversed order is only safe when the swapped token looks like an
    # abbreviated first name (e.g. "VJ", "J", "AJ"), not a full given name.
    if (
        len(raw_first_tokens) == 1
        and raw_surface_tokens
        and _is_abbreviated_surface_token(raw_surface_tokens[-1])
    ):
        reversed_first = [raw_last_name]
        reversed_last = raw_first_tokens[0]
        if _check_first_name_match(reversed_first, candidate_first_tokens, reversed_last, candidate_last_name):
            return True

    if (
        len(candidate_first_tokens) == 1
        and candidate_surface_tokens
        and _is_abbreviated_surface_token(candidate_surface_tokens[-1])
    ):
        reversed_first = [candidate_last_name]
        reversed_last = candidate_first_tokens[0]
        if _check_first_name_match(raw_first_tokens, reversed_first, raw_last_name, reversed_last):
            return True

    return False


def _resolve_contextual_player_names(raw_list: list[RawOddsData]) -> list[RawOddsData]:
    names_by_match: dict[str, Counter[str]] = defaultdict(Counter)

    for raw in raw_list:
        if not raw.player_name:
            continue

        home_resolution = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        away_resolution = resolve_team_name(
            raw.away_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if (
            raw.start_time is None
            or home_resolution.team_id is None
            or away_resolution.team_id is None
        ):
            continue
        match_id = generate_match_id(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        names_by_match[match_id][raw.player_name.strip()] += 1

    # Pre-pass: merge names that differ only by punctuation, spacing, or diacritics.
    case_replacements: dict[tuple[str, str], str] = {}
    compact_canonical_names: set[tuple[str, str]] = set()
    for match_id, name_counts in names_by_match.items():
        by_compact: dict[str, list[str]] = defaultdict(list)
        for name in name_counts:
            by_compact[_compact_person_name(name)].append(name)
        for compact_key, variants in by_compact.items():
            if not compact_key:
                continue
            if len(variants) <= 1:
                continue
            best = max(
                variants,
                key=lambda v: (
                    name_counts[v],
                    _name_surface_richness(v),
                    v,
                ),
            )
            merged_count = sum(name_counts[v] for v in variants)
            compact_canonical_names.add((match_id, best))
            for v in variants:
                if v != best:
                    case_replacements[(match_id, v)] = best
                    name_counts[best] = merged_count
                    del name_counts[v]

    replacements: dict[tuple[str, str], str] = dict(case_replacements)

    for match_id, name_counts in names_by_match.items():
        observed_names = list(name_counts)
        for raw_name in observed_names:
            raw_parts = _player_name_parts(raw_name)
            if not raw_parts:
                continue

            raw_first_tokens, _ = raw_parts
            raw_completeness = _player_name_completeness(raw_first_tokens)
            raw_is_single_initial = len(raw_first_tokens) == 1 and len(raw_first_tokens[0]) == 1
            candidates = [
                candidate
                for candidate in observed_names
                if candidate != raw_name and _is_contextual_player_match(raw_name, candidate)
            ]
            if not candidates:
                continue
            candidate_first_names = _collapse_first_name_variants({
                _player_name_parts(candidate)[0][0] for candidate in candidates if _player_name_parts(candidate)
            })
            if len(candidate_first_names) > 1:
                continue

            ranked = sorted(
                candidates,
                key=lambda candidate: (
                    name_counts[candidate],
                    _player_name_completeness(_player_name_parts(candidate)[0]),
                    len(candidate.strip()),
                    candidate,
                ),
                reverse=True,
            )
            best_candidate = ranked[0]
            best_parts = _player_name_parts(best_candidate)
            if not best_parts:
                continue
            best_completeness = _player_name_completeness(best_parts[0])
            raw_has_compact_alias_support = (match_id, raw_name) in compact_canonical_names
            if best_completeness < raw_completeness:
                continue
            if best_completeness == raw_completeness and name_counts[best_candidate] <= name_counts[raw_name]:
                continue

            if len(ranked) > 1:
                runner_up = ranked[1]
                runner_up_parts = _player_name_parts(runner_up)
                if runner_up_parts and (
                    name_counts[runner_up],
                    _player_name_completeness(runner_up_parts[0]),
                    len(runner_up.strip()),
                ) == (
                    name_counts[best_candidate],
                    best_completeness,
                    len(best_candidate.strip()),
                ):
                    continue

            replacements[(match_id, raw_name)] = best_candidate

    resolved: list[RawOddsData] = []
    for raw in raw_list:
        if not raw.player_name:
            resolved.append(raw)
            continue

        home_resolution = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        away_resolution = resolve_team_name(
            raw.away_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if (
            raw.start_time is None
            or home_resolution.team_id is None
            or away_resolution.team_id is None
        ):
            resolved.append(raw)
            continue
        match_id = generate_match_id(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        replacement = replacements.get((match_id, raw.player_name.strip()))
        seen_replacements: set[str] = set()
        while replacement and replacement not in seen_replacements:
            seen_replacements.add(replacement)
            next_replacement = replacements.get((match_id, replacement))
            if not next_replacement:
                break
            replacement = next_replacement
        if not replacement:
            resolved.append(raw)
            continue

        resolved.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                sport=raw.sport,
                home_team=raw.home_team,
                away_team=raw.away_team,
                market_type=raw.market_type,
                player_name=replacement,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )

    return resolved


def normalize_league_id(raw_league_id: str, bookmaker_id: str | None = None) -> str:
    return resolve_league(raw_league_id, bookmaker_id=bookmaker_id).league_id


def _event_identity_slot(
    start_time: str | None,
    sport: str,
) -> tuple[str, str]:
    if not start_time:
        raise ValueError("Exact kickoff time is required for event matching")
    return (sport, start_time)


def _display_event_slot_time(slot: tuple[str, str]) -> str:
    return slot[1]


def generate_match_id(
    home_team: int | str,
    away_team: int | str,
    start_time: str | None,
    sport: str = DEFAULT_SPORT,
) -> str:
    raw = f"{sport}:{_event_identity_slot(start_time, sport)[1]}:{home_team}:{away_team}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class _EventSlotResolution:
    sport: str
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    league_id: str
    confidence: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class _TeamReviewCandidate:
    team_id: int
    team_name: str
    score: float
    matched_alias: str | None = None


@dataclass(frozen=True)
class _TeamReviewSlotCandidate:
    team_id: int
    team_name: str
    counterpart_team: str
    canonical_home_team: str
    canonical_away_team: str


@dataclass(frozen=True)
class _CanonicalMatchup:
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str


def _event_slot_key(
    home_team_id: int,
    away_team_id: int,
    start_time: str | None,
    sport: str,
) -> tuple[tuple[str, str], tuple[int, int]]:
    return (
        _event_identity_slot(start_time, sport),
        tuple(sorted((home_team_id, away_team_id))),
    )


def _slot_orientation_key(
    home_team_id: int,
    away_team_id: int,
) -> tuple[int, int]:
    return (home_team_id, away_team_id)


def _choose_majority_value(counter: Counter[object]) -> object:
    return min(counter.items(), key=lambda item: (-item[1], item[0]))[0]


def _build_event_slot_resolutions(
    raw_list: list[RawOddsData],
) -> dict[tuple[tuple[str, str], tuple[int, int]], _EventSlotResolution]:
    orientation_counts: dict[
        tuple[tuple[str, str], tuple[int, int]],
        Counter[tuple[int, int]],
    ] = defaultdict(Counter)
    league_counts: dict[tuple[tuple[str, str], tuple[int, int]], Counter[str]] = defaultdict(Counter)
    team_names: dict[int, str] = {}
    display_names: dict[str, str] = {}

    for raw in raw_list:
        if raw.start_time is None:
            continue
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        home_resolution = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        away_resolution = resolve_team_name(
            raw.away_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if (
            home_resolution.team_id is None
            or away_resolution.team_id is None
            or home_resolution.team_id == away_resolution.team_id
        ):
            continue

        slot = _event_slot_key(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        orientation_counts[slot][
            _slot_orientation_key(home_resolution.team_id, away_resolution.team_id)
        ] += 1
        league_counts[slot][direct_league.league_id] += 1
        team_names[home_resolution.team_id] = home_resolution.team_name
        team_names[away_resolution.team_id] = away_resolution.team_name
        display_names[direct_league.league_id] = direct_league.display_name

    resolutions: dict[tuple[tuple[str, str], tuple[int, int]], _EventSlotResolution] = {}
    for slot, orientations in orientation_counts.items():
        chosen_home_id, chosen_away_id = _choose_majority_value(orientations)
        slot_league_counts = league_counts[slot]
        chosen_league = _choose_majority_value(slot_league_counts)
        confidence = "high" if len(orientations) == 1 else "medium"
        if len(slot_league_counts) > 1 and confidence == "high":
            confidence = "medium"

        league_evidence = ", ".join(
            f"{display_names.get(league_id, league_id)} x{count}"
            for league_id, count in sorted(
                slot_league_counts.items(),
                key=lambda item: (-item[1], display_names.get(item[0], item[0])),
            )
        )
        resolutions[slot] = _EventSlotResolution(
            sport=slot[0][0],
            home_team_id=chosen_home_id,
            away_team_id=chosen_away_id,
            home_team=team_names[chosen_home_id],
            away_team=team_names[chosen_away_id],
            league_id=chosen_league,
            confidence=confidence,
            evidence=(
                f"Sport: {slot[0][0]}",
                f"Exact start time: {_display_event_slot_time(slot[0])}",
                f"Canonical event: {team_names[chosen_home_id]} vs {team_names[chosen_away_id]}",
                f"League votes: {league_evidence}",
            ),
        )
    return resolutions


def _team_candidate_score(raw_team_name: str, candidate_team_name: str) -> float:
    raw_key = _normalize_team_key(raw_team_name)
    candidate_key = _normalize_team_key(candidate_team_name)
    if not raw_key or not candidate_key:
        return 0.0
    return float(
        max(
            fuzz.token_set_ratio(raw_key, candidate_key),
            fuzz.partial_ratio(raw_key, candidate_key),
        )
    )


def _rank_team_review_candidates(
    raw_team_name: str,
    candidate_teams: list[tuple[int, str]],
    *,
    threshold: float = TEAM_REVIEW_CANDIDATE_THRESHOLD,
) -> list[_TeamReviewCandidate]:
    raw_key = _normalize_team_key(raw_team_name)
    ranked: list[_TeamReviewCandidate] = []
    seen_team_ids: set[int] = set()

    for team_id, candidate_team in candidate_teams:
        candidate_key = _normalize_team_key(candidate_team)
        if (
            not candidate_key
            or candidate_key == raw_key
            or team_id in seen_team_ids
        ):
            continue
        seen_team_ids.add(team_id)
        score = _team_candidate_score(raw_team_name, candidate_team)
        if score < threshold:
            continue
        ranked.append(_TeamReviewCandidate(team_id, candidate_team, score))

    return sorted(ranked, key=lambda item: (-item.score, item.team_name))[
        :TEAM_REVIEW_MAX_CANDIDATES
    ]


def _team_review_slot_candidates(
    slot_resolutions: list[_EventSlotResolution],
    *,
    counterpart_team_id: int,
    raw_team_name: str,
) -> dict[int, _TeamReviewSlotCandidate]:
    candidates: dict[int, _TeamReviewSlotCandidate] = {}
    raw_key = _normalize_team_key(raw_team_name)

    for resolution in slot_resolutions:
        if counterpart_team_id not in {
            resolution.home_team_id,
            resolution.away_team_id,
        }:
            continue

        candidate_team_id = (
            resolution.away_team_id
            if resolution.home_team_id == counterpart_team_id
            else resolution.home_team_id
        )
        candidate_team = (
            resolution.away_team
            if resolution.home_team_id == counterpart_team_id
            else resolution.home_team
        )
        candidate_key = _normalize_team_key(candidate_team)
        if not candidate_key or candidate_key == raw_key:
            continue
        candidates.setdefault(
            candidate_team_id,
            _TeamReviewSlotCandidate(
                team_id=candidate_team_id,
                team_name=candidate_team,
                counterpart_team=(
                    resolution.home_team
                    if resolution.home_team_id == counterpart_team_id
                    else resolution.away_team
                ),
                canonical_home_team=resolution.home_team,
                canonical_away_team=resolution.away_team,
            ),
        )

    return candidates


def _to_review_candidates(
    candidates: list[_TeamReviewCandidate],
) -> list[TeamReviewCandidate]:
    return [
        TeamReviewCandidate(
            team_id=candidate.team_id,
            team_name=candidate.team_name,
            score=candidate.score,
            matched_alias=candidate.matched_alias,
        )
        for candidate in candidates
    ]


def _search_global_review_candidates(
    raw_team_name: str,
    *,
    sport: str,
) -> list[_TeamReviewCandidate]:
    return [
        _TeamReviewCandidate(
            team_id=candidate.team_id,
            team_name=candidate.team_name,
            score=float(candidate.score),
            matched_alias=candidate.matched_alias,
        )
        for candidate in search_canonical_team_candidates(
            raw_team_name,
            sport=sport,
            limit=TEAM_REVIEW_MAX_CANDIDATES,
        )
    ]


def _build_team_review_cases(
    raw_list: list[RawOddsData],
    slot_resolutions: dict[tuple[tuple[str, str], tuple[int, int]], _EventSlotResolution],
) -> list[TeamReviewDiagnostic]:
    slots_by_start_time: dict[tuple[str, str], list[_EventSlotResolution]] = defaultdict(list)
    for (slot_time, _), resolution in slot_resolutions.items():
        slots_by_start_time[slot_time].append(resolution)

    review_cases: dict[tuple[str, str, str, str, str], TeamReviewDiagnostic] = {}

    for raw in raw_list:
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        if raw.start_time is None:
            continue

        candidate_slots = slots_by_start_time.get((raw.sport, raw.start_time), [])

        team_inputs = (raw.home_team, raw.away_team)
        team_resolutions = [
            resolve_team_name(
                team_name,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
            )
            for team_name in team_inputs
        ]

        for team_index, raw_team_name in enumerate(team_inputs):
            team_resolution = team_resolutions[team_index]
            if team_resolution.team_id is not None:
                continue

            counterpart_resolution = team_resolutions[1 - team_index]
            ranked_candidates: list[_TeamReviewCandidate] = []
            review_kind = "candidate_search"
            confidence = "low"
            evidence = [f"Exact start time: {raw.start_time}"]
            matched_counterpart_team = (
                counterpart_resolution.team_name
                if counterpart_resolution.team_id is not None
                else None
            )
            canonical_home_team: str | None = None
            canonical_away_team: str | None = None

            if counterpart_resolution.team_id is not None:
                slot_candidates = _team_review_slot_candidates(
                    candidate_slots,
                    counterpart_team_id=counterpart_resolution.team_id,
                    raw_team_name=raw_team_name,
                )
                if len(slot_candidates) == 1:
                    slot_candidate = next(iter(slot_candidates.values()))
                    ranked_candidates = [
                        _TeamReviewCandidate(
                            team_id=slot_candidate.team_id,
                            team_name=slot_candidate.team_name,
                            score=_team_candidate_score(raw_team_name, slot_candidate.team_name),
                        )
                    ]
                    review_kind = "alias_suggestion"
                    confidence = "high"
                    canonical_home_team = slot_candidate.canonical_home_team
                    canonical_away_team = slot_candidate.canonical_away_team
                    evidence.extend(
                        [
                            f"Matched other team: {slot_candidate.counterpart_team}",
                            f"Canonical event: {slot_candidate.canonical_home_team} vs {slot_candidate.canonical_away_team}",
                            "Unique canonical event found at the same sport and kickoff",
                        ]
                    )
                elif slot_candidates:
                    ranked_candidates = _rank_team_review_candidates(
                        raw_team_name,
                        [
                            (candidate.team_id, candidate.team_name)
                            for candidate in slot_candidates.values()
                        ],
                        threshold=0.0,
                    )
                    review_kind = "candidate_search"
                    confidence = "medium"
                    evidence.extend(
                        [
                            f"Matched other team: {counterpart_resolution.team_name}",
                            "Multiple canonical events share that team at this exact kickoff",
                        ]
                    )

            if not ranked_candidates:
                ranked_candidates = _search_global_review_candidates(
                    raw_team_name,
                    sport=raw.sport,
                )
                if ranked_candidates:
                    confidence = (
                        "medium"
                        if ranked_candidates[0].score >= TEAM_REVIEW_CANDIDATE_THRESHOLD
                        else "low"
                    )
                    evidence.append("Top fuzzy matches across canonical teams in this sport")
                else:
                    evidence.append("No canonical team matched this label in the current database")

            suggested_candidate = ranked_candidates[0] if ranked_candidates else None
            review_key = (
                raw.bookmaker_id,
                raw.sport,
                normalize_identity_text(raw_team_name),
                raw.start_time,
                matched_counterpart_team or "",
            )
            if review_key in review_cases:
                continue

            review_cases[review_key] = TeamReviewDiagnostic(
                bookmaker_id=raw.bookmaker_id,
                raw_league_id=raw.league_id,
                normalized_raw_league_id=normalize_identity_text(raw.league_id),
                sport=raw.sport,
                scope_league_id=direct_league.league_id,
                raw_team_name=raw_team_name,
                normalized_raw_team_name=team_resolution.team_name,
                suggested_team_id=(
                    suggested_candidate.team_id if suggested_candidate is not None else None
                ),
                suggested_team_name=(
                    suggested_candidate.team_name
                    if suggested_candidate is not None
                    else None
                ),
                start_time=raw.start_time,
                review_kind=review_kind,
                reason_code=(
                    "candidate_team_match_same_start_time"
                    if matched_counterpart_team
                    else "candidate_team_search"
                ),
                confidence=confidence,
                similarity_score=(
                    suggested_candidate.score if suggested_candidate is not None else None
                ),
                candidate_teams=_to_review_candidates(ranked_candidates),
                matched_counterpart_team=matched_counterpart_team,
                canonical_home_team=canonical_home_team,
                canonical_away_team=canonical_away_team,
                evidence=evidence,
            )

    return list(review_cases.values())


def normalize_market_type(raw_type: str) -> str:
    key = raw_type.strip().lower().replace("&", "+").replace("+", " + ")
    key = " ".join(key.split())
    return _MARKET_TYPE_MAPPING.get(key, key)


def _is_unresolved_shared_platform_prop(raw: RawOddsData) -> bool:
    return bool(raw.player_name and raw.away_team.strip() == raw.player_name.strip())


def _format_matchup(matchup: tuple[str, str]) -> str:
    if isinstance(matchup, _CanonicalMatchup):
        return f"{matchup.home_team} vs {matchup.away_team}"
    return f"{matchup[0]} vs {matchup[1]}"


def _separate_missing_start_times(
    raw_list: list[RawOddsData],
) -> tuple[list[RawOddsData], list[UnresolvedOddsDiagnostic]]:
    timed_rows: list[RawOddsData] = []
    unresolved: list[UnresolvedOddsDiagnostic] = []

    for raw in raw_list:
        if raw.start_time:
            timed_rows.append(raw)
            continue
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        unresolved.append(
            UnresolvedOddsDiagnostic(
                bookmaker_id=raw.bookmaker_id,
                raw_league_id=raw.league_id,
                league_id=direct_league.league_id,
                sport=raw.sport,
                market_type=raw.market_type,
                player_name=raw.player_name,
                raw_team_name=f"{raw.home_team} vs {raw.away_team}",
                normalized_team_name=f"{raw.home_team.strip()} vs {raw.away_team.strip()}",
                start_time=None,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                reason_code="missing_start_time",
                candidate_count=0,
                candidate_matchups=[],
                available_matchups_same_slot=[],
            )
        )

    return timed_rows, unresolved


def _autocreate_exact_match_teams(raw_list: list[RawOddsData]) -> None:
    matchup_counts: Counter[tuple[str, str, tuple[str, str]]] = Counter()
    team_display_names: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for raw in raw_list:
        if _is_unresolved_shared_platform_prop(raw) or raw.start_time is None:
            continue
        home_key = normalize_identity_text(raw.home_team)
        away_key = normalize_identity_text(raw.away_team)
        if not home_key or not away_key or home_key == away_key:
            continue
        pair_key = (raw.sport, raw.start_time, tuple(sorted((home_key, away_key))))
        matchup_counts[pair_key] += 1
        team_display_names[(raw.sport, home_key)][raw.home_team.strip()] += 1
        team_display_names[(raw.sport, away_key)][raw.away_team.strip()] += 1

    for sport, _start_time, pair_keys in matchup_counts:
        if matchup_counts[(sport, _start_time, pair_keys)] < 2:
            continue
        for team_key in pair_keys:
            display_counter = team_display_names.get((sport, team_key), Counter())
            if not display_counter:
                continue
            display_name = max(
                display_counter.items(),
                key=lambda item: (item[1], len(item[0]), item[0]),
            )[0]
            if resolve_team_name(display_name, sport=sport).team_id is None:
                create_canonical_team(display_name=display_name, sport=sport)


def _build_canonical_matchups(
    raw_list: list[RawOddsData],
) -> dict[tuple[tuple[str, str], tuple[int, int]], _CanonicalMatchup]:
    counts: dict[
        tuple[tuple[str, str], tuple[int, int]],
        dict[tuple[int, int], int],
    ] = {}
    team_names: dict[int, str] = {}

    for raw in raw_list:
        if _is_unresolved_shared_platform_prop(raw) or raw.start_time is None:
            continue

        home_resolution = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        away_resolution = resolve_team_name(
            raw.away_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if (
            home_resolution.team_id is None
            or away_resolution.team_id is None
            or home_resolution.team_id == away_resolution.team_id
        ):
            continue

        slot = _event_slot_key(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        orientation = (home_resolution.team_id, away_resolution.team_id)
        counts.setdefault(slot, {})[orientation] = counts.setdefault(slot, {}).get(
            orientation,
            0,
        ) + 1
        team_names[home_resolution.team_id] = home_resolution.team_name
        team_names[away_resolution.team_id] = away_resolution.team_name

    canonical: dict[tuple[tuple[str, str], tuple[int, int]], _CanonicalMatchup] = {}
    for slot, orientations in counts.items():
        chosen_home_id, chosen_away_id = min(
            orientations.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        canonical[slot] = _CanonicalMatchup(
            home_team_id=chosen_home_id,
            away_team_id=chosen_away_id,
            home_team=team_names[chosen_home_id],
            away_team=team_names[chosen_away_id],
        )
    return canonical


def _build_inferred_shared_platform_matchups(
    raw_list: list[RawOddsData],
    matchups_by_slot: dict[tuple[str, str], list[_CanonicalMatchup]],
) -> dict[tuple[str, str], list[_CanonicalMatchup]]:
    teams_by_slot: dict[tuple[str, str], dict[int, str]] = defaultdict(dict)

    for raw in raw_list:
        if not _is_unresolved_shared_platform_prop(raw) or raw.start_time is None:
            continue

        known_team = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if known_team.team_id is None:
            continue
        slot = _event_identity_slot(raw.start_time, raw.sport)
        existing_matchups = matchups_by_slot.get(slot, [])
        if any(
            known_team.team_id in {matchup.home_team_id, matchup.away_team_id}
            for matchup in existing_matchups
        ):
            continue
        teams_by_slot[slot][known_team.team_id] = known_team.team_name

    inferred: dict[tuple[str, str], list[_CanonicalMatchup]] = defaultdict(list)
    for slot, teams in teams_by_slot.items():
        if len(teams) != 2:
            continue
        ordered = sorted(teams.items(), key=lambda item: item[1])
        inferred[slot].append(
            _CanonicalMatchup(
                home_team_id=ordered[0][0],
                away_team_id=ordered[1][0],
                home_team=ordered[0][1],
                away_team=ordered[1][1],
            )
        )

    return dict(inferred)


def _resolve_shared_platform_matchups(
    raw_list: list[RawOddsData],
) -> tuple[list[RawOddsData], list[UnresolvedOddsDiagnostic]]:
    canonical_matchups = _build_canonical_matchups(raw_list)
    matchups_by_slot: dict[tuple[str, str], list[_CanonicalMatchup]] = {}
    for (slot, _matchup_key), matchup in canonical_matchups.items():
        matchups_by_slot.setdefault(slot, []).append(matchup)
    for slot, inferred_matchups in _build_inferred_shared_platform_matchups(
        raw_list, matchups_by_slot
    ).items():
        matchups_by_slot.setdefault(slot, []).extend(inferred_matchups)

    resolved: list[RawOddsData] = []
    unresolved: list[UnresolvedOddsDiagnostic] = []

    for raw in raw_list:
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)

        if not _is_unresolved_shared_platform_prop(raw):
            home_resolution = resolve_team_name(
                raw.home_team,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
            )
            away_resolution = resolve_team_name(
                raw.away_team,
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
            )
            resolved.append(raw)
            canonical = None
            if (
                raw.start_time is not None
                and home_resolution.team_id is not None
                and away_resolution.team_id is not None
            ):
                canonical = canonical_matchups.get(
                    _event_slot_key(
                        home_resolution.team_id,
                        away_resolution.team_id,
                        raw.start_time,
                        raw.sport,
                    )
                )
            if canonical:
                resolved[-1] = RawOddsData(
                    bookmaker_id=raw.bookmaker_id,
                    league_id=raw.league_id,
                    sport=raw.sport,
                    home_team=canonical.home_team,
                    away_team=canonical.away_team,
                    market_type=raw.market_type,
                    player_name=raw.player_name,
                    threshold=raw.threshold,
                    over_odds=raw.over_odds,
                    under_odds=raw.under_odds,
                    start_time=raw.start_time,
                )
            continue

        known_team = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        slot = _event_identity_slot(raw.start_time, raw.sport)
        candidates = [
            matchup
            for matchup in matchups_by_slot.get(slot, [])
            if known_team.team_id is not None
            and known_team.team_id in {matchup.home_team_id, matchup.away_team_id}
        ]

        if len(candidates) != 1:
            reason_code = (
                "no_canonical_matchup_for_team_at_slot"
                if len(candidates) == 0
                else "ambiguous_multiple_matchups_for_team_at_slot"
            )
            unresolved.append(
                UnresolvedOddsDiagnostic(
                    bookmaker_id=raw.bookmaker_id,
                    raw_league_id=raw.league_id,
                    league_id=direct_league.league_id,
                    sport=raw.sport,
                    market_type=raw.market_type,
                    player_name=raw.player_name,
                    raw_team_name=raw.home_team,
                    normalized_team_name=known_team.team_name,
                    start_time=raw.start_time,
                    threshold=raw.threshold,
                    over_odds=raw.over_odds,
                    under_odds=raw.under_odds,
                    reason_code=reason_code,
                    candidate_count=len(candidates),
                    candidate_matchups=[_format_matchup(matchup) for matchup in candidates[:8]],
                    available_matchups_same_slot=[
                        _format_matchup(matchup)
                        for matchup in matchups_by_slot.get(slot, [])[:12]
                    ],
                )
            )
            logger.warning(
                "Dropping unresolved shared-platform prop for %s (%s, %s, %s)",
                raw.player_name,
                raw.bookmaker_id,
                known_team.team_name,
                reason_code,
            )
            continue

        selected = candidates[0]
        resolved.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                sport=raw.sport,
                home_team=selected.home_team,
                away_team=selected.away_team,
                market_type=raw.market_type,
                player_name=raw.player_name,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )

    return resolved, unresolved


def normalize_odds_with_diagnostics(
    raw_list: list[RawOddsData],
) -> tuple[
    list[NormalizedOdds],
    list[UnresolvedOddsDiagnostic],
    list[TeamReviewDiagnostic],
]:
    results: list[NormalizedOdds] = []
    timed_raw_list, missing_start_time = _separate_missing_start_times(raw_list)
    _autocreate_exact_match_teams(timed_raw_list)
    resolved_shared_platform, unresolved_shared_platform = _resolve_shared_platform_matchups(
        timed_raw_list
    )
    resolved_raw_list = _resolve_contextual_player_names(resolved_shared_platform)
    slot_resolutions = _build_event_slot_resolutions(resolved_raw_list)

    for raw in resolved_raw_list:
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        slot_home = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        slot_away = resolve_team_name(
            raw.away_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if (
            raw.start_time is None
            or slot_home.team_id is None
            or slot_away.team_id is None
        ):
            continue
        slot = _event_slot_key(
            slot_home.team_id,
            slot_away.team_id,
            raw.start_time,
            raw.sport,
        )
        slot_resolution = slot_resolutions.get(slot)
        if slot_resolution is None:
            continue

        match_id = generate_match_id(
            slot_resolution.home_team_id,
            slot_resolution.away_team_id,
            raw.start_time,
            raw.sport,
        )
        player = normalize_player_name(raw.player_name)
        market = normalize_market_type(raw.market_type)

        results.append(
            NormalizedOdds(
                match_id=match_id,
                bookmaker_id=raw.bookmaker_id,
                league_id=slot_resolution.league_id or direct_league.league_id,
                sport=raw.sport,
                home_team_id=slot_resolution.home_team_id,
                away_team_id=slot_resolution.away_team_id,
                home_team=slot_resolution.home_team,
                away_team=slot_resolution.away_team,
                market_type=market,
                player_name=player,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )

    team_review_cases = _build_team_review_cases(resolved_raw_list, slot_resolutions)
    return (
        results,
        [*missing_start_time, *unresolved_shared_platform],
        team_review_cases,
    )


def normalize_odds_with_issues(
    raw_list: list[RawOddsData],
) -> tuple[list[NormalizedOdds], list[UnresolvedOddsDiagnostic]]:
    normalized, unresolved, _ = normalize_odds_with_diagnostics(raw_list)
    return normalized, unresolved


def normalize_odds(raw_list: list[RawOddsData]) -> list[NormalizedOdds]:
    return normalize_odds_with_diagnostics(raw_list)[0]
