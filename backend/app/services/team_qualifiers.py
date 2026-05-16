"""Team-qualifier detection (women / youth / reserve markers).

Hoisted out of ``outcome_normalizer.py`` so the team matcher
(``team_registry.search_canonical_team_candidates`` and the slot ranker in
``normalizer.py``) can apply the same qualifier rules without dragging in
the entire outcome-resolution stack.

A *qualifier* is a normalized marker token attached to a team name that
signals "this is the women's team", "this is the U19 youth team",
"this is the reserve / B team", etc. Cross-bookmaker matching of these
markers is brittle because:

* The same marker is written many ways (``B``, ``II``, ``2``, ``(R)``,
  ``Am``, ``Mladi``, ``M19``/``U19``, ``Frauen``, ``DFF`` …).
* Some bookmakers omit them entirely.
* Some markers collide with location abbreviations (``z`` in football is
  often a location suffix; ``B`` can mean Belgium).

The qualifier set returned by :func:`team_qualifiers` is the canonical
normalized form ``{"women", "u19", "b", "ii", "2", "res", "reserve",
"reserves", "u17"..."u23", "youth"}`` plus any foreign-women token from
:data:`FOREIGN_WOMEN_TOKENS`. It is the input to higher-level decisions
like "hard-block this candidate" or "demote this candidate".

Convenience predicates (:func:`is_women_team`, :func:`has_youth_marker`,
:func:`has_reserve_marker`, :func:`youth_ages`) classify the qualifier
set into the tiers the matcher cares about.
"""
from __future__ import annotations

import re

from .text_normalizer import normalize_identity_text


# Foreign-language women markers seen in real-world team names. Tokens are
# matched after ``normalize_identity_text`` (NFKD strip + lower + alnum-only),
# so include the post-normalization form (e.g. "feminin" covers French
# "Féminin" because diacritics are stripped). Conservative additions only —
# tokens that could plausibly appear as part of a regular team name (e.g.
# bare "fem") are intentionally omitted.
FOREIGN_WOMEN_TOKENS: frozenset[str] = frozenset(
    {
        "frauen",      # German
        "damen",       # German (formal)
        "feminino",    # Portuguese (m.)
        "feminina",    # Portuguese (f.)
        "femminile",   # Italian
        "femenino",    # Spanish (m.)
        "femenina",    # Spanish (f.)
        "feminin",     # French/Romanian (post-diacritic strip)
        "feminines",   # French plural
        "kvinnor",     # Swedish
        "naiset",      # Finnish
        "vrouwen",     # Dutch
        "kvinder",     # Danish
        "dff",         # Swedish "Damfotboll Förening" club designation
    }
)


TEAM_QUALIFIER_TOKENS: frozenset[str] = frozenset(
    {
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
) | FOREIGN_WOMEN_TOKENS


# Cross-sport aliases for explicit women markers. Plain ASCII "z" is not in
# this set because it is a common location abbreviation in football; only
# explicit marker syntax such as "(Ž)" or "Ž/" is treated as women.
WOMEN_QUALIFIER_ALIASES: frozenset[str] = (
    frozenset({"w", "wom", "women"}) | FOREIGN_WOMEN_TOKENS
)

WOMEN_MARKER_TOKENS: frozenset[str] = (
    frozenset({"w", "wom", "women"}) | FOREIGN_WOMEN_TOKENS
)


EXPLICIT_Z_WOMEN_MARKER_RE = re.compile(
    r"(^|\s)ž(?=$|\s)|\(\s*[žz]\s*\)|^\s*[žz]\s*/",
    re.IGNORECASE,
)


_YOUTH_AGES: frozenset[str] = frozenset({"17", "18", "19", "20", "21", "23"})


def team_qualifiers(name: str, *, sport: str | None = None) -> set[str]:
    """Return the set of qualifier markers detected in ``name``.

    The returned set is a subset of :data:`TEAM_QUALIFIER_TOKENS` plus the
    sentinel ``"women"`` (which subsumes ``"w"`` / ``"wom"`` / explicit
    Ž markers). Empty set means "no qualifiers detected" — i.e. the team
    is treated as the senior men's first-team.

    ``sport`` is reserved for future per-sport adjustments and currently
    ignored.
    """
    del sport  # currently unused; kept in signature for backwards compat
    tokens = normalize_identity_text(name).split()
    qualifiers: set[str] = set()
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
            if token == "u" and next_token in _YOUTH_AGES:
                return True
            if token in active_qualifier_tokens:
                return True
            index += 1
        return False

    for index, token in enumerate(tokens):
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if token == "u" and next_token in _YOUTH_AGES:
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
                token in ({"women", "wom"} | FOREIGN_WOMEN_TOKENS)
                and index == 0
                and len(tokens) > 1
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
            # Plain ASCII Z is intentionally not a universal women alias.
            # The explicit-marker regex above handles "(Ž)", "(Z)", "Ž/",
            # and "Z/" without breaking football abbreviations such as
            # "FK Borac Z" for Zvornik.
            continue
        if token not in active_qualifier_tokens:
            continue
        qualifiers.add(token)
    return qualifiers


def strip_explicit_z_women_markers(name: str) -> str:
    """Strip the explicit "Ž" / "(Z)" / "Z/" women markers from ``name``.

    Leaves the rest of the string intact so the matcher can still see the
    base team name. Inverse of the ``EXPLICIT_Z_WOMEN_MARKER_RE`` check in
    :func:`team_qualifiers`.
    """
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


def is_women_team(qualifiers: set[str]) -> bool:
    """Convenience: True iff the qualifier set marks a women's team."""
    return "women" in qualifiers


_YOUTH_QUALIFIERS: frozenset[str] = frozenset(
    {"u17", "u18", "u19", "u20", "u21", "u23", "youth"}
)
_RESERVE_QUALIFIERS: frozenset[str] = frozenset(
    {"b", "ii", "2", "res", "reserve", "reserves"}
)


def has_youth_marker(qualifiers: set[str]) -> bool:
    """True iff the qualifier set contains any youth-team marker."""
    return bool(qualifiers & _YOUTH_QUALIFIERS)


def has_reserve_marker(qualifiers: set[str]) -> bool:
    """True iff the qualifier set contains any reserve / B-team marker."""
    return bool(qualifiers & _RESERVE_QUALIFIERS)


def youth_ages(qualifiers: set[str]) -> set[str]:
    """Return the explicit youth-age qualifiers (``u17`` … ``u23``)."""
    return qualifiers & frozenset({"u17", "u18", "u19", "u20", "u21", "u23"})
