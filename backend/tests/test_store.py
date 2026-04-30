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
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
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

    current = await odds_store.get_odds_for_match("m1")
    assert len(current) == 1
    assert current[0].threshold == 16.5
    assert current[0].source_url == "https://example.com/m1"

    history = await odds_store.get_odds_history_for_match("m1")
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_upsert_odds_preserves_existing_source_url_when_new_snapshot_has_none():
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
            source_url="https://example.com/m1",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at="2026-04-11T20:06:00.735723",
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
        scraped_at="2026-04-11T20:11:00.735723",
    )
    await odds_store.set_current_snapshot("2026-04-11T20:11:00.735723")

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

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.upsert_odds(fresh_odds, scraped_at="2026-04-11T20:06:00.735723")

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

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.upsert_odds(fresh_odds, scraped_at="2026-04-11T20:06:00.735723")

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
async def test_insert_and_get_discrepancy():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    disc_id = await odds_store.insert_discrepancy(
        match_id="m1",
        market_type="player_points",
        player_name="Iffe Lundberg",
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=16.5,
        threshold_b=18.5,
        odds_a=1.85,
        odds_b=2.00,
        gap=2.0,
        profit_margin=0.04,
        middle_profit_margin=0.96,
    )
    assert disc_id > 0

    discs = await odds_store.get_discrepancies()
    assert len(discs) == 1
    assert discs[0].gap == 2.0
    assert discs[0].middle_profit_margin == 0.96


@pytest.mark.asyncio
async def test_get_discrepancies_filters_by_search_before_pagination():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("m2", "euroleague", "Monaco", "Barcelona")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    await odds_store.insert_discrepancy(
        match_id="m2",
        market_type="player_points",
        player_name="Mike James",
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=16.5,
        threshold_b=18.5,
        odds_a=1.85,
        odds_b=2.00,
        gap=2.0,
        profit_margin=0.20,
    )
    await odds_store.insert_discrepancy(
        match_id="m1",
        market_type="player_points",
        player_name="Kendrick Nunn",
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=14.5,
        threshold_b=16.5,
        odds_a=1.85,
        odds_b=2.00,
        gap=2.0,
        profit_margin=0.01,
    )

    discs = await odds_store.get_discrepancies(search="nunn", limit=1)

    assert len(discs) == 1
    assert discs[0].player_name == "Kendrick Nunn"


@pytest.mark.asyncio
async def test_discrepancy_leg_match_id_migration_preserves_foreign_keys(
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
    rows = await db.execute_fetchall("PRAGMA foreign_key_list(discrepancies)")
    foreign_keys = {(row[3], row[2]) for row in rows}

    assert ("bookmaker_a_match_id", "matches") in foreign_keys
    assert ("bookmaker_b_match_id", "matches") in foreign_keys


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
               INSERT INTO discrepancies (resolved_event_id, market_type)
               VALUES ('missing-event', 'player_points');
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
    discrepancy_fks = await db.execute_fetchall("PRAGMA foreign_key_list(discrepancies)")
    opportunity_fks = await db.execute_fetchall("PRAGMA foreign_key_list(opportunities)")

    assert ("resolved_event_id", "resolved_events") in {
        (row[3], row[2]) for row in discrepancy_fks
    }
    assert ("resolved_event_id", "resolved_events") in {
        (row[3], row[2]) for row in opportunity_fks
    }
    discrepancy_stale_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS count FROM discrepancies WHERE resolved_event_id IS NULL"
    )
    opportunity_stale_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS count FROM opportunities WHERE resolved_event_id IS NULL"
    )
    assert discrepancy_stale_rows[0]["count"] == 1
    assert opportunity_stale_rows[0]["count"] == 1

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            "INSERT INTO discrepancies (resolved_event_id, market_type) VALUES (?, ?)",
            ("still-missing-event", "player_points"),
        )
        await db.commit()
    await db.rollback()

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
async def test_get_discrepancies_filters_by_involved_bookmakers():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("m2", "euroleague", "Monaco", "Barcelona")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")

    await odds_store.insert_discrepancy(
        match_id="m1",
        market_type="player_points",
        player_name="Iffe Lundberg",
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=16.5,
        threshold_b=18.5,
        odds_a=1.85,
        odds_b=2.00,
        gap=2.0,
        profit_margin=0.04,
    )
    await odds_store.insert_discrepancy(
        match_id="m2",
        market_type="player_points",
        player_name="Mike James",
        bookmaker_a_id="maxbet",
        bookmaker_b_id="mozzart",
        threshold_a=19.5,
        threshold_b=21.5,
        odds_a=1.9,
        odds_b=1.85,
        gap=2.0,
        profit_margin=0.03,
    )

    discs = await odds_store.get_discrepancies(bookmaker_ids=["meridian"])

    assert len(discs) == 1
    assert discs[0].bookmaker_b_id == "meridian"


