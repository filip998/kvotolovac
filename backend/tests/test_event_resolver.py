from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.models.schemas import (
    NormalizedOdds,
    NormalizedOutcomeOffer,
    RawOddsData,
    RawOutcomeOffer,
    TeamReviewCandidate,
    TeamReviewDiagnostic,
)
from app.services import event_resolver as event_resolver_module
from app.services.event_resolver import (
    EventCandidate,
    _CandidateGroup,
    _PairResolution,
    _comparison_team_text,
    _contextual_merge_source_ids,
    _event_review_case,
    SameTimeCanonicalSlot,
    _same_time_slot_orientation,
    build_event_resolution_groups,
    resolve_and_persist_events,
)
from app.services.normalizer import generate_match_id
from app.services.outcome_normalizer import (
    _same_team_context,
    _team_qualifiers,
    normalize_outcome_offers_with_context,
)
from app.services.team_registry import create_canonical_team
from app.store import odds_store


START_TIME = "2030-01-01T20:00:00+00:00"


def _canonical_slot(
    *,
    home_team_id: int,
    away_team_id: int,
    home_team: str,
    away_team: str,
    support_bookmakers: frozenset[str],
) -> SameTimeCanonicalSlot:
    return SameTimeCanonicalSlot(
        sport="basketball",
        start_time=START_TIME,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team=home_team,
        away_team=away_team,
        support_bookmakers=support_bookmakers,
        raw_league_id="test_league",
    )


def test_team_auto_merge_guardrails_require_qualifier_compatibility():
    source_slot = _canonical_slot(
        home_team_id=1,
        away_team_id=2,
        home_team="Barcelona 2",
        away_team="Real Madrid 2",
        support_bookmakers=frozenset({"book-c"}),
    )
    target_slot = _canonical_slot(
        home_team_id=3,
        away_team_id=4,
        home_team="Barcelona B",
        away_team="Real Madrid B",
        support_bookmakers=frozenset({"book-a", "book-b"}),
    )

    assert _same_time_slot_orientation(source_slot, target_slot) is None


def test_event_review_case_metadata_records_exact_source_variant_pairs():
    left_candidate = EventCandidate(
        match_id="match-z",
        bookmaker_id="book-z",
        sport="basketball",
        start_time=START_TIME,
        home_team_id=1,
        away_team_id=2,
        home_team="Z Home",
        away_team="Z Away",
    )
    right_candidate = EventCandidate(
        match_id="match-a",
        bookmaker_id="book-a",
        sport="basketball",
        start_time=START_TIME,
        home_team_id=3,
        away_team_id=4,
        home_team="A Home",
        away_team="A Away",
    )

    review_case = _event_review_case(
        _CandidateGroup(index=1, candidates=(left_candidate,)),
        _CandidateGroup(index=2, candidates=(right_candidate,)),
        _PairResolution(
            confidence=0.8,
            score=80.0,
            weak_side_score=70.0,
            orientation="as_listed",
            reason_code="possible_event_equivalence_low_confidence",
            evidence=("fuzzy team label match",),
        ),
    )

    assert review_case.metadata["source_variants"] == [
        {"match_id": "match-a", "bookmaker_id": "book-a"},
        {"match_id": "match-z", "bookmaker_id": "book-z"},
    ]
    assert review_case.candidate_resolved_event_id is None


def test_event_resolution_groups_keep_sports_separate_for_same_teams_and_time():
    candidates = [
        EventCandidate(
            match_id="basketball-match",
            bookmaker_id="book-a",
            sport="basketball",
            start_time=START_TIME,
            home_team_id=1,
            away_team_id=2,
            home_team="Team Alpha",
            away_team="Team Beta",
        ),
        EventCandidate(
            match_id="football-match",
            bookmaker_id="book-b",
            sport="football",
            start_time=START_TIME,
            home_team_id=1,
            away_team_id=2,
            home_team="Team Alpha",
            away_team="Team Beta",
        ),
    ]

    resolutions, review_cases = build_event_resolution_groups(candidates)

    assert review_cases == []
    assert {(resolution.sport, resolution.primary_match_id) for resolution in resolutions} == {
        ("basketball", "basketball-match"),
        ("football", "football-match"),
    }


async def _seed_bookmakers(*bookmaker_ids: str) -> None:
    for bookmaker_id in bookmaker_ids:
        await odds_store.upsert_bookmaker(bookmaker_id, bookmaker_id.title())


async def _seed_league(league_id: str, sport: str) -> None:
    await odds_store.upsert_league(league_id, league_id.replace("_", " ").title(), sport)


async def _store_match(row: NormalizedOdds | NormalizedOutcomeOffer) -> None:
    await odds_store.upsert_match(
        id=row.match_id,
        league_id=row.league_id,
        sport=row.sport,
        home_team=row.home_team,
        away_team=row.away_team,
        home_team_id=row.home_team_id,
        away_team_id=row.away_team_id,
        start_time=row.start_time,
    )


def _basketball_odds(
    bookmaker_id: str,
    *,
    match_id: str,
    league_id: str,
    home_team_id: int,
    away_team_id: int,
    home_team: str,
    away_team: str,
    threshold: float,
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
        market_type="player_points",
        player_name="Test Player",
        threshold=threshold,
        over_odds=1.9,
        under_odds=1.9,
        start_time=START_TIME,
    )


@pytest.mark.asyncio
async def test_event_resolver_persists_exact_basketball_group(team_registry_file):
    home = create_canonical_team(display_name="Partizan", sport="basketball")
    away = create_canonical_team(display_name="Crvena Zvezda", sport="basketball")
    match_id = generate_match_id(home.team_id, away.team_id, START_TIME, "basketball")
    league_id = "euroleague"
    await _seed_bookmakers("mozzart", "meridian")
    await _seed_league(league_id, "basketball")
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=match_id,
            league_id=league_id,
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home.team_name,
            away_team=away.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=match_id,
            league_id=league_id,
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home.team_name,
            away_team=away.team_name,
            threshold=13.5,
        ),
    ]
    await _store_match(normalized[0])
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            source_url=f"https://{row.bookmaker_id}.example/event",
            market_type="player_points",
            player_name="Test Player",
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    assert result.resolved_event_members == 2
    event = await odds_store.get_resolved_event(f"evt_{match_id}")
    assert event is not None
    assert event.method == "exact"
    assert event.primary_match_id == match_id
    assert {member.bookmaker_id for member in event.members} == {"mozzart", "meridian"}
    assert {member.source_home_team for member in event.members} == {"Partizan"}


