from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import logging

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
from .team_registry import create_canonical_team, remember_team_alias
from .text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_FOOTBALL_AUTO_MATCH_AVG_THRESHOLD = 78
_FOOTBALL_AUTO_MATCH_SIDE_THRESHOLD = 70
_FOOTBALL_AUTO_MATCH_MARGIN = 8
_LOW_SIGNAL_TEAM_TOKENS = {"bc", "bk", "kk", "fc", "fk", "club", "team", "sc", "cf", "cd", "ce"}


@dataclass(frozen=True)
class _OutcomeEvent:
    bookmaker_id: str
    sport: str
    start_time: str
    home_team: str
    away_team: str


@dataclass(frozen=True)
class _OutcomeEventPair:
    left: _OutcomeEvent
    right: _OutcomeEvent
    home_score: float
    away_score: float

    @property
    def score(self) -> float:
        return (self.home_score + self.away_score) / 2


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


def _display_name_for_aliases(*names: str) -> str:
    return max(
        (name.strip() for name in names if name.strip()),
        key=lambda value: (len(_significant_tokens(value)), len(value), value),
    )


def _remember_auto_alias(bookmaker_id: str, raw_team_name: str, canonical_name: str, sport: str) -> None:
    try:
        remember_team_alias(
            bookmaker_id=bookmaker_id,
            raw_team_name=raw_team_name,
            team_name=canonical_name,
            sport=sport,
            source="auto_review",
        )
    except ValueError:
        logger.warning(
            "Skipping football auto alias %s -> %s for bookmaker %s",
            raw_team_name,
            canonical_name,
            bookmaker_id,
            exc_info=True,
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
    home_score = _team_similarity(left.home_team, right.home_team)
    away_score = _team_similarity(left.away_team, right.away_team)
    if home_score < _FOOTBALL_AUTO_MATCH_SIDE_THRESHOLD or away_score < _FOOTBALL_AUTO_MATCH_SIDE_THRESHOLD:
        return None
    pair = _OutcomeEventPair(
        left=left,
        right=right,
        home_score=home_score,
        away_score=away_score,
    )
    if pair.score < _FOOTBALL_AUTO_MATCH_AVG_THRESHOLD:
        return None
    return pair


def _autocreate_confident_cross_book_football_aliases(raw_list: list[RawOutcomeOffer]) -> None:
    events_by_slot: dict[tuple[str, str], list[_OutcomeEvent]] = defaultdict(list)
    for event in _unique_events(raw_list):
        events_by_slot[(event.sport, event.start_time)].append(event)

    for events in events_by_slot.values():
        candidates_by_event: dict[_OutcomeEvent, list[_OutcomeEventPair]] = defaultdict(list)
        for idx, left in enumerate(events):
            for right in events[idx + 1 :]:
                pair = _pair_candidates(left, right)
                if pair is None:
                    continue
                candidates_by_event[left].append(pair)
                candidates_by_event[right].append(pair)

        accepted: list[_OutcomeEventPair] = []
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

        for pair in accepted:
            home_name = _display_name_for_aliases(pair.left.home_team, pair.right.home_team)
            away_name = _display_name_for_aliases(pair.left.away_team, pair.right.away_team)
            _remember_auto_alias(pair.left.bookmaker_id, pair.left.home_team, home_name, pair.left.sport)
            _remember_auto_alias(pair.right.bookmaker_id, pair.right.home_team, home_name, pair.right.sport)
            _remember_auto_alias(pair.left.bookmaker_id, pair.left.away_team, away_name, pair.left.sport)
            _remember_auto_alias(pair.right.bookmaker_id, pair.right.away_team, away_name, pair.right.sport)


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


def normalize_outcome_offers_with_diagnostics(
    raw_list: list[RawOutcomeOffer],
) -> tuple[
    list[NormalizedOutcomeOffer],
    list[UnresolvedOddsDiagnostic],
    list[TeamReviewDiagnostic],
]:
    _autocreate_cross_book_football_teams(raw_list)
    _autocreate_confident_cross_book_football_aliases(raw_list)
    _, _, team_review_cases = normalize_odds_with_diagnostics(
        _team_review_proxy_rows(raw_list),
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
