from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Protocol

from .event_matching import (
    EventCandidate,
    _OrientationScore,
    _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE,
    _expand_dotted_token,
)
from ..team_identity import (
    REASON_QUALIFIER_MISMATCH,
    comparison_team_text as _comparison_team_text,
    event_similarity_score_from_parts as _event_similarity_score_from_parts,
    match_unification_significant_tokens as _identity_significant_tokens,
    team_qualifiers as _team_qualifiers,
)
from ..tennis_name_matcher import (
    TENNIS_BROAD_DRIFT_MINUTES,
    tennis_competitor_pair_matches,
)

_HIGH_FUZZY_AVG_SCORE = 85.0
_HIGH_FUZZY_SIDE_SCORE = 75.0
_HIGH_FUZZY_AVG_SCORE_NON_SUBSET = 90.0
_HIGH_FUZZY_SIDE_SCORE_NON_SUBSET = 82.0
_REVIEW_FUZZY_AVG_SCORE = 65.0
_FUZZY_ORIENTATION_MARGIN = 8.0

# Anchored low-confidence merge: applies only when the two groups are at the
# exact same (sport, start_time) slot AND a non-fuzzy corroborator is present
# (token subset on the weak side, or shared significant token + same league).
# Combined bookmaker count >= _ANCHORED_MIN_BOOKMAKERS guards against the
# 2-bookmaker false-positive cases preserved by the South/North Korea and
# Austria/Australia regression tests.
_ANCHORED_FUZZY_AVG_SCORE = 70.0
_ANCHORED_FUZZY_SIDE_SCORE = 50.0
_ANCHORED_MIN_BOOKMAKERS = 3
_CANONICAL_SIDE_ANCHOR_AVG_SCORE = 65.0
_CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE = 45.0
_CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS = 3
_CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS_NON_TARGETED = 5

# Same-bookmaker conflict resolution by quorum: if one exact group dwarfs the
# other in distinct bookmaker count, fold the smaller group into the larger
# despite a same-bookmaker overlap. Uses the immutable per-group bookmaker
# sets, never the (mutable) DSU root sizes, so the decision is order-independent.
_QUORUM_FUZZY_AVG_SCORE = 80.0
_QUORUM_FUZZY_SIDE_SCORE = 60.0
_QUORUM_CANONICAL_SIDE_ANCHOR_AVG_SCORE = 70.0
_QUORUM_CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE = 45.0
_QUORUM_MIN_LARGER_BOOKMAKERS = 5
_QUORUM_MIN_BOOKMAKER_DIFFERENCE = 3

REASON_HIGH_CONFIDENCE_FUZZY_EVENT_MATCH = "high_confidence_fuzzy_event_match"
REASON_AMBIGUOUS_EVENT_ORIENTATION = "ambiguous_event_orientation"
REASON_POSSIBLE_EVENT_EQUIVALENCE_LOW_CONFIDENCE = (
    "possible_event_equivalence_low_confidence"
)
REASON_CONFLICTING_SAME_BOOKMAKER_EVENT_CANDIDATE = (
    "conflicting_same_bookmaker_event_candidate"
)


class EventMergeDecisionCategory(str, Enum):
    HIGH_CONFIDENCE_FUZZY_MERGE = "high_confidence_fuzzy_merge"
    ANCHORED_MERGE = "anchored_merge"
    AMBIGUOUS_ORIENTATION_REVIEW = "ambiguous_orientation_review"
    LOW_CONFIDENCE_REVIEW = "low_confidence_review"
    QUALIFIER_CONFLICT = "qualifier_conflict"
    BELOW_REVIEW_THRESHOLD = "below_review_threshold"
    SAME_BOOKMAKER_CONFLICT = "same_bookmaker_conflict"
    QUORUM_OVERRIDE = "quorum_override"