@pytest.mark.asyncio
async def test_event_resolver_persists_football_outcome_candidates(team_registry_file):
    home = create_canonical_team(display_name="Arsenal", sport="football")
    away = create_canonical_team(display_name="Chelsea", sport="football")
    match_id = generate_match_id(home.team_id, away.team_id, START_TIME, "football")
    league_id = "premier_league"
    await _seed_bookmakers("maxbet", "balkanbet")
    await _seed_league(league_id, "football")
    normalized = [
        NormalizedOutcomeOffer(
            match_id=match_id,
            bookmaker_id="maxbet",
            league_id=league_id,
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home.team_name,
            away_team=away.team_name,
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.9,
            line=2.5,
            raw_label="Over 2.5",
            start_time=START_TIME,
        ),
        NormalizedOutcomeOffer(
            match_id=match_id,
            bookmaker_id="balkanbet",
            league_id=league_id,
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home.team_name,
            away_team=away.team_name,
            market_type="football_total_goals",
            outcome_code="under",
            odds=1.95,
            line=2.5,
            raw_label="Under 2.5",
            start_time=START_TIME,
        ),
    ]
    await _store_match(normalized[0])
    raw = [
        RawOutcomeOffer(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            source_url=f"https://{row.bookmaker_id}.example/football-event",
            market_type=row.market_type,
            outcome_code=row.outcome_code,
            odds=row.odds,
            line=row.line,
            raw_label=row.raw_label,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=[],
        raw_outcome_offers=raw,
        normalized_odds=[],
        normalized_outcome_offers=normalized,
    )

    assert result.resolved_events == 1
    event = await odds_store.get_resolved_event(f"evt_{match_id}")
    assert event is not None
    assert event.sport == "football"
    assert {member.source_home_team for member in event.members} == {"Arsenal"}
    assert {member.source_away_team for member in event.members} == {"Chelsea"}


@pytest.mark.asyncio
async def test_event_resolver_reuses_precomputed_football_outcome_resolutions(
    monkeypatch,
    team_registry_file,
):
    await _seed_bookmakers("maxbet", "balkanbet", "unusedbet")
    await _seed_league("premier_league", "football")
    raw = [
        RawOutcomeOffer(
            bookmaker_id="maxbet",
            league_id="premier_league",
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            source_url="https://maxbet.example/football-event",
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.9,
            line=2.5,
            raw_label="Over 2.5",
            start_time=START_TIME,
        ),
        RawOutcomeOffer(
            bookmaker_id="balkanbet",
            league_id="premier_league",
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            source_url="https://balkanbet.example/football-event",
            market_type="football_total_goals",
            outcome_code="under",
            odds=1.95,
            line=2.5,
            raw_label="Under 2.5",
            start_time=START_TIME,
        ),
        RawOutcomeOffer(
            bookmaker_id="unusedbet",
            league_id="premier_league",
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            source_url="https://unusedbet.example/football-event",
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.91,
            line=2.5,
            raw_label="Over 2.5",
            start_time=START_TIME,
        ),
    ]
    outcome_result = normalize_outcome_offers_with_context(raw)
    assert len(outcome_result.football_event_resolutions) == 3
    persisted_normalized = [
        row
        for row in outcome_result.normalized
        if row.bookmaker_id in {"maxbet", "balkanbet"}
    ]
    for row in persisted_normalized:
        await _store_match(row)

    def fail_rebuild(*args, **kwargs):
        raise AssertionError("football resolutions should be reused")

    monkeypatch.setattr(
        event_resolver_module,
        "_build_football_event_resolutions",
        fail_rebuild,
    )

    result = await resolve_and_persist_events(
        raw_odds=[],
        raw_outcome_offers=raw,
        normalized_odds=[],
        normalized_outcome_offers=persisted_normalized,
        football_event_resolutions=outcome_result.football_event_resolutions,
    )

    assert result.resolved_events == 1
    assert result.benchmark is not None
    assert result.benchmark.reused_football_event_resolution_count == 2
    event = await odds_store.get_resolved_event(
        f"evt_{persisted_normalized[0].match_id}"
    )
    assert event is not None
    assert {member.source_home_team for member in event.members} == {"Arsenal"}
    assert {member.source_away_team for member in event.members} == {"Chelsea"}
    assert {member.source_url for member in event.members} == {
        "https://maxbet.example/football-event",
        "https://balkanbet.example/football-event",
    }


@pytest.mark.asyncio
async def test_event_resolver_fuzzy_groups_football_without_team_merge(
    team_registry_file,
):
    arsenal = create_canonical_team(display_name="Arsenal", sport="football")
    arsenal_fc = create_canonical_team(display_name="Arsenal FC", sport="football")
    chelsea = create_canonical_team(display_name="Chelsea", sport="football")
    league_id = "premier_league"
    await _seed_bookmakers("maxbet", "balkanbet")
    await _seed_league(league_id, "football")
    maxbet_match_id = generate_match_id(
        arsenal.team_id,
        chelsea.team_id,
        START_TIME,
        "football",
    )
    balkanbet_match_id = generate_match_id(
        arsenal_fc.team_id,
        chelsea.team_id,
        START_TIME,
        "football",
    )
    normalized = [
        NormalizedOutcomeOffer(
            match_id=maxbet_match_id,
            bookmaker_id="maxbet",
            league_id=league_id,
            sport="football",
            home_team_id=arsenal.team_id,
            away_team_id=chelsea.team_id,
            home_team=arsenal.team_name,
            away_team=chelsea.team_name,
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.9,
            line=2.5,
            raw_label="Over 2.5",
            start_time=START_TIME,
        ),
        NormalizedOutcomeOffer(
            match_id=balkanbet_match_id,
            bookmaker_id="balkanbet",
            league_id=league_id,
            sport="football",
            home_team_id=arsenal_fc.team_id,
            away_team_id=chelsea.team_id,
            home_team=arsenal_fc.team_name,
            away_team=chelsea.team_name,
            market_type="football_total_goals",
            outcome_code="under",
            odds=1.95,
            line=2.5,
            raw_label="Under 2.5",
            start_time=START_TIME,
        ),
    ]
    for row in normalized:
        await _store_match(row)

    result = await resolve_and_persist_events(
        raw_odds=[],
        raw_outcome_offers=[],
        normalized_odds=[],
        normalized_outcome_offers=normalized,
    )

    events = await odds_store.list_resolved_events(sport="football")

    assert result.resolved_events == 1
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.match_id for member in event.members} == {
        maxbet_match_id,
        balkanbet_match_id,
    }
    with sqlite3.connect(settings.db_path) as conn:
        active_teams = {
            name
            for name, is_active, merged_into in conn.execute(
                """
                SELECT display_name, is_active, merged_into_team_id
                FROM canonical_teams
                WHERE sport = 'football'
                """
            ).fetchall()
            if is_active and merged_into is None
        }
        match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    assert {"Arsenal", "Arsenal FC", "Chelsea"} <= active_teams
    assert match_count == 2


@pytest.mark.asyncio
async def test_event_resolver_fuzzy_groups_distinct_match_ids_without_team_merge(
    team_registry_file,
):
    partizan = create_canonical_team(display_name="Partizan", sport="basketball")
    kk_partizan = create_canonical_team(display_name="KK Partizan", sport="basketball")
    zvezda = create_canonical_team(display_name="Crvena Zvezda", sport="basketball")
    league_id = "aba_league"
    await _seed_bookmakers("mozzart", "meridian")
    await _seed_league(league_id, "basketball")
    mozzart_match_id = generate_match_id(
        partizan.team_id,
        zvezda.team_id,
        START_TIME,
        "basketball",
    )
    meridian_match_id = generate_match_id(
        kk_partizan.team_id,
        zvezda.team_id,
        START_TIME,
        "basketball",
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=mozzart_match_id,
            league_id=league_id,
            home_team_id=partizan.team_id,
            away_team_id=zvezda.team_id,
            home_team=partizan.team_name,
            away_team=zvezda.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=meridian_match_id,
            league_id=league_id,
            home_team_id=kk_partizan.team_id,
            away_team_id=zvezda.team_id,
            home_team=kk_partizan.team_name,
            away_team=zvezda.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    event = await odds_store.get_resolved_event(f"evt_{meridian_match_id}")
    assert event is None
    event = await odds_store.get_resolved_event(f"evt_{mozzart_match_id}")
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.match_id for member in event.members} == {mozzart_match_id, meridian_match_id}

    with sqlite3.connect(settings.db_path) as conn:
        active_teams = {
            name
            for name, is_active, merged_into in conn.execute(
                """
                SELECT display_name, is_active, merged_into_team_id
                FROM canonical_teams
                WHERE sport = 'basketball'
                """
            ).fetchall()
            if is_active and merged_into is None
        }
        match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    assert {"Partizan", "KK Partizan", "Crvena Zvezda"} <= active_teams
    assert match_count == 2


