from __future__ import annotations

from app.models.schemas import (
    NormalizedOdds,
    NormalizedOutcomeOffer,
    RawOddsData,
)
from app.services.match_unification import candidate_extraction as extraction_module
from app.services.match_unification.candidate_extraction import (
    _EventCandidateExtractionStats,
    _raw_odds_sources,
    extract_event_candidates,
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
    assert stats.source_match_source_count == 2
    assert stats.source_match_scored_source_count == 2


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