class EventDiagnosticDecisionCategory(str, Enum):
    SPLIT_DIAGNOSTIC = "split_diagnostic"
    OVERMERGE_DIAGNOSTIC = "overmerge_diagnostic"


class _FuzzyScoreStats(Protocol):
    fuzzy_score_count: int


class _CandidateGroupLike(Protocol):
    candidates: tuple[EventCandidate, ...]

    @property
    def bookmakers(self) -> set[str]: ...


@dataclass(frozen=True)
class _PairResolution:
    confidence: float
    score: float
    weak_side_score: float
    orientation: str
    reason_code: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class EventPairEvidence:
    left: EventCandidate
    right: EventCandidate
    orientation: str | None
    score: float
    weak_side_score: float
    home_score: float
    away_score: float
    second_best_score: float | None
    time_evidence: str
    combined_bookmaker_count: int
    weak_side_subset_or_equal: bool = False
    anchored_detail: str | None = None
    identity_reasons: frozenset[str] = frozenset()
    is_tennis: bool = False

    @property
    def confidence(self) -> float:
        return self.score / 100

    @property
    def has_compatible_orientation(self) -> bool:
        return self.orientation is not None


@dataclass(frozen=True)
class EventMergeDecision:
    category: EventMergeDecisionCategory
    pair: _PairResolution | None
    evidence: EventPairEvidence | None = None
    causes_union: bool = False
    emits_review_case: bool = False
    diagnostic_only: bool = False
    ignored: bool = False
    requires_group_context: bool = False
    quorum_override: bool = False

    @property
    def score(self) -> float:
        if self.pair is not None:
            return self.pair.score
        if self.evidence is not None:
            return self.evidence.score
        return 0.0

    @property
    def reason_code(self) -> str | None:
        return self.pair.reason_code if self.pair is not None else None


@dataclass(frozen=True)
class EventDiagnosticDecision:
    category: EventDiagnosticDecisionCategory
    reason_code: str
    score: float
    weak_side_score: float
    orientation: str
    shared_side: str | None = None
    max_start_delta_minutes: float = 0.0
    diagnostic_only: bool = True
    causes_union: bool = False
    emits_review_case: bool = False


def _parse_event_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_time_delta_minutes(left: str, right: str) -> float | None:
    if left == right:
        return 0.0
    left_dt = _parse_event_time(left)
    right_dt = _parse_event_time(right)
    if left_dt is None or right_dt is None:
        return None
    try:
        return abs((left_dt - right_dt).total_seconds()) / 60
    except TypeError:
        return None


def _significant_team_tokens(team_name: str, *, sport: str | None = None) -> set[str]:
    return _identity_significant_tokens(team_name, sport=sport)