@pytest.mark.asyncio
async def test_event_resolver_auto_merges_compound_subset_event_at_lowered_thresholds(
    team_registry_file,
):
    hermine = create_canonical_team(display_name="Hermine Nantes", sport="basketball")
    hermine_basket = create_canonical_team(
        display_name="Hermine Nantes Basket", sport="basketball"
    )
    saint_chamond = create_canonical_team(
        display_name="Saint-Chamond", sport="basketball"
    )
    league_id = "francuska_lnb_pro_b"
    await _seed_bookmakers("superbet", "meridian")
    await _seed_league(league_id, "basketball")
    superbet_match_id = generate_match_id(
        hermine.team_id,
        saint_chamond.team_id,
        START_TIME,
        "basketball",
    )
    meridian_match_id = generate_match_id(
        hermine_basket.team_id,
        saint_chamond.team_id,
        START_TIME,
        "basketball",
    )
    normalized = [
        _basketball_odds(
            "superbet",
            match_id=superbet_match_id,
            league_id=league_id,
            home_team_id=hermine.team_id,
            away_team_id=saint_chamond.team_id,
            home_team=hermine.team_name,
            away_team=saint_chamond.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=meridian_match_id,
            league_id=league_id,
            home_team_id=hermine_basket.team_id,
            away_team_id=saint_chamond.team_id,
            home_team=hermine_basket.team_name,
            away_team=saint_chamond.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.match_id for member in event.members} == {
        superbet_match_id,
        meridian_match_id,
    }

    with sqlite3.connect(settings.db_path) as conn:
        active_teams = {
            name
            for name, is_active, merged_into in conn.execute(
                """
                SELECT display_name, is_active, merged_into_team_id
                FROM canonical_teams
                WHERE sport = 'basketball'
                """
            ).fetchall()
            if is_active and merged_into is None
        }

    assert {"Hermine Nantes", "Hermine Nantes Basket", "Saint-Chamond"} <= active_teams


@pytest.mark.asyncio
async def test_event_resolver_does_not_auto_merge_distinct_same_token_teams(
    team_registry_file,
):
    south_korea = create_canonical_team(display_name="South Korea", sport="basketball")
    north_korea = create_canonical_team(display_name="North Korea", sport="basketball")
    japan = create_canonical_team(display_name="Japan", sport="basketball")
    league_id = "asian_cup"
    await _seed_bookmakers("superbet", "meridian")
    await _seed_league(league_id, "basketball")
    superbet_match_id = generate_match_id(
        south_korea.team_id,
        japan.team_id,
        START_TIME,
        "basketball",
    )
    meridian_match_id = generate_match_id(
        north_korea.team_id,
        japan.team_id,
        START_TIME,
        "basketball",
    )
    normalized = [
        _basketball_odds(
            "superbet",
            match_id=superbet_match_id,
            league_id=league_id,
            home_team_id=south_korea.team_id,
            away_team_id=japan.team_id,
            home_team=south_korea.team_name,
            away_team=japan.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=meridian_match_id,
            league_id=league_id,
            home_team_id=north_korea.team_id,
            away_team_id=japan.team_id,
            home_team=north_korea.team_name,
            away_team=japan.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    # Two distinct events whose only similarity is sharing the token "Korea"
    # must NOT auto-merge even at the lowered fuzzy thresholds. They land in
    # separate resolved events and a pending Event Review case is created so
    # a human can decide.
    assert result.resolved_events == 2
    assert result.review_cases >= 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert {event.method for event in events} == {"exact"}
    assert {event.id for event in events} == {
        f"evt_{superbet_match_id}",
        f"evt_{meridian_match_id}",
    }


@pytest.mark.asyncio
async def test_event_resolver_does_not_auto_merge_distinct_non_subset_teams(
    team_registry_file,
):
    austria = create_canonical_team(display_name="Austria", sport="basketball")
    australia = create_canonical_team(display_name="Australia", sport="basketball")
    niger = create_canonical_team(display_name="Niger", sport="basketball")
    nigeria = create_canonical_team(display_name="Nigeria", sport="basketball")
    league_id = "world_cup"
    await _seed_bookmakers("superbet", "meridian")
    await _seed_league(league_id, "basketball")
    superbet_match_id = generate_match_id(
        austria.team_id,
        niger.team_id,
        START_TIME,
        "basketball",
    )
    meridian_match_id = generate_match_id(
        australia.team_id,
        nigeria.team_id,
        START_TIME,
        "basketball",
    )
    normalized = [
        _basketball_odds(
            "superbet",
            match_id=superbet_match_id,
            league_id=league_id,
            home_team_id=austria.team_id,
            away_team_id=niger.team_id,
            home_team=austria.team_name,
            away_team=niger.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=meridian_match_id,
            league_id=league_id,
            home_team_id=australia.team_id,
            away_team_id=nigeria.team_id,
            home_team=australia.team_name,
            away_team=nigeria.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    # Distinct teams with non-subset names that score in the 82-89 band
    # (Austria/Australia + Niger/Nigeria) must not auto-merge: the
    # non-subset path retains the strict avg >= 90 / weak >= 82 floor.
    assert result.resolved_events == 2
    events = await odds_store.list_resolved_events(sport="basketball")
    assert {event.method for event in events} == {"exact"}


@pytest.mark.asyncio
async def test_event_resolver_anchored_low_conf_merges_with_three_bookmakers_and_league_anchor(
    team_registry_file,
):
    """Heuristic 1: anchored low-confidence merge.

    Pisek-style fragmentation. The weak side score (~64) sits below the
    standard auto-merge floor (75) but the pair has:

    * avg score 81.8 >= ``_ANCHORED_FUZZY_AVG_SCORE`` (70)
    * weak side 63.6 >= ``_ANCHORED_FUZZY_SIDE_SCORE`` (50)
    * shared significant token ("Pisek") + same source league
    * 3 unique bookmakers across both groups

    so the anchored corroborated branch fires and the events merge.
    """

    srsni = create_canonical_team(display_name="Srsni Pisek", sport="basketball")
    sokol = create_canonical_team(display_name="Sokol Pisek", sport="basketball")
    pardubice = create_canonical_team(display_name="Pardubice", sport="basketball")
    league_id = "ceska_liga"
    await _seed_bookmakers("mozzart", "meridian", "superbet")
    await _seed_league(league_id, "basketball")
    mozzart_match_id = generate_match_id(
        srsni.team_id, pardubice.team_id, START_TIME, "basketball"
    )
    other_match_id = generate_match_id(
        sokol.team_id, pardubice.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=mozzart_match_id,
            league_id=league_id,
            home_team_id=srsni.team_id,
            away_team_id=pardubice.team_id,
            home_team=srsni.team_name,
            away_team=pardubice.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=other_match_id,
            league_id=league_id,
            home_team_id=sokol.team_id,
            away_team_id=pardubice.team_id,
            home_team=sokol.team_name,
            away_team=pardubice.team_name,
            threshold=13.5,
        ),
        _basketball_odds(
            "superbet",
            match_id=other_match_id,
            league_id=league_id,
            home_team_id=sokol.team_id,
            away_team_id=pardubice.team_id,
            home_team=sokol.team_name,
            away_team=pardubice.team_name,
            threshold=14.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.bookmaker_id for member in event.members} == {
        "mozzart",
        "meridian",
        "superbet",
    }


@pytest.mark.asyncio
async def test_event_resolver_anchored_low_conf_does_not_merge_with_two_bookmakers(
    team_registry_file,
):
    """Negative regression: same Pisek-style pair with only 2 bookmakers
    must NOT fire the anchored branch.

    This is the exact corroborator that distinguishes real fragmentations
    from the South/North Korea regression case (2 bookmakers, weak side
    81.8). Without the bookmaker-count gate the anchored branch would also
    fire on the Korea case, regressing
    :func:`test_event_resolver_does_not_auto_merge_distinct_same_token_teams`.
    """

    srsni = create_canonical_team(display_name="Srsni Pisek", sport="basketball")
    sokol = create_canonical_team(display_name="Sokol Pisek", sport="basketball")
    pardubice = create_canonical_team(display_name="Pardubice", sport="basketball")
    league_id = "ceska_liga"
    await _seed_bookmakers("mozzart", "meridian")
    await _seed_league(league_id, "basketball")
    mozzart_match_id = generate_match_id(
        srsni.team_id, pardubice.team_id, START_TIME, "basketball"
    )
    meridian_match_id = generate_match_id(
        sokol.team_id, pardubice.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=mozzart_match_id,
            league_id=league_id,
            home_team_id=srsni.team_id,
            away_team_id=pardubice.team_id,
            home_team=srsni.team_name,
            away_team=pardubice.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=meridian_match_id,
            league_id=league_id,
            home_team_id=sokol.team_id,
            away_team_id=pardubice.team_id,
            home_team=sokol.team_name,
            away_team=pardubice.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 2
    events = await odds_store.list_resolved_events(sport="basketball")
    assert {event.method for event in events} == {"exact"}


@pytest.mark.asyncio
async def test_event_resolver_quorum_resolves_same_bookmaker_conflict(
    team_registry_file,
):
    """Heuristic 2: quorum override for same-bookmaker conflicts.

    Heidelberg-style fragmentation: 9 bookmakers report the canonical
    "Heidelberg" / "Mitteldeutscher" pair, while two outliers (one of which
    is also in the larger group) carry the longer "Heidelberg Academics" /
    "Mitteldeutscher BC" labels. Standard auto-merge bails because the
    same-bookmaker overlap (`pinnbet`) blocks `dsu.can_union`. The quorum
    override forces the merge because the larger group has 9 bookmakers and
    exceeds the smaller (2) by 7 — well past
    ``_QUORUM_MIN_LARGER_BOOKMAKERS`` and ``_QUORUM_MIN_BOOKMAKER_DIFFERENCE``.

    An audit review case with reason
    ``auto_quorum_resolved_with_audit`` is also persisted so operators can
    spot the override.
    """

    heidelberg = create_canonical_team(
        display_name="Heidelberg", sport="basketball"
    )
    mitteldeutscher = create_canonical_team(
        display_name="Mitteldeutscher", sport="basketball"
    )
    academics = create_canonical_team(
        display_name="Heidelberg Academics", sport="basketball"
    )
    mbc = create_canonical_team(
        display_name="Mitteldeutscher BC", sport="basketball"
    )
    league_id = "bbl"
    larger_books = [
        "mozzart",
        "meridian",
        "superbet",
        "maxbet",
        "soccerbet",
        "pinnbet",
        "balkanbet",
        "betole",
        "oktagonbet",
    ]
    smaller_books = ["bookmaker365", "pinnbet"]  # pinnbet overlaps both groups
    await _seed_bookmakers(*set(larger_books) | set(smaller_books))
    await _seed_league(league_id, "basketball")
    larger_match_id = generate_match_id(
        heidelberg.team_id,
        mitteldeutscher.team_id,
        START_TIME,
        "basketball",
    )
    smaller_match_id = generate_match_id(
        academics.team_id, mbc.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            book,
            match_id=larger_match_id,
            league_id=league_id,
            home_team_id=heidelberg.team_id,
            away_team_id=mitteldeutscher.team_id,
            home_team=heidelberg.team_name,
            away_team=mitteldeutscher.team_name,
            threshold=12.5 + idx * 0.1,
        )
        for idx, book in enumerate(larger_books)
    ] + [
        _basketball_odds(
            book,
            match_id=smaller_match_id,
            league_id=league_id,
            home_team_id=academics.team_id,
            away_team_id=mbc.team_id,
            home_team=academics.team_name,
            away_team=mbc.team_name,
            threshold=13.5 + idx * 0.1,
        )
        for idx, book in enumerate(smaller_books)
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.match_id for member in event.members} == {
        larger_match_id,
        smaller_match_id,
    }
    # Quorum overrides are logged for operator visibility but DO NOT add an
    # audit row to the operator review queue — the user's "few wrong is OK"
    # preference explicitly favours emptying the queue rather than parking
    # auto-resolved pairs there.
    review_cases = await odds_store.list_event_review_cases(status="pending")
    assert all(
        case.reason_code != "auto_quorum_resolved_with_audit"
        for case in review_cases
    ), f"unexpected audit case persisted; got {[c.reason_code for c in review_cases]}"


@pytest.mark.asyncio
async def test_event_resolver_quorum_does_not_fire_on_symmetric_same_bookmaker_conflict(
    team_registry_file,
):
    """Negative: quorum override needs a clear size advantage.

    Two groups of equal bookmaker size with same-bookmaker overlap stay as
    a conflict review case — the override would be making an arbitrary
    choice between two equally well-supported groupings.
    """

    heidelberg = create_canonical_team(
        display_name="Heidelberg", sport="basketball"
    )
    mitteldeutscher = create_canonical_team(
        display_name="Mitteldeutscher", sport="basketball"
    )
    academics = create_canonical_team(
        display_name="Heidelberg Academics", sport="basketball"
    )
    mbc = create_canonical_team(
        display_name="Mitteldeutscher BC", sport="basketball"
    )
    league_id = "bbl"
    group_a_books = ["mozzart", "meridian", "superbet", "pinnbet"]
    group_b_books = ["maxbet", "soccerbet", "balkanbet", "pinnbet"]
    await _seed_bookmakers(*set(group_a_books) | set(group_b_books))
    await _seed_league(league_id, "basketball")
    larger_match_id = generate_match_id(
        heidelberg.team_id,
        mitteldeutscher.team_id,
        START_TIME,
        "basketball",
    )
    smaller_match_id = generate_match_id(
        academics.team_id, mbc.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            book,
            match_id=larger_match_id,
            league_id=league_id,
            home_team_id=heidelberg.team_id,
            away_team_id=mitteldeutscher.team_id,
            home_team=heidelberg.team_name,
            away_team=mitteldeutscher.team_name,
            threshold=12.5 + idx * 0.1,
        )
        for idx, book in enumerate(group_a_books)
    ] + [
        _basketball_odds(
            book,
            match_id=smaller_match_id,
            league_id=league_id,
            home_team_id=academics.team_id,
            away_team_id=mbc.team_id,
            home_team=academics.team_name,
            away_team=mbc.team_name,
            threshold=13.5 + idx * 0.1,
        )
        for idx, book in enumerate(group_b_books)
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 2
    events = await odds_store.list_resolved_events(sport="basketball")
    assert {event.method for event in events} == {"exact"}


@pytest.mark.asyncio
async def test_event_resolver_dot_expansion_merges_compound_abbreviation(
    team_registry_file,
):
    """Heuristic 3: dotted-token expansion (``Ch.More`` → ``Cherno More``).

    Without expansion the weak-side fuzzy score stays around 78 — below the
    standard subset threshold (75) but enough that compound dots like
    ``Ch.More`` (no space after the dot) used to never expand at all.
    After the substitution the pair scores 100/100 and merges via the
    standard high-confidence path.
    """

    cherno_full = create_canonical_team(display_name="Cherno More", sport="basketball")
    spartak_full = create_canonical_team(
        display_name="Spartak Pleven", sport="basketball"
    )
    cherno_short = create_canonical_team(display_name="Ch.More", sport="basketball")
    spartak_short = create_canonical_team(
        display_name="Spartak Pl.", sport="basketball"
    )
    league_id = "nbl_bg"
    await _seed_bookmakers("mozzart", "maxbet", "meridian")
    await _seed_league(league_id, "basketball")
    full_match_id = generate_match_id(
        cherno_full.team_id, spartak_full.team_id, START_TIME, "basketball"
    )
    short_match_id = generate_match_id(
        cherno_short.team_id, spartak_short.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=full_match_id,
            league_id=league_id,
            home_team_id=cherno_full.team_id,
            away_team_id=spartak_full.team_id,
            home_team=cherno_full.team_name,
            away_team=spartak_full.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=full_match_id,
            league_id=league_id,
            home_team_id=cherno_full.team_id,
            away_team_id=spartak_full.team_id,
            home_team=cherno_full.team_name,
            away_team=spartak_full.team_name,
            threshold=13.0,
        ),
        _basketball_odds(
            "maxbet",
            match_id=short_match_id,
            league_id=league_id,
            home_team_id=cherno_short.team_id,
            away_team_id=spartak_short.team_id,
            home_team=cherno_short.team_name,
            away_team=spartak_short.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.bookmaker_id for member in event.members} == {
        "mozzart",
        "meridian",
        "maxbet",
    }


@pytest.mark.asyncio
async def test_event_resolver_women_marker_merges_w_and_wom_variants(
    team_registry_file,
):
    """Heuristic 3: the ``wom`` qualifier alias ensures ``Sao Jose W`` and
    ``Sao Jose Wom.`` strip to the same canonical women-suffixed name in
    ``_team_qualifiers``, so :func:`_same_team_context` matches them and
    the fuzzy match (score 95.5 / weak 90.9) merges them via the standard
    high-confidence path.
    """

    sao_jose_w = create_canonical_team(display_name="Sao Jose W", sport="basketball")
    sao_jose_wom = create_canonical_team(
        display_name="Sao Jose Wom.", sport="basketball"
    )
    santo_andre = create_canonical_team(
        display_name="Santo Andre", sport="basketball"
    )
    league_id = "lbf"
    await _seed_bookmakers("mozzart", "meridian")
    await _seed_league(league_id, "basketball")
    w_match_id = generate_match_id(
        sao_jose_w.team_id, santo_andre.team_id, START_TIME, "basketball"
    )
    wom_match_id = generate_match_id(
        sao_jose_wom.team_id, santo_andre.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=w_match_id,
            league_id=league_id,
            home_team_id=sao_jose_w.team_id,
            away_team_id=santo_andre.team_id,
            home_team=sao_jose_w.team_name,
            away_team=santo_andre.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=wom_match_id,
            league_id=league_id,
            home_team_id=sao_jose_wom.team_id,
            away_team_id=santo_andre.team_id,
            home_team=sao_jose_wom.team_name,
            away_team=santo_andre.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.bookmaker_id for member in event.members} == {
        "mozzart",
        "meridian",
    }


@pytest.mark.asyncio
async def test_event_resolver_women_marker_recognises_terminal_z(
    team_registry_file,
):
    """An explicit standalone ``Ž`` marker (``Sao Jose (Ž)`` after diacritic
    strip) is treated as a women qualifier and pairs with ``Sao Jose Wom.``.
    """

    sao_jose_z = create_canonical_team(display_name="Sao Jose (Ž)", sport="basketball")
    sao_jose_wom = create_canonical_team(
        display_name="Sao Jose Wom.", sport="basketball"
    )
    santo_andre_z = create_canonical_team(
        display_name="Santo Andre (Ž)", sport="basketball"
    )
    santo_andre_wom = create_canonical_team(
        display_name="Santo Andre Wom.", sport="basketball"
    )
    league_id = "lbf"
    await _seed_bookmakers("mozzart", "meridian", "superbet")
    await _seed_league(league_id, "basketball")
    z_match_id = generate_match_id(
        sao_jose_z.team_id, santo_andre_z.team_id, START_TIME, "basketball"
    )
    wom_match_id = generate_match_id(
        sao_jose_wom.team_id, santo_andre_wom.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=z_match_id,
            league_id=league_id,
            home_team_id=sao_jose_z.team_id,
            away_team_id=santo_andre_z.team_id,
            home_team=sao_jose_z.team_name,
            away_team=santo_andre_z.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=wom_match_id,
            league_id=league_id,
            home_team_id=sao_jose_wom.team_id,
            away_team_id=santo_andre_wom.team_id,
            home_team=sao_jose_wom.team_name,
            away_team=santo_andre_wom.team_name,
            threshold=13.5,
        ),
        _basketball_odds(
            "superbet",
            match_id=wom_match_id,
            league_id=league_id,
            home_team_id=sao_jose_wom.team_id,
            away_team_id=santo_andre_wom.team_id,
            home_team=sao_jose_wom.team_name,
            away_team=santo_andre_wom.team_name,
            threshold=14.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"


def test_event_resolver_merges_reported_sao_jose_women_fragments():
    candidates = [
        EventCandidate(
            match_id="sao-jose-wom",
            bookmaker_id=bookmaker_id,
            sport="football",
            start_time=START_TIME,
            home_team_id=1,
            away_team_id=2,
            home_team="Sao Jose Wom.",
            away_team="Santo Andre Wom.",
            source_league_id="brazil_1_z",
            source_league_name="Brazil 1 Z",
        )
        for bookmaker_id in ("admiralbet", "betole", "oktagonbet", "pinnbet")
    ] + [
        EventCandidate(
            match_id="sao-jose-dos-campos-w",
            bookmaker_id="merkurxtip",
            sport="football",
            start_time=START_TIME,
            home_team_id=3,
            away_team_id=4,
            home_team="Sao Jose Dos Campos W",
            away_team="Santo Andre Wom.",
            source_league_id="lbf_women",
            source_league_name="LBF Women",
        ),
        EventCandidate(
            match_id="sao-jose-z",
            bookmaker_id="superbet",
            sport="football",
            start_time=START_TIME,
            home_team_id=5,
            away_team_id=6,
            home_team="Sao Jose Campos (Ž)",
            away_team="Santo Andre (Ž)",
            source_league_id="brazil_lbf_z",
            source_league_name="Brazil LBF Z",
        ),
        EventCandidate(
            match_id="sao-jose-z-slash",
            bookmaker_id="volcanobet",
            sport="football",
            start_time=START_TIME,
            home_team_id=7,
            away_team_id=8,
            home_team="Ž/Sao Jose Dos Campos",
            away_team="Ž/Santo Andre Apaba",
            source_league_id="brazil_1_z",
            source_league_name="Brazil 1 Z",
        ),
        EventCandidate(
            match_id="sao-jose-z-slash",
            bookmaker_id="balkanbet",
            sport="football",
            start_time=START_TIME,
            home_team_id=7,
            away_team_id=8,
            home_team="Sao Jose Dos Campos SP",
            away_team="Santo Andre/Apaba",
            source_league_id="balkanbet_tournament_44979",
            source_league_name="Balkanbet Tournament 44979",
        ),
    ]

    resolutions, review_cases = build_event_resolution_groups(candidates)

    assert review_cases == []
    assert len(resolutions) == 1
    event = resolutions[0]
    assert event.method == "auto_fuzzy_high"
    assert {member.bookmaker_id for member in event.members} == {
        "admiralbet",
        "betole",
        "oktagonbet",
        "pinnbet",
        "merkurxtip",
        "superbet",
        "volcanobet",
        "balkanbet",
    }
    assert {member.match_id for member in event.members} == {
        "sao-jose-wom",
        "sao-jose-dos-campos-w",
        "sao-jose-z",
        "sao-jose-z-slash",
    }


@pytest.mark.asyncio
async def test_event_resolver_women_marker_does_not_merge_women_into_men(
    team_registry_file,
):
    """Negative: a women-tagged team must not merge with the same-stem men's
    team. ``Sao Jose W`` carries the women qualifier; bare ``Sao Jose``
    does not. :func:`_same_team_context` rejects the pair so the resolver
    never reaches the fuzzy stage.
    """

    sao_jose_w = create_canonical_team(display_name="Sao Jose W", sport="basketball")
    sao_jose_men = create_canonical_team(display_name="Sao Jose", sport="basketball")
    santo_andre = create_canonical_team(
        display_name="Santo Andre", sport="basketball"
    )
    league_id = "lbf"
    await _seed_bookmakers("mozzart", "meridian")
    await _seed_league(league_id, "basketball")
    w_match_id = generate_match_id(
        sao_jose_w.team_id, santo_andre.team_id, START_TIME, "basketball"
    )
    men_match_id = generate_match_id(
        sao_jose_men.team_id, santo_andre.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=w_match_id,
            league_id=league_id,
            home_team_id=sao_jose_w.team_id,
            away_team_id=santo_andre.team_id,
            home_team=sao_jose_w.team_name,
            away_team=santo_andre.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=men_match_id,
            league_id=league_id,
            home_team_id=sao_jose_men.team_id,
            away_team_id=santo_andre.team_id,
            home_team=sao_jose_men.team_name,
            away_team=santo_andre.team_name,
            threshold=13.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 2
    events = await odds_store.list_resolved_events(sport="basketball")
    assert {event.method for event in events} == {"exact"}


@pytest.mark.asyncio
async def test_event_resolver_anchored_low_conf_respects_weak_side_floor(
    team_registry_file,
):
    """Negative: the anchored branch refuses to merge when the weak side
    fuzzy score is below ``_ANCHORED_FUZZY_SIDE_SCORE`` (50).

    Tartu-style fragmentations (``Tartu Ulikool`` ↔ ``Maks-and-Moorits``,
    weak ~32) sit below that floor on purpose — fuzzy alone cannot
    distinguish them from genuine false-positive cases. They remain in the
    manual review queue.
    """

    tartu = create_canonical_team(display_name="Tartu Ulikool", sport="basketball")
    maks = create_canonical_team(display_name="Maks-and-Moorits", sport="basketball")
    parnu = create_canonical_team(display_name="Parnu", sport="basketball")
    league_id = "estonia"
    await _seed_bookmakers("mozzart", "meridian", "superbet")
    await _seed_league(league_id, "basketball")
    tartu_match_id = generate_match_id(
        tartu.team_id, parnu.team_id, START_TIME, "basketball"
    )
    maks_match_id = generate_match_id(
        maks.team_id, parnu.team_id, START_TIME, "basketball"
    )
    normalized = [
        _basketball_odds(
            "mozzart",
            match_id=tartu_match_id,
            league_id=league_id,
            home_team_id=tartu.team_id,
            away_team_id=parnu.team_id,
            home_team=tartu.team_name,
            away_team=parnu.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=maks_match_id,
            league_id=league_id,
            home_team_id=maks.team_id,
            away_team_id=parnu.team_id,
            home_team=maks.team_name,
            away_team=parnu.team_name,
            threshold=13.5,
        ),
        _basketball_odds(
            "superbet",
            match_id=maks_match_id,
            league_id=league_id,
            home_team_id=maks.team_id,
            away_team_id=parnu.team_id,
            home_team=maks.team_name,
            away_team=parnu.team_name,
            threshold=14.5,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    # Tartu Ulikool vs Maks-and-Moorits weak side ~32 — never auto-merges.
    assert result.resolved_events == 2
    events = await odds_store.list_resolved_events(sport="basketball")
    assert {event.method for event in events} == {"exact"}


@pytest.mark.asyncio
async def test_event_resolver_transitive_anchored_merges_no_spurious_review_case(
    team_registry_file,
):
    """Regression: when three groups merge transitively (A↔B and A↔C both
    fire anchored), the B↔C pair must be skipped because the groups already
    share a DSU root. Without the same-root guard, ``dsu.can_union`` would
    return False on the redundant pair and emit a spurious
    ``conflicting_same_bookmaker_event_candidate`` review case (or, worse,
    re-evaluate the quorum override on an already-merged component).
    """

    srsni = create_canonical_team(display_name="Srsni Pisek", sport="basketball")
    sokol = create_canonical_team(display_name="Sokol Pisek", sport="basketball")
    bk = create_canonical_team(display_name="BK Pisek", sport="basketball")
    pardubice = create_canonical_team(display_name="Pardubice", sport="basketball")
    league_id = "ceska_liga"
    await _seed_bookmakers("mozzart", "meridian", "superbet", "maxbet")
    await _seed_league(league_id, "basketball")
    a_match_id = generate_match_id(
        srsni.team_id, pardubice.team_id, START_TIME, "basketball"
    )
    b_match_id = generate_match_id(
        sokol.team_id, pardubice.team_id, START_TIME, "basketball"
    )
    c_match_id = generate_match_id(
        bk.team_id, pardubice.team_id, START_TIME, "basketball"
    )
    normalized = [
        # Group A: 2 bookmakers (mozzart, meridian) — Srsni Pisek
        _basketball_odds(
            "mozzart",
            match_id=a_match_id,
            league_id=league_id,
            home_team_id=srsni.team_id,
            away_team_id=pardubice.team_id,
            home_team=srsni.team_name,
            away_team=pardubice.team_name,
            threshold=12.5,
        ),
        _basketball_odds(
            "meridian",
            match_id=a_match_id,
            league_id=league_id,
            home_team_id=srsni.team_id,
            away_team_id=pardubice.team_id,
            home_team=srsni.team_name,
            away_team=pardubice.team_name,
            threshold=13.0,
        ),
        # Group B: 1 bookmaker (superbet) — Sokol Pisek
        _basketball_odds(
            "superbet",
            match_id=b_match_id,
            league_id=league_id,
            home_team_id=sokol.team_id,
            away_team_id=pardubice.team_id,
            home_team=sokol.team_name,
            away_team=pardubice.team_name,
            threshold=13.5,
        ),
        # Group C: 1 bookmaker (maxbet) — BK Pisek
        _basketball_odds(
            "maxbet",
            match_id=c_match_id,
            league_id=league_id,
            home_team_id=bk.team_id,
            away_team_id=pardubice.team_id,
            home_team=bk.team_name,
            away_team=pardubice.team_name,
            threshold=14.0,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOddsData(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="basketball",
            home_team=row.home_team,
            away_team=row.away_team,
            market_type=row.market_type,
            player_name=row.player_name,
            threshold=row.threshold,
            over_odds=row.over_odds,
            under_odds=row.under_odds,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=raw,
        raw_outcome_offers=[],
        normalized_odds=normalized,
        normalized_outcome_offers=[],
    )

    assert result.resolved_events == 1
    events = await odds_store.list_resolved_events(sport="basketball")
    assert len(events) == 1
    event = await odds_store.get_resolved_event(events[0].id)
    assert event is not None
    assert event.method == "auto_fuzzy_high"
    assert {member.bookmaker_id for member in event.members} == {
        "mozzart",
        "meridian",
        "superbet",
        "maxbet",
    }
    review_cases = await odds_store.list_event_review_cases(status="pending")
    assert all(
        case.reason_code != "conflicting_same_bookmaker_event_candidate"
        for case in review_cases
    ), (
        "transitive merges should not surface a same-bookmaker conflict; "
        f"got {[c.reason_code for c in review_cases]}"
    )


@pytest.mark.asyncio
async def test_event_resolver_anchored_low_conf_does_not_apply_to_football(
    team_registry_file,
):
    """Negative regression: anchored low-confidence merging is restricted to
    basketball. Football fixtures with shared city tokens — e.g.
    ``Manchester United vs Liverpool`` ↔ ``Manchester City vs Liverpool``
    (weak side ~62) — must not auto-merge despite passing every other
    anchored predicate (3 bookmakers, same league, shared significant
    token "Manchester").
    """

    united = create_canonical_team(
        display_name="Manchester United", sport="football"
    )
    city = create_canonical_team(display_name="Manchester City", sport="football")
    liverpool = create_canonical_team(display_name="Liverpool", sport="football")
    league_id = "premier_league"
    await _seed_bookmakers("maxbet", "balkanbet", "superbet")
    await _seed_league(league_id, "football")
    united_match_id = generate_match_id(
        united.team_id, liverpool.team_id, START_TIME, "football"
    )
    city_match_id = generate_match_id(
        city.team_id, liverpool.team_id, START_TIME, "football"
    )
    normalized: list[NormalizedOutcomeOffer] = [
        NormalizedOutcomeOffer(
            match_id=united_match_id,
            bookmaker_id="maxbet",
            league_id=league_id,
            sport="football",
            home_team_id=united.team_id,
            away_team_id=liverpool.team_id,
            home_team=united.team_name,
            away_team=liverpool.team_name,
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.9,
            line=2.5,
            raw_label="Over 2.5",
            start_time=START_TIME,
        ),
        NormalizedOutcomeOffer(
            match_id=city_match_id,
            bookmaker_id="balkanbet",
            league_id=league_id,
            sport="football",
            home_team_id=city.team_id,
            away_team_id=liverpool.team_id,
            home_team=city.team_name,
            away_team=liverpool.team_name,
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.85,
            line=2.5,
            raw_label="Over 2.5",
            start_time=START_TIME,
        ),
        NormalizedOutcomeOffer(
            match_id=city_match_id,
            bookmaker_id="superbet",
            league_id=league_id,
            sport="football",
            home_team_id=city.team_id,
            away_team_id=liverpool.team_id,
            home_team=city.team_name,
            away_team=liverpool.team_name,
            market_type="football_total_goals",
            outcome_code="under",
            odds=1.95,
            line=2.5,
            raw_label="Under 2.5",
            start_time=START_TIME,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOutcomeOffer(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="football",
            home_team=row.home_team,
            away_team=row.away_team,
            source_url=f"https://{row.bookmaker_id}.example/football-event",
            market_type=row.market_type,
            outcome_code=row.outcome_code,
            odds=row.odds,
            line=row.line,
            raw_label=row.raw_label,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=[],
        raw_outcome_offers=raw,
        normalized_odds=[],
        normalized_outcome_offers=normalized,
    )

    assert result.resolved_events == 2
    events = await odds_store.list_resolved_events(sport="football")
    assert {event.method for event in events} == {"exact"}


@pytest.mark.asyncio
async def test_event_resolver_dot_expansion_does_not_apply_to_football(
    team_registry_file,
):
    """Negative regression for round-2 review: the ``_expand_dotted_token``
    pre-processing wrapped in ``_resolver_team_similarity`` is sport-gated to
    basketball. Football fixtures with compound abbreviations like
    ``St.Petersburg`` (Russia) and ``Stockholm Petersburg`` (a hypothetical
    European team sharing the geographic suffix) must NOT auto-merge despite
    pre-PR fuzzy similarity (~78) being insufficient for the standard
    high-confidence path. Pre-fix, the dot expansion would inflate the score
    to 100/100 and force a false-positive merge for any sport — this test
    locks in the sport gate so the regression cannot return.
    """

    saint_petersburg = create_canonical_team(
        display_name="St.Petersburg", sport="football"
    )
    stockholm_petersburg = create_canonical_team(
        display_name="Stockholm Petersburg", sport="football"
    )
    cska = create_canonical_team(display_name="CSKA Moscow", sport="football")
    league_id = "rpl"
    await _seed_bookmakers("maxbet", "balkanbet")
    await _seed_league(league_id, "football")
    sp_match_id = generate_match_id(
        saint_petersburg.team_id, cska.team_id, START_TIME, "football"
    )
    sk_match_id = generate_match_id(
        stockholm_petersburg.team_id, cska.team_id, START_TIME, "football"
    )
    normalized = [
        NormalizedOutcomeOffer(
            match_id=sp_match_id,
            bookmaker_id="maxbet",
            league_id=league_id,
            sport="football",
            home_team_id=saint_petersburg.team_id,
            away_team_id=cska.team_id,
            home_team=saint_petersburg.team_name,
            away_team=cska.team_name,
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.9,
            line=2.5,
            raw_label="Over 2.5",
            start_time=START_TIME,
        ),
        NormalizedOutcomeOffer(
            match_id=sk_match_id,
            bookmaker_id="balkanbet",
            league_id=league_id,
            sport="football",
            home_team_id=stockholm_petersburg.team_id,
            away_team_id=cska.team_id,
            home_team=stockholm_petersburg.team_name,
            away_team=cska.team_name,
            market_type="football_total_goals",
            outcome_code="under",
            odds=1.95,
            line=2.5,
            raw_label="Under 2.5",
            start_time=START_TIME,
        ),
    ]
    for row in normalized:
        await _store_match(row)
    raw = [
        RawOutcomeOffer(
            bookmaker_id=row.bookmaker_id,
            league_id=league_id,
            sport="football",
            home_team=row.home_team,
            away_team=row.away_team,
            source_url=f"https://{row.bookmaker_id}.example/football-event",
            market_type=row.market_type,
            outcome_code=row.outcome_code,
            odds=row.odds,
            line=row.line,
            raw_label=row.raw_label,
            start_time=START_TIME,
        )
        for row in normalized
    ]

    result = await resolve_and_persist_events(
        raw_odds=[],
        raw_outcome_offers=raw,
        normalized_odds=[],
        normalized_outcome_offers=normalized,
    )

    assert result.resolved_events == 2, (
        "St.Petersburg and Stockholm Petersburg are distinct football teams "
        "that share only a geographic-suffix token; the resolver must not "
        "merge them via dot expansion."
    )
    events = await odds_store.list_resolved_events(sport="football")
    assert {event.method for event in events} == {"exact"}


def test_expand_dotted_token_ambiguous_geographic_prefix_blocked():
    """Defense-in-depth unit test: even within sports that allow dot
    expansion, the ``_AMBIGUOUS_DOT_PREFIXES`` blocklist prevents short
    geographic / honorific abbreviations (``St.``, ``Mt.``, ``Ft.``,
    ``Pt.``, ``Dr.``, ``Mr.``, ``Av.``) from being substituted with
    distinct counterpart tokens. The genuine ``Ch.`` → ``Cherno``
    expansion is unaffected.

    This is a unit test on the helper rather than an end-to-end resolver
    test because the resolver's anchored low-confidence path can still
    merge events through other corroborators (shared significant token +
    same league). The blocklist's job is narrow: stop the dot-expansion
    branch from inflating the fuzzy score for known-ambiguous prefixes.
    """

    from app.services.event_resolver import _expand_dotted_token  # noqa: PLC0415

    # Each ambiguous prefix is preserved verbatim despite the counterpart
    # offering a unique expansion candidate AND a shared anchor token
    # ("Petersburg", "Olympus", etc.) that satisfies the structural anchor
    # check. The blocklist short-circuits expansion before we reach the
    # candidate selection.
    assert _expand_dotted_token("St.Petersburg", "Stockholm Petersburg") == (
        "St. Petersburg"
    ), "`St.` is in the ambiguous-prefix blocklist and must not expand."
    assert _expand_dotted_token("Mt.Vesuvius", "Manchester Vesuvius") == (
        "Mt. Vesuvius"
    ), "`Mt.` is in the ambiguous-prefix blocklist."
    assert _expand_dotted_token("Pt.Lions", "Portland Lions") == "Pt. Lions"
    assert _expand_dotted_token("Ft.Wayne", "Fortune Wayne") == "Ft. Wayne"

    # Genuine non-ambiguous expansions still work (regression for the
    # original Cherno More user case).
    assert (
        _expand_dotted_token("Ch.More", "Cherno More") == "Cherno More"
    ), "Non-ambiguous prefix `Ch.` must continue to expand."
    assert (
        _expand_dotted_token("Spartak Pl.", "Spartak Pleven") == "Spartak Pleven"
    ), "Non-ambiguous trailing-dot tokens still resolve."


def test_team_qualifiers_explicit_z_marker_is_cross_sport_but_plain_z_is_not():
    """Explicit ``Ž`` marker syntax is cross-sport, but plain ASCII ``Z`` is
    still too ambiguous to mean women by itself.
    """

    assert _team_qualifiers("FK Borac Z", sport="football") == set()
    assert _team_qualifiers("FK Borac Zvornik", sport="football") == set()
    assert _team_qualifiers("FK Crvena Zvezda Z", sport="football") == set()
    assert _team_qualifiers("FK Crvena Zvezda", sport="football") == set()
    assert _same_team_context("FK Borac Z", "FK Borac Zvornik", sport="football")
    assert _same_team_context(
        "FK Crvena Zvezda Z", "FK Crvena Zvezda", sport="football"
    )
    assert _team_qualifiers("Sao Jose Z", sport="basketball") == set()
    assert _team_qualifiers("Sao Jose Z", sport="tennis") == set()
    assert _team_qualifiers("Sao Jose (Ž)", sport="basketball") == {"women"}
    assert _team_qualifiers("Sao Jose (Ž)", sport="football") == {"women"}
    assert _team_qualifiers("Ž/Sao Jose Dos Campos", sport="football") == {"women"}
    assert _team_qualifiers("Z/Sao Jose Dos Campos", sport="volleyball") == {"women"}


def test_comparison_text_preserves_plain_z_when_explicit_marker_is_present():
    assert _comparison_team_text("FK Borac Z (Ž)", sport="football") == "fk borac z"
    assert _comparison_team_text("FK Borac (Ž)", sport="football") == "fk borac"


def test_team_qualifiers_wom_alias_applies_cross_sport():
    assert _team_qualifiers("Sao Jose Wom", sport="football") == {"women"}
    assert _team_qualifiers("Sao Jose Wom", sport="tennis") == {"women"}
    assert _team_qualifiers("Sao Jose Women", sport="football") == {"women"}
    assert _team_qualifiers("Sao Jose Wom") == {"women"}
    assert _team_qualifiers("Sao Jose Wom", sport="basketball") == {"women"}
    assert _same_team_context(
        "Sao Jose Wom", "Sao Jose Women", sport="football"
    )


def test_contextual_merge_source_ids_threads_sport_to_helpers():
    """Integration regression for scheduler-driven canonical-team auto-merge.

    ``Wom`` and ``Women`` are intentionally cross-sport women markers now, so
    the same contextual merge should be eligible in basketball and football.
    The separate plain-``Z`` tests cover the Slavic-football abbreviation case
    that originally motivated the old basketball-only gate.
    """

    basketball_case = TeamReviewDiagnostic(
        bookmaker_id="meridian",
        raw_league_id="norway_basket",
        normalized_raw_league_id="norway_basket",
        sport="basketball",
        scope_league_id="norway_basket",
        raw_team_name="Aalesund Wom",
        normalized_raw_team_name="aalesund wom",
        suggested_team_id=101,
        suggested_team_name="Aalesund Women",
        start_time=START_TIME,
        reason_code="candidate_team_match_same_start_time",
        confidence="very_high",
        similarity_score=92.0,
        matched_counterpart_team="Bergen Women",
        canonical_home_team="Aalesund Women",
        canonical_away_team="Bergen Women",
        candidate_teams=[
            TeamReviewCandidate(
                team_id=101,
                team_name="Aalesund Women",
                score=92.0,
                slot_support=3,
                canonical_home_team="Aalesund Women",
                canonical_away_team="Bergen Women",
            ),
            TeamReviewCandidate(
                team_id=202,
                team_name="Aalesund Wom",
                score=92.0,
                slot_support=2,
                canonical_home_team="Aalesund Wom",
                canonical_away_team="Bergen Women",
            ),
        ],
    )

    assert _contextual_merge_source_ids(basketball_case) == {202}, (
        "Basketball case: ``Aalesund Wom`` (lower slot_support) must merge "
        "into ``Aalesund Women``. Both names alias to the ``women`` "
        "qualifier in basketball, the symmetric score is 92.3 (>=88), "
        "neither name is a strict subset of the other, and the canonical "
        "event teams overlap on ``Bergen Women``."
    )

    football_case = basketball_case.model_copy(update={"sport": "football"})
    assert _contextual_merge_source_ids(football_case) == {202}, (
        "Football women-marker cases should follow the same contextual "
        "canonical merge path as basketball."
    )
