from __future__ import annotations

from app.models.schemas import (
    NormalizedOdds,
    NormalizedOutcomeOffer,
    RawOddsData,
    RawOutcomeOffer,
)
from app.services.match_unification import candidate_extraction as extraction_module
from app.services.match_unification.candidate_extraction import (
    _EventCandidateExtractionStats,
    _raw_odds_sources,
    extract_event_candidates,
)
from app.services.match_unification.source_matching import (
    RawEventSource,
    SourceMatcher,
    SourceMatchQuery,
)
from app.services.normalizer import generate_match_id
from app.services.outcome_normalizer import (
    _event_key_from_raw,
    _OutcomeEventResolution,
    _OutcomeEventSlot,
)


START_TIME = "2030-01-01T20:00:00+00:00"


def _normalized_odds(
    bookmaker_id: str,
    *,
    match_id: str,
    league_id: str,
    home_team_id: int,
    away_team_id: int,
    home_team: str,
    away_team: str,
    threshold: float,
    source_url: str | None = None,
) -> NormalizedOdds:
    return NormalizedOdds(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id=league_id,
        sport="basketball",
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team=home_team,
        away_team=away_team,
        source_url=source_url,
        market_type="player_points",
        player_name="Test Player",
        threshold=threshold,
        over_odds=1.9,
        under_odds=1.9,
        start_time=START_TIME,
    )


def _raw_odds(
    *,
    bookmaker_id: str = "book-a",
    league_id: str = "league",
    home_team: str = "Team Alpha",
    away_team: str = "Team Beta",
    source_url: str | None = "https://example.test/match",
    threshold: float = 10.5,
) -> RawOddsData:
    return RawOddsData(
        bookmaker_id=bookmaker_id,
        league_id=league_id,
        sport="basketball",
        home_team=home_team,
        away_team=away_team,
        source_url=source_url,
        market_type="player_points",
        player_name="Test Player",
        threshold=threshold,
        over_odds=1.9,
        under_odds=1.9,
        start_time=START_TIME,
    )


def _raw_source(
    *,
    bookmaker_id: str = "book-a",
    sport: str = "basketball",
    start_time: str = START_TIME,
    home_team: str = "Team Alpha",
    away_team: str = "Team Beta",
    league_id: str = "league",
    league_name: str = "League",
    source_url: str | None = "https://example.test/match",
    source_kind: str = "raw_odds",
) -> RawEventSource:
    return RawEventSource(
        bookmaker_id=bookmaker_id,
        sport=sport,
        start_time=start_time,
        home_team=home_team,
        away_team=away_team,
        league_id=league_id,
        league_name=league_name,
        source_url=source_url,
        source_kind=source_kind,
    )


def _normalized_outcome_offer(
    *,
    bookmaker_id: str = "book-a",
    match_id: str = "match-a",
    league_id: str = "league",
    home_team: str = "Team Alpha",
    away_team: str = "Team Beta",
    source_url: str | None = None,
) -> NormalizedOutcomeOffer:
    return NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id=league_id,
        sport="basketball",
        home_team_id=1,
        away_team_id=2,
        home_team=home_team,
        away_team=away_team,
        source_url=source_url,
        market_type="match_winner",
        outcome_code="home",
        odds=1.9,
        start_time=START_TIME,
    )


def test_source_match_interface_exact_url_uses_fast_path_without_fallback():
    matcher = SourceMatcher(
        [
            _raw_source(source_url="https://example.test/match"),
            _raw_source(
                home_team="Team Gamma",
                away_team="Team Delta",
                source_url="https://example.test/other",
            ),
        ]
    )

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Team Alpha",
            away_team="Team Beta",
            source_url="https://example.test/match",
            league_id="league",
        )
    )

    assert result.strategy == "exact_url"
    assert result.source is not None
    assert result.source.source_url == "https://example.test/match"
    assert result.fallback_scan_attempted is False
    assert result.score is not None
    assert result.score >= 100.0
    assert result.score > result.threshold
    assert result.orientation == "same_order"


def test_source_match_interface_listed_pair_when_url_is_missing():
    matcher = SourceMatcher([_raw_source(source_url=None)])

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Team Alpha",
            away_team="Team Beta",
            league_id="league",
        )
    )

    assert result.strategy == "listed_pair"
    assert result.source is not None
    assert result.reason == "accepted"
    assert result.orientation == "same_order"
    assert result.fallback_scan_attempted is False


def test_source_match_interface_unordered_pair_records_reversed_orientation():
    matcher = SourceMatcher(
        [
            _raw_source(
                home_team="Team Beta",
                away_team="Team Alpha",
                source_url=None,
            )
        ]
    )

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Team Alpha",
            away_team="Team Beta",
            league_id="league",
        )
    )

    assert result.strategy == "unordered_pair"
    assert result.source is not None
    assert result.orientation == "reversed"


