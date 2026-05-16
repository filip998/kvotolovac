from __future__ import annotations

import re

from rapidfuzz import fuzz

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
TEAM_QUALIFIER_TOKENS = {
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
WOMEN_QUALIFIER_ALIASES = frozenset({"w", "wom", "women"})
WOMEN_MARKER_TOKENS = frozenset({"w", "wom", "women"})
AGGRESSIVE_MERGE_SPORTS = frozenset({"basketball"})
EXPLICIT_Z_WOMEN_MARKER_RE = re.compile(
    r"(^|\s)ž(?=$|\s)|\(\s*[žz]\s*\)|^\s*[žz]\s*/",
    re.IGNORECASE,
)


def strip_explicit_z_women_markers(name: str) -> str:
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


def team_qualifiers(name: str, *, sport: str | None = None) -> set[str]:
    tokens = normalize_identity_text(name).split()
    qualifiers: set[str] = set()
    youth_ages = {"17", "18", "19", "20", "21", "23"}
    active_qualifier_tokens = TEAM_QUALIFIER_TOKENS | {"wom"}

    if EXPLICIT_Z_WOMEN_MARKER_RE.search(name):
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
            if index > 0 and (
                index == len(tokens) - 1
                or next_token == "team"
                or suffix_has_qualifier(index + 1)
            ):
                qualifiers.add(token)
            continue
        if token in WOMEN_QUALIFIER_ALIASES:
            is_explicit_prefix = (
                token in {"women", "wom"} and index == 0 and len(tokens) > 1
            )
            is_suffix = index > 0 and (
                index == len(tokens) - 1
                or next_token in {"team", "women"}
                or suffix_has_qualifier(index + 1)
            )
            if is_explicit_prefix or is_suffix:
                qualifiers.add("women")
            continue
        if token == "z":
            continue
        if token not in active_qualifier_tokens:
            continue
        qualifiers.add(token)
    return qualifiers


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

