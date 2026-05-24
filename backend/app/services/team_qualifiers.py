from __future__ import annotations

from .team_identity import (
    EXPLICIT_Z_WOMEN_MARKER_RE,
    FOREIGN_WOMEN_TOKENS,
    TEAM_QUALIFIER_TOKENS,
    WOMEN_MARKER_TOKENS,
    WOMEN_QUALIFIER_ALIASES,
    has_reserve_marker,
    has_youth_marker,
    is_women_team,
    strip_explicit_z_women_markers,
    team_qualifiers,
    youth_ages,
)

__all__ = [
    "EXPLICIT_Z_WOMEN_MARKER_RE",
    "FOREIGN_WOMEN_TOKENS",
    "TEAM_QUALIFIER_TOKENS",
    "WOMEN_MARKER_TOKENS",
    "WOMEN_QUALIFIER_ALIASES",
    "has_reserve_marker",
    "has_youth_marker",
    "is_women_team",
    "strip_explicit_z_women_markers",
    "team_qualifiers",
    "youth_ages",
]