def test_source_match_interface_fallback_scan_hit_is_observable():
    matcher = SourceMatcher(
        [
            _raw_source(
                home_team="Team Alpha FC",
                away_team="Team Beta FC",
                source_url=None,
            )
        ]
    )

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Alpha Team",
            away_team="Beta Team",
            league_id="league",
        )
    )

    assert result.strategy == "fallback_scan"
    assert result.source is not None
    assert result.fallback_scan_attempted is True
    assert result.reason == "accepted"


def test_source_match_interface_fallback_scan_miss_records_below_threshold_reason():
    matcher = SourceMatcher(
        [
            _raw_source(
                home_team="Red Sharks",
                away_team="Blue Whales",
                source_url=None,
            )
        ]
    )

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Team Alpha",
            away_team="Team Beta",
            league_id="league",
        )
    )

    assert result.strategy == "no_match"
    assert result.source is None
    assert result.fallback_scan_attempted is True
    assert result.reason == "score_below_threshold"
    assert result.score is not None
    assert result.score < result.threshold
    assert result.attempts[-1].decision == "score_below_threshold"


def test_source_match_interface_no_slot_is_observable():
    matcher = SourceMatcher([])

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Team Alpha",
            away_team="Team Beta",
            league_id="league",
        )
    )

    assert result.strategy == "no_slot"
    assert result.reason == "no_slot"
    assert result.source is None
    assert result.source_count_in_slot == 0
    assert result.slot_key == ("book-a", "basketball", START_TIME)


def test_source_match_interface_dense_slot_chooses_highest_scoring_source():
    matcher = SourceMatcher(
        [
            _raw_source(
                home_team="Team Alpha",
                away_team="Team Beta",
                league_id="other-league",
                source_url="https://example.test/listed-only",
            ),
            _raw_source(
                home_team="Alpha Team",
                away_team="Beta Team",
                league_id="league",
                source_url="https://example.test/fuzzy-with-league",
            ),
        ]
    )

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Team Alpha",
            away_team="Team Beta",
            league_id="league",
        )
    )

    assert result.strategy == "fallback_scan"
    assert result.source is not None
    assert result.source.source_url == "https://example.test/fuzzy-with-league"
    assert any(
        attempt.strategy == "listed_pair" and attempt.reason == "below_slot_max"
        for attempt in result.attempts
    )


def test_source_match_interface_rejects_non_first_fast_path_before_fallback():
    matcher = SourceMatcher(
        [
            _raw_source(
                home_team="Team Gamma",
                away_team="Team Delta",
                source_url=None,
            ),
            _raw_source(
                home_team="Team Alpha",
                away_team="Team Beta",
                source_url=None,
            ),
        ]
    )

    result = matcher.match(
        SourceMatchQuery(
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team="Team Alpha",
            away_team="Team Beta",
            league_id="league",
        )
    )

    assert result.strategy == "fallback_scan"
    assert result.source is not None
    assert result.source.home_team == "Team Alpha"
    assert any(
        attempt.strategy == "listed_pair"
        and attempt.reason == "not_first_slot_source"
        for attempt in result.attempts
    )


def test_extract_event_candidates_dedupes_normalized_rows_before_source_matching():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[_raw_odds()],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=11.5,
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_kind == "raw_odds"
    assert stats.normalized_odds_rows_scanned == 2
    assert stats.normalized_odds_candidates_emitted == 1
    assert stats.source_match_lookup_count == 1
    assert stats.source_match_source_count == 1


