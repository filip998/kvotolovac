from __future__ import annotations

import asyncio
import sqlite3

import aiosqlite
import pytest

from app.config import settings
from app.database import close_db, get_db, init_db
from app.models.schemas import (
    EventReviewCaseIn,
    NormalizedOdds,
    NormalizedOutcomeOffer,
    OpportunityLeg,
    ResolvedEventIn,
    ResolvedEventMemberIn,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from app.services.opportunity_analyzer import Opportunity
from app.store import odds_store


@pytest.mark.asyncio
async def test_upsert_and_get_bookmaker():
    await odds_store.upsert_bookmaker("mozzart", "Mozzart", "https://mozzartbet.com")
    bookmakers = await odds_store.get_bookmakers()
    assert len(bookmakers) == 1
    assert bookmakers[0].id == "mozzart"
    assert bookmakers[0].name == "Mozzart"


@pytest.mark.asyncio
async def test_upsert_and_get_league():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    leagues = await odds_store.get_leagues()
    assert len(leagues) == 1
    assert leagues[0].sport == "basketball"


@pytest.mark.asyncio
async def test_upsert_and_get_match():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at="2026-04-11T20:06:00.735723",
    )
    await odds_store.set_current_snapshot("2026-04-11T20:06:00.735723")
    matches = await odds_store.get_matches()
    assert len(matches) == 1
    assert matches[0].home_team == "Partizan"
    assert [book.id for book in matches[0].available_bookmakers] == ["mozzart"]


@pytest.mark.asyncio
async def test_get_match_by_id():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    match = await odds_store.get_match("m1")
    assert match is not None
    assert match.away_team == "Crvena Zvezda"


@pytest.mark.asyncio
async def test_get_nonexistent_match():
    match = await odds_store.get_match("nonexistent")
    assert match is None


@pytest.mark.asyncio
async def test_upsert_odds_and_history():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")

    odds = NormalizedOdds(
        match_id="m1",
        bookmaker_id="mozzart",
        league_id="euroleague",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        source_url="https://example.com/m1",
        market_type="player_points",
        player_name="Iffe Lundberg",
        threshold=16.5,
        over_odds=1.85,
        under_odds=1.95,
    )
    await odds_store.upsert_odds(odds, scraped_at="2026-04-11T20:06:00.735723")
    await odds_store.set_current_snapshot("2026-04-11T20:06:00.735723")

    current = await odds_store.get_odds_for_match("m1")
    assert len(current) == 1
    assert current[0].threshold == 16.5
    assert current[0].source_url == "https://example.com/m1"

    history = await odds_store.get_odds_history_for_match("m1")
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_get_current_canonical_offers_for_matches_reads_odds_and_outcome_offers():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_league("premier_league", "Premier League", "football")
    await odds_store.upsert_match(
        "basketball-match",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_match(
        "football-match",
        "premier_league",
        "Team Alpha",
        "Team Beta",
        sport="football",
    )
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    scraped_at = "2030-01-01T19:55:00+00:00"

    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="basketball-match",
            bookmaker_id="mozzart",
            league_id="euroleague",
            sport="basketball",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            source_url="https://example.com/basketball",
            market_type="player_points",
            player_name="Nikola Jovic",
            threshold=16.5,
            over_odds=1.91,
            under_odds=1.87,
        ),
        scraped_at=scraped_at,
    )
    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="football-match",
            bookmaker_id="maxbet",
            league_id="premier_league",
            sport="football",
            home_team="Team Alpha",
            away_team="Team Beta",
            source_url="https://example.com/football",
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.85,
            line=2.5,
            raw_label="3+",
        ),
        scraped_at=scraped_at,
    )
    await odds_store.set_current_snapshot(scraped_at)

    offers = await odds_store.get_current_canonical_offers_for_matches(
        ["basketball-match", "football-match"]
    )

    assert [(offer.bookmaker_id, offer.outcome_code) for offer in offers] == [
        ("mozzart", "over"),
        ("mozzart", "under"),
        ("maxbet", "over"),
    ]
    basketball_market = offers[0].market
    football_market = offers[2].market
    assert basketball_market.market_type == "player_points"
    assert basketball_market.subject_type == "player"
    assert basketball_market.subject_name == "Nikola Jovic"
    assert offers[0].scraped_at == scraped_at
    assert football_market.market_type == "football_total_goals"
    assert football_market.subject_type == "event"
    assert football_market.line == 2.5
    assert offers[2].scraped_at == scraped_at


@pytest.mark.asyncio
async def test_current_canonical_offers_use_active_resolved_event_identity():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match(
        "bookmaker-match-a",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_match(
        "bookmaker-match-b",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    scraped_at = "2030-01-01T19:55:00+00:00"
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="resolved-partizan-zvezda",
            sport="basketball",
            start_time="2030-01-01T20:00:00+00:00",
            primary_match_id="bookmaker-match-a",
            method="manual",
        )
    )
    for match_id, bookmaker_id, player_name in (
        ("bookmaker-match-a", "mozzart", "Nikola Jokić"),
        ("bookmaker-match-b", "maxbet", "N. Jokic"),
    ):
        await odds_store.link_resolved_event_member(
            ResolvedEventMemberIn(
                resolved_event_id="resolved-partizan-zvezda",
                match_id=match_id,
                bookmaker_id=bookmaker_id,
            )
        )
        await odds_store.upsert_odds(
            NormalizedOdds(
                match_id=match_id,
                bookmaker_id=bookmaker_id,
                league_id="euroleague",
                sport="basketball",
                home_team="Partizan",
                away_team="Crvena Zvezda",
                market_type="player_points",
                player_name=player_name,
                threshold=16.5,
                over_odds=1.91,
                under_odds=None,
            ),
            scraped_at=scraped_at,
        )
    await odds_store.set_current_snapshot(scraped_at)

    offers = await odds_store.get_current_canonical_offers_for_matches(
        ["bookmaker-match-a", "bookmaker-match-b"]
    )

    assert len(offers) == 2
    assert {offer.market.event_id for offer in offers} == {"resolved-partizan-zvezda"}
    assert len({offer.market_key for offer in offers}) == 1
    assert {offer.market.subject_name for offer in offers} == {"Nikola Jokić"}
    assert {offer.market.subject_key for offer in offers} == {
        offers[0].market.subject_key
    }
    assert offers[0].market.subject_key
    assert offers[0].market.subject_key.startswith("ply_")


@pytest.mark.asyncio
async def test_current_canonical_offers_capture_snapshot_once(monkeypatch):
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_league("premier_league", "Premier League", "football")
    await odds_store.upsert_match(
        "basketball-match",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_match(
        "football-match",
        "premier_league",
        "Team Alpha",
        "Team Beta",
        sport="football",
    )
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    stable_snapshot = "2030-01-01T19:55:00+00:00"
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="basketball-match",
            bookmaker_id="mozzart",
            league_id="euroleague",
            sport="basketball",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Nikola Jovic",
            threshold=16.5,
            over_odds=1.91,
            under_odds=None,
        ),
        scraped_at=stable_snapshot,
    )
    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="football-match",
            bookmaker_id="maxbet",
            league_id="premier_league",
            sport="football",
            home_team="Team Alpha",
            away_team="Team Beta",
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.85,
            line=2.5,
        ),
        scraped_at=stable_snapshot,
    )
    calls = 0

    async def fake_snapshot_filter(db, alias):
        nonlocal calls
        calls += 1
        return f"{alias}.scraped_at = ?", [stable_snapshot]

    monkeypatch.setattr(
        odds_store,
        "_current_or_legacy_snapshot_filter",
        fake_snapshot_filter,
    )

    offers = await odds_store.get_current_canonical_offers_for_matches(
        ["basketball-match", "football-match"]
    )

    assert calls == 1
    assert [(offer.bookmaker_id, offer.scraped_at) for offer in offers] == [
        ("mozzart", stable_snapshot),
        ("maxbet", stable_snapshot),
    ]