class _ResolverTextCache:
    def __init__(self, stats: _FuzzyScoreStats | None = None) -> None:
        self._stats = stats
        self._qualifiers: dict[tuple[str, str | None], set[str]] = {}
        self._comparison_text: dict[tuple[str, str | None], str] = {}
        self._significant_tokens: dict[tuple[str, str | None], set[str]] = {}
        self._expanded_tokens: dict[tuple[str, str, str | None], str] = {}
        self._team_similarity: dict[tuple[str, str, str | None], float] = {}
        self._orientation_scores: dict[
            tuple[str, str, str, str, str | None],
            tuple[_OrientationScore, ...],
        ] = {}

    def qualifiers(self, name: str, *, sport: str | None = None) -> set[str]:
        key = (name, sport)
        cached = self._qualifiers.get(key)
        if cached is None:
            cached = _team_qualifiers(name, sport=sport)
            self._qualifiers[key] = cached
        return cached

    def comparison_text(self, name: str, *, sport: str | None = None) -> str:
        key = (name, sport)
        cached = self._comparison_text.get(key)
        if cached is None:
            cached = _comparison_team_text(name, sport=sport)
            self._comparison_text[key] = cached
        return cached

    def significant_tokens(self, name: str, *, sport: str | None = None) -> set[str]:
        key = (name, sport)
        cached = self._significant_tokens.get(key)
        if cached is None:
            cached = _significant_team_tokens(name, sport=sport)
            self._significant_tokens[key] = cached
        return cached

    def same_context(self, left: str, right: str, *, sport: str | None = None) -> bool:
        return self.qualifiers(left, sport=sport) == self.qualifiers(right, sport=sport)

    def expanded_token(
        self,
        name: str,
        counterpart: str,
        *,
        sport: str | None = None,
    ) -> str:
        key = (name, counterpart, sport)
        cached = self._expanded_tokens.get(key)
        if cached is None:
            cached = (
                _expand_dotted_token(name, counterpart)
                if sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE
                else name
            )
            self._expanded_tokens[key] = cached
        return cached

    def team_similarity(self, left: str, right: str, *, sport: str | None = None) -> float:
        expanded_left = self.expanded_token(left, right, sport=sport)
        expanded_right = self.expanded_token(right, left, sport=sport)
        key = (expanded_left, expanded_right, sport)
        reverse_key = (expanded_right, expanded_left, sport)
        cached = self._team_similarity.get(key)
        if cached is None:
            cached = self._team_similarity.get(reverse_key)
        if cached is not None:
            return cached

        score_result = _event_similarity_score_from_parts(
            self.comparison_text(expanded_left, sport=sport),
            self.comparison_text(expanded_right, sport=sport),
            self.significant_tokens(expanded_left, sport=sport),
            self.significant_tokens(expanded_right, sport=sport),
        )
        if self._stats is not None and score_result.used_fuzzy_score:
            self._stats.fuzzy_score_count += 1
        score = score_result.score
        self._team_similarity[key] = score
        return score

    def orientation_scores(
        self,
        left_home: str,
        left_away: str,
        right_home: str,
        right_away: str,
        *,
        sport: str | None = None,
    ) -> list[_OrientationScore]:
        key = (left_home, left_away, right_home, right_away, sport)
        cached = self._orientation_scores.get(key)
        if cached is not None:
            return list(cached)

        scores: list[_OrientationScore] = []
        if self.same_context(left_home, right_home, sport=sport) and self.same_context(
            left_away,
            right_away,
            sport=sport,
        ):
            scores.append(
                _OrientationScore(
                    orientation="as_listed",
                    home_score=self.team_similarity(left_home, right_home, sport=sport),
                    away_score=self.team_similarity(left_away, right_away, sport=sport),
                )
            )
        if self.same_context(left_home, right_away, sport=sport) and self.same_context(
            left_away,
            right_home,
            sport=sport,
        ):
            scores.append(
                _OrientationScore(
                    orientation="reversed",
                    home_score=self.team_similarity(left_home, right_away, sport=sport),
                    away_score=self.team_similarity(left_away, right_home, sport=sport),
                )
            )
        scores = sorted(scores, key=lambda score: score.avg_score, reverse=True)
        self._orientation_scores[key] = tuple(scores)
        return scores


def _is_subset_or_equal_token_pair(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
    text_cache: _ResolverTextCache | None = None,
) -> bool:
    if text_cache is not None:
        left_tokens = text_cache.significant_tokens(left_name, sport=sport)
        right_tokens = text_cache.significant_tokens(right_name, sport=sport)
    else:
        left_tokens = _significant_team_tokens(left_name, sport=sport)
        right_tokens = _significant_team_tokens(right_name, sport=sport)
    if not left_tokens or not right_tokens:
        return False
    return left_tokens <= right_tokens or right_tokens <= left_tokens


def _weak_side_pair_is_subset_or_equal(
    left_home: str,
    left_away: str,
    right_home: str,
    right_away: str,
    score: _OrientationScore,
    *,
    sport: str | None = None,
    text_cache: _ResolverTextCache | None = None,
) -> bool:
    if score.orientation == "as_listed":
        home_pair = (left_home, right_home)
        away_pair = (left_away, right_away)
    else:
        home_pair = (left_home, right_away)
        away_pair = (left_away, right_home)
    weak_pair = home_pair if score.home_score <= score.away_score else away_pair
    return _is_subset_or_equal_token_pair(*weak_pair, sport=sport, text_cache=text_cache)


