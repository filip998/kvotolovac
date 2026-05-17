from __future__ import annotations

from dataclasses import dataclass
import re

from ..tennis_name_matcher import tennis_competitor_pair_matches
from ..text_normalizer import normalize_identity_text
from .team_text import (
    AGGRESSIVE_MERGE_SPORTS as _AGGRESSIVE_MERGE_SPORTS,
    same_team_context as _same_team_context,
    team_similarity as _team_similarity,
)


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

# 2-letter dot-prefixes that overlap heavily with non-team words (street,
# fort, mount, port, point, doctor, mister, avenue, saint) and would
# otherwise produce false-positive expansions even within basketball
# (e.g. ``St.Petersburg`` ↔ ``Stockholm Petersburg``). Keeping these out of
# the dot-expansion logic preserves the genuine ``Ch.More`` ↔ ``Cherno More``
# case while blocking the geographic collision class.
_AMBIGUOUS_DOT_PREFIXES: frozenset[str] = frozenset(
    {"st", "ft", "mt", "pt", "dr", "mr", "av"}
)


def _expand_dotted_token(name: str, counterpart: str) -> str:
    """Substitute dot-truncated tokens (``Ch.``, ``Pl.``, ``Ch.More``) by an
    unambiguous expansion drawn from ``counterpart``.

    Only used inside the Match Unification — keeps shared :func:`_team_similarity`
    untouched so football pairing is unaffected. Restrictions:

    * Token must end with ``.`` and have at least 2 characters of prefix
      (1-letter prefixes are too ambiguous, e.g. ``B.`` could be Bayern,
      Brest, Belgrade, …).
    * The prefix must not be in ``_AMBIGUOUS_DOT_PREFIXES`` — these short
      geographic / honorific prefixes (``St``, ``Mt``, ``Ft``, …) collide
      with real team-name tokens (``Stockholm``, ``Manchester``, ``Fort``,
      …) and the structural anchor check below cannot disambiguate them.
    * The counterpart must contain exactly one token starting with that
      prefix; ambiguous expansions are dropped.
    * The source name must contain at least one OTHER non-dotted token that
      already appears in the counterpart — this anchors the expansion in
      genuine name overlap and blocks coincidences like
      ``St. Petersburg`` ↔ ``Stockholm Giants`` where ``St`` would
      otherwise expand to ``Stockholm`` purely on prefix uniqueness.

    Compound tokens with internal dots (``Ch.More`` → ``Ch. More``) are
    pre-split before expansion so a missing space after the period does not
    mask the abbreviation.
    """

    spaced = re.sub(r"\.(?=\S)", ". ", name)
    counterpart_spaced = re.sub(r"\.(?=\S)", ". ", counterpart)
    counterpart_tokens = counterpart_spaced.split()
    counterpart_token_set = {token.lower().rstrip(".") for token in counterpart_tokens}
    source_tokens = spaced.split()
    has_anchor = any(
        not token.endswith(".") and token.lower() in counterpart_token_set
        for token in source_tokens
    )
    if not has_anchor:
        return name
    output: list[str] = []
    for token in source_tokens:
        if not token.endswith(".") or len(token) < 3:
            output.append(token)
            continue
        prefix = token[:-1].lower()
        if len(prefix) < 2:
            output.append(token)
            continue
        if prefix in _AMBIGUOUS_DOT_PREFIXES:
            output.append(token)
            continue
        candidates = [
            candidate
            for candidate in counterpart_tokens
            if len(candidate) > len(prefix)
            and candidate.lower().startswith(prefix)
        ]
        if len(candidates) == 1:
            output.append(candidates[0])
        else:
            output.append(token)
    return " ".join(output)


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

    expanded_left = left
    expanded_right = right
    if sport in _TARGETED_SPORTS_FOR_AGGRESSIVE_MERGE:
        expanded_left = _expand_dotted_token(left, right)
        expanded_right = _expand_dotted_token(right, left)
    return _team_similarity(expanded_left, expanded_right, sport=sport)


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