@pytest.mark.asyncio
async def test_upsert_odds_preserves_existing_source_url_within_snapshot():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    snapshot_at = "2026-04-11T20:06:00.735723"

    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            source_url="https://example.com/m1",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at=snapshot_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            source_url=None,
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.9,
            under_odds=1.9,
        ),
        scraped_at=snapshot_at,
    )
    await odds_store.set_current_snapshot(snapshot_at)

    current = await odds_store.get_odds_for_match("m1")

    assert len(current) == 1
    assert current[0].source_url == "https://example.com/m1"


@pytest.mark.asyncio
async def test_get_matches_returns_only_latest_scrape_batch():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("fresh", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")

    stale_odds = NormalizedOdds(
        match_id="stale",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Bayern Munich",
        away_team="Maccabi Tel Aviv",
        market_type="player_points",
        player_name="Saben Lee",
        threshold=13.5,
        over_odds=1.8,
        under_odds=2.0,
    )
    fresh_odds = NormalizedOdds(
        match_id="fresh",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=6.5,
        over_odds=2.09,
        under_odds=1.66,
    )

    stale_snapshot_at = "2026-04-10T13:39:04.516801"
    fresh_snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_odds(stale_odds, scraped_at=stale_snapshot_at)
    await odds_store.set_current_snapshot(stale_snapshot_at)
    await odds_store.upsert_odds(fresh_odds, scraped_at=fresh_snapshot_at)
    await odds_store.set_current_snapshot(fresh_snapshot_at)

    matches = await odds_store.get_matches()

    assert [match.id for match in matches] == ["fresh"]
    assert [book.id for book in matches[0].available_bookmakers] == ["meridian"]


@pytest.mark.asyncio
async def test_get_matches_filters_by_bookmakers_and_keeps_full_coverage():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("m2", "euroleague", "Monaco", "Barcelona")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    batch_scraped_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=17.5,
            over_odds=1.9,
            under_odds=1.85,
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m2",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Monaco",
            away_team="Barcelona",
            market_type="player_points",
            player_name="Mike James",
            threshold=19.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    matches = await odds_store.get_matches(bookmaker_ids=["meridian"])

    assert [match.id for match in matches] == ["m1"]
    assert [book.id for book in matches[0].available_bookmakers] == ["meridian", "mozzart"]


@pytest.mark.asyncio
async def test_get_odds_for_match_returns_only_latest_scrape_batch():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    stale_odds = NormalizedOdds(
        match_id="m1",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=5.5,
        over_odds=1.91,
        under_odds=1.8,
    )
    fresh_odds = NormalizedOdds(
        match_id="m1",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=6.5,
        over_odds=2.09,
        under_odds=1.66,
    )

    stale_snapshot_at = "2026-04-10T13:39:04.516801"
    fresh_snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_odds(stale_odds, scraped_at=stale_snapshot_at)
    await odds_store.set_current_snapshot(stale_snapshot_at)
    await odds_store.upsert_odds(fresh_odds, scraped_at=fresh_snapshot_at)
    await odds_store.set_current_snapshot(fresh_snapshot_at)

    current = await odds_store.get_odds_for_match("m1")
    history = await odds_store.get_odds_history_for_match("m1")

    assert len(current) == 1
    assert current[0].threshold == 6.5
    assert len(history) == 2


@pytest.mark.asyncio
async def test_upsert_odds_keeps_line_and_milestone_rows_separate():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("oktagonbet", "OktagonBet")

    line = NormalizedOdds(
        match_id="m1",
        bookmaker_id="oktagonbet",
        league_id="euroleague",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type="player_points",
        player_name="Iffe Lundberg",
        threshold=9.5,
        over_odds=1.85,
        under_odds=1.95,
    )
    milestone = NormalizedOdds(
        match_id="m1",
        bookmaker_id="oktagonbet",
        league_id="euroleague",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type="player_points_milestones",
        player_name="Iffe Lundberg",
        threshold=9.5,
        over_odds=1.85,
        under_odds=None,
    )

    batch_scraped_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_odds(line, scraped_at=batch_scraped_at)
    await odds_store.upsert_odds(milestone, scraped_at=batch_scraped_at)
    await odds_store.set_current_snapshot(batch_scraped_at)

    current = await odds_store.get_odds_for_match("m1")
    assert len(current) == 2
    assert {offer.market_type for offer in current} == {
        "player_points",
        "player_points_milestones",
    }


@pytest.mark.asyncio
async def test_insert_and_get_unresolved_odds():
    batch_scraped_at = "2026-04-13T16:36:09.440629"
    await odds_store.upsert_bookmaker("admiralbet", "AdmiralBet")
    await odds_store.upsert_league("aba_liga", "ABA Liga", "basketball")
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="admiralbet",
            raw_league_id="AdmiralBet ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="P. Nikolic",
            raw_team_name="Borac Cacak",
            normalized_team_name="Borac Cacak",
            start_time="2026-04-13T16:00:00+00:00",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
            candidate_matchups=[],
            available_matchups_same_slot=["Dubai vs Buducnost"],
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    unresolved = await odds_store.get_unresolved_odds()

    assert len(unresolved) == 1
    assert unresolved[0].bookmaker_name == "AdmiralBet"
    assert unresolved[0].league_name == "ABA Liga"
    assert unresolved[0].available_matchups_same_slot == ["Dubai vs Buducnost"]


@pytest.mark.asyncio
async def test_get_unresolved_odds_filters_by_multiple_bookmakers():
    batch_scraped_at = "2026-04-13T18:00:00+00:00"
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    for bookmaker_id in ("maxbet", "meridian"):
        await odds_store.insert_unresolved_odds(
            UnresolvedOddsDiagnostic(
                bookmaker_id=bookmaker_id,
                raw_league_id="ABA Liga",
                league_id="aba_liga",
                market_type="player_points",
                player_name="S. Ilic",
                raw_team_name="Borac",
                normalized_team_name="Borac",
                start_time="2026-04-13T18:00:00+00:00",
                threshold=10.5,
                over_odds=1.8,
                under_odds=2.0,
                reason_code="no_canonical_matchup_for_team_at_slot",
                candidate_count=0,
            ),
            scraped_at=batch_scraped_at,
        )
    await odds_store.set_current_snapshot(batch_scraped_at)

    unresolved = await odds_store.get_unresolved_odds(bookmaker_ids=["meridian"])

    assert len(unresolved) == 1
    assert unresolved[0].bookmaker_id == "meridian"


@pytest.mark.asyncio
async def test_get_unresolved_odds_respects_current_snapshot():
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="maxbet",
            raw_league_id="ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="S. Ilic",
            raw_team_name="Borac",
            normalized_team_name="Borac",
            start_time="2026-04-13T16:00:00+00:00",
            threshold=9.5,
            over_odds=1.9,
            under_odds=1.9,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
        ),
        scraped_at="2026-04-13T16:00:00+00:00",
    )
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="maxbet",
            raw_league_id="ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="S. Ilic",
            raw_team_name="Borac",
            normalized_team_name="Borac",
            start_time="2026-04-13T18:00:00+00:00",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
        ),
        scraped_at="2026-04-13T18:00:00+00:00",
    )
    await odds_store.set_current_snapshot("2026-04-13T18:00:00+00:00")

    unresolved = await odds_store.get_unresolved_odds()

    assert len(unresolved) == 1
    assert unresolved[0].threshold == 10.5


