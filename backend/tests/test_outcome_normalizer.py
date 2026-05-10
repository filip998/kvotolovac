from __future__ import annotations

import sqlite3

from app.config import settings
from app.models.schemas import RawOddsData, RawOutcomeOffer
from app.services import normalizer as normalizer_service
from app.services.normalizer import (
    TeamReviewDiagnosticsMetrics,
    build_team_review_cases_for_diagnostics,
    generate_match_id,
    normalize_odds_with_diagnostics,
)
from app.services.outcome_normalizer import (
    _FootballEventResolutionStats,
    _build_football_event_resolutions,
    _same_team_context,
    _team_similarity,
    _team_qualifiers,
    normalize_outcome_offers_with_benchmark,
    normalize_outcome_offers_with_diagnostics,
)
from app.services.team_registry import create_canonical_team, remember_team_alias


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


def _tennis_offer(
    bookmaker_id: str,
    home_team: str,
    away_team: str,
    *,
    market_type: str = "tennis_match_winner",
    outcome_code: str = "home",
) -> RawOutcomeOffer:
    return RawOutcomeOffer(
        bookmaker_id=bookmaker_id,
        league_id="tennis_test_league",
        sport="tennis",
        home_team=home_team,
        away_team=away_team,
        market_type=market_type,
        outcome_code=outcome_code,
        odds=2.1,
        line=None,
        raw_label=outcome_code,
        start_time=START_TIME,
    )


def _proxy_row(
    bookmaker_id: str,
    home_team: str,
    away_team: str,
    *,
    start_time: str | None = START_TIME,
) -> RawOddsData:
    return RawOddsData(
        bookmaker_id=bookmaker_id,
        league_id="football_test_league",
        sport="football",
        home_team=home_team,
        away_team=away_team,
        source_url=None,
        market_type="football_result",
        player_name=None,
        threshold=0.0,
        over_odds=1.9,
        under_odds=None,
        start_time=start_time,
    )


def _review_payloads(cases):
    return [case.model_dump(mode="json") for case in cases]


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


def test_team_review_proxy_helper_matches_legacy_diagnostics(team_registry_file):
    create_canonical_team(display_name="Municipal Limeno", sport="football")
    create_canonical_team(display_name="Aston Villa", sport="football")
    create_canonical_team(display_name="Nottingham Forest", sport="football")
    create_canonical_team(display_name="Nottingham Forrest", sport="football")
    rows = [
        _proxy_row("admiralbet", "CD Municipal Limeno", "Completely New Opponent"),
        _proxy_row("superbet", "Aston Villa", "Nottingham Forest"),
        _proxy_row("maxbet", "Aston Villa", "Nottingham Forrest"),
        _proxy_row("365", "Aston Villa", "Nottm.Forest"),
        _proxy_row("pinnbet", "No Start Home", "No Start Away", start_time=None),
    ]

    legacy_cases = normalize_odds_with_diagnostics(
        rows,
        log_unresolved_shared_platform=False,
    )[2]
    helper_cases = build_team_review_cases_for_diagnostics(rows)

    assert _review_payloads(helper_cases) == _review_payloads(legacy_cases)


def test_team_review_proxy_helper_preserves_exact_autocreate_side_effect(team_registry_file):
    rows = [
        _proxy_row("superbet", "Batch Proxy Home", "Batch Proxy Away"),
        _proxy_row("balkanbet", "Batch Proxy Home", "Batch Proxy Away"),
    ]

    review_cases = build_team_review_cases_for_diagnostics(rows)

    assert review_cases == []
    assert {"Batch Proxy Home", "Batch Proxy Away"} <= _canonical_team_names()


def test_team_review_proxy_helper_caches_global_candidate_search(
    monkeypatch,
    team_registry_file,
):
    create_canonical_team(display_name="Municipal Limeno", sport="football")
    calls: list[tuple[str, str]] = []
    original_search = normalizer_service.search_canonical_team_candidates

    def spy_search(raw_team_name, *, sport, limit):
        calls.append((raw_team_name, sport))
        return original_search(raw_team_name, sport=sport, limit=limit)

    monkeypatch.setattr(
        normalizer_service,
        "search_canonical_team_candidates",
        spy_search,
    )
    metrics = TeamReviewDiagnosticsMetrics()
    rows = [
        _proxy_row("superbet", "CD Municipal Limeno", "Unknown One"),
        _proxy_row("balkanbet", "CD Municipal Limeno", "Unknown Two"),
    ]

    build_team_review_cases_for_diagnostics(rows, metrics=metrics)

    assert calls.count(("CD Municipal Limeno", "football")) == 1
    assert metrics.global_candidate_cache_hit_count >= 1


