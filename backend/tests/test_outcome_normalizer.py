from __future__ import annotations

from app.services.outcome_normalizer import _team_similarity


def test_team_similarity_does_not_force_strict_subset_to_exact_match():
    assert _team_similarity("Arsenal", "Arsenal Tula") < 100.0


def test_team_similarity_allows_low_signal_prefix_difference():
    assert _team_similarity("Llosetense", "CD Llosetense") == 100.0

