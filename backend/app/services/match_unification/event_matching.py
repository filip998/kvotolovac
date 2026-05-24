from __future__ import annotations

from dataclasses import dataclass

from ..tennis_name_matcher import tennis_competitor_pair_matches
from ..team_identity import (
    AGGRESSIVE_MERGE_SPORTS as _AGGRESSIVE_MERGE_SPORTS,
    event_team_similarity as _event_team_similarity,
    expand_dotted_team_token as _expand_dotted_token,
    same_team_context as _same_team_context,
)
from ..text_normalizer import normalize_identity_text


@dataclass(frozen=True)
class EventCandidate:
    match_id: str
    bookmaker_id: str
    sport: str
    start_time: str
    home_team_id: int | None
    away_team_id: int | None
    home_team: str
    away_team: str
    source_league_id: str | None = None
    source_league_name: str | None = None
    source_home_team: str | None = None
    source_away_team: str | None = None
    source_start_time: str | None = None
    source_url: str | None = None
    source_kind: str = "normalized"

    @property
    def bookmaker_member_key(self) -> tuple[str, str]:
        return (self.match_id, self.bookmaker_id)

    @property
    def exact_event_key(self) -> tuple[str, str, tuple[int, int] | tuple[str, str]]:
        if self.home_team_id is not None and self.away_team_id is not None:
            team_key: tuple[int, int] | tuple[str, str] = tuple(
                sorted((self.home_team_id, self.away_team_id))
            )
        else:
            team_key = tuple(
                sorted(
                    (
                        normalize_identity_text(self.home_team),
                        normalize_identity_text(self.away_team),
                    )
                )
            )
        return (self.sport, self.start_time, team_key)


@dataclass(frozen=True)
class _OrientationScore:
    orientation: str
    home_score: float
    away_score: float

    @property
    def avg_score(self) -> float:
        return (self.home_score + self.away_score) / 2

    @property
    def weak_side_score(self) -> float:
        return min(self.home_score, self.away_score)


# Sports for which the Match Unification resolver activates aggressive aliasing & dot-expansion
# heuristics. Re-exported alias of the Match Unification team-text rules so the
# modules cannot drift; new sports must be enabled in exactly one place.
_TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE: frozenset[str] = _AGGRESSIVE_MERGE_SPORTS

def _resolver_team_similarity(
    left: str, right: str, *, sport: str | None = None
) -> float:
    """Match-Unification-local team similarity that pre-expands dot-truncations.

    Equivalent to :func:`_team_similarity` for cases without dotted
    abbreviations.

    Sport-gated: dot expansion only fires for sports in
    ``_TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE``. Football has its own
    pairing flow with stricter handling, and the dot-expansion logic
    cannot structurally distinguish ``Ch.More`` (basketball — should expand)
    from ``St.Petersburg`` ↔ ``Stockholm Petersburg`` (football — would
    incorrectly merge two distinct cities).
    """

    return _event_team_similarity(left, right, sport=sport, expand_dotted=True)


def _orientation_scores(
    left_home: str,
    left_away: str,
    right_home: str,
    right_away: str,
    *,
    sport: str | None = None,
) -> list[_OrientationScore]:
    if sport == "tennis":
        return [
            _OrientationScore(
                orientation=match.orientation,
                home_score=match.home_score,
                away_score=match.away_score,
            )
            for match in tennis_competitor_pair_matches(
                left_home,
                left_away,
                right_home,
                right_away,
            )
        ]

    scores: list[_OrientationScore] = []
    if _same_team_context(
        left_home,
        right_home,
        sport=sport,
    ) and _same_team_context(left_away, right_away, sport=sport):
        scores.append(
            _OrientationScore(
                orientation="as_listed",
                home_score=_resolver_team_similarity(left_home, right_home, sport=sport),
                away_score=_resolver_team_similarity(left_away, right_away, sport=sport),
            )
        )
    if _same_team_context(
        left_home,
        right_away,
        sport=sport,
    ) and _same_team_context(left_away, right_home, sport=sport):
        scores.append(
            _OrientationScore(
                orientation="reversed",
                home_score=_resolver_team_similarity(left_home, right_away, sport=sport),
                away_score=_resolver_team_similarity(left_away, right_home, sport=sport),
            )
        )
    return sorted(scores, key=lambda score: score.avg_score, reverse=True)