def _matching_canonical_sides(
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    orientation: str,
) -> set[str]:
    if orientation == "as_listed":
        home_pair = (left_candidate.home_team_id, right_candidate.home_team_id)
        away_pair = (left_candidate.away_team_id, right_candidate.away_team_id)
    else:
        home_pair = (left_candidate.home_team_id, right_candidate.away_team_id)
        away_pair = (left_candidate.away_team_id, right_candidate.home_team_id)

    matching: set[str] = set()
    if home_pair[0] is not None and home_pair[0] == home_pair[1]:
        matching.add("home")
    if away_pair[0] is not None and away_pair[0] == away_pair[1]:
        matching.add("away")
    return matching


def _shared_significant_tokens(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
    text_cache: _ResolverTextCache | None = None,
) -> set[str]:
    if text_cache is not None:
        return text_cache.significant_tokens(
            left_name, sport=sport
        ) & text_cache.significant_tokens(right_name, sport=sport)
    return _significant_team_tokens(left_name, sport=sport) & _significant_team_tokens(
        right_name,
        sport=sport,
    )


def _anchored_low_conf_detail(
    *,
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    top: _OrientationScore,
    combined_bookmaker_count: int,
    text_cache: _ResolverTextCache | None = None,
) -> str | None:
    """Lower-threshold corroborated merge for same-slot pairs.

    The subset/equal branch is cross-sport so explicit women-marker variants
    can merge outside basketball. The looser shared-token + same-league branch
    remains restricted to the historically tuned aggressive sports because it
    can otherwise merge common false positives such as ``Manchester United`` ↔
    ``Manchester City``.
    """

    if combined_bookmaker_count < _ANCHORED_MIN_BOOKMAKERS:
        return None

    matching_canonical_sides = _matching_canonical_sides(
        left_candidate,
        right_candidate,
        top.orientation,
    )
    if matching_canonical_sides:
        min_bookmakers = (
            _CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS
            if left_candidate.sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE
            and right_candidate.sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE
            else _CANONICAL_SIDE_ANCHOR_MIN_BOOKMAKERS_NON_TARGETED
        )
        if (
            combined_bookmaker_count >= min_bookmakers
            and top.avg_score >= _CANONICAL_SIDE_ANCHOR_AVG_SCORE
            and top.weak_side_score >= _CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE
        ):
            return "canonical side anchored"

    if top.avg_score < _ANCHORED_FUZZY_AVG_SCORE:
        return None
    if top.weak_side_score < _ANCHORED_FUZZY_SIDE_SCORE:
        return None

    if top.orientation == "as_listed":
        home_pair = (left_candidate.home_team, right_candidate.home_team)
        away_pair = (left_candidate.away_team, right_candidate.away_team)
    else:
        home_pair = (left_candidate.home_team, right_candidate.away_team)
        away_pair = (left_candidate.away_team, right_candidate.home_team)
    weak_pair = home_pair if top.home_score <= top.away_score else away_pair

    if _is_subset_or_equal_token_pair(
        *weak_pair,
        sport=left_candidate.sport,
        text_cache=text_cache,
    ):
        return "token subset anchored"

    if left_candidate.sport not in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        return None
    if right_candidate.sport not in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        return None

    if not _shared_significant_tokens(
        *weak_pair,
        sport=left_candidate.sport,
        text_cache=text_cache,
    ):
        return None

    left_league = left_candidate.source_league_id
    right_league = right_candidate.source_league_id
    if left_league and right_league and left_league == right_league:
        return "league anchored"
    return None


