from __future__ import annotations

from dataclasses import dataclass
import re
from typing import AbstractSet

from rapidfuzz import fuzz

from .text_normalizer import normalize_identity_text


# Foreign-language women markers seen in real-world team names. Tokens are
# matched after ``normalize_identity_text`` (NFKD strip + lower + alnum-only).
FOREIGN_WOMEN_TOKENS: frozenset[str] = frozenset(
    {
        "frauen",
        "damen",
        "feminino",
        "feminina",
        "femminile",
        "femenino",
        "femenina",
        "feminin",
        "feminines",
        "kvinnor",
        "naiset",
        "vrouwen",
        "kvinder",
        "dff",
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

# Plain ASCII "z" is not a universal women alias; explicit marker syntax such
# as "(Z)" or "Z/" is handled by EXPLICIT_Z_WOMEN_MARKER_RE.
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
EXPLICIT_W_WOMEN_PREFIX_RE = re.compile(
    r"^\s*(?:w\s*/|\(\s*w\s*\))",
    re.IGNORECASE,
)

YOUTH_AGE_DIGITS: frozenset[str] = frozenset({"17", "18", "19", "20", "21", "23"})
YOUTH_AGES: frozenset[str] = frozenset({"u17", "u18", "u19", "u20", "u21", "u23"})
YOUTH_QUALIFIERS: frozenset[str] = YOUTH_AGES | {"youth"}
RESERVE_QUALIFIERS: frozenset[str] = frozenset(
    {"b", "ii", "2", "res", "reserve", "reserves"}
)

LOW_SIGNAL_TEAM_TOKENS: frozenset[str] = frozenset(
    {
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
)
MATCH_UNIFICATION_LOW_SIGNAL_TEAM_TOKENS: frozenset[str] = frozenset(
    {"bc", "bk", "kk", "fc", "fk", "club", "team"}
)

AGGRESSIVE_MERGE_SPORTS: frozenset[str] = frozenset({"basketball"})

AMBIGUOUS_DOT_PREFIXES: frozenset[str] = frozenset(
    {"st", "ft", "mt", "pt", "dr", "mr", "av"}
)

REASON_QUALIFIER_MISMATCH = "qualifier_mismatch"
REASON_UNSAFE_SUBSET = "unsafe_subset"
REASON_LOW_FUZZY_SCORE = "low_fuzzy_score"
REASON_DOTTED_EXPANSION = "dotted_expansion"
REASON_AUTO_MERGE_SAFE = "auto_merge_safe"
REASON_REVIEW_ONLY = "review_only"
REASON_WOMEN_MISMATCH = "women_mismatch"
REASON_YOUTH_AGE_MISMATCH = "youth_age_mismatch"
REASON_YOUTH_MARKER_MISMATCH = "youth_marker_mismatch"
REASON_RESERVE_MARKER_MISMATCH = "reserve_marker_mismatch"
REASON_UNSAFE_SUBSET_OVERRIDE = "unsafe_subset_override"

CANONICAL_TEAM_AUTO_MERGE_THRESHOLD = 88.0
POLICY_CANONICAL_MERGE_SAFETY = "canonical_merge_safety"


@dataclass(frozen=True)
class TeamSimilarityScore:
    score: float
    used_fuzzy_score: bool = False


@dataclass(frozen=True)
class TeamIdentityComparison:
    left_name: str
    right_name: str
    sport: str | None
    policy: str
    score: float
    left_qualifiers: frozenset[str]
    right_qualifiers: frozenset[str]
    left_comparison_text: str
    right_comparison_text: str
    left_significant_tokens: frozenset[str]
    right_significant_tokens: frozenset[str]
    left_expanded_name: str
    right_expanded_name: str
    dotted_expanded: bool
    unsafe_subset: bool
    reasons: frozenset[str]

    @property
    def qualifier_match(self) -> bool:
        return self.left_qualifiers == self.right_qualifiers

    @property
    def auto_merge_safe(self) -> bool:
        return REASON_AUTO_MERGE_SAFE in self.reasons

    @property
    def review_only(self) -> bool:
        return REASON_REVIEW_ONLY in self.reasons

    @property
    def low_fuzzy_score(self) -> bool:
        return REASON_LOW_FUZZY_SCORE in self.reasons


@dataclass(frozen=True)
class QualifierGateDecision:
    admit: bool
    youth_marker_mismatch: bool = False
    reserve_marker_mismatch: bool = False
    explicit_age_mismatch: bool = False
    women_mismatch: bool = False
    reasons: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CanonicalTeamMergeSafetyDecision:
    analysis: TeamIdentityComparison
    merge_mode: str
    allow_unsafe_subset_override: bool
    blocking_reasons: frozenset[str]
    override_reasons: frozenset[str] = frozenset()
    policy: str = POLICY_CANONICAL_MERGE_SAFETY

    @property
    def allowed(self) -> bool:
        return not self.blocking_reasons

    @property
    def reasons(self) -> frozenset[str]:
        return self.analysis.reasons


def team_qualifiers(name: str, *, sport: str | None = None) -> set[str]:
    """Return normalized women, reserve, and youth qualifiers in ``name``.

    ``sport`` is reserved for future per-sport adjustments and currently
    ignored. Persistence, score thresholds, and registry consensus status are
    intentionally outside this module.
    """
    del sport
    tokens = normalize_identity_text(name).split()
    qualifiers: set[str] = set()
    active_qualifier_tokens = TEAM_QUALIFIER_TOKENS | {"wom"}

    if EXPLICIT_Z_WOMEN_MARKER_RE.search(name) or EXPLICIT_W_WOMEN_PREFIX_RE.search(
        name
    ):
        qualifiers.add("women")

    def suffix_has_qualifier(start_index: int) -> bool:
        index = start_index
        while index < len(tokens):
            token = tokens[index]
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            if token == "team":
                index += 1
                continue
            if token == "u" and next_token in YOUTH_AGE_DIGITS:
                return True
            if token in active_qualifier_tokens:
                return True
            index += 1
        return False

    for index, token in enumerate(tokens):
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if token == "u" and next_token in YOUTH_AGE_DIGITS:
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
            continue
        if token not in active_qualifier_tokens:
            continue
        qualifiers.add(token)
    return qualifiers


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


def is_women_team(qualifiers: AbstractSet[str]) -> bool:
    return "women" in qualifiers


def has_youth_marker(qualifiers: AbstractSet[str]) -> bool:
    return bool(qualifiers & YOUTH_QUALIFIERS)


def has_reserve_marker(qualifiers: AbstractSet[str]) -> bool:
    return bool(qualifiers & RESERVE_QUALIFIERS)


def youth_ages(qualifiers: AbstractSet[str]) -> set[str]:
    return set(qualifiers & YOUTH_AGES)


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


def significant_tokens(
    name: str,
    *,
    sport: str | None = None,
    low_signal_tokens: AbstractSet[str] = LOW_SIGNAL_TEAM_TOKENS,
) -> set[str]:
    return {
        token
        for token in comparison_team_text(name, sport=sport).split()
        if token not in low_signal_tokens
    }


def match_unification_significant_tokens(
    name: str,
    *,
    sport: str | None = None,
) -> set[str]:
    return significant_tokens(
        name,
        sport=sport,
        low_signal_tokens=MATCH_UNIFICATION_LOW_SIGNAL_TEAM_TOKENS,
    )


def event_similarity_score_from_parts(
    left_key: str,
    right_key: str,
    left_tokens: AbstractSet[str],
    right_tokens: AbstractSet[str],
) -> TeamSimilarityScore:
    if not left_key or not right_key:
        return TeamSimilarityScore(0.0)
    if left_key == right_key:
        return TeamSimilarityScore(100.0)
    if left_tokens and left_tokens == right_tokens:
        return TeamSimilarityScore(100.0)
    return TeamSimilarityScore(
        float(fuzz.token_sort_ratio(left_key, right_key)),
        used_fuzzy_score=True,
    )


def team_similarity(left: str, right: str, *, sport: str | None = None) -> float:
    left_key = comparison_team_text(left, sport=sport)
    right_key = comparison_team_text(right, sport=sport)
    left_tokens = significant_tokens(left, sport=sport)
    right_tokens = significant_tokens(right, sport=sport)
    return event_similarity_score_from_parts(
        left_key,
        right_key,
        left_tokens,
        right_tokens,
    ).score


def same_team_context(left: str, right: str, *, sport: str | None = None) -> bool:
    return team_qualifiers(left, sport=sport) == team_qualifiers(right, sport=sport)


def uses_aggressive_dotted_expansion(sport: str | None) -> bool:
    return sport in AGGRESSIVE_MERGE_SPORTS


def expand_dotted_team_token(name: str, counterpart: str) -> str:
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
        if prefix in AMBIGUOUS_DOT_PREFIXES:
            output.append(token)
            continue
        candidates = [
            candidate
            for candidate in counterpart_tokens
            if len(candidate) > len(prefix) and candidate.lower().startswith(prefix)
        ]
        if len(candidates) == 1:
            output.append(candidates[0])
        else:
            output.append(token)
    return " ".join(output)


def _meaningfully_expanded(original: str, expanded: str) -> bool:
    return normalize_identity_text(original) != normalize_identity_text(expanded)


def expand_dotted_team_pair(
    left: str,
    right: str,
    *,
    sport: str | None = None,
) -> tuple[str, str, bool]:
    if not uses_aggressive_dotted_expansion(sport):
        return left, right, False
    expanded_left = expand_dotted_team_token(left, right)
    expanded_right = expand_dotted_team_token(right, left)
    return (
        expanded_left,
        expanded_right,
        _meaningfully_expanded(left, expanded_left)
        or _meaningfully_expanded(right, expanded_right),
    )


def event_team_similarity(
    left: str,
    right: str,
    *,
    sport: str | None = None,
    expand_dotted: bool = False,
) -> float:
    expanded_left, expanded_right, _expanded = (
        expand_dotted_team_pair(left, right, sport=sport)
        if expand_dotted
        else (left, right, False)
    )
    return team_similarity(expanded_left, expanded_right, sport=sport)


def subset_or_equal_significant_tokens(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
) -> bool:
    left_tokens = match_unification_significant_tokens(left_name, sport=sport)
    right_tokens = match_unification_significant_tokens(right_name, sport=sport)
    return bool(
        left_tokens
        and right_tokens
        and (left_tokens <= right_tokens or right_tokens <= left_tokens)
    )


def unsafe_compound_subset_match(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
) -> bool:
    left_tokens = match_unification_significant_tokens(left_name, sport=sport)
    right_tokens = match_unification_significant_tokens(right_name, sport=sport)
    return bool(
        left_tokens
        and right_tokens
        and (left_tokens < right_tokens or right_tokens < left_tokens)
    )


def comparison_texts_are_compatible_from_parts(
    left_text: str,
    right_text: str,
    left_tokens: AbstractSet[str],
    right_tokens: AbstractSet[str],
    *,
    team_ids: tuple[int, int] | None = None,
) -> bool:
    if team_ids is not None and team_ids[0] == team_ids[1]:
        return True
    if left_text == right_text:
        return True
    if not left_tokens or not right_tokens:
        return False
    return left_tokens <= right_tokens or right_tokens <= left_tokens


def comparison_texts_are_compatible(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
    team_ids: tuple[int, int] | None = None,
) -> bool:
    return comparison_texts_are_compatible_from_parts(
        comparison_team_text(left_name, sport=sport),
        comparison_team_text(right_name, sport=sport),
        significant_tokens(left_name, sport=sport),
        significant_tokens(right_name, sport=sport),
        team_ids=team_ids,
    )


def canonical_team_similarity_score(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
) -> float:
    left_key = comparison_team_text(left_name, sport=sport)
    right_key = comparison_team_text(right_name, sport=sport)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 100.0

    left_tokens = match_unification_significant_tokens(left_name, sport=sport)
    right_tokens = match_unification_significant_tokens(right_name, sport=sport)
    if not left_tokens or not right_tokens:
        return 0.0
    if left_tokens == right_tokens:
        return 100.0
    return float(
        min(
            fuzz.ratio(left_key, right_key),
            fuzz.token_sort_ratio(left_key, right_key),
        )
    )


def canonical_team_auto_merge_analysis(
    source_team_name: str,
    target_team_name: str,
    *,
    sport: str | None = None,
    threshold: float,
) -> TeamIdentityComparison:
    left_qualifiers = frozenset(team_qualifiers(source_team_name, sport=sport))
    right_qualifiers = frozenset(team_qualifiers(target_team_name, sport=sport))
    left_text = comparison_team_text(source_team_name, sport=sport)
    right_text = comparison_team_text(target_team_name, sport=sport)
    left_tokens = frozenset(
        match_unification_significant_tokens(source_team_name, sport=sport)
    )
    right_tokens = frozenset(
        match_unification_significant_tokens(target_team_name, sport=sport)
    )
    unsafe_subset = bool(
        left_tokens
        and right_tokens
        and (left_tokens < right_tokens or right_tokens < left_tokens)
    )
    score = canonical_team_similarity_score(
        source_team_name,
        target_team_name,
        sport=sport,
    )
    reasons: set[str] = set()
    if left_qualifiers != right_qualifiers:
        reasons.add(REASON_QUALIFIER_MISMATCH)
    elif unsafe_subset:
        reasons.add(REASON_UNSAFE_SUBSET)
    elif score < threshold:
        reasons.add(REASON_LOW_FUZZY_SCORE)
    else:
        reasons.add(REASON_AUTO_MERGE_SAFE)
    if REASON_AUTO_MERGE_SAFE not in reasons:
        reasons.add(REASON_REVIEW_ONLY)
    return TeamIdentityComparison(
        left_name=source_team_name,
        right_name=target_team_name,
        sport=sport,
        policy="canonical_auto_merge",
        score=score,
        left_qualifiers=left_qualifiers,
        right_qualifiers=right_qualifiers,
        left_comparison_text=left_text,
        right_comparison_text=right_text,
        left_significant_tokens=left_tokens,
        right_significant_tokens=right_tokens,
        left_expanded_name=source_team_name,
        right_expanded_name=target_team_name,
        dotted_expanded=False,
        unsafe_subset=unsafe_subset,
        reasons=frozenset(reasons),
    )


def canonical_team_merge_safety_decision_from_analysis(
    analysis: TeamIdentityComparison,
    *,
    merge_mode: str = "manual",
    allow_unsafe_subset_override: bool = False,
) -> CanonicalTeamMergeSafetyDecision:
    if merge_mode not in {"manual", "automatic"}:
        raise ValueError("merge_mode must be either 'manual' or 'automatic'")

    blocking_reasons: set[str] = set()
    override_reasons: set[str] = set()

    if REASON_QUALIFIER_MISMATCH in analysis.reasons:
        blocking_reasons.add(REASON_QUALIFIER_MISMATCH)

    if REASON_UNSAFE_SUBSET in analysis.reasons:
        if allow_unsafe_subset_override:
            override_reasons.add(REASON_UNSAFE_SUBSET_OVERRIDE)
        else:
            blocking_reasons.add(REASON_UNSAFE_SUBSET)

    if (
        merge_mode == "automatic"
        and not analysis.auto_merge_safe
        and not blocking_reasons
    ):
        blocking_reasons.add(REASON_REVIEW_ONLY)
        if REASON_LOW_FUZZY_SCORE in analysis.reasons:
            blocking_reasons.add(REASON_LOW_FUZZY_SCORE)

    return CanonicalTeamMergeSafetyDecision(
        analysis=analysis,
        merge_mode=merge_mode,
        allow_unsafe_subset_override=allow_unsafe_subset_override,
        blocking_reasons=frozenset(blocking_reasons),
        override_reasons=frozenset(override_reasons),
    )


def canonical_team_merge_safety_decision(
    source_team_name: str,
    target_team_name: str,
    *,
    sport: str | None = None,
    merge_mode: str = "manual",
    allow_unsafe_subset_override: bool = False,
    auto_merge_threshold: float = CANONICAL_TEAM_AUTO_MERGE_THRESHOLD,
) -> CanonicalTeamMergeSafetyDecision:
    analysis = canonical_team_auto_merge_analysis(
        source_team_name,
        target_team_name,
        sport=sport,
        threshold=auto_merge_threshold,
    )
    return canonical_team_merge_safety_decision_from_analysis(
        analysis,
        merge_mode=merge_mode,
        allow_unsafe_subset_override=allow_unsafe_subset_override,
    )


def explain_event_team_similarity(
    left: str,
    right: str,
    *,
    sport: str | None = None,
    expand_dotted: bool = False,
    auto_threshold: float | None = None,
) -> TeamIdentityComparison:
    expanded_left, expanded_right, dotted_expanded = (
        expand_dotted_team_pair(left, right, sport=sport)
        if expand_dotted
        else (left, right, False)
    )
    left_qualifiers = frozenset(team_qualifiers(left, sport=sport))
    right_qualifiers = frozenset(team_qualifiers(right, sport=sport))
    left_text = comparison_team_text(expanded_left, sport=sport)
    right_text = comparison_team_text(expanded_right, sport=sport)
    left_tokens = frozenset(significant_tokens(expanded_left, sport=sport))
    right_tokens = frozenset(significant_tokens(expanded_right, sport=sport))
    score_result = event_similarity_score_from_parts(
        left_text,
        right_text,
        left_tokens,
        right_tokens,
    )
    reasons: set[str] = set()
    if dotted_expanded:
        reasons.add(REASON_DOTTED_EXPANSION)
    if left_qualifiers != right_qualifiers:
        reasons.add(REASON_QUALIFIER_MISMATCH)
    if auto_threshold is not None:
        if left_qualifiers == right_qualifiers and score_result.score >= auto_threshold:
            reasons.add(REASON_AUTO_MERGE_SAFE)
        else:
            reasons.add(REASON_REVIEW_ONLY)
            if score_result.score < auto_threshold:
                reasons.add(REASON_LOW_FUZZY_SCORE)
    return TeamIdentityComparison(
        left_name=left,
        right_name=right,
        sport=sport,
        policy="event_similarity",
        score=score_result.score,
        left_qualifiers=left_qualifiers,
        right_qualifiers=right_qualifiers,
        left_comparison_text=left_text,
        right_comparison_text=right_text,
        left_significant_tokens=left_tokens,
        right_significant_tokens=right_tokens,
        left_expanded_name=expanded_left,
        right_expanded_name=expanded_right,
        dotted_expanded=dotted_expanded,
        unsafe_subset=False,
        reasons=frozenset(reasons),
    )


def canonical_candidate_qualifier_gate(
    raw_qualifiers: AbstractSet[str],
    cand_qualifiers: AbstractSet[str],
    cand_women_status: str,
) -> QualifierGateDecision:
    raw_is_women = is_women_team(raw_qualifiers)
    if cand_women_status == "women" and not raw_is_women:
        return QualifierGateDecision(
            admit=False,
            women_mismatch=True,
            reasons=frozenset({REASON_QUALIFIER_MISMATCH, REASON_WOMEN_MISMATCH}),
        )
    if cand_women_status == "men" and raw_is_women:
        return QualifierGateDecision(
            admit=False,
            women_mismatch=True,
            reasons=frozenset({REASON_QUALIFIER_MISMATCH, REASON_WOMEN_MISMATCH}),
        )

    raw_ages = youth_ages(raw_qualifiers)
    cand_ages = youth_ages(cand_qualifiers)
    if raw_ages and cand_ages and not (raw_ages & cand_ages):
        return QualifierGateDecision(
            admit=False,
            explicit_age_mismatch=True,
            reasons=frozenset({REASON_QUALIFIER_MISMATCH, REASON_YOUTH_AGE_MISMATCH}),
        )

    reasons: set[str] = set()
    youth_marker_mismatch = has_youth_marker(raw_qualifiers) != has_youth_marker(
        cand_qualifiers
    )
    reserve_marker_mismatch = has_reserve_marker(raw_qualifiers) != has_reserve_marker(
        cand_qualifiers
    )
    if youth_marker_mismatch:
        reasons.update({REASON_QUALIFIER_MISMATCH, REASON_YOUTH_MARKER_MISMATCH})
    if reserve_marker_mismatch:
        reasons.update({REASON_QUALIFIER_MISMATCH, REASON_RESERVE_MARKER_MISMATCH})
    return QualifierGateDecision(
        admit=True,
        youth_marker_mismatch=youth_marker_mismatch,
        reserve_marker_mismatch=reserve_marker_mismatch,
        reasons=frozenset(reasons),
    )