@pytest.mark.asyncio
async def test_legacy_discrepancies_table_is_dropped(
    tmp_path,
    monkeypatch,
):
    await close_db()
    legacy_db_path = tmp_path / "legacy.db"
    with sqlite3.connect(legacy_db_path) as conn:
        conn.execute(
            """CREATE TABLE discrepancies (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   match_id TEXT REFERENCES matches(id),
                   market_type TEXT NOT NULL,
                   player_name TEXT,
                   bookmaker_a_id TEXT,
                   bookmaker_b_id TEXT,
                   threshold_a REAL,
                   threshold_b REAL,
                   odds_a REAL,
                   odds_b REAL,
                   gap REAL,
                   profit_margin REAL,
                   detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   is_active BOOLEAN DEFAULT TRUE
               )"""
        )
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{legacy_db_path}")

    await init_db(str(legacy_db_path))
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'discrepancies'"
    )

    assert rows == []


@pytest.mark.asyncio
async def test_match_bookmaker_sources_migration_is_snapshot_scoped(
    tmp_path,
    monkeypatch,
):
    await close_db()
    legacy_db_path = tmp_path / "legacy_sources.db"
    with sqlite3.connect(legacy_db_path) as conn:
        conn.execute(
            """CREATE TABLE match_bookmaker_sources (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   match_id TEXT NOT NULL REFERENCES matches(id),
                   bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id),
                   source_url TEXT,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   UNIQUE(match_id, bookmaker_id)
               )"""
        )
        conn.execute(
            """INSERT INTO match_bookmaker_sources (
                   match_id,
                   bookmaker_id,
                   source_url
               ) VALUES ('match-1', 'meridian', 'https://legacy.example')"""
        )
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{legacy_db_path}")

    await init_db(str(legacy_db_path))
    db = await get_db()
    columns = await db.execute_fetchall("PRAGMA table_info(match_bookmaker_sources)")
    indexes = await db.execute_fetchall("PRAGMA index_list(match_bookmaker_sources)")
    rows = await db.execute_fetchall(
        """SELECT snapshot_id, match_id, bookmaker_id, source_url
           FROM match_bookmaker_sources"""
    )

    assert "snapshot_id" in {row[1] for row in columns}
    assert not any(
        str(row[1]).startswith("sqlite_autoindex_match_bookmaker_sources")
        for row in indexes
    )
    assert "idx_match_bookmaker_sources_unique_snapshot" in {row[1] for row in indexes}
    assert [tuple(row) for row in rows] == [
        (None, "match-1", "meridian", "https://legacy.example")
    ]

    await db.execute(
        "INSERT INTO bookmakers (id, name) VALUES ('meridian', 'Meridian')"
    )
    await db.execute(
        """INSERT INTO matches (id, home_team, away_team)
           VALUES ('match-1', 'Home', 'Away')"""
    )
    await db.commit()
    await db.execute(
        """INSERT INTO match_bookmaker_sources (
               snapshot_id,
               match_id,
               bookmaker_id,
               source_url
           ) VALUES ('snapshot-1', 'match-1', 'meridian', 'https://snapshot.example')"""
    )
    await db.commit()
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """INSERT INTO match_bookmaker_sources (
                   snapshot_id,
                   match_id,
                   bookmaker_id,
                   source_url
               ) VALUES ('snapshot-1', 'match-1', 'meridian', 'https://dupe.example')"""
        )
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_odds_history_migration_backfills_snapshot_metadata(
    tmp_path,
    monkeypatch,
):
    await close_db()
    legacy_db_path = tmp_path / "legacy_history.db"
    history_at = "2026-04-11T20:06:00.735723"
    with sqlite3.connect(legacy_db_path) as conn:
        conn.execute(
            """CREATE TABLE odds_history (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   match_id TEXT,
                   bookmaker_id TEXT,
                   market_type TEXT,
                   player_name TEXT,
                   threshold REAL,
                   over_odds REAL,
                   under_odds REAL,
                   scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.execute(
            """INSERT INTO odds_history (
                   match_id,
                   bookmaker_id,
                   market_type,
                   player_name,
                   threshold,
                   over_odds,
                   under_odds,
                   scraped_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "match-1",
                "meridian",
                "player_points",
                "Saben Lee",
                13.5,
                1.8,
                2.0,
                history_at,
            ),
        )
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{legacy_db_path}")

    await init_db(str(legacy_db_path))
    db = await get_db()
    history_rows = await db.execute_fetchall(
        "SELECT snapshot_id, scraped_at FROM odds_history"
    )
    snapshot_rows = await db.execute_fetchall(
        "SELECT id, scraped_at, status FROM scrape_snapshots"
    )

    assert [(row["snapshot_id"], row["scraped_at"]) for row in history_rows] == [
        (history_at, history_at)
    ]
    assert [(row["id"], row["scraped_at"], row["status"]) for row in snapshot_rows] == [
        (history_at, history_at, "published")
    ]


