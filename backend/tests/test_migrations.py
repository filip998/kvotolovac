from __future__ import annotations

import sqlite3

import pytest

from app.database import close_db, get_db, init_db
from app.migrations.runner import (
    DatabaseMigrationRequired,
    current_revision,
    head_revision,
    migrate_database_to_head,
    upgrade_database,
)


@pytest.mark.asyncio
async def test_upgrade_database_creates_current_schema(tmp_path):
    await close_db()
    db_path = tmp_path / "fresh-migrated.db"

    upgrade_database(str(db_path))

    assert current_revision(str(db_path)) == head_revision(str(db_path))
    with sqlite3.connect(db_path) as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        opportunity_fks = conn.execute(
            "PRAGMA foreign_key_list(opportunities)"
        ).fetchall()
        merge_history_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(team_merge_history)").fetchall()
        }

    assert {
        "alembic_version",
        "matches",
        "odds",
        "outcome_offers",
        "opportunities",
        "scrape_snapshots",
        "canonical_teams",
        "team_aliases",
        "telegram_notification_profiles",
        "telegram_notification_deliveries",
        "telegram_command_state",
        "telegram_command_executions",
        "telegram_command_message_deliveries",
    }.issubset(table_names)
    assert ("resolved_event_id", "resolved_events") in {
        (row[3], row[2]) for row in opportunity_fks
    }
    assert {
        "merge_source",
        "merge_reason",
        "identity_policy",
        "identity_decision",
    }.issubset(merge_history_columns)

    await init_db(str(db_path))
    db = await get_db()
    pragma_row = await (await db.execute("PRAGMA foreign_keys")).fetchone()
    assert pragma_row is not None
    assert pragma_row[0] == 1


@pytest.mark.asyncio
async def test_init_db_rejects_unmigrated_database(tmp_path):
    await close_db()
    db_path = tmp_path / "unmigrated.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY)")

    with pytest.raises(DatabaseMigrationRequired, match="alembic upgrade head"):
        await init_db(str(db_path))


def test_migrate_database_to_head_upgrades_stale_database(tmp_path):
    db_path = tmp_path / "stale.db"
    upgrade_database(str(db_path), "0005_middle_ev_opportunity_fields")
    assert current_revision(str(db_path)) == "0005_middle_ev_opportunity_fields"

    previous_revision, migrated_revision = migrate_database_to_head(str(db_path))

    assert previous_revision == "0005_middle_ev_opportunity_fields"
    assert migrated_revision == head_revision(str(db_path))
    assert current_revision(str(db_path)) == migrated_revision
    with sqlite3.connect(db_path) as conn:
        profile_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(telegram_notification_profiles)"
            ).fetchall()
        }
        index_names = {
            row[1]
            for row in conn.execute(
                "PRAGMA index_list(telegram_notification_profiles)"
            ).fetchall()
        }
        merge_history_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(team_merge_history)").fetchall()
        }
    assert "min_middle_ev_percent" in profile_columns
    assert "command_permission_preset" in profile_columns
    assert "allowed_commands" in profile_columns
    assert "ux_telegram_profiles_command_chat" in index_names
    assert "merge_source" in merge_history_columns
    assert "identity_decision" in merge_history_columns


def test_telegram_command_migration_does_not_grant_admin_by_label(tmp_path):
    db_path = tmp_path / "telegram-command-admin.db"
    upgrade_database(str(db_path), "0006_telegram_middle_ev_threshold")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO telegram_notification_profiles (
                   label,
                   chat_id,
                   enabled,
                   min_gap,
                   min_roi_percent,
                   min_middle_ev_percent,
                   bookmaker_ids
               ) VALUES (
                   'FilipTanic', '12345', 1, 0, 0, 0, '[]'
               )"""
        )

    migrate_database_to_head(str(db_path))

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """SELECT command_permission_preset, allowed_commands
               FROM telegram_notification_profiles
               WHERE label = 'FilipTanic'"""
        ).fetchone()

    assert row == ("none", "[]")


@pytest.mark.asyncio
async def test_legacy_duplicate_offer_rows_are_snapshot_scoped_before_unique_indexes(
    tmp_path,
):
    await close_db()
    db_path = tmp_path / "legacy-duplicate-offers.db"
    first_snapshot = "2026-04-11T20:00:00"
    second_snapshot = "2026-04-11T20:05:00"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE bookmakers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE leagues (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sport TEXT NOT NULL DEFAULT 'basketball'
            );
            CREATE TABLE matches (
                id TEXT PRIMARY KEY,
                league_id TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                start_time TEXT,
                status TEXT NOT NULL DEFAULT 'upcoming',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE odds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT,
                bookmaker_id TEXT,
                market_type TEXT NOT NULL,
                player_name TEXT,
                threshold REAL NOT NULL,
                over_odds REAL,
                under_odds REAL,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE outcome_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT,
                bookmaker_id TEXT,
                market_type TEXT NOT NULL,
                outcome_code TEXT NOT NULL,
                line REAL,
                odds REAL NOT NULL,
                raw_label TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute("INSERT INTO bookmakers (id, name) VALUES ('meridian', 'Meridian')")
        conn.execute(
            """INSERT INTO leagues (id, name, sport)
               VALUES ('euroleague', 'Euroleague', 'basketball')"""
        )
        conn.execute(
            """INSERT INTO matches (
                   id, league_id, home_team, away_team, start_time
               ) VALUES (
                   'match-1', 'euroleague', 'Home', 'Away', '2026-04-11T20:00:00'
               )"""
        )
        for scraped_at in (first_snapshot, second_snapshot):
            conn.execute(
                """INSERT INTO odds (
                       match_id, bookmaker_id, market_type, player_name, threshold,
                       over_odds, under_odds, scraped_at
                   ) VALUES (
                       'match-1', 'meridian', 'player_points', 'Player One',
                       16.5, 1.9, 1.9, ?
                   )""",
                (scraped_at,),
            )
            conn.execute(
                """INSERT INTO outcome_offers (
                       match_id, bookmaker_id, market_type, outcome_code, line,
                       odds, scraped_at
                   ) VALUES (
                       'match-1', 'meridian', 'game_total', 'over',
                       154.5, 1.9, ?
                   )""",
                (scraped_at,),
            )

    upgrade_database(str(db_path))

    with sqlite3.connect(db_path) as conn:
        odds_snapshot_ids = [
            row[0] for row in conn.execute("SELECT snapshot_id FROM odds ORDER BY id")
        ]
        offer_snapshot_ids = [
            row[0]
            for row in conn.execute("SELECT snapshot_id FROM outcome_offers ORDER BY id")
        ]
        index_names = {
            row[1] for row in conn.execute("PRAGMA index_list(odds)").fetchall()
        } | {
            row[1]
            for row in conn.execute("PRAGMA index_list(outcome_offers)").fetchall()
        }

    assert odds_snapshot_ids == [first_snapshot, second_snapshot]
    assert offer_snapshot_ids == [first_snapshot, second_snapshot]
    assert {
        "idx_odds_unique_snapshot_line",
        "idx_outcome_offers_unique_line",
    }.issubset(index_names)
