from __future__ import annotations

import sqlite3

from app.config import settings
from app.models.schemas import RawOutcomeOffer
from app.services.normalizer import generate_match_id
from app.services.outcome_normalizer import (
    _team_similarity,
    normalize_outcome_offers_with_diagnostics,
)
from app.services.team_registry import create_canonical_team


START_TIME = "2030-01-01T20:00:00+00:00"


def _offer(
    bookmaker_id: str,
    home_team: str,
    away_team: str,
    *,
    market_type: str = "football_total_goals",
    outcome_code: str = "over",
) -> RawOutcomeOffer:
    return RawOutcomeOffer(
        bookmaker_id=bookmaker_id,
        league_id="football_test_league",
        sport="football",
        home_team=home_team,
        away_team=away_team,
        market_type=market_type,
        outcome_code=outcome_code,
        odds=1.9,
        line=2.5 if market_type == "football_total_goals" else None,
        raw_label=outcome_code,
        start_time=START_TIME,
    )


def _auto_review_alias_count() -> int:
    with sqlite3.connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM team_aliases WHERE source = 'auto_review'"
        ).fetchone()
    return int(row[0])


def _canonical_team_names() -> set[str]:
    with sqlite3.connect(settings.db_path) as conn:
        rows = conn.execute("SELECT display_name FROM canonical_teams").fetchall()
    return {str(row[0]) for row in rows}


def test_team_similarity_does_not_force_strict_subset_to_exact_match():
    assert _team_similarity("Arsenal", "Arsenal Tula") < 100.0


def test_team_similarity_allows_low_signal_prefix_difference():
    assert _team_similarity("Llosetense", "CD Llosetense") == 100.0