def _passes_anchored_low_conf(
    *,
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    top: _OrientationScore,
    combined_bookmaker_count: int,
    text_cache: _ResolverTextCache | None = None,
) -> bool:
    return (
        _anchored_low_conf_detail(
            left_candidate=left_candidate,
            right_candidate=right_candidate,
            top=top,
            combined_bookmaker_count=combined_bookmaker_count,
            text_cache=text_cache,
        )
        is not None
    )


def _qualifier_conflict_evidence(
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    *,
    text_cache: _ResolverTextCache,
) -> EventPairEvidence:
    return EventPairEvidence(
        left=left_candidate,
        right=right_candidate,
        orientation=None,
        score=0.0,
        weak_side_score=0.0,
        home_score=0.0,
        away_score=0.0,
        second_best_score=None,
        time_evidence=f"Exact start time: {left_candidate.start_time}",
        combined_bookmaker_count=2,
        identity_reasons=frozenset({REASON_QUALIFIER_MISMATCH}),
    )


def _score_tennis_candidate_pair(
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
) -> EventPairEvidence | None:
    delta = _event_time_delta_minutes(
        left_candidate.start_time,
        right_candidate.start_time,
    )
    if delta is None or delta > TENNIS_BROAD_DRIFT_MINUTES:
        return None
    tennis_matches = [
        match
        for match in tennis_competitor_pair_matches(
            left_candidate.home_team,
            left_candidate.away_team,
            right_candidate.home_team,
            right_candidate.away_team,
        )
        if delta <= match.max_time_delta_minutes
    ]
    if not tennis_matches:
        return None
    top_match = tennis_matches[0]
    time_evidence = (
        f"Exact start time: {left_candidate.start_time}"
        if delta == 0
        else (
            "Tennis start-time drift: "
            f"{delta:.1f} minutes "
            f"({left_candidate.start_time} vs {right_candidate.start_time})"
        )
    )
    return EventPairEvidence(
        left=left_candidate,
        right=right_candidate,
        orientation=top_match.orientation,
        score=top_match.avg_score,
        weak_side_score=top_match.weak_side_score,
        home_score=top_match.home_score,
        away_score=top_match.away_score,
        second_best_score=tennis_matches[1].avg_score if len(tennis_matches) > 1 else None,
        time_evidence=time_evidence,
        combined_bookmaker_count=2,
        is_tennis=True,
    )


def score_event_candidate_pair(
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    *,
    combined_bookmaker_count: int = 2,
    text_cache: _ResolverTextCache | None = None,
) -> EventPairEvidence | None:
    if left_candidate.sport != right_candidate.sport:
        return None
    if left_candidate.sport == "tennis" and right_candidate.sport == "tennis":
        return _score_tennis_candidate_pair(left_candidate, right_candidate)
    if left_candidate.start_time != right_candidate.start_time:
        return None

    text_cache = text_cache or _ResolverTextCache()
    scores = text_cache.orientation_scores(
        left_candidate.home_team,
        left_candidate.away_team,
        right_candidate.home_team,
        right_candidate.away_team,
        sport=left_candidate.sport,
    )
    if not scores:
        return _qualifier_conflict_evidence(
            left_candidate,
            right_candidate,
            text_cache=text_cache,
        )

    top = scores[0]
    return EventPairEvidence(
        left=left_candidate,
        right=right_candidate,
        orientation=top.orientation,
        score=top.avg_score,
        weak_side_score=top.weak_side_score,
        home_score=top.home_score,
        away_score=top.away_score,
        second_best_score=scores[1].avg_score if len(scores) > 1 else None,
        time_evidence=f"Exact start time: {left_candidate.start_time}",
        combined_bookmaker_count=combined_bookmaker_count,
        weak_side_subset_or_equal=_weak_side_pair_is_subset_or_equal(
            left_candidate.home_team,
            left_candidate.away_team,
            right_candidate.home_team,
            right_candidate.away_team,
            top,
            sport=left_candidate.sport,
            text_cache=text_cache,
        ),
        anchored_detail=_anchored_low_conf_detail(
            left_candidate=left_candidate,
            right_candidate=right_candidate,
            top=top,
            combined_bookmaker_count=combined_bookmaker_count,
            text_cache=text_cache,
        ),
    )