def test_team_review_proxy_helper_caches_slot_candidate_search(team_registry_file):
    create_canonical_team(display_name="Aston Villa", sport="football")
    create_canonical_team(display_name="Nottingham Forest", sport="football")
    create_canonical_team(display_name="Nottingham Forrest", sport="football")
    metrics = TeamReviewDiagnosticsMetrics()
    rows = [
        _proxy_row("superbet", "Aston Villa", "Nottingham Forest"),
        _proxy_row("maxbet", "Aston Villa", "Nottingham Forrest"),
        _proxy_row("365", "Aston Villa", "Nottm.Forest"),
        _proxy_row("365", "Aston Villa", "Nottm.Forest"),
    ]

    review_cases = build_team_review_cases_for_diagnostics(rows, metrics=metrics)

    assert any(case.raw_team_name == "Nottm.Forest" for case in review_cases)
    assert metrics.slot_candidate_search_count >= 1
    assert metrics.slot_candidate_cache_hit_count >= 1


def test_team_qualifiers_treat_prefix_women_as_explicit_marker():
    assert _team_qualifiers("Women Sao Jose", sport="football") == {"women"}
    assert _same_team_context("Women Sao Jose", "Sao Jose Women", sport="football")


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


def test_cross_book_football_autocreate_reports_multiple_batch_created_teams(team_registry_file):
    raw = [
        _offer("maxbet", "Batch Home FC", "Batch Away FC", outcome_code="over"),
        _offer("balkanbet", "Batch Home FC", "Batch Away FC", outcome_code="under"),
    ]

    normalized, unresolved, review_cases, benchmark = normalize_outcome_offers_with_benchmark(raw)

    assert len(normalized) == 2
    assert unresolved == []
    assert review_cases == []
    assert benchmark.auto_created_football_team_count == 2
    assert {"Batch Home FC", "Batch Away FC"} <= _canonical_team_names()
    assert len(benchmark.run_details) == 1
    assert benchmark.run_details[0].raw_outcome_offer_count == 2
    assert benchmark.run_details[0].event_resolution_offer_count == 2
    assert benchmark.event_resolution_offer_count == 2
    assert benchmark.direct_resolution_attempt_count == 0
    assert benchmark.row_iteration_ms <= benchmark.row_normalization_ms
    assert {
        row.bookmaker_id: row.raw_rows for row in benchmark.bookmakers
    } == {"balkanbet": 1, "maxbet": 1}
    assert {
        row.bookmaker_id: row.event_resolution_rows for row in benchmark.bookmakers
    } == {"balkanbet": 1, "maxbet": 1}
    assert benchmark.top_football_event_buckets
    assert benchmark.top_football_event_buckets[0].event_count == 2
    assert benchmark.top_football_event_buckets[0].candidate_pair_count == 1


def test_football_event_resolution_benchmark_counts_pair_and_fuzzy_work(team_registry_file):
    raw = [
        _offer("maxbet", "Basket Sibirsk", "CSKA Moscow", outcome_code="over"),
        _offer("balkanbet", "Blec Sybirsk", "CSKA Moscow", outcome_code="under"),
    ]
    stats = _FootballEventResolutionStats()

    _build_football_event_resolutions(raw, stats=stats)

    assert stats.football_event_pair_candidate_count == 1
    assert stats.football_event_fuzzy_score_count >= 1