def test_exact_cross_book_football_event_normalizes_without_auto_aliases(team_registry_file):
    raw = [
        _offer("maxbet", "Basket Sibirsk", "CSKA Moscow", outcome_code="over"),
        _offer("balkanbet", "Basket Sibirsk", "CSKA Moscow", outcome_code="under"),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_one_strong_football_team_matches_event_without_alias_write(team_registry_file):
    basket = create_canonical_team(display_name="Basket Sibirsk", sport="football")
    cska = create_canonical_team(display_name="CSKA Moscow", sport="football")
    expected_match_id = generate_match_id(
        basket.team_id,
        cska.team_id,
        START_TIME,
        "football",
    )
    raw = [
        _offer("maxbet", "Basket Sibirsk", "CSKA Moscow", outcome_code="over"),
        _offer("balkanbet", "Blec Sybirsk", "CSKA Moscow", outcome_code="under"),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert {offer.match_id for offer in normalized} == {expected_match_id}
    assert {offer.bookmaker_id for offer in normalized} == {"maxbet", "balkanbet"}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_weak_football_event_pair_remains_unmatched(team_registry_file):
    create_canonical_team(display_name="Basket Sibirsk", sport="football")
    create_canonical_team(display_name="CSKA Moscow", sport="football")
    raw = [
        _offer("maxbet", "Basket Sibirsk", "CSKA Moskva", outcome_code="over"),
        _offer("balkanbet", "Blec Sybirsk", "CSKA Moscow", outcome_code="under"),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert normalized == []
    assert {row.reason_code for row in unresolved} == {
        "unresolved_away_team",
        "unresolved_home_team",
    }
    assert review_cases
    assert _auto_review_alias_count() == 0


def test_reversed_football_event_swaps_orientation_sensitive_outcomes(team_registry_file):
    home = create_canonical_team(display_name="Team Alpha", sport="football")
    away = create_canonical_team(display_name="Team Beta", sport="football")
    expected_match_id = generate_match_id(
        home.team_id,
        away.team_id,
        START_TIME,
        "football",
    )
    raw = [
        _offer(
            "maxbet",
            "Team Alpha",
            "Team Beta",
            market_type="football_result",
            outcome_code="home",
        ),
        _offer(
            "balkanbet",
            "Team Beta",
            "Team Alpha",
            market_type="football_result",
            outcome_code="home",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert unresolved == []
    assert review_cases == []
    assert {offer.match_id for offer in normalized} == {expected_match_id}
    assert {
        (offer.bookmaker_id, offer.outcome_code)
        for offer in normalized
    } == {("maxbet", "home"), ("balkanbet", "away")}
    assert _auto_review_alias_count() == 0


def test_close_competing_football_candidates_are_not_auto_matched(team_registry_file):
    create_canonical_team(display_name="Basket Sibirsk", sport="football")
    create_canonical_team(display_name="Buket Sibirsk", sport="football")
    create_canonical_team(display_name="CSKA Moscow", sport="football")
    raw = [
        _offer("maxbet", "Blec Sybirsk", "CSKA Moscow", outcome_code="over"),
        _offer("balkanbet", "Basket Sibirsk", "CSKA Moscow", outcome_code="under"),
        _offer("meridian", "Buket Sibirsk", "CSKA Moscow", outcome_code="under"),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert {offer.bookmaker_id for offer in normalized} == {"balkanbet", "meridian"}
    assert any(row.bookmaker_id == "maxbet" for row in unresolved)
    assert _auto_review_alias_count() == 0


def test_fuzzy_football_event_match_does_not_create_aliases(team_registry_file):
    raw = [
        _offer("maxbet", "CD Llosetense", "CSKA Moskva", outcome_code="over"),
        _offer("balkanbet", "Llosetense", "CSKA Moscow", outcome_code="under"),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert unresolved == []
    assert "CD Llosetense" in _canonical_team_names()
    assert "CSKA Moskva" in _canonical_team_names()
    assert _auto_review_alias_count() == 0


def test_football_event_match_rejects_mismatched_team_qualifiers(team_registry_file):
    create_canonical_team(display_name="Manchester United", sport="football")
    create_canonical_team(display_name="Liverpool", sport="football")
    raw = [
        _offer("maxbet", "Manchester United", "Liverpool", outcome_code="over"),
        _offer("balkanbet", "Manchester United U21", "Liverpool U21", outcome_code="under"),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert {offer.bookmaker_id for offer in normalized} == {"maxbet"}
    assert any(row.bookmaker_id == "balkanbet" for row in unresolved)
    assert _auto_review_alias_count() == 0


def test_football_event_match_rejects_split_u21_qualifier(team_registry_file):
    create_canonical_team(display_name="Manchester United", sport="football")
    create_canonical_team(display_name="Liverpool", sport="football")
    raw = [
        _offer("maxbet", "Manchester United", "Liverpool", outcome_code="over"),
        _offer("balkanbet", "Manchester United U-21", "Liverpool U-21", outcome_code="under"),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert {offer.bookmaker_id for offer in normalized} == {"maxbet"}
    assert any(row.bookmaker_id == "balkanbet" for row in unresolved)
    assert _auto_review_alias_count() == 0


def test_football_event_match_rejects_b_team_qualifier(team_registry_file):
    create_canonical_team(display_name="Real Madrid", sport="football")
    create_canonical_team(display_name="Barcelona", sport="football")
    raw = [
        _offer("maxbet", "Real Madrid", "Barcelona", outcome_code="over"),
        _offer("balkanbet", "Real Madrid B Team", "Barcelona B Team", outcome_code="under"),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert {offer.bookmaker_id for offer in normalized} == {"maxbet"}
    assert any(row.bookmaker_id == "balkanbet" for row in unresolved)
    assert _auto_review_alias_count() == 0


def test_football_event_match_rejects_b_team_youth_qualifier(team_registry_file):
    create_canonical_team(display_name="Barcelona U19", sport="football")
    create_canonical_team(display_name="Real Madrid U19", sport="football")
    raw = [
        _offer("maxbet", "Barcelona U19", "Real Madrid U19", outcome_code="over"),
        _offer("balkanbet", "Barcelona B U-19", "Real Madrid B U-19", outcome_code="under"),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert {offer.bookmaker_id for offer in normalized} == {"maxbet"}
    assert any(row.bookmaker_id == "balkanbet" for row in unresolved)
    assert _auto_review_alias_count() == 0


def test_reversed_unknown_football_market_is_not_emitted_with_wrong_orientation(team_registry_file):
    create_canonical_team(display_name="Team Alpha", sport="football")
    create_canonical_team(display_name="Team Beta", sport="football")
    raw = [
        _offer(
            "maxbet",
            "Team Alpha",
            "Team Beta",
            market_type="football_future_market",
            outcome_code="home_side",
        ),
        _offer(
            "balkanbet",
            "Team Beta",
            "Team Alpha",
            market_type="football_future_market",
            outcome_code="home_side",
        ),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert unresolved == []
    assert [(offer.bookmaker_id, offer.outcome_code) for offer in normalized] == [
        ("maxbet", "home_side")
    ]
    assert _auto_review_alias_count() == 0