def classify_event_pair_evidence(evidence: EventPairEvidence) -> EventMergeDecision:
    if not evidence.has_compatible_orientation:
        return EventMergeDecision(
            category=EventMergeDecisionCategory.QUALIFIER_CONFLICT,
            pair=None,
            evidence=evidence,
            ignored=True,
        )

    second_score = evidence.second_best_score
    if (
        second_score is not None
        and evidence.score >= _REVIEW_FUZZY_AVG_SCORE
        and evidence.score - second_score < _FUZZY_ORIENTATION_MARGIN
    ):
        reason = (
            "Multiple tennis player orientations scored too closely for automatic grouping"
            if evidence.is_tennis
            else "Multiple team orientations scored too closely for automatic grouping"
        )
        return EventMergeDecision(
            category=EventMergeDecisionCategory.AMBIGUOUS_ORIENTATION_REVIEW,
            pair=_PairResolution(
                confidence=evidence.confidence,
                score=evidence.score,
                weak_side_score=evidence.weak_side_score,
                orientation=evidence.orientation,
                reason_code=REASON_AMBIGUOUS_EVENT_ORIENTATION,
                evidence=(evidence.time_evidence, reason),
            ),
            evidence=evidence,
            emits_review_case=True,
        )

    if evidence.is_tennis:
        return EventMergeDecision(
            category=EventMergeDecisionCategory.HIGH_CONFIDENCE_FUZZY_MERGE,
            pair=_PairResolution(
                confidence=evidence.confidence,
                score=evidence.score,
                weak_side_score=evidence.weak_side_score,
                orientation=evidence.orientation,
                reason_code=REASON_HIGH_CONFIDENCE_FUZZY_EVENT_MATCH,
                evidence=(
                    evidence.time_evidence,
                    (
                        "High-confidence cross-bookmaker tennis players: "
                        f"{evidence.left.home_team} vs {evidence.left.away_team} ↔ "
                        f"{evidence.right.home_team} vs {evidence.right.away_team} "
                        f"({evidence.orientation}, score {evidence.score:.1f})"
                    ),
                ),
            ),
            evidence=evidence,
            requires_group_context=True,
        )

    if (
        evidence.weak_side_subset_or_equal
        and evidence.score >= _HIGH_FUZZY_AVG_SCORE
        and evidence.weak_side_score >= _HIGH_FUZZY_SIDE_SCORE
    ) or (
        evidence.score >= _HIGH_FUZZY_AVG_SCORE_NON_SUBSET
        and evidence.weak_side_score >= _HIGH_FUZZY_SIDE_SCORE_NON_SUBSET
    ):
        return EventMergeDecision(
            category=EventMergeDecisionCategory.HIGH_CONFIDENCE_FUZZY_MERGE,
            pair=_PairResolution(
                confidence=evidence.confidence,
                score=evidence.score,
                weak_side_score=evidence.weak_side_score,
                orientation=evidence.orientation,
                reason_code=REASON_HIGH_CONFIDENCE_FUZZY_EVENT_MATCH,
                evidence=(
                    evidence.time_evidence,
                    (
                        "High-confidence cross-bookmaker fuzzy teams: "
                        f"{evidence.left.home_team} vs {evidence.left.away_team} ↔ "
                        f"{evidence.right.home_team} vs {evidence.right.away_team} "
                        f"({evidence.orientation}, score {evidence.score:.1f})"
                    ),
                ),
            ),
            evidence=evidence,
            requires_group_context=True,
        )

    if evidence.anchored_detail is not None:
        return EventMergeDecision(
            category=EventMergeDecisionCategory.ANCHORED_MERGE,
            pair=_PairResolution(
                confidence=evidence.confidence,
                score=evidence.score,
                weak_side_score=evidence.weak_side_score,
                orientation=evidence.orientation,
                reason_code=REASON_HIGH_CONFIDENCE_FUZZY_EVENT_MATCH,
                evidence=(
                    evidence.time_evidence,
                    (
                        "Anchored low-confidence cross-bookmaker fuzzy teams: "
                        f"{evidence.left.home_team} vs {evidence.left.away_team} ↔ "
                        f"{evidence.right.home_team} vs {evidence.right.away_team} "
                        f"({evidence.orientation}, score {evidence.score:.1f}, "
                        f"weak {evidence.weak_side_score:.1f}, "
                        f"{evidence.combined_bookmaker_count} bookmakers, "
                        f"{evidence.anchored_detail})"
                    ),
                ),
            ),
            evidence=evidence,
            requires_group_context=True,
        )

    if evidence.score >= _REVIEW_FUZZY_AVG_SCORE:
        return EventMergeDecision(
            category=EventMergeDecisionCategory.LOW_CONFIDENCE_REVIEW,
            pair=_PairResolution(
                confidence=evidence.confidence,
                score=evidence.score,
                weak_side_score=evidence.weak_side_score,
                orientation=evidence.orientation,
                reason_code=REASON_POSSIBLE_EVENT_EQUIVALENCE_LOW_CONFIDENCE,
                evidence=(
                    evidence.time_evidence,
                    (
                        "Potential cross-bookmaker event match below auto threshold: "
                        f"{evidence.left.home_team} vs {evidence.left.away_team} ↔ "
                        f"{evidence.right.home_team} vs {evidence.right.away_team} "
                        f"({evidence.orientation}, score {evidence.score:.1f})"
                    ),
                ),
            ),
            evidence=evidence,
            emits_review_case=True,
        )

    return EventMergeDecision(
        category=EventMergeDecisionCategory.BELOW_REVIEW_THRESHOLD,
        pair=None,
        evidence=evidence,
        ignored=True,
    )


