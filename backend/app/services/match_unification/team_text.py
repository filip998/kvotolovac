from __future__ import annotations

from rapidfuzz import fuzz

from ..team_qualifiers import (
    EXPLICIT_Z_WOMEN_MARKER_RE,
    FOREIGN_WOMEN_TOKENS,
    TEAM_QUALIFIER_TOKENS,
    WOMEN_MARKER_TOKENS,
    WOMEN_QUALIFIER_ALIASES,
    strip_explicit_z_women_markers,
    team_qualifiers,
)
from ..text_normalizer import normalize_identity_text

LOW_SIGNAL_TEAM_TOKENS = {
    "bc",
    "bk",
    "kk",
    "fc",
    "fk",
    "club",
    "team",
    "sc",
    "cf",
    "cd",
    "ce",
}
AGGRESSIVE_MERGE_SPORTS = frozenset({"basketball"})


def comparison_team_text(team_name: str, *, sport: str | None = None) -> str:
    qualifiers = team_qualifiers(team_name, sport=sport)
    comparison_name = (
        strip_explicit_z_women_markers(team_name)
        if "women" in qualifiers
        else team_name
    )
    tokens = normalize_identity_text(comparison_name).split()
    if "women" in qualifiers:
        tokens = [token for token in tokens if token not in WOMEN_MARKER_TOKENS]
    return " ".join(tokens)


def significant_tokens(name: str, *, sport: str | None = None) -> set[str]:
    return {
        token
        for token in comparison_team_text(name, sport=sport).split()
        if token not in LOW_SIGNAL_TEAM_TOKENS
    }


def team_similarity(left: str, right: str, *, sport: str | None = None) -> float:
    left_key = comparison_team_text(left, sport=sport)
    right_key = comparison_team_text(right, sport=sport)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 100.0
    left_tokens = significant_tokens(left, sport=sport)
    right_tokens = significant_tokens(right, sport=sport)
    if left_tokens and left_tokens == right_tokens:
        return 100.0
    return float(fuzz.token_sort_ratio(left_key, right_key))


def same_team_context(left: str, right: str, *, sport: str | None = None) -> bool:
    return team_qualifiers(left, sport=sport) == team_qualifiers(right, sport=sport)