def test_football_event_resolution_skips_supported_disjoint_canonical_slots(
    team_registry_file,
):
    for name in ("Team Alpha", "Team Beta", "Team Gamma", "Team Delta"):
        create_canonical_team(display_name=name, sport="football")
    raw = [
        _offer("book-a", "Team Alpha", "Team Beta", outcome_code="over"),
        _offer("book-b", "Team Alpha", "Team Beta", outcome_code="under"),
        _offer("book-c", "Team Gamma", "Team Delta", outcome_code="over"),
        _offer("book-d", "Team Gamma", "Team Delta", outcome_code="under"),
    ]
    stats = _FootballEventResolutionStats()

    _build_football_event_resolutions(raw, stats=stats)

    assert stats.football_event_canonical_conflict_skip_count == 4
    assert stats.football_event_canonical_conflict_fuzzy_score_avoided_count == 16
    assert stats.football_event_pair_candidate_count == 2


def test_football_event_resolution_keeps_singleton_disjoint_slots_for_repair(
    team_registry_file,
):
    for name in ("Basket Sibirsk", "Blec Sybirsk", "CSKA Moscow", "CSKA Moskva"):
        create_canonical_team(display_name=name, sport="football")
    raw = [
        _offer("maxbet", "Basket Sibirsk", "CSKA Moscow", outcome_code="over"),
        _offer("balkanbet", "Blec Sybirsk", "CSKA Moskva", outcome_code="under"),
    ]
    stats = _FootballEventResolutionStats()

    _build_football_event_resolutions(raw, stats=stats)

    assert stats.football_event_canonical_conflict_skip_count == 0
    assert stats.football_event_pair_candidate_count == 1


def test_football_event_resolution_keeps_text_overlap_disjoint_slots(
    team_registry_file,
):
    for name in (
        "Sao Jose Campos",
        "Santo Andre",
        "Sao Jose Wom.",
        "Santo Andre Wom.",
    ):
        create_canonical_team(display_name=name, sport="football")
    raw = [
        _offer("book-a", "Sao Jose Campos", "Santo Andre", outcome_code="over"),
        _offer("book-b", "Sao Jose Campos", "Santo Andre", outcome_code="under"),
        _offer("book-c", "Sao Jose Wom.", "Santo Andre Wom.", outcome_code="over"),
        _offer("book-d", "Sao Jose Wom.", "Santo Andre Wom.", outcome_code="under"),
    ]
    stats = _FootballEventResolutionStats()

    _build_football_event_resolutions(raw, stats=stats)

    assert stats.football_event_canonical_conflict_skip_count == 0
    assert stats.football_event_pair_candidate_count >= 4


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