def classify_event_candidate_pair(
    left_candidate: EventCandidate,
    right_candidate: EventCandidate,
    *,
    combined_bookmaker_count: int = 2,
    text_cache: _ResolverTextCache | None = None,
) -> EventMergeDecision | None:
    evidence = score_event_candidate_pair(
        left_candidate,
        right_candidate,
        combined_bookmaker_count=combined_bookmaker_count,
        text_cache=text_cache,
    )
    if evidence is None:
        return None
    decision = classify_event_pair_evidence(evidence)
    return decision


def best_group_pair_decision(
    left: _CandidateGroupLike,
    right: _CandidateGroupLike,
    *,
    text_cache: _ResolverTextCache | None = None,
) -> EventMergeDecision | None:
    text_cache = text_cache or _ResolverTextCache()
    best: EventMergeDecision | None = None
    combined_bookmaker_count = len(left.bookmakers | right.bookmakers)
    for left_candidate in left.candidates:
        for right_candidate in right.candidates:
            decision = classify_event_candidate_pair(
                left_candidate,
                right_candidate,
                combined_bookmaker_count=combined_bookmaker_count,
                text_cache=text_cache,
            )
            if decision is None or decision.ignored or decision.pair is None:
                continue
            if best is None or decision.score > best.score:
                best = decision
    return best


