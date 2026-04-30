from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.models.schemas import NormalizedOdds, NormalizedOutcomeOffer, RawOddsData, RawOutcomeOffer
from app.services.event_resolver import (
    SameTimeCanonicalSlot,
    _same_time_slot_orientation,
    resolve_and_persist_events,
)
from app.services.normalizer import generate_match_id
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
