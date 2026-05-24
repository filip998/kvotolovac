from __future__ import annotations

import pytest

from app.services.team_identity import (
    REASON_AUTO_MERGE_SAFE,
    REASON_DOTTED_EXPANSION,
    REASON_LOW_FUZZY_SCORE,
    REASON_QUALIFIER_MISMATCH,
    REASON_REVIEW_ONLY,
    REASON_UNSAFE_SUBSET,
    REASON_UNSAFE_SUBSET_OVERRIDE,
    canonical_candidate_qualifier_gate,
    canonical_team_auto_merge_analysis,
    canonical_team_merge_safety_decision,
    comparison_team_text,
    event_team_similarity,
    expand_dotted_team_pair,
    explain_event_team_similarity,
    same_team_context,
    significant_tokens,
    subset_or_equal_significant_tokens,
    team_qualifiers,
    unsafe_compound_subset_match,
)


@pytest.mark.parametrize(
    "name",
    [
        "ž Partizan",
        "W/Partizan",
        "Partizan W",
        "Partizan Women",
        "Partizan Wom",
    ],
)
def test_team_qualifiers_detect_women_markers(name):
    assert team_qualifiers(name, sport="football") == {"women"}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Barcelona 2", {"2"}),
        ("Barcelona II", {"ii"}),
        ("Barcelona B", {"b"}),
        ("Barcelona res", {"res"}),
        ("Barcelona reserve", {"reserve"}),
        ("Barcelona reserves", {"reserves"}),
    ],
)
def test_team_qualifiers_detect_reserve_markers(name, expected):
    assert team_qualifiers(name, sport="football") == expected


@pytest.mark.parametrize("age", ["17", "18", "19", "20", "21", "23"])
def test_team_qualifiers_detect_youth_markers(age):
    assert team_qualifiers(f"Crvena zvezda U{age}", sport="football") == {f"u{age}"}
    assert team_qualifiers(f"Crvena zvezda U-{age}", sport="football") == {f"u{age}"}


def test_same_team_context_requires_matching_qualifier_semantics():
    assert same_team_context("W/Partizan", "Partizan Women", sport="football")
    assert not same_team_context("Partizan", "Partizan W", sport="football")
    assert not same_team_context("Barcelona", "Barcelona B", sport="football")
    assert not same_team_context("Real Madrid U19", "Real Madrid U21", sport="football")
    assert team_qualifiers("W Connection", sport="football") == set()
    assert team_qualifiers("W. Bromwich Albion", sport="football") == set()


def test_low_signal_tokens_are_stripped_consistently():
    assert comparison_team_text("FC Barcelona Women", sport="football") == "fc barcelona"
    assert significant_tokens("FC Barcelona Women", sport="football") == {"barcelona"}
    assert significant_tokens("CD CE SC FK Barcelona Team", sport="football") == {
        "barcelona"
    }


def test_dotted_expansion_is_sport_gated_to_aggressive_sports():
    basketball = explain_event_team_similarity(
        "Ch.More",
        "Cherno More",
        sport="basketball",
        expand_dotted=True,
        auto_threshold=90.0,
    )
    football = explain_event_team_similarity(
        "Ch.More",
        "Cherno More",
        sport="football",
        expand_dotted=True,
        auto_threshold=90.0,
    )

    assert basketball.score == 100.0
    assert basketball.dotted_expanded
    assert REASON_DOTTED_EXPANSION in basketball.reasons
    assert football.score < 100.0
    assert not football.dotted_expanded
    assert REASON_DOTTED_EXPANSION not in football.reasons


def test_ambiguous_dotted_prefixes_do_not_expand():
    left, right, expanded = expand_dotted_team_pair(
        "St.Petersburg",
        "Stockholm Petersburg",
        sport="basketball",
    )

    assert left == "St. Petersburg"
    assert right == "Stockholm Petersburg"
    assert not expanded
    assert (
        event_team_similarity(
            "St.Petersburg",
            "Stockholm Petersburg",
            sport="basketball",
            expand_dotted=True,
        )
        < 100.0
    )