@pytest.mark.asyncio
async def test_resolved_event_id_migration_preserves_foreign_keys(
    tmp_path,
    monkeypatch,
):
    await close_db()
    legacy_db_path = tmp_path / "legacy_resolved_event_fk.db"
    with sqlite3.connect(legacy_db_path) as conn:
        conn.executescript(
            """CREATE TABLE discrepancies (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   match_id TEXT REFERENCES matches(id),
                   resolved_event_id TEXT,
                   market_type TEXT NOT NULL,
                   player_name TEXT,
                   bookmaker_a_id TEXT,
                   bookmaker_a_match_id TEXT,
                   bookmaker_b_id TEXT,
                   bookmaker_b_match_id TEXT,
                   threshold_a REAL,
                   threshold_b REAL,
                   odds_a REAL,
                   odds_b REAL,
                   gap REAL,
                   profit_margin REAL,
                   detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   is_active BOOLEAN DEFAULT TRUE
               );
               CREATE TABLE opportunities (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   sport TEXT NOT NULL,
                   match_id TEXT REFERENCES matches(id),
                   resolved_event_id TEXT,
                   opportunity_type TEXT NOT NULL,
                   market_type TEXT NOT NULL,
                   line REAL,
                   profit_margin REAL,
                   legs TEXT NOT NULL DEFAULT '[]',
                   detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   is_active BOOLEAN DEFAULT TRUE
               );
               INSERT INTO opportunities (
                   sport,
                   resolved_event_id,
                   opportunity_type,
                   market_type,
                   legs
               )
               VALUES (
                   'basketball',
                   'missing-event',
                   'cross_bookmaker_arbitrage',
                   'player_points',
                   '[]'
               );"""
        )
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{legacy_db_path}")

    await init_db(str(legacy_db_path))
    db = await get_db()
    opportunity_fks = await db.execute_fetchall("PRAGMA foreign_key_list(opportunities)")

    assert ("resolved_event_id", "resolved_events") in {
        (row[3], row[2]) for row in opportunity_fks
    }
    opportunity_stale_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS count FROM opportunities WHERE resolved_event_id IS NULL"
    )
    assert opportunity_stale_rows[0]["count"] == 1

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """INSERT INTO opportunities (
                   sport,
                   resolved_event_id,
                   opportunity_type,
                   market_type,
                   legs
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                "basketball",
                "still-missing-event",
                "cross_bookmaker_arbitrage",
                "player_points",
                "[]",
            ),
        )
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_event_review_case_fk_failure_rolls_back_connection():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match(
        "match-1",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
        start_time="2030-01-01T20:00:00+00:00",
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await odds_store.upsert_event_review_case(
            EventReviewCaseIn(
                fingerprint="bad-candidate-event",
                sport="basketball",
                start_time="2030-01-01T20:00:00+00:00",
                primary_match_id="match-1",
                candidate_resolved_event_id="missing-event",
                candidate_match_ids=["match-1"],
                reason_code="candidate_event_equivalence",
                source_bookmaker_ids=[],
            )
        )

    case_id = await odds_store.upsert_event_review_case(
        EventReviewCaseIn(
            fingerprint="good-candidate-event",
            sport="basketball",
            start_time="2030-01-01T20:00:00+00:00",
            primary_match_id="match-1",
            candidate_match_ids=["match-1"],
            reason_code="candidate_event_equivalence",
            source_bookmaker_ids=[],
        )
    )

    assert case_id > 0
@pytest.mark.asyncio
async def test_notifications_crud():
    nid = await odds_store.insert_notification("opportunity", "Test", "msg", {"gap": 2.0})
    assert nid > 0
    notifs = await odds_store.get_notifications()
    assert len(notifs) == 1
    assert notifs[0].title == "Test"


@pytest.mark.asyncio
async def test_cleanup_retained_data_prunes_stale_snapshot_rows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "persist_inapp_notifications", False)
    monkeypatch.setattr(settings, "odds_history_retention_days", 7)
    monkeypatch.setattr(settings, "team_review_retention_days", 30)

    current_snapshot_at = "2026-04-20T12:00:00"
    stale_snapshot_at = "2026-04-10T12:00:00"
    old_history_at = "2026-03-01T12:00:00"
    recent_history_at = "2026-04-18T12:00:00"

    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("fresh", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")

    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="stale",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Bayern Munich",
            away_team="Maccabi Tel Aviv",
            market_type="player_points",
            player_name="Saben Lee",
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at=old_history_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="fresh",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Maccabi Tel Aviv",
            away_team="Hapoel Tel-Aviv",
            market_type="player_points",
            player_name="Tamir Blatt",
            threshold=6.5,
            over_odds=2.09,
            under_odds=1.66,
        ),
        scraped_at=current_snapshot_at,
    )
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            league_id="euroleague",
            market_type="player_points",
            player_name="Saben Lee",
            raw_team_name="Bayern Munich",
            normalized_team_name="bayern munich",
            start_time=stale_snapshot_at,
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_match_found",
        ),
        scraped_at=stale_snapshot_at,
    )
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            league_id="euroleague",
            market_type="player_points",
            player_name="Tamir Blatt",
            raw_team_name="Maccabi Tel Aviv",
            normalized_team_name="maccabi tel aviv",
            start_time=current_snapshot_at,
            threshold=6.5,
            over_odds=2.09,
            under_odds=1.66,
            reason_code="no_match_found",
        ),
        scraped_at=current_snapshot_at,
    )
    old_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            normalized_raw_league_id="euroleague",
            scope_league_id="euroleague",
            raw_team_name="Old Alias",
            normalized_raw_team_name="old alias",
            start_time=old_history_at,
            reason_code="candidate_team_match_same_start_time",
            matched_counterpart_team="Maccabi Tel Aviv",
            canonical_home_team="Old Team",
            canonical_away_team="Maccabi Tel Aviv",
        ),
        scraped_at=old_history_at,
    )
    recent_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            normalized_raw_league_id="euroleague",
            scope_league_id="euroleague",
            raw_team_name="Recent Alias",
            normalized_raw_team_name="recent alias",
            start_time=recent_history_at,
            reason_code="candidate_team_match_same_start_time",
            matched_counterpart_team="Hapoel Tel-Aviv",
            canonical_home_team="Recent Team",
            canonical_away_team="Hapoel Tel-Aviv",
        ),
        scraped_at=recent_history_at,
    )

    old_notification_id = await odds_store.insert_notification(
        "opportunity", "Old Alert", "body", {"gap": 2.0}
    )
    recent_notification_id = await odds_store.insert_notification(
        "opportunity", "Recent Alert", "body", {"gap": 2.0}
    )
    db = await get_db()
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-01 12:00:00", old_notification_id),
    )
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-19 12:00:00", recent_notification_id),
    )
    await db.commit()
    await odds_store.persist_event_resolution_batch(
        snapshot_id=stale_snapshot_at,
        events=[
            ResolvedEventIn(
                id="evt-stale",
                sport="basketball",
                start_time=stale_snapshot_at,
                primary_match_id="stale",
                method="exact",
            )
        ],
        members=[
            ResolvedEventMemberIn(
                resolved_event_id="evt-stale",
                match_id="stale",
                bookmaker_id="meridian",
            )
        ],
        review_cases=[],
    )
    await odds_store.persist_event_resolution_batch(
        snapshot_id=current_snapshot_at,
        events=[
            ResolvedEventIn(
                id="evt-current",
                sport="basketball",
                start_time=current_snapshot_at,
                primary_match_id="fresh",
                method="exact",
            )
        ],
        members=[
            ResolvedEventMemberIn(
                resolved_event_id="evt-current",
                match_id="fresh",
                bookmaker_id="meridian",
            )
        ],
        review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=current_snapshot_at,
        snapshot_at=current_snapshot_at,
        opportunities=[],
        detected_at=current_snapshot_at,
    )
    await db.execute(
        """INSERT INTO opportunity_publishes (
               id,
               snapshot_id,
               detected_at,
               status,
               opportunity_count
           )
           VALUES (?, ?, ?, 'published', 1)""",
        ("publish-stale", stale_snapshot_at, stale_snapshot_at),
    )
    await db.execute(
        """INSERT INTO opportunities (
               publish_id,
               sport,
               match_id,
               opportunity_type,
               market_type,
               profit_margin,
               market_keys,
               legs,
               detected_at,
               is_active
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)""",
        (
            "publish-stale",
            "basketball",
            "stale",
            "same_line_arbitrage",
            "player_points",
            0.01,
            "[]",
            "[]",
            stale_snapshot_at,
        ),
    )
    await db.commit()

    counts = await odds_store.cleanup_retained_data(current_snapshot_at)

    odds_rows = await db.execute_fetchall(
        "SELECT match_id, scraped_at FROM odds ORDER BY match_id"
    )
    unresolved_rows = await db.execute_fetchall(
        "SELECT raw_team_name, scraped_at FROM unresolved_odds ORDER BY id"
    )
    history_rows = await db.execute_fetchall(
        "SELECT match_id, scraped_at FROM odds_history ORDER BY id"
    )
    review_rows = await db.execute_fetchall(
        "SELECT id, raw_team_name FROM team_review_cases ORDER BY id"
    )
    notification_rows = await db.execute_fetchall(
        "SELECT id FROM notifications ORDER BY id"
    )
    member_rows = await db.execute_fetchall(
        "SELECT snapshot_id, resolved_event_id FROM resolved_event_members ORDER BY id"
    )
    scrape_snapshot_rows = await db.execute_fetchall(
        "SELECT id FROM scrape_snapshots ORDER BY id"
    )
    publish_rows = await db.execute_fetchall(
        "SELECT id FROM opportunity_publishes ORDER BY id"
    )
    opportunity_rows = await db.execute_fetchall(
        "SELECT publish_id FROM opportunities ORDER BY id"
    )

    assert [(row["match_id"], row["scraped_at"]) for row in odds_rows] == [
        ("fresh", current_snapshot_at)
    ]
    assert [(row["raw_team_name"], row["scraped_at"]) for row in unresolved_rows] == [
        ("Maccabi Tel Aviv", current_snapshot_at)
    ]
    assert [(row["match_id"], row["scraped_at"]) for row in history_rows] == [
        ("fresh", current_snapshot_at)
    ]
    assert [(row["id"], row["raw_team_name"]) for row in review_rows] == [
        (recent_case_id, "Recent Alias")
    ]
    assert notification_rows == []
    assert [
        (row["snapshot_id"], row["resolved_event_id"]) for row in member_rows
    ] == [(current_snapshot_at, "evt-current")]
    assert [row["id"] for row in scrape_snapshot_rows] == [current_snapshot_at]
    assert len(publish_rows) == 1
    assert publish_rows[0]["id"].startswith(f"{current_snapshot_at}:")
    assert opportunity_rows == []
    assert counts == {
        "deleted_stale_odds": 1,
        "deleted_stale_unresolved_odds": 1,
        "deleted_stale_resolved_event_members": 1,
        "deleted_stale_match_bookmaker_sources": 1,
        "deleted_stale_opportunities": 1,
        "deleted_stale_opportunity_publishes": 1,
        "deleted_stale_scrape_snapshots": 3,
        "deleted_odds_history": 1,
        "deleted_team_review_cases": 1,
        "deleted_notifications": 2,
    }
    assert old_case_id != recent_case_id


@pytest.mark.asyncio
async def test_cleanup_retained_data_keeps_recent_notifications_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "persist_inapp_notifications", True)
    monkeypatch.setattr(settings, "notification_retention_days", 3)

    current_snapshot_at = "2026-04-20T12:00:00"
    old_notification_id = await odds_store.insert_notification(
        "opportunity", "Old Alert", "body", {"gap": 2.0}
    )
    recent_notification_id = await odds_store.insert_notification(
        "opportunity", "Recent Alert", "body", {"gap": 2.0}
    )

    db = await get_db()
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-10 12:00:00", old_notification_id),
    )
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-19 12:00:00", recent_notification_id),
    )
    await db.commit()

    counts = await odds_store.cleanup_retained_data(current_snapshot_at)
    notification_rows = await db.execute_fetchall(
        "SELECT id FROM notifications ORDER BY id"
    )

    assert [row["id"] for row in notification_rows] == [recent_notification_id]
    assert counts["deleted_notifications"] == 1


@pytest.mark.asyncio
async def test_cleanup_retained_data_uses_isolated_connection_for_transaction(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "persist_inapp_notifications", False)

    pause_delete = asyncio.Event()
    allow_delete = asyncio.Event()
    original_connect = odds_store.aiosqlite.connect

    class PausingConnection:
        def __init__(self, conn):
            self._conn = conn

        async def execute(self, sql, parameters=()):
            if sql.strip() == "DELETE FROM notifications":
                pause_delete.set()
                await allow_delete.wait()
            return await self._conn.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    async def fake_connect(*args, **kwargs):
        conn = await original_connect(*args, **kwargs)
        return PausingConnection(conn)

    monkeypatch.setattr(odds_store.aiosqlite, "connect", fake_connect)

    old_notification_id = await odds_store.insert_notification(
        "opportunity", "Old Alert", "body", {"gap": 2.0}
    )
    db = await get_db()
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-01 12:00:00", old_notification_id),
    )
    await db.commit()

    cleanup_task = asyncio.create_task(
        odds_store.cleanup_retained_data("2026-04-20T12:00:00")
    )
    await pause_delete.wait()

    insert_task = asyncio.create_task(
        odds_store.insert_notification("opportunity", "Concurrent Alert", "body", {"gap": 2.5})
    )
    await asyncio.sleep(0)
    allow_delete.set()

    counts = await cleanup_task
    inserted_id = await insert_task
    notification_rows = await db.execute_fetchall(
        "SELECT id, title FROM notifications ORDER BY id"
    )

    assert inserted_id > 0
    assert [(row["id"], row["title"]) for row in notification_rows] == [
        (inserted_id, "Concurrent Alert")
    ]
    assert counts["deleted_notifications"] == 1


@pytest.mark.asyncio
async def test_system_status():
    status = await odds_store.get_system_status()
    assert status.status == "ok"
    assert status.total_matches == 0


@pytest.mark.asyncio
async def test_system_status_counts_only_latest_scrape_batch():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("fresh", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    stale_odds = NormalizedOdds(
        match_id="stale",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Bayern Munich",
        away_team="Maccabi Tel Aviv",
        market_type="player_points",
        player_name="Saben Lee",
        threshold=13.5,
        over_odds=1.8,
        under_odds=2.0,
    )
    fresh_odds_a = NormalizedOdds(
        match_id="fresh",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=6.5,
        over_odds=2.09,
        under_odds=1.66,
    )
    fresh_odds_b = NormalizedOdds(
        match_id="fresh",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_assists",
        player_name="Tamir Blatt",
        threshold=5.5,
        over_odds=2.0,
        under_odds=1.73,
    )

    stale_snapshot_at = "2026-04-10T13:39:04.516801"
    fresh_snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_odds(stale_odds, scraped_at=stale_snapshot_at)
    await odds_store.set_current_snapshot(stale_snapshot_at)
    await odds_store.upsert_odds(fresh_odds_a, scraped_at=fresh_snapshot_at)
    await odds_store.upsert_odds(fresh_odds_b, scraped_at=fresh_snapshot_at)
    await odds_store.set_current_snapshot(fresh_snapshot_at)

    status = await odds_store.get_system_status()

    assert status.total_matches == 1
    assert status.total_odds == 2
    assert status.last_scrape_at == "2026-04-11T20:06:00.735723"


@pytest.mark.asyncio
async def test_current_snapshot_can_hide_previous_rows_without_new_odds():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    stale_odds = NormalizedOdds(
        match_id="stale",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Bayern Munich",
        away_team="Maccabi Tel Aviv",
        market_type="player_points",
        player_name="Saben Lee",
        threshold=13.5,
        over_odds=1.8,
        under_odds=2.0,
    )

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.set_current_snapshot("2026-04-11T20:06:00.735723")

    matches = await odds_store.get_matches()
    status = await odds_store.get_system_status()

    assert matches == []
    assert status.total_matches == 0
    assert status.total_odds == 0
    assert status.last_scrape_at == "2026-04-11T20:06:00.735723"


@pytest.mark.asyncio
async def test_system_status_counts_outcome_offer_only_snapshot():
    snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    persisted = await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=snapshot_at,
        odds=[],
        outcome_offers=[
            NormalizedOutcomeOffer(
                match_id="football-match",
                bookmaker_id="maxbet",
                league_id="premier-league",
                sport="football",
                home_team="Arsenal",
                away_team="Chelsea",
                market_type="football_total_goals",
                outcome_code="over",
                odds=1.85,
                line=2.5,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=str(persisted["snapshot_id"]),
        snapshot_at=snapshot_at,
        opportunities=[],
        detected_at=snapshot_at,
    )

    status = await odds_store.get_system_status()

    assert status.total_matches == 1
    assert status.total_odds == 1
    assert status.last_scrape_at == snapshot_at


@pytest.mark.asyncio
async def test_persisted_snapshot_is_hidden_until_opportunity_publish():
    old_snapshot_at = "2026-04-11T20:06:00.735723"
    new_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match("old", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="old",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Bayern Munich",
            away_team="Maccabi Tel Aviv",
            market_type="player_points",
            player_name="Saben Lee",
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at=old_snapshot_at,
    )
    await odds_store.set_current_snapshot(old_snapshot_at)

    persisted = await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=new_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="new",
                bookmaker_id="meridian",
                league_id="euroleague",
                home_team="Partizan",
                away_team="Crvena Zvezda",
                market_type="player_points",
                player_name="Carlik Jones",
                threshold=14.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )

    status_before_publish = await odds_store.get_system_status()
    matches_before_publish = await odds_store.get_matches()
    assert status_before_publish.last_scrape_at == old_snapshot_at
    assert status_before_publish.total_matches == 1
    assert matches_before_publish[0].id == "old"

    await odds_store.publish_opportunities(
        snapshot_id=str(persisted["snapshot_id"]),
        snapshot_at=new_snapshot_at,
        opportunities=[],
        detected_at=new_snapshot_at,
    )

    status_after_publish = await odds_store.get_system_status()
    matches_after_publish = await odds_store.get_matches()
    assert status_after_publish.last_scrape_at == new_snapshot_at
    assert status_after_publish.total_matches == 1
    assert status_after_publish.total_opportunities == 0
    assert matches_after_publish[0].id == "new"


@pytest.mark.asyncio
async def test_unpublished_snapshot_match_metadata_does_not_leak_to_public_reads():
    old_snapshot_at = "2026-04-11T20:06:00.735723"
    new_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=old_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Old Home",
                away_team="Old Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=old_snapshot_at,
        snapshot_at=old_snapshot_at,
        opportunities=[
            Opportunity(
                sport="basketball",
                match_id="shared-match",
                opportunity_type="same_line_arbitrage",
                market_type="player_points",
                subject_type="player",
                subject_key="saben lee",
                subject_name="Saben Lee",
                line=13.5,
                profit_margin=0.02,
                middle_profit_margin=None,
                legs=[
                    OpportunityLeg(
                        bookmaker_id="meridian",
                        market_type="player_points",
                        outcome_code="over",
                        odds=2.05,
                        line=13.5,
                    )
                ],
            )
        ],
        detected_at=old_snapshot_at,
    )

    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=new_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="New Home",
                away_team="New Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T21:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )

    matches = await odds_store.get_matches()
    odds = await odds_store.get_current_normalized_odds_for_matches(["shared-match"])
    opportunities = await odds_store.get_opportunities()

    assert [(match.home_team, match.away_team, match.start_time) for match in matches] == [
        ("Old Home", "Old Away", "2026-04-11T20:00:00+00:00")
    ]
    assert [(item.home_team, item.away_team, item.start_time) for item in odds] == [
        ("Old Home", "Old Away", "2026-04-11T20:00:00+00:00")
    ]
    assert [
        (item.home_team, item.away_team, item.start_time) for item in opportunities
    ] == [("Old Home", "Old Away", "2026-04-11T20:00:00+00:00")]


@pytest.mark.asyncio
async def test_unpublished_snapshot_source_urls_do_not_leak_to_public_reads():
    old_snapshot_at = "2026-04-11T20:06:00.735723"
    new_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=old_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Old Home",
                away_team="Old Away",
                source_url="https://old.example/match",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=old_snapshot_at,
        snapshot_at=old_snapshot_at,
        opportunities=[
            Opportunity(
                sport="basketball",
                match_id="shared-match",
                opportunity_type="same_line_arbitrage",
                market_type="player_points",
                subject_type="player",
                subject_key="saben lee",
                subject_name="Saben Lee",
                line=13.5,
                profit_margin=0.02,
                middle_profit_margin=None,
                legs=[
                    OpportunityLeg(
                        bookmaker_id="meridian",
                        market_type="player_points",
                        outcome_code="over",
                        odds=2.05,
                        line=13.5,
                    )
                ],
            )
        ],
        detected_at=old_snapshot_at,
    )

    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=new_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="New Home",
                away_team="New Away",
                source_url="https://new-hidden.example/match",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T21:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )

    current_odds = await odds_store.get_odds_for_match("shared-match")
    current_canonical_rows = await odds_store.get_current_normalized_odds_for_matches(
        ["shared-match"]
    )
    current_opportunities = await odds_store.get_opportunities()
    hidden_snapshot_rows = await odds_store.get_current_normalized_odds_for_matches(
        ["shared-match"],
        snapshot_id=new_snapshot_at,
    )

    assert [row.source_url for row in current_odds] == ["https://old.example/match"]
    assert [row.source_url for row in current_canonical_rows] == [
        "https://old.example/match"
    ]
    assert current_opportunities[0].legs[0].source_url == "https://old.example/match"
    assert [row.source_url for row in hidden_snapshot_rows] == [
        "https://new-hidden.example/match"
    ]


@pytest.mark.asyncio
async def test_legacy_null_source_url_does_not_leak_to_snapshot_public_reads():
    snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Home",
                away_team="Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[
            NormalizedOutcomeOffer(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Home",
                away_team="Away",
                market_type="game_winner",
                outcome_code="home",
                odds=1.8,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=snapshot_at,
        snapshot_at=snapshot_at,
        opportunities=[
            Opportunity(
                sport="basketball",
                match_id="shared-match",
                opportunity_type="same_line_arbitrage",
                market_type="player_points",
                subject_type="player",
                subject_key="saben lee",
                subject_name="Saben Lee",
                line=13.5,
                profit_margin=0.02,
                middle_profit_margin=None,
                legs=[
                    OpportunityLeg(
                        bookmaker_id="meridian",
                        market_type="player_points",
                        outcome_code="over",
                        odds=2.05,
                        line=13.5,
                    )
                ],
            )
        ],
        detected_at=snapshot_at,
    )
    db = await get_db()
    await db.execute(
        """INSERT INTO match_bookmaker_sources (
               snapshot_id,
               match_id,
               bookmaker_id,
               source_url
           ) VALUES (NULL, ?, ?, ?)""",
        ("shared-match", "meridian", "https://new-hidden.example/match"),
    )
    await db.commit()

    current_odds = await odds_store.get_odds_for_match("shared-match")
    current_canonical_rows = await odds_store.get_current_normalized_odds_for_matches(
        ["shared-match"]
    )
    current_outcome_rows = (
        await odds_store.get_current_normalized_outcome_offers_for_matches(
            ["shared-match"]
        )
    )
    outcome_offers = await odds_store.get_outcome_offers(match_id="shared-match")
    current_opportunities = await odds_store.get_opportunities()

    assert [row.source_url for row in current_odds] == [None]
    assert [row.source_url for row in current_canonical_rows] == [None]
    assert [row.source_url for row in current_outcome_rows] == [None]
    assert [row.source_url for row in outcome_offers] == [None]
    assert current_opportunities[0].legs[0].source_url is None


@pytest.mark.asyncio
async def test_unpublished_event_resolution_source_url_uses_batch_snapshot_scope():
    old_snapshot_at = "2026-04-11T20:06:00.735723"
    new_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=old_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Old Home",
                away_team="Old Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=old_snapshot_at,
        snapshot_at=old_snapshot_at,
        opportunities=[],
        detected_at=old_snapshot_at,
    )
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=new_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="New Home",
                away_team="New Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T21:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.persist_event_resolution_batch(
        snapshot_id=new_snapshot_at,
        events=[
            ResolvedEventIn(
                id="evt-new",
                sport="basketball",
                start_time="2026-04-11T21:00:00+00:00",
                primary_match_id="shared-match",
                method="exact",
            )
        ],
        members=[
            ResolvedEventMemberIn(
                resolved_event_id="evt-new",
                match_id="shared-match",
                bookmaker_id="meridian",
                source_url="https://new-hidden.example/event",
            )
        ],
        review_cases=[],
    )

    current_odds = await odds_store.get_odds_for_match("shared-match")
    db = await get_db()
    source_rows = await db.execute_fetchall(
        """SELECT snapshot_id, source_url
           FROM match_bookmaker_sources
           ORDER BY snapshot_id"""
    )

    assert [row.source_url for row in current_odds] == [None]
    assert [(row["snapshot_id"], row["source_url"]) for row in source_rows] == [
        (new_snapshot_at, "https://new-hidden.example/event")
    ]


@pytest.mark.asyncio
async def test_unpublished_snapshot_odds_history_does_not_leak_to_public_reads():
    old_snapshot_at = "2026-04-11T20:06:00.735723"
    new_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=old_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Old Home",
                away_team="Old Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=old_snapshot_at,
        snapshot_at=old_snapshot_at,
        opportunities=[],
        detected_at=old_snapshot_at,
    )

    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=new_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="New Home",
                away_team="New Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T21:00:00+00:00",
            ),
            NormalizedOdds(
                match_id="hidden-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Hidden Home",
                away_team="Hidden Away",
                source_url="https://hidden.example/odds",
                market_type="player_points",
                player_name="Hidden Player",
                threshold=13.5,
                over_odds=2.1,
                under_odds=1.7,
                start_time="2026-04-11T22:00:00+00:00",
            ),
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )

    shared_history = await odds_store.get_odds_history_for_match("shared-match")
    hidden_history = await odds_store.get_odds_history_for_match("hidden-match")
    hidden_public_match = await odds_store.get_match(
        "hidden-match",
        require_current_snapshot=True,
    )

    assert [(row.scraped_at, row.over_odds) for row in shared_history] == [
        (old_snapshot_at, 1.8)
    ]
    assert hidden_history == []
    assert hidden_public_match is None


@pytest.mark.asyncio
async def test_first_unpublished_snapshot_history_and_match_stay_hidden():
    snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="hidden-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Hidden Home",
                away_team="Hidden Away",
                market_type="player_points",
                player_name="Hidden Player",
                threshold=13.5,
                over_odds=2.1,
                under_odds=1.7,
                start_time="2026-04-11T22:00:00+00:00",
            )
        ],
        outcome_offers=[
            NormalizedOutcomeOffer(
                match_id="hidden-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Hidden Home",
                away_team="Hidden Away",
                source_url="https://hidden.example/offer",
                market_type="game_winner",
                outcome_code="home",
                odds=2.1,
                start_time="2026-04-11T22:00:00+00:00",
            )
        ],
        unresolved_odds=[],
        team_review_cases=[],
    )

    hidden_history = await odds_store.get_odds_history_for_match("hidden-match")
    hidden_public_match = await odds_store.get_match(
        "hidden-match",
        require_current_snapshot=True,
    )
    hidden_odds = await odds_store.get_odds_for_match("hidden-match")
    hidden_normalized_odds = await odds_store.get_current_normalized_odds_for_matches(
        ["hidden-match"]
    )
    hidden_normalized_offers = (
        await odds_store.get_current_normalized_outcome_offers_for_matches(
            ["hidden-match"]
        )
    )
    hidden_outcome_offers = await odds_store.get_outcome_offers(match_id="hidden-match")
    matches = await odds_store.get_matches()
    status = await odds_store.get_system_status()

    assert hidden_history == []
    assert hidden_public_match is None
    assert hidden_odds == []
    assert hidden_normalized_odds == []
    assert hidden_normalized_offers == []
    assert hidden_outcome_offers == []
    assert matches == []
    assert status.total_matches == 0
    assert status.total_odds == 0
    assert status.last_scrape_at is None


@pytest.mark.asyncio
async def test_current_reads_use_latest_published_snapshot_when_state_row_is_missing():
    old_snapshot_at = "2026-04-11T20:06:00.735723"
    new_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=old_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Old Home",
                away_team="Old Away",
                source_url="https://old.example/odds",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=old_snapshot_at,
        snapshot_at=old_snapshot_at,
        opportunities=[],
        detected_at=old_snapshot_at,
    )
    db = await get_db()
    await db.execute("DELETE FROM scrape_state")
    await db.commit()
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=new_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="New Home",
                away_team="New Away",
                source_url="https://hidden.example/odds",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T21:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )

    matches = await odds_store.get_matches()
    current_odds = await odds_store.get_odds_for_match("shared-match")
    status = await odds_store.get_system_status()

    assert [(match.home_team, match.start_time) for match in matches] == [
        ("Old Home", "2026-04-11T20:00:00+00:00")
    ]
    assert [(row.source_url, row.over_odds) for row in current_odds] == [
        ("https://old.example/odds", 1.8)
    ]
    assert status.total_matches == 1
    assert status.total_odds == 1
    assert status.last_scrape_at == old_snapshot_at


@pytest.mark.asyncio
async def test_cleanup_retained_data_keeps_snapshot_metadata_for_retained_history(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "persist_inapp_notifications", False)
    monkeypatch.setattr(settings, "odds_history_retention_days", 7)
    monkeypatch.setattr(settings, "team_review_retention_days", 0)

    old_snapshot_at = "2026-04-18T12:00:00"
    current_snapshot_at = "2026-04-20T12:00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    for snapshot_at, over_odds in (
        (old_snapshot_at, 1.8),
        (current_snapshot_at, 1.9),
    ):
        await odds_store.persist_scrape_snapshot_batch(
            snapshot_at=snapshot_at,
            odds=[
                NormalizedOdds(
                    match_id="shared-match",
                    bookmaker_id="meridian",
                    league_id="euroleague",
                    sport="basketball",
                    home_team="Home",
                    away_team="Away",
                    market_type="player_points",
                    player_name="Saben Lee",
                    threshold=13.5,
                    over_odds=over_odds,
                    under_odds=2.0,
                    start_time="2026-04-20T20:00:00+00:00",
                )
            ],
            outcome_offers=[],
            unresolved_odds=[],
            team_review_cases=[],
        )
        await odds_store.publish_opportunities(
            snapshot_id=snapshot_at,
            snapshot_at=snapshot_at,
            opportunities=[],
            detected_at=snapshot_at,
        )

    counts = await odds_store.cleanup_retained_data(current_snapshot_at)
    history = await odds_store.get_odds_history_for_match("shared-match")
    db = await get_db()
    snapshot_rows = await db.execute_fetchall(
        "SELECT id, status FROM scrape_snapshots ORDER BY id"
    )

    assert [(row.scraped_at, row.over_odds) for row in history] == [
        (current_snapshot_at, 1.9),
        (old_snapshot_at, 1.8),
    ]
    assert [(row["id"], row["status"]) for row in snapshot_rows] == [
        (old_snapshot_at, "published"),
        (current_snapshot_at, "published"),
    ]
    assert counts["deleted_stale_scrape_snapshots"] == 0


@pytest.mark.asyncio
async def test_unpublished_event_resolution_does_not_leak_to_public_match_reads():
    old_snapshot_at = "2026-04-11T20:06:00.735723"
    new_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=old_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Old Home",
                away_team="Old Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.persist_event_resolution_batch(
        snapshot_id=old_snapshot_at,
        events=[
            ResolvedEventIn(
                id="evt-old",
                sport="basketball",
                start_time="2026-04-11T20:00:00+00:00",
                primary_match_id="shared-match",
                method="exact",
            )
        ],
        members=[
            ResolvedEventMemberIn(
                resolved_event_id="evt-old",
                match_id="shared-match",
                bookmaker_id="meridian",
            )
        ],
        review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=old_snapshot_at,
        snapshot_at=old_snapshot_at,
        opportunities=[],
        detected_at=old_snapshot_at,
    )

    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=new_snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="New Home",
                away_team="New Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T21:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.persist_event_resolution_batch(
        snapshot_id=new_snapshot_at,
        events=[
            ResolvedEventIn(
                id="evt-new",
                sport="basketball",
                start_time="2026-04-11T21:00:00+00:00",
                primary_match_id="shared-match",
                method="exact",
            )
        ],
        members=[
            ResolvedEventMemberIn(
                resolved_event_id="evt-new",
                match_id="shared-match",
                bookmaker_id="meridian",
            )
        ],
        review_cases=[],
    )

    matches = await odds_store.get_matches()
    current_event_members = await odds_store.get_eligible_resolved_event_members_for_odds(
        await odds_store.get_current_normalized_odds_for_matches(["shared-match"])
    )
    new_snapshot_event_members = (
        await odds_store.get_eligible_resolved_event_members_for_odds(
            await odds_store.get_current_normalized_odds_for_matches(
                ["shared-match"],
                snapshot_id=new_snapshot_at,
            ),
            snapshot_id=new_snapshot_at,
        )
    )

    assert [(match.home_team, match.resolved_event_id) for match in matches] == [
        ("Old Home", "evt-old")
    ]
    assert [member.resolved_event_id for member in current_event_members] == ["evt-old"]
    assert [member.resolved_event_id for member in new_snapshot_event_members] == [
        "evt-new"
    ]


@pytest.mark.asyncio
async def test_manual_event_resolution_overrides_snapshot_auto_member():
    snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="shared-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Home",
                away_team="Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T20:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )
    await odds_store.publish_opportunities(
        snapshot_id=snapshot_at,
        snapshot_at=snapshot_at,
        opportunities=[],
        detected_at=snapshot_at,
    )
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-manual",
            sport="basketball",
            start_time="2026-04-11T20:00:00+00:00",
            primary_match_id="shared-match",
            method="manual",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt-manual",
            match_id="shared-match",
            bookmaker_id="meridian",
        )
    )
    await odds_store.persist_event_resolution_batch(
        snapshot_id=snapshot_at,
        events=[
            ResolvedEventIn(
                id="evt-auto",
                sport="basketball",
                start_time="2026-04-11T20:00:00+00:00",
                primary_match_id="shared-match",
                method="exact",
            )
        ],
        members=[
            ResolvedEventMemberIn(
                resolved_event_id="evt-auto",
                match_id="shared-match",
                bookmaker_id="meridian",
            )
        ],
        review_cases=[],
    )

    odds = await odds_store.get_current_normalized_odds_for_matches(["shared-match"])
    members = await odds_store.get_eligible_resolved_event_members_for_odds(odds)
    match = (await odds_store.get_matches())[0]
    member = await odds_store.get_resolved_event_member(
        match_id="shared-match",
        bookmaker_id="meridian",
    )

    assert match.resolved_event_id == "evt-manual"
    assert [item.resolved_event_id for item in members] == ["evt-manual"]
    assert member is not None
    assert member.resolved_event_id == "evt-manual"


@pytest.mark.asyncio
async def test_merge_matches_preserves_same_offer_key_across_snapshots():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match("target-match", "euroleague", "Target Home", "Target Away")
    await odds_store.upsert_match("source-match", "euroleague", "Source Home", "Source Away")

    for snapshot_at in ("2026-04-11T20:06:00.735723", "2026-04-11T20:11:00.735723"):
        await odds_store.upsert_odds(
            NormalizedOdds(
                match_id="source-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                home_team="Source Home",
                away_team="Source Away",
                market_type="player_points",
                player_name="Saben Lee",
                threshold=13.5,
                over_odds=1.8,
                under_odds=2.0,
            ),
            scraped_at=snapshot_at,
        )
        await odds_store.upsert_outcome_offer(
            NormalizedOutcomeOffer(
                match_id="source-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Source Home",
                away_team="Source Away",
                market_type="game_winner",
                outcome_code="home",
                odds=1.9,
            ),
            scraped_at=snapshot_at,
        )

    await odds_store.merge_matches(
        target_match_id="target-match",
        source_match_ids=["source-match"],
    )

    db = await get_db()
    odds_rows = await db.execute_fetchall(
        "SELECT snapshot_id, match_id FROM odds ORDER BY snapshot_id"
    )
    offer_rows = await db.execute_fetchall(
        "SELECT snapshot_id, match_id FROM outcome_offers ORDER BY snapshot_id"
    )

    assert [(row["snapshot_id"], row["match_id"]) for row in odds_rows] == [
        ("2026-04-11T20:06:00.735723", "target-match"),
        ("2026-04-11T20:11:00.735723", "target-match"),
    ]
    assert [(row["snapshot_id"], row["match_id"]) for row in offer_rows] == [
        ("2026-04-11T20:06:00.735723", "target-match"),
        ("2026-04-11T20:11:00.735723", "target-match"),
    ]


@pytest.mark.asyncio
async def test_failed_opportunity_publish_preserves_previous_publish():
    snapshot_at = "2026-04-11T20:06:00.735723"
    next_snapshot_at = "2026-04-11T20:11:00.735723"
    await odds_store.upsert_league("premier-league", "Premier League", "football")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_bookmaker("balkanbet", "BalkanBet")
    await odds_store.upsert_match(
        "football-match",
        "premier-league",
        "Arsenal",
        "Chelsea",
        sport="football",
        start_time="2026-04-11T20:00:00+00:00",
    )
    old_opportunity = Opportunity(
        sport="football",
        match_id="football-match",
        opportunity_type="same_line_arbitrage",
        market_type="football_total_goals",
        line=2.5,
        profit_margin=0.02,
        middle_profit_margin=None,
        legs=[
            OpportunityLeg(
                bookmaker_id="maxbet",
                market_type="football_total_goals",
                outcome_code="under",
                line=2.5,
                odds=1.95,
            ),
            OpportunityLeg(
                bookmaker_id="balkanbet",
                market_type="football_total_goals",
                outcome_code="over",
                line=2.5,
                odds=2.10,
            ),
        ],
    )
    await odds_store.publish_opportunities(
        snapshot_id=snapshot_at,
        snapshot_at=snapshot_at,
        opportunities=[old_opportunity],
        detected_at=snapshot_at,
    )

    with pytest.raises(AttributeError):
        await odds_store.publish_opportunities(
            snapshot_id=next_snapshot_at,
            snapshot_at=next_snapshot_at,
            opportunities=[object()],
            detected_at=next_snapshot_at,
        )

    opportunities = await odds_store.get_opportunities(sport="football")
    status = await odds_store.get_system_status()
    assert len(opportunities) == 1
    assert opportunities[0].match_id == "football-match"
    assert status.total_opportunities == 1


@pytest.mark.asyncio
async def test_legacy_fallback_groups_recent_rows_before_snapshot_exists():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("old", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("recent-a", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")
    await odds_store.upsert_match("recent-b", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="old",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Bayern Munich",
            away_team="Maccabi Tel Aviv",
            market_type="player_points",
            player_name="Saben Lee",
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at="2026-04-10T13:39:04.516801",
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="recent-a",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Maccabi Tel Aviv",
            away_team="Hapoel Tel-Aviv",
            market_type="player_points",
            player_name="Tamir Blatt",
            threshold=6.5,
            over_odds=2.09,
            under_odds=1.66,
        ),
        scraped_at="2026-04-11T20:00:00.000001",
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="recent-b",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at="2026-04-11T20:05:00.000001",
    )
    db = await get_db()
    await db.execute("DELETE FROM scrape_snapshots")
    await db.commit()

    matches = await odds_store.get_matches(limit=10)
    status = await odds_store.get_system_status()

    assert {match.id for match in matches} == {"recent-a", "recent-b"}
    assert status.total_matches == 2
    assert status.total_odds == 2
    assert status.last_scrape_at == "2026-04-11T20:05:00.000001"