def test_source_match_exact_url_fast_path_avoids_full_slot_scan():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[
            _raw_odds(source_url="https://example.test/match"),
            _raw_odds(
                home_team="Team Gamma",
                away_team="Team Delta",
                source_url="https://example.test/other",
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
                source_url="https://example.test/match",
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.test/match"
    assert candidates[0].source_kind == "raw_odds"
    assert stats.source_match_source_count == 2
    assert stats.source_match_scored_source_count == 1
    assert stats.source_match_index_candidate_count == 1
    assert stats.source_match_exact_url_hit_count == 1
    assert stats.source_match_fallback_scan_count == 0


def test_source_match_listed_pair_fast_path_when_first_slot_source_is_exact_match():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[
            _raw_odds(source_url="https://example.test/match"),
            _raw_odds(
                home_team="Team Gamma",
                away_team="Team Delta",
                source_url="https://example.test/other",
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.test/match"
    assert stats.source_match_scored_source_count == 1
    assert stats.source_match_listed_pair_hit_count == 1
    assert stats.source_match_fallback_scan_count == 0


def test_source_match_unordered_pair_fast_path_preserves_reversed_source_metadata():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[
            _raw_odds(
                home_team="Team Beta",
                away_team="Team Alpha",
                source_url="https://example.test/reversed",
            ),
            _raw_odds(
                home_team="Team Gamma",
                away_team="Team Delta",
                source_url="https://example.test/other",
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_home_team == "Team Beta"
    assert candidates[0].source_away_team == "Team Alpha"
    assert candidates[0].source_url == "https://example.test/reversed"
    assert stats.source_match_scored_source_count == 1
    assert stats.source_match_unordered_pair_hit_count == 1
    assert stats.source_match_fallback_scan_count == 0


def test_source_match_index_hit_falls_back_when_full_slot_can_score_higher():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[
            _raw_odds(
                home_team="Team Alpha",
                away_team="Team Beta",
                source_url="https://example.test/listed-only",
                league_id="other-league",
            ),
            _raw_odds(
                home_team="Team Alphaa",
                away_team="Team Beta",
                source_url="https://example.test/fuzzy-with-league",
                league_id="league",
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.test/fuzzy-with-league"
    assert stats.source_match_listed_pair_hit_count == 0
    assert stats.source_match_fallback_scan_count == 1
    assert stats.source_match_source_count == 2
    assert stats.source_match_scored_source_count == 2


def test_source_match_duplicate_pair_falls_back_to_preserve_first_slot_tie():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[
            _raw_odds(
                source_url="https://example.test/first",
                threshold=10.5,
            ),
            _raw_odds(
                source_url="https://example.test/second",
                threshold=11.5,
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
                source_url="https://example.test/second",
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.test/second"
    assert stats.source_match_exact_url_hit_count == 1
    assert stats.source_match_fallback_scan_count == 0


def test_extract_event_candidates_preserves_non_first_fast_path_fallback_gate():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[
            _raw_odds(
                home_team="Team Gamma",
                away_team="Team Delta",
                source_url="https://example.test/first",
            ),
            _raw_odds(
                home_team="Team Alpha",
                away_team="Team Beta",
                source_url="https://example.test/second",
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_url == "https://example.test/second"
    assert stats.source_match_fallback_scan_count == 1
    assert stats.source_match_fallback_scan_hit_count == 1
    assert stats.source_match_fallback_scan_miss_count == 0
    assert (
        stats.source_match_fallback_scan_hit_count
        == stats.source_match_strategy_counts["fallback_scan"]
    )
    assert stats.source_match_rejected_fast_path_count == 2
    assert stats.source_match_reason_counts["accepted"] == 1
    assert stats.source_match_attempt_reason_counts["not_first_slot_source"] == 2


def test_source_match_no_index_hit_uses_fallback_scan():
    stats = _EventCandidateExtractionStats()
    candidates = extract_event_candidates(
        raw_odds=[
            _raw_odds(
                home_team="Team Alpha",
                away_team="Team Beta",
                source_url=None,
            ),
            _raw_odds(
                home_team="Team Gamma",
                away_team="Team Delta",
                source_url=None,
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Unlisted Home",
                away_team="Unlisted Away",
                threshold=10.5,
                source_url="https://example.test/missing",
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert candidates[0].source_kind == "normalized_odds"
    assert candidates[0].source_url == "https://example.test/missing"
    assert stats.source_match_fallback_scan_count == 1
    assert stats.source_match_fallback_scan_miss_count == 1
    assert stats.source_match_fallback_scan_hit_count == 0
    assert stats.source_match_source_count == 2
    assert stats.source_match_scored_source_count == 2


def test_source_match_stats_separate_no_slot_from_no_match():
    stats = _EventCandidateExtractionStats()

    candidates = extract_event_candidates(
        raw_odds=[],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    assert len(candidates) == 1
    assert stats.source_match_strategy_counts == {"no_slot": 1}
    bookmaker_rows = {row.key: row for row in stats.source_match_bookmakers}
    assert bookmaker_rows["book-a"].no_slot_count == 1
    assert bookmaker_rows["book-a"].no_match_count == 0


def test_source_match_stats_include_per_bookmaker_summary():
    stats = _EventCandidateExtractionStats()

    extract_event_candidates(
        raw_odds=[
            _raw_odds(bookmaker_id="book-a"),
            _raw_odds(
                bookmaker_id="book-b",
                source_url="https://book-b.example/match",
            ),
        ],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
            _normalized_odds(
                "book-b",
                match_id="match-b",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
                source_url="https://book-b.example/match",
            ),
        ],
        normalized_outcome_offers=[],
        stats=stats,
    )

    bookmaker_rows = {row.key: row for row in stats.source_match_bookmakers}
    assert set(bookmaker_rows) == {"book-a", "book-b"}
    assert bookmaker_rows["book-a"].lookup_count == 1
    assert bookmaker_rows["book-b"].matched_count == 1
    assert stats.source_match_strategy_counts == {"exact_url": 1, "listed_pair": 1}
    assert stats.source_match_reason_counts == {"accepted": 2}


def test_extract_event_candidates_duplicate_representative_prefers_source_url():
    candidates = extract_event_candidates(
        raw_odds=[],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ),
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=3,
                away_team_id=4,
                home_team="Team Gamma",
                away_team="Team Delta",
                threshold=11.5,
            ).model_copy(update={"source_url": "https://example.test/with-url"}),
        ],
        normalized_outcome_offers=[],
    )

    assert len(candidates) == 1
    assert candidates[0].home_team == "Team Gamma"
    assert candidates[0].away_team == "Team Delta"
    assert candidates[0].source_url == "https://example.test/with-url"


def test_extract_event_candidates_preserves_normalized_loop_merge_precedence():
    candidates = extract_event_candidates(
        raw_odds=[],
        raw_outcome_offers=[],
        normalized_odds=[
            _normalized_odds(
                "book-a",
                match_id="match-a",
                league_id="league",
                home_team_id=1,
                away_team_id=2,
                home_team="Team Alpha",
                away_team="Team Beta",
                threshold=10.5,
            ).model_copy(update={"source_url": "https://example.test/odds"}),
        ],
        normalized_outcome_offers=[
            _normalized_outcome_offer(source_url="https://example.test/outcome"),
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].source_kind == "normalized_odds"
    assert candidates[0].source_url == "https://example.test/odds"


def test_football_raw_resolution_candidates_only_emit_stored_match_bookmakers():
    home_team_id = 10
    away_team_id = 20
    stored_match_id = generate_match_id(
        home_team_id,
        away_team_id,
        START_TIME,
        "football",
    )
    raw_stored = RawOutcomeOffer(
        bookmaker_id="book-a",
        league_id="premier_league",
        sport="football",
        home_team="Arsenal",
        away_team="Chelsea",
        source_url="https://example.test/stored",
        market_type="football_result",
        outcome_code="home",
        odds=2.0,
        start_time=START_TIME,
    )
    raw_unstored = RawOutcomeOffer(
        bookmaker_id="book-b",
        league_id="premier_league",
        sport="football",
        home_team="Liverpool",
        away_team="Everton",
        source_url="https://example.test/unstored",
        market_type="football_result",
        outcome_code="home",
        odds=2.0,
        start_time=START_TIME,
    )
    stored_key = _event_key_from_raw(raw_stored)
    unstored_key = _event_key_from_raw(raw_unstored)
    assert stored_key is not None
    assert unstored_key is not None
    football_resolutions = {
        stored_key: _OutcomeEventResolution(
            slot=_OutcomeEventSlot(
                sport="football",
                start_time=START_TIME,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        ),
        unstored_key: _OutcomeEventResolution(
            slot=_OutcomeEventSlot(
                sport="football",
                start_time=START_TIME,
                home_team_id=30,
                away_team_id=40,
                home_team="Liverpool",
                away_team="Everton",
            )
        ),
    }
    stats = _EventCandidateExtractionStats()

    candidates = extract_event_candidates(
        raw_odds=[],
        raw_outcome_offers=[raw_stored, raw_unstored],
        normalized_odds=[],
        normalized_outcome_offers=[
            NormalizedOutcomeOffer(
                match_id=stored_match_id,
                bookmaker_id="book-a",
                league_id="premier_league",
                sport="football",
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                home_team="Arsenal",
                away_team="Chelsea",
                source_url="https://example.test/stored",
                market_type="football_result",
                outcome_code="home",
                odds=2.0,
                start_time=START_TIME,
            )
        ],
        football_event_resolutions=football_resolutions,
        stats=stats,
    )

    assert stats.football_raw_candidate_count == 1
    assert len(candidates) == 1
    assert candidates[0].match_id == stored_match_id
    assert candidates[0].bookmaker_id == "book-a"


def test_raw_source_extraction_resolves_league_once_per_unique_source(monkeypatch):
    calls = []

    def fake_resolve_league(league_id: str, *, bookmaker_id: str):
        calls.append((league_id, bookmaker_id))
        return type(
            "LeagueResolution",
            (),
            {"league_id": league_id, "display_name": f"{bookmaker_id}:{league_id}"},
        )()

    monkeypatch.setattr(extraction_module, "resolve_league", fake_resolve_league)

    sources = _raw_odds_sources(
        [
            _raw_odds(threshold=10.5),
            _raw_odds(threshold=11.5),
            _raw_odds(source_url="https://example.test/other", threshold=12.5),
        ],
    )

    assert len(sources) == 2
    assert calls == [("league", "book-a"), ("league", "book-a")]