def _quorum_resolution_passes(
    left: _CandidateGroupLike,
    right: _CandidateGroupLike,
    pair: _PairResolution,
) -> bool:
    """Same-bookmaker conflict override using immutable per-group bookmaker sets.

    Restricted to basketball (``_TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE``):
    football has its own outcome_normalizer pairing flow with stricter
    handling, and the quorum thresholds were tuned for the basketball
    Heidelberg-style fragmentations the user reported.

    The DSU root sizes mutate during the pair loop, so basing this decision on
    them would make the outcome order-dependent. Reading ``left.bookmakers``
    and ``right.bookmakers`` (the original exact-group sets) keeps the override
    deterministic.

    Required:

    * ``pair.score >= _QUORUM_FUZZY_AVG_SCORE`` and
      ``pair.weak_side_score >= _QUORUM_FUZZY_SIDE_SCORE`` (high-confidence
      fuzzy match already established).
    * Larger group has ``>= _QUORUM_MIN_LARGER_BOOKMAKERS`` bookmakers.
    * Larger group exceeds the smaller by ``>= _QUORUM_MIN_BOOKMAKER_DIFFERENCE``
      bookmakers (4 vs 4 would not qualify; 9 vs 2 does).
    """

    representative = next(iter(left.candidates), None)
    if representative is None:
        return False
    if representative.sport not in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        return False

    passes_standard_threshold = (
        pair.score >= _QUORUM_FUZZY_AVG_SCORE
        and pair.weak_side_score >= _QUORUM_FUZZY_SIDE_SCORE
    )
    passes_canonical_anchor_threshold = False
    if not passes_standard_threshold:
        for left_candidate in left.candidates:
            for right_candidate in right.candidates:
                if not _matching_canonical_sides(
                    left_candidate,
                    right_candidate,
                    pair.orientation,
                ):
                    continue
                passes_canonical_anchor_threshold = (
                    pair.score >= _QUORUM_CANONICAL_SIDE_ANCHOR_AVG_SCORE
                    and pair.weak_side_score
                    >= _QUORUM_CANONICAL_SIDE_ANCHOR_WEAK_SIDE_SCORE
                )
                if passes_canonical_anchor_threshold:
                    break
            if passes_canonical_anchor_threshold:
                break

    if not passes_standard_threshold and not passes_canonical_anchor_threshold:
        return False

    larger_count = max(len(left.bookmakers), len(right.bookmakers))
    smaller_count = min(len(left.bookmakers), len(right.bookmakers))
    if larger_count < _QUORUM_MIN_LARGER_BOOKMAKERS:
        return False
    if larger_count - smaller_count < _QUORUM_MIN_BOOKMAKER_DIFFERENCE:
        return False
    return True


def with_group_context(
    decision: EventMergeDecision,
    *,
    can_union: bool,
    quorum_passes: bool,
    combined_bookmaker_count: int,
) -> EventMergeDecision:
    if decision.category not in {
        EventMergeDecisionCategory.HIGH_CONFIDENCE_FUZZY_MERGE,
        EventMergeDecisionCategory.ANCHORED_MERGE,
    }:
        return decision
    if decision.pair is None:
        return decision
    if can_union:
        return replace(decision, causes_union=True, requires_group_context=False)
    if quorum_passes:
        quorum_pair = replace(
            decision.pair,
            evidence=(
                *decision.pair.evidence,
                (
                    "Quorum-resolved same-bookmaker conflict: "
                    f"larger group has {combined_bookmaker_count} "
                    "combined bookmakers and dominant size advantage"
                ),
            ),
        )
        return EventMergeDecision(
            category=EventMergeDecisionCategory.QUORUM_OVERRIDE,
            pair=quorum_pair,
            evidence=decision.evidence,
            causes_union=True,
            quorum_override=True,
        )

    conflict_pair = replace(
        decision.pair,
        reason_code=REASON_CONFLICTING_SAME_BOOKMAKER_EVENT_CANDIDATE,
        evidence=(
            *decision.pair.evidence,
            (
                "Automatic grouping skipped because both groups "
                "contain the same bookmaker"
            ),
        ),
    )
    return EventMergeDecision(
        category=EventMergeDecisionCategory.SAME_BOOKMAKER_CONFLICT,
        pair=conflict_pair,
        evidence=decision.evidence,
        emits_review_case=True,
    )