@pytest.mark.asyncio
async def test_get_discrepancy_detail():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match_bookmaker_source(
        match_id="m1",
        bookmaker_id="mozzart",
        source_url="https://example.com/mozzart/m1",
    )
    await odds_store.upsert_match_bookmaker_source(
        match_id="m1",
        bookmaker_id="meridian",
        source_url="https://example.com/meridian/m1",
    )

    disc_id = await odds_store.insert_discrepancy(
        match_id="m1", market_type="player_points", player_name="Lundberg",
        bookmaker_a_id="mozzart", bookmaker_b_id="meridian",
        threshold_a=16.5, threshold_b=18.5,
        odds_a=1.85, odds_b=2.0, gap=2.0, profit_margin=0.04, middle_profit_margin=0.96,
    )
    detail = await odds_store.get_discrepancy(disc_id)
    assert detail is not None
    assert detail.bookmaker_a_name == "Mozzart"
    assert detail.bookmaker_a_source_url == "https://example.com/mozzart/m1"
    assert detail.bookmaker_b_source_url == "https://example.com/meridian/m1"
    assert detail.home_team == "Partizan"
    assert detail.middle_profit_margin == 0.96


@pytest.mark.asyncio
async def test_deactivate_discrepancies():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "A", "B")
    await odds_store.upsert_bookmaker("a", "A")
    await odds_store.upsert_bookmaker("b", "B")

    await odds_store.insert_discrepancy(
        "m1", "player_points", "P", "a", "b", 10, 12, 1.9, 2.0, 2.0, 0.03
    )
    await odds_store.deactivate_all_discrepancies()
    active = await odds_store.get_discrepancies(active_only=True)
    assert len(active) == 0


@pytest.mark.asyncio
async def test_notifications_crud():
    nid = await odds_store.insert_notification("discrepancy", "Test", "msg", {"gap": 2.0})
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
    await odds_store.insert_discrepancy(
        "stale",
        "player_points",
        "Saben Lee",
        "meridian",
        "mozzart",
        13.5,
        15.5,
        1.8,
        1.95,
        2.0,
        0.03,
    )
    await odds_store.deactivate_all_discrepancies()
    active_discrepancy_id = await odds_store.insert_discrepancy(
        "fresh",
        "player_points",
        "Tamir Blatt",
        "meridian",
        "mozzart",
        6.5,
        8.5,
        2.09,
        1.95,
        2.0,
        0.04,
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
        "discrepancy", "Old Alert", "body", {"gap": 2.0}
    )
    recent_notification_id = await odds_store.insert_notification(
        "discrepancy", "Recent Alert", "body", {"gap": 2.0}
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

    counts = await odds_store.cleanup_retained_data(current_snapshot_at)

    odds_rows = await db.execute_fetchall(
        "SELECT match_id, scraped_at FROM odds ORDER BY match_id"
    )
    unresolved_rows = await db.execute_fetchall(
        "SELECT raw_team_name, scraped_at FROM unresolved_odds ORDER BY id"
    )
    discrepancy_rows = await db.execute_fetchall(
        "SELECT id, is_active FROM discrepancies ORDER BY id"
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

    assert [(row["match_id"], row["scraped_at"]) for row in odds_rows] == [
        ("fresh", current_snapshot_at)
    ]
    assert [(row["raw_team_name"], row["scraped_at"]) for row in unresolved_rows] == [
        ("Maccabi Tel Aviv", current_snapshot_at)
    ]
    assert [(row["id"], row["is_active"]) for row in discrepancy_rows] == [
        (active_discrepancy_id, 1)
    ]
    assert [(row["match_id"], row["scraped_at"]) for row in history_rows] == [
        ("fresh", current_snapshot_at)
    ]
    assert [(row["id"], row["raw_team_name"]) for row in review_rows] == [
        (recent_case_id, "Recent Alias")
    ]
    assert notification_rows == []
    assert counts == {
        "deleted_stale_odds": 1,
        "deleted_stale_unresolved_odds": 1,
        "deleted_inactive_discrepancies": 1,
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
        "discrepancy", "Old Alert", "body", {"gap": 2.0}
    )
    recent_notification_id = await odds_store.insert_notification(
        "discrepancy", "Recent Alert", "body", {"gap": 2.0}
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
        "discrepancy", "Old Alert", "body", {"gap": 2.0}
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
        odds_store.insert_notification("discrepancy", "Concurrent Alert", "body", {"gap": 2.5})
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

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.upsert_odds(fresh_odds_a, scraped_at="2026-04-11T20:06:00.735723")
    await odds_store.upsert_odds(fresh_odds_b, scraped_at="2026-04-11T20:06:00.735723")

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

    matches = await odds_store.get_matches(limit=10)
    status = await odds_store.get_system_status()

    assert {match.id for match in matches} == {"recent-a", "recent-b"}
    assert status.total_matches == 2
    assert status.total_odds == 2
    assert status.last_scrape_at == "2026-04-11T20:05:00.000001"