def test_unsafe_subset_cases_are_named_separately_from_event_subset_anchors():
    assert unsafe_compound_subset_match("FC Barcelona", "Barcelona B", sport="football")
    assert not unsafe_compound_subset_match("FC Barcelona", "Barcelona", sport="football")
    assert unsafe_compound_subset_match("Arsenal CF", "Arsenal", sport="football")
    assert subset_or_equal_significant_tokens(
        "Hermine Nantes",
        "Hermine Nantes Basket",
        sport="basketball",
    )


def test_canonical_auto_merge_analysis_distinguishes_blocking_reasons():
    safe = canonical_team_auto_merge_analysis(
        "Sao Jose W",
        "Sao Jose Women",
        sport="basketball",
        threshold=88.0,
    )
    qualifier_mismatch = canonical_team_auto_merge_analysis(
        "Partizan",
        "Partizan W",
        sport="basketball",
        threshold=88.0,
    )
    unsafe_subset = canonical_team_auto_merge_analysis(
        "Arsenal",
        "Arsenal Tula",
        sport="football",
        threshold=88.0,
    )
    low_score = canonical_team_auto_merge_analysis(
        "Manchester United",
        "Manchester City",
        sport="football",
        threshold=88.0,
    )

    assert safe.auto_merge_safe
    assert not safe.review_only
    assert qualifier_mismatch.review_only
    assert REASON_QUALIFIER_MISMATCH in qualifier_mismatch.reasons
    assert unsafe_subset.review_only
    assert REASON_UNSAFE_SUBSET in unsafe_subset.reasons
    assert low_score.review_only
    assert low_score.low_fuzzy_score


def test_canonical_candidate_qualifier_gate_returns_structural_decision():
    women_mismatch = canonical_candidate_qualifier_gate(
        frozenset({"women"}),
        frozenset(),
        "men",
    )
    age_mismatch = canonical_candidate_qualifier_gate(
        frozenset({"u19"}),
        frozenset({"u23"}),
        "men",
    )
    marker_mismatch = canonical_candidate_qualifier_gate(
        frozenset({"u19"}),
        frozenset(),
        "men",
    )

    assert not women_mismatch.admit
    assert women_mismatch.women_mismatch
    assert not age_mismatch.admit
    assert age_mismatch.explicit_age_mismatch
    assert marker_mismatch.admit
    assert marker_mismatch.youth_marker_mismatch


def test_canonical_merge_safety_decision_classifies_manual_and_automatic_paths():
    manual_review_only = canonical_team_merge_safety_decision(
        "Manchester United",
        "Manchester City",
        sport="football",
        merge_mode="manual",
    )
    automatic_review_only = canonical_team_merge_safety_decision(
        "Manchester United",
        "Manchester City",
        sport="football",
        merge_mode="automatic",
    )
    unsafe_subset_override = canonical_team_merge_safety_decision(
        "Arsenal Tula",
        "Arsenal",
        sport="football",
        merge_mode="manual",
        allow_unsafe_subset_override=True,
    )
    qualifier_mismatch = canonical_team_merge_safety_decision(
        "Partizan",
        "Partizan Women",
        sport="basketball",
        merge_mode="manual",
        allow_unsafe_subset_override=True,
    )

    assert manual_review_only.allowed
    assert REASON_REVIEW_ONLY in manual_review_only.reasons
    assert not automatic_review_only.allowed
    assert automatic_review_only.blocking_reasons == frozenset(
        {REASON_LOW_FUZZY_SCORE, REASON_REVIEW_ONLY}
    )
    assert unsafe_subset_override.allowed
    assert unsafe_subset_override.override_reasons == frozenset(
        {REASON_UNSAFE_SUBSET_OVERRIDE}
    )
    assert REASON_UNSAFE_SUBSET in unsafe_subset_override.reasons
    assert not qualifier_mismatch.allowed
    assert qualifier_mismatch.blocking_reasons == frozenset(
        {REASON_QUALIFIER_MISMATCH}
    )


def test_canonical_merge_safety_decision_marks_auto_safe_pair():
    decision = canonical_team_merge_safety_decision(
        "Sao Jose W",
        "Sao Jose Women",
        sport="basketball",
        merge_mode="automatic",
    )

    assert decision.allowed
    assert REASON_AUTO_MERGE_SAFE in decision.reasons