def test_unresolved_football_outcome_includes_same_slot_context(team_registry_file):
    create_canonical_team(display_name="Aston Villa", sport="football")
    create_canonical_team(display_name="Nottingham Forest", sport="football")
    create_canonical_team(display_name="Nottingham Forrest", sport="football")
    raw = [
        _offer(
            "superbet",
            "Aston Villa",
            "Nottingham Forest",
            market_type="football_result",
            outcome_code="home",
        ),
        _offer(
            "maxbet",
            "Aston Villa",
            "Nottingham Forrest",
            market_type="football_result",
            outcome_code="away",
        ),
        _offer(
            "365",
            "Aston Villa",
            "Nottm.Forest",
            market_type="football_result",
            outcome_code="away",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    warning = next(row for row in unresolved if row.bookmaker_id == "365")
    assert {offer.bookmaker_id for offer in normalized} == {"superbet", "maxbet"}
    assert warning.raw_team_name == "Nottm.Forest"
    assert warning.reason_code == "unresolved_away_team"
    assert warning.candidate_count == 2
    assert warning.candidate_matchups == [
        "Aston Villa vs Nottingham Forest",
        "Aston Villa vs Nottingham Forrest",
    ]
    assert warning.available_matchups_same_slot == [
        "Aston Villa vs Nottingham Forest",
        "Aston Villa vs Nottingham Forrest",
    ]
    assert any(case.raw_team_name == "Nottm.Forest" for case in review_cases)
    assert _auto_review_alias_count() == 0


def test_empty_football_outcome_side_remains_unresolved(team_registry_file):
    create_canonical_team(display_name="Aston Villa", sport="football")
    create_canonical_team(display_name="Nottingham Forest", sport="football")
    raw = [
        _offer(
            "superbet",
            "Aston Villa",
            "Nottingham Forest",
            market_type="football_result",
            outcome_code="home",
        ),
        _offer(
            "365",
            "Aston Villa",
            "",
            market_type="football_result",
            outcome_code="away",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert {offer.bookmaker_id for offer in normalized} == {"superbet"}
    warning = next(row for row in unresolved if row.bookmaker_id == "365")
    assert warning.raw_team_name == ""
    assert warning.reason_code == "unresolved_away_team"
    assert review_cases == []


def test_football_unresolved_team_dedupes_across_outcome_markets(team_registry_file):
    create_canonical_team(display_name="Known Away", sport="football")
    raw = [
        _offer(
            "365",
            "Unknown Home",
            "Known Away",
            market_type="football_result",
            outcome_code="home",
        ),
        _offer(
            "365",
            "Unknown Home",
            "Known Away",
            market_type="football_double_chance",
            outcome_code="home_or_draw",
        ),
        _offer(
            "365",
            "Unknown Home",
            "Known Away",
            market_type="football_total_goals",
            outcome_code="over",
        ),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert normalized == []
    assert [
        (row.raw_team_name, row.reason_code)
        for row in unresolved
    ] == [("Unknown Home", "unresolved_home_team")]


def test_outcome_benchmark_distinguishes_football_alias_misses_from_unknowns(
    team_registry_file,
):
    create_canonical_team(display_name="Municipal Limeno", sport="football")
    raw = [
        _offer(
            "admiralbet",
            "CD Municipal Limeno",
            "Completely New Opponent",
            market_type="football_result",
            outcome_code="home",
        )
    ]

    _normalized, unresolved, review_cases, benchmark = (
        normalize_outcome_offers_with_benchmark(raw)
    )

    assert len(unresolved) == 2
    assert {case.raw_team_name for case in review_cases} == {
        "CD Municipal Limeno",
        "Completely New Opponent",
    }
    assert benchmark.football_team_review_case_count == 2
    assert benchmark.football_team_review_alias_miss_count == 1
    assert benchmark.football_team_review_unknown_count == 1
    assert benchmark.football_team_review_global_alias_miss_count == 1
    assert benchmark.football_team_review_same_slot_alias_miss_count == 0
    assert benchmark.direct_resolution_attempt_count == 1
    assert benchmark.skipped_unresolved_row_count == 1
    assert benchmark.unresolved_context_ms >= 0


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


def test_reversed_tennis_match_winner_swaps_home_away_outcomes(team_registry_file):
    home = create_canonical_team(display_name="Novak Djokovic", sport="tennis")
    away = create_canonical_team(display_name="Carlos Alcaraz", sport="tennis")
    remember_team_alias(
        bookmaker_id="maxbet",
        raw_team_name="Djokovic N.",
        team_name="Novak Djokovic",
        sport="tennis",
    )
    remember_team_alias(
        bookmaker_id="maxbet",
        raw_team_name="Alcaraz C.",
        team_name="Carlos Alcaraz",
        sport="tennis",
    )
    expected_match_id = generate_match_id(
        home.team_id,
        away.team_id,
        START_TIME,
        "tennis",
    )
    raw = [
        _tennis_offer("maxbet", "Djokovic N.", "Alcaraz C.", outcome_code="home"),
        _tennis_offer(
            "balkanbet",
            "Carlos Alcaraz",
            "Novak Djokovic",
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


def test_tennis_initial_variants_merge_when_unambiguous(team_registry_file):
    raw = [
        _tennis_offer("maxbet", "Djokovic N.", "Alcaraz C.", outcome_code="home"),
        _tennis_offer(
            "balkanbet",
            "Novak Djokovic",
            "Carlos Alcaraz",
            outcome_code="away",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert unresolved == []
    assert review_cases == []


def test_tennis_shared_surname_initial_collision_remains_unresolved(team_registry_file):
    raw = [
        _tennis_offer("maxbet", "Smith J.", "Brown A.", outcome_code="home"),
        _tennis_offer("balkanbet", "Smith A.", "Brown A.", outcome_code="away"),
    ]

    normalized, unresolved, _ = normalize_outcome_offers_with_diagnostics(raw)

    assert normalized == []
    assert {row.reason_code for row in unresolved} == {
        "unresolved_home_team",
        "unresolved_away_team",
    }


def test_single_book_tennis_uses_event_resolution_when_competitors_are_known(
    team_registry_file,
):
    create_canonical_team(display_name="Novak Djokovic", sport="tennis")
    create_canonical_team(display_name="Carlos Alcaraz", sport="tennis")
    raw = [
        _tennis_offer("maxbet", "Novak Djokovic", "Carlos Alcaraz", outcome_code="home")
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 1
    assert unresolved == []
    assert review_cases == []


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


def test_football_event_match_ignores_equivalent_women_marker_text(team_registry_file):
    raw = [
        _offer(
            "superbet",
            "Sao Jose Campos (Ž)",
            "Santo Andre (Ž)",
            outcome_code="over",
        ),
        _offer(
            "pinnbet",
            "Sao Jose Wom.",
            "Santo Andre Wom.",
            outcome_code="under",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_collapses_existing_women_marker_variant_canonicals(team_registry_file):
    create_canonical_team(display_name="Sao Jose Campos (Ž)", sport="football")
    create_canonical_team(display_name="Santo Andre (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Wom.", sport="football")
    create_canonical_team(display_name="Santo Andre Wom.", sport="football")
    raw = [
        _offer(
            "superbet",
            "Sao Jose Campos (Ž)",
            "Santo Andre (Ž)",
            outcome_code="over",
        ),
        _offer(
            "pinnbet",
            "Sao Jose Wom.",
            "Santo Andre Wom.",
            outcome_code="under",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert {
        (offer.home_team_id, offer.away_team_id) for offer in normalized
    } == {(normalized[0].home_team_id, normalized[0].away_team_id)}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_collapses_one_sided_existing_women_marker_variant(team_registry_file):
    create_canonical_team(display_name="Sao Jose Campos (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Wom.", sport="football")
    create_canonical_team(display_name="Santo Andre", sport="football")
    raw = [
        _offer(
            "superbet",
            "Sao Jose Campos (Ž)",
            "Santo Andre",
            outcome_code="over",
        ),
        _offer(
            "pinnbet",
            "Sao Jose Wom.",
            "Santo Andre",
            outcome_code="under",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert {
        (offer.home_team_id, offer.away_team_id) for offer in normalized
    } == {(normalized[0].home_team_id, normalized[0].away_team_id)}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_collapses_marker_variant_with_multiple_same_style_counterparts(team_registry_file):
    create_canonical_team(display_name="Sao Jose Campos (Ž)", sport="football")
    create_canonical_team(display_name="Santo Andre (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Wom.", sport="football")
    create_canonical_team(display_name="Santo Andre Wom.", sport="football")
    raw = [
        _offer(
            "superbet",
            "Sao Jose Campos (Ž)",
            "Santo Andre (Ž)",
            outcome_code="over",
        ),
        _offer(
            "pinnbet",
            "Sao Jose Wom.",
            "Santo Andre Wom.",
            outcome_code="under",
        ),
        _offer(
            "maxbet",
            "Sao Jose Wom.",
            "Santo Andre Wom.",
            outcome_code="under",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 3
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert {
        (offer.home_team_id, offer.away_team_id) for offer in normalized
    } == {(normalized[0].home_team_id, normalized[0].away_team_id)}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_collapses_prefix_suffix_women_marker_variants(team_registry_file):
    create_canonical_team(display_name="Women Sao Jose Campos", sport="football")
    create_canonical_team(display_name="Sao Jose Women", sport="football")
    create_canonical_team(display_name="Santo Andre", sport="football")
    raw = [
        _offer("superbet", "Women Sao Jose Campos", "Santo Andre", outcome_code="over"),
        _offer("pinnbet", "Sao Jose Women", "Santo Andre", outcome_code="under"),
        _offer("maxbet", "Sao Jose Women", "Santo Andre", outcome_code="under"),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 3
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_does_not_bypass_ranking_for_same_women_marker_style(team_registry_file):
    create_canonical_team(display_name="Sao Jose (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Campos (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Dos Campos (Ž)", sport="football")
    create_canonical_team(display_name="Santo Andre", sport="football")
    raw = [
        _offer("superbet", "Sao Jose (Ž)", "Santo Andre", outcome_code="over"),
        _offer("pinnbet", "Sao Jose Campos (Ž)", "Santo Andre", outcome_code="under"),
        _offer(
            "maxbet",
            "Sao Jose Dos Campos (Ž)",
            "Santo Andre",
            outcome_code="under",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)
    match_ids = {offer.bookmaker_id: offer.match_id for offer in normalized}

    assert len(normalized) == 3
    assert match_ids["pinnbet"] == match_ids["maxbet"]
    assert match_ids["superbet"] != match_ids["pinnbet"]
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_moves_whole_component_for_later_marker_pair(team_registry_file):
    create_canonical_team(display_name="Sao Jose Wom.", sport="football")
    create_canonical_team(display_name="Sao Jose Campos (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Dos Campos (Ž)", sport="football")
    create_canonical_team(display_name="Santo Andre", sport="football")
    raw = [
        _offer("superbet", "Sao Jose Wom.", "Santo Andre", outcome_code="over"),
        _offer("pinnbet", "Sao Jose Campos (Ž)", "Santo Andre", outcome_code="under"),
        _offer(
            "maxbet",
            "Sao Jose Dos Campos (Ž)",
            "Santo Andre",
            outcome_code="under",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 3
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_does_not_bypass_ranking_for_different_women_stems(team_registry_file):
    create_canonical_team(display_name="Manchester United Women", sport="football")
    create_canonical_team(display_name="Manchester City Wom.", sport="football")
    create_canonical_team(display_name="Liverpool Women", sport="football")
    raw = [
        _offer("superbet", "Manchester United Women", "Liverpool Women", outcome_code="over"),
        _offer("pinnbet", "Manchester United Women", "Liverpool Women", outcome_code="under"),
        _offer("maxbet", "Manchester City Wom.", "Liverpool Women", outcome_code="under"),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)
    match_ids = {offer.bookmaker_id: offer.match_id for offer in normalized}

    assert len(normalized) == 3
    assert match_ids["superbet"] == match_ids["pinnbet"]
    assert match_ids["maxbet"] != match_ids["superbet"]
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_football_event_match_uses_canonical_ids_for_unmarked_alias_side_in_marker_bypass(team_registry_file):
    create_canonical_team(display_name="Sao Jose Campos (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Dos Campos Wom.", sport="football")
    create_canonical_team(display_name="Santo Andre", sport="football")
    remember_team_alias(
        bookmaker_id="superbet",
        raw_team_name="St Andre",
        team_name="Santo Andre",
        sport="football",
    )
    raw = [
        _offer("superbet", "Sao Jose Campos (Ž)", "St Andre", outcome_code="over"),
        _offer("pinnbet", "Sao Jose Dos Campos Wom.", "Santo Andre", outcome_code="under"),
        _offer("maxbet", "Sao Jose Dos Campos Wom.", "Santo Andre", outcome_code="under"),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 3
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert unresolved == []
    assert review_cases == []
    assert _auto_review_alias_count() == 0


def test_reversed_football_event_collapses_existing_women_marker_variant_canonicals(team_registry_file):
    create_canonical_team(display_name="Sao Jose Campos (Ž)", sport="football")
    create_canonical_team(display_name="Santo Andre (Ž)", sport="football")
    create_canonical_team(display_name="Sao Jose Wom.", sport="football")
    create_canonical_team(display_name="Santo Andre Wom.", sport="football")
    raw = [
        _offer(
            "superbet",
            "Sao Jose Campos (Ž)",
            "Santo Andre (Ž)",
            market_type="football_result",
            outcome_code="home",
        ),
        _offer(
            "pinnbet",
            "Santo Andre Wom.",
            "Sao Jose Wom.",
            market_type="football_result",
            outcome_code="home",
        ),
    ]

    normalized, unresolved, review_cases = normalize_outcome_offers_with_diagnostics(raw)

    assert len(normalized) == 2
    assert {offer.match_id for offer in normalized} == {normalized[0].match_id}
    assert {
        (offer.bookmaker_id, offer.outcome_code)
        for offer in normalized
    } == {("superbet", "home"), ("pinnbet", "away")}
    assert unresolved == []
    assert review_cases == []
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
