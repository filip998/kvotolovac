"""Migrate legacy inline schema compatibility changes.

Revision ID: 0002_legacy_compatibility
Revises: 0001_current_schema
"""

from __future__ import annotations

import sqlite3

from alembic import op


revision = "0002_legacy_compatibility"
down_revision = "0001_current_schema"
branch_labels = None
depends_on = None


def _conn() -> sqlite3.Connection:
    connection = op.get_bind().connection
    return getattr(connection, "driver_connection", connection)


def _fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    connection = _conn()
    original_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(sql, params)
        return cursor.fetchall()
    finally:
        connection.row_factory = original_factory


def _execute(sql: str, params: tuple = ()) -> None:
    _conn().execute(sql, params)


def _columns(table_name: str) -> list[sqlite3.Row]:
    return _fetchall(f"PRAGMA table_info({table_name})")


def _column_names(table_name: str) -> set[str]:
    return {row["name"] for row in _columns(table_name)}


def _table_has_foreign_key(table_name: str, *, from_column: str, target_table: str) -> bool:
    rows = _fetchall(f"PRAGMA foreign_key_list({table_name})")
    return any(row["table"] == target_table and row["from"] == from_column for row in rows)


def _index_sql_contains(*, index_name: str, expected: str) -> bool | None:
    rows = _fetchall(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index_name,),
    )
    if not rows:
        return None
    sql = rows[0]["sql"] or ""
    return expected.lower() in str(sql).lower()


def _backfill_table_snapshot_metadata(table_name: str, column_name: str = "scraped_at") -> None:
    columns = _column_names(table_name)
    if "snapshot_id" not in columns or column_name not in columns:
        return
    _execute(
        f"""INSERT OR IGNORE INTO scrape_snapshots (
                id,
                scraped_at,
                completed_at,
                status
            )
            SELECT DISTINCT {column_name}, {column_name}, {column_name}, 'published'
            FROM {table_name}
            WHERE {column_name} IS NOT NULL"""
    )
    _execute(
        f"""UPDATE {table_name}
            SET snapshot_id = {column_name}
            WHERE snapshot_id IS NULL
              AND {column_name} IS NOT NULL"""
    )


def _assert_no_duplicate_rows(table_name: str, key_sql: str, index_name: str) -> None:
    rows = _fetchall(
        f"""SELECT {key_sql}, COUNT(*) AS row_count
            FROM {table_name}
            GROUP BY {key_sql}
            HAVING COUNT(*) > 1
            LIMIT 5"""
    )
    if not rows:
        return
    examples = [tuple(row) for row in rows]
    raise RuntimeError(
        f"Cannot create {index_name}; {table_name} contains duplicate rows for "
        f"the target unique key. Examples: {examples!r}"
    )


def _rebuild_matches() -> None:
    _execute(
        """
        CREATE TABLE matches__new (
            id TEXT PRIMARY KEY,
            league_id TEXT REFERENCES leagues(id),
            sport TEXT NOT NULL DEFAULT 'basketball',
            home_team_id INTEGER REFERENCES canonical_teams(id),
            away_team_id INTEGER REFERENCES canonical_teams(id),
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            start_time TIMESTAMP,
            status TEXT DEFAULT 'upcoming',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _execute(
        """
        INSERT INTO matches__new (
            id,
            league_id,
            sport,
            home_team_id,
            away_team_id,
            home_team,
            away_team,
            start_time,
            status,
            created_at
        )
        SELECT
            id,
            league_id,
            sport,
            CASE
                WHEN home_team_id IS NULL THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM canonical_teams
                    WHERE canonical_teams.id = matches.home_team_id
                ) THEN home_team_id
                ELSE NULL
            END,
            CASE
                WHEN away_team_id IS NULL THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM canonical_teams
                    WHERE canonical_teams.id = matches.away_team_id
                ) THEN away_team_id
                ELSE NULL
            END,
            home_team,
            away_team,
            start_time,
            status,
            created_at
        FROM matches
        """
    )
    _execute("DROP TABLE matches")
    _execute("ALTER TABLE matches__new RENAME TO matches")


def _rebuild_resolved_event_members_for_snapshots() -> None:
    _execute("DROP INDEX IF EXISTS idx_resolved_event_members_event")
    _execute("DROP INDEX IF EXISTS idx_resolved_event_members_match")
    _execute("DROP INDEX IF EXISTS idx_resolved_event_members_unique_snapshot")
    _execute(
        """
        CREATE TABLE resolved_event_members__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT,
            resolved_event_id TEXT NOT NULL REFERENCES resolved_events(id),
            match_id TEXT NOT NULL REFERENCES matches(id),
            bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id),
            orientation TEXT NOT NULL DEFAULT 'as_listed',
            confidence REAL,
            status TEXT NOT NULL DEFAULT 'active',
            source_url TEXT,
            source_league_id TEXT,
            source_league_name TEXT,
            source_home_team TEXT,
            source_away_team TEXT,
            source_start_time TIMESTAMP,
            evidence TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _execute(
        """
        INSERT INTO resolved_event_members__new (
            id,
            snapshot_id,
            resolved_event_id,
            match_id,
            bookmaker_id,
            orientation,
            confidence,
            status,
            source_url,
            source_league_id,
            source_league_name,
            source_home_team,
            source_away_team,
            source_start_time,
            evidence,
            metadata,
            created_at,
            updated_at
        )
        SELECT
            id,
            snapshot_id,
            resolved_event_id,
            match_id,
            bookmaker_id,
            orientation,
            confidence,
            status,
            source_url,
            source_league_id,
            source_league_name,
            source_home_team,
            source_away_team,
            source_start_time,
            evidence,
            metadata,
            created_at,
            updated_at
        FROM resolved_event_members
        ORDER BY id ASC
        """
    )
    _execute("DROP TABLE resolved_event_members")
    _execute("ALTER TABLE resolved_event_members__new RENAME TO resolved_event_members")


def _rebuild_match_bookmaker_sources_for_snapshots() -> None:
    existing = _column_names("match_bookmaker_sources")
    snapshot_expr = "snapshot_id" if "snapshot_id" in existing else "NULL"
    _execute("DROP INDEX IF EXISTS idx_match_bookmaker_sources_unique_snapshot")
    _execute("DROP INDEX IF EXISTS idx_match_bookmaker_sources_lookup")
    _execute(
        """
        CREATE TABLE match_bookmaker_sources__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT,
            match_id TEXT NOT NULL REFERENCES matches(id),
            bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id),
            source_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _execute(
        f"""
        INSERT INTO match_bookmaker_sources__new (
            id,
            snapshot_id,
            match_id,
            bookmaker_id,
            source_url,
            created_at,
            updated_at
        )
        SELECT
            id,
            {snapshot_expr},
            match_id,
            bookmaker_id,
            source_url,
            created_at,
            updated_at
        FROM match_bookmaker_sources
        """
    )
    _execute("DROP TABLE match_bookmaker_sources")
    _execute("ALTER TABLE match_bookmaker_sources__new RENAME TO match_bookmaker_sources")


def _rebuild_odds_for_snapshots() -> None:
    _execute("DROP INDEX IF EXISTS idx_odds_unique_snapshot_line")
    _execute("DROP INDEX IF EXISTS idx_odds_snapshot")
    _execute(
        """
        CREATE TABLE odds__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT,
            match_id TEXT REFERENCES matches(id),
            bookmaker_id TEXT REFERENCES bookmakers(id),
            market_type TEXT NOT NULL,
            player_name TEXT,
            threshold REAL NOT NULL,
            over_odds REAL,
            under_odds REAL,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _execute(
        """
        INSERT OR REPLACE INTO odds__new (
            id,
            snapshot_id,
            match_id,
            bookmaker_id,
            market_type,
            player_name,
            threshold,
            over_odds,
            under_odds,
            scraped_at
        )
        SELECT
            id,
            COALESCE(snapshot_id, scraped_at),
            match_id,
            bookmaker_id,
            market_type,
            player_name,
            threshold,
            over_odds,
            under_odds,
            scraped_at
        FROM odds
        ORDER BY id ASC
        """
    )
    _execute("DROP TABLE odds")
    _execute("ALTER TABLE odds__new RENAME TO odds")


def _rebuild_opportunities() -> None:
    _execute(
        """
        CREATE TABLE opportunities__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publish_id TEXT,
            sport TEXT NOT NULL,
            match_id TEXT REFERENCES matches(id),
            resolved_event_id TEXT REFERENCES resolved_events(id),
            opportunity_type TEXT NOT NULL,
            market_type TEXT NOT NULL,
            subject_type TEXT,
            subject_key TEXT,
            subject_name TEXT,
            line REAL,
            profit_margin REAL,
            middle_profit_margin REAL,
            market_keys TEXT NOT NULL DEFAULT '[]',
            legs TEXT NOT NULL DEFAULT '[]',
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        )
        """
    )
    _execute(
        """
        INSERT INTO opportunities__new (
            id,
            publish_id,
            sport,
            match_id,
            resolved_event_id,
            opportunity_type,
            market_type,
            subject_type,
            subject_key,
            subject_name,
            line,
            profit_margin,
            middle_profit_margin,
            market_keys,
            legs,
            detected_at,
            is_active
        )
        SELECT
            id,
            publish_id,
            sport,
            match_id,
            CASE
                WHEN resolved_event_id IS NULL THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM resolved_events
                    WHERE resolved_events.id = opportunities.resolved_event_id
                ) THEN resolved_event_id
                ELSE NULL
            END,
            opportunity_type,
            market_type,
            subject_type,
            subject_key,
            subject_name,
            line,
            profit_margin,
            middle_profit_margin,
            market_keys,
            legs,
            detected_at,
            is_active
        FROM opportunities
        """
    )
    _execute("DROP TABLE opportunities")
    _execute("ALTER TABLE opportunities__new RENAME TO opportunities")


def _rebuild_team_review_cases() -> None:
    _execute(
        """
        CREATE TABLE team_review_cases__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT,
            bookmaker_id TEXT REFERENCES bookmakers(id),
            raw_league_id TEXT NOT NULL,
            normalized_raw_league_id TEXT NOT NULL,
            sport TEXT NOT NULL DEFAULT 'basketball',
            scope_league_id TEXT,
            raw_team_name TEXT NOT NULL,
            normalized_raw_team_name TEXT NOT NULL,
            suggested_team_id INTEGER REFERENCES canonical_teams(id),
            suggested_team_name TEXT,
            start_time TIMESTAMP,
            review_kind TEXT NOT NULL DEFAULT 'alias_suggestion',
            reason_code TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'medium',
            similarity_score REAL,
            candidate_teams TEXT NOT NULL DEFAULT '[]',
            matched_counterpart_team TEXT,
            canonical_home_team TEXT,
            canonical_away_team TEXT,
            evidence TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP,
            declined_at TIMESTAMP
        )
        """
    )
    _execute(
        """
        INSERT INTO team_review_cases__new (
            id,
            snapshot_id,
            bookmaker_id,
            raw_league_id,
            normalized_raw_league_id,
            sport,
            scope_league_id,
            raw_team_name,
            normalized_raw_team_name,
            suggested_team_id,
            suggested_team_name,
            start_time,
            review_kind,
            reason_code,
            confidence,
            similarity_score,
            candidate_teams,
            matched_counterpart_team,
            canonical_home_team,
            canonical_away_team,
            evidence,
            status,
            scraped_at,
            approved_at,
            declined_at
        )
        SELECT
            id,
            COALESCE(snapshot_id, scraped_at),
            bookmaker_id,
            raw_league_id,
            normalized_raw_league_id,
            sport,
            scope_league_id,
            raw_team_name,
            normalized_raw_team_name,
            CASE
                WHEN suggested_team_id IS NULL THEN NULL
                WHEN EXISTS (
                    SELECT 1
                    FROM canonical_teams
                    WHERE canonical_teams.id = team_review_cases.suggested_team_id
                ) THEN suggested_team_id
                ELSE NULL
            END,
            suggested_team_name,
            start_time,
            review_kind,
            reason_code,
            confidence,
            similarity_score,
            candidate_teams,
            matched_counterpart_team,
            canonical_home_team,
            canonical_away_team,
            evidence,
            status,
            scraped_at,
            approved_at,
            declined_at
        FROM team_review_cases
        """
    )
    _execute("DROP TABLE team_review_cases")
    _execute("ALTER TABLE team_review_cases__new RENAME TO team_review_cases")


def _backfill_snapshot_metadata() -> None:
    for table_name, column_name in (
        ("odds", "scraped_at"),
        ("odds_history", "scraped_at"),
        ("outcome_offers", "scraped_at"),
        ("unresolved_odds", "scraped_at"),
        ("team_review_cases", "scraped_at"),
    ):
        _backfill_table_snapshot_metadata(table_name, column_name)

    _execute(
        """INSERT OR IGNORE INTO opportunity_publishes (
               id,
               snapshot_id,
               detected_at,
               status,
               opportunity_count
           )
           SELECT
               detected_at,
               detected_at,
               detected_at,
               'published',
               COUNT(*)
           FROM opportunities
           WHERE detected_at IS NOT NULL
           GROUP BY detected_at"""
    )
    _execute(
        """UPDATE opportunities
           SET publish_id = detected_at
           WHERE publish_id IS NULL
             AND detected_at IS NOT NULL
             AND is_active = TRUE"""
    )
    _execute(
        """UPDATE scrape_state
           SET current_snapshot_id = COALESCE(current_snapshot_id, current_snapshot_at)
           WHERE id = 1
             AND current_snapshot_at IS NOT NULL"""
    )
    _execute(
        """UPDATE scrape_state
           SET current_opportunity_publish_id = COALESCE(
                   current_opportunity_publish_id,
                   (
                       SELECT publish_id
                       FROM opportunities
                       WHERE is_active = TRUE
                         AND publish_id IS NOT NULL
                       GROUP BY publish_id
                       ORDER BY MAX(detected_at) DESC
                       LIMIT 1
                   )
               )
           WHERE id = 1
             AND current_opportunity_publish_id IS NULL"""
    )
    _execute(
        """INSERT OR IGNORE INTO snapshot_matches (
               snapshot_id,
               match_id,
               league_id,
               sport,
               home_team_id,
               away_team_id,
               home_team,
               away_team,
               start_time,
               status
           )
           SELECT DISTINCT
               COALESCE(o.snapshot_id, o.scraped_at),
               m.id,
               m.league_id,
               m.sport,
               m.home_team_id,
               m.away_team_id,
               m.home_team,
               m.away_team,
               m.start_time,
               m.status
           FROM odds o
           JOIN matches m ON m.id = o.match_id
           WHERE COALESCE(o.snapshot_id, o.scraped_at) IS NOT NULL"""
    )
    _execute(
        """INSERT OR IGNORE INTO snapshot_matches (
               snapshot_id,
               match_id,
               league_id,
               sport,
               home_team_id,
               away_team_id,
               home_team,
               away_team,
               start_time,
               status
           )
           SELECT DISTINCT
               COALESCE(oo.snapshot_id, oo.scraped_at),
               m.id,
               m.league_id,
               m.sport,
               m.home_team_id,
               m.away_team_id,
               m.home_team,
               m.away_team,
               m.start_time,
               m.status
           FROM outcome_offers oo
           JOIN matches m ON m.id = oo.match_id
           WHERE COALESCE(oo.snapshot_id, oo.scraped_at) IS NOT NULL"""
    )
    _execute(
        """INSERT OR IGNORE INTO match_bookmaker_sources (
               snapshot_id,
               match_id,
               bookmaker_id,
               source_url,
               created_at,
               updated_at
           )
           SELECT DISTINCT
               scoped_sources.snapshot_id,
               legacy_sources.match_id,
               legacy_sources.bookmaker_id,
               legacy_sources.source_url,
               legacy_sources.created_at,
               legacy_sources.updated_at
           FROM match_bookmaker_sources legacy_sources
           JOIN (
               SELECT DISTINCT
                   COALESCE(snapshot_id, scraped_at) AS snapshot_id,
                   match_id,
                   bookmaker_id
               FROM odds
               WHERE COALESCE(snapshot_id, scraped_at) IS NOT NULL
               UNION
               SELECT DISTINCT
                   COALESCE(snapshot_id, scraped_at) AS snapshot_id,
                   match_id,
                   bookmaker_id
               FROM outcome_offers
               WHERE COALESCE(snapshot_id, scraped_at) IS NOT NULL
           ) scoped_sources
             ON scoped_sources.match_id = legacy_sources.match_id
            AND scoped_sources.bookmaker_id = legacy_sources.bookmaker_id
           WHERE legacy_sources.snapshot_id IS NULL
             AND legacy_sources.source_url IS NOT NULL"""
    )


def upgrade() -> None:
    _execute(
        """CREATE TABLE IF NOT EXISTS runtime_scrape_settings (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               applied_config TEXT NOT NULL,
               pending_config TEXT,
               applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               pending_at TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    _execute(
        """CREATE TABLE IF NOT EXISTS scrape_snapshots (
               id TEXT PRIMARY KEY,
               scraped_at TIMESTAMP NOT NULL UNIQUE,
               started_at TIMESTAMP,
               completed_at TIMESTAMP,
               status TEXT NOT NULL DEFAULT 'persisted',
               matches_count INTEGER NOT NULL DEFAULT 0,
               odds_count INTEGER NOT NULL DEFAULT 0,
               outcome_offers_count INTEGER NOT NULL DEFAULT 0,
               unresolved_odds_count INTEGER NOT NULL DEFAULT 0,
               team_review_cases_count INTEGER NOT NULL DEFAULT 0,
               error TEXT,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    _execute(
        """CREATE TABLE IF NOT EXISTS opportunity_publishes (
               id TEXT PRIMARY KEY,
               snapshot_id TEXT,
               detected_at TIMESTAMP NOT NULL,
               status TEXT NOT NULL DEFAULT 'published',
               opportunity_count INTEGER NOT NULL DEFAULT 0,
               error TEXT,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )

    match_columns = _columns("matches")
    existing_matches = {row["name"] for row in match_columns}
    if match_columns and "sport" not in existing_matches:
        _execute("ALTER TABLE matches ADD COLUMN sport TEXT NOT NULL DEFAULT 'basketball'")
        existing_matches.add("sport")
    if match_columns and "home_team_id" not in existing_matches:
        _execute("ALTER TABLE matches ADD COLUMN home_team_id INTEGER")
        existing_matches.add("home_team_id")
    if match_columns and "away_team_id" not in existing_matches:
        _execute("ALTER TABLE matches ADD COLUMN away_team_id INTEGER")
        existing_matches.add("away_team_id")
    if match_columns and (
        not _table_has_foreign_key(
            "matches",
            from_column="home_team_id",
            target_table="canonical_teams",
        )
        or not _table_has_foreign_key(
            "matches",
            from_column="away_team_id",
            target_table="canonical_teams",
        )
    ):
        _rebuild_matches()

    _execute(
        """CREATE TABLE IF NOT EXISTS match_bookmaker_sources (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               snapshot_id TEXT,
               match_id TEXT NOT NULL REFERENCES matches(id),
               bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id),
               source_url TEXT,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    source_columns = _columns("match_bookmaker_sources")
    existing_sources = {row["name"] for row in source_columns}
    source_indexes = _fetchall("PRAGMA index_list(match_bookmaker_sources)")
    if source_columns and (
        "snapshot_id" not in existing_sources
        or any(str(row["name"]).startswith("sqlite_autoindex_match_bookmaker_sources") for row in source_indexes)
    ):
        _rebuild_match_bookmaker_sources_for_snapshots()
    _execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_match_bookmaker_sources_unique_snapshot
           ON match_bookmaker_sources (
               COALESCE(snapshot_id, ''),
               match_id,
               bookmaker_id
           )"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_match_bookmaker_sources_lookup
           ON match_bookmaker_sources (match_id, bookmaker_id, snapshot_id)"""
    )

    _execute(
        """CREATE TABLE IF NOT EXISTS snapshot_matches (
               snapshot_id TEXT NOT NULL,
               match_id TEXT NOT NULL REFERENCES matches(id),
               league_id TEXT REFERENCES leagues(id),
               sport TEXT NOT NULL DEFAULT 'basketball',
               home_team_id INTEGER REFERENCES canonical_teams(id),
               away_team_id INTEGER REFERENCES canonical_teams(id),
               home_team TEXT NOT NULL,
               away_team TEXT NOT NULL,
               start_time TIMESTAMP,
               status TEXT DEFAULT 'upcoming',
               PRIMARY KEY (snapshot_id, match_id)
           )"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_snapshot_matches_match
           ON snapshot_matches (match_id, snapshot_id)"""
    )

    resolved_member_columns = _columns("resolved_event_members")
    existing_resolved_members = {row["name"] for row in resolved_member_columns}
    if resolved_member_columns and "snapshot_id" not in existing_resolved_members:
        _execute("ALTER TABLE resolved_event_members ADD COLUMN snapshot_id TEXT")
        existing_resolved_members.add("snapshot_id")
    resolved_member_indexes = _fetchall("PRAGMA index_list(resolved_event_members)")
    if resolved_member_columns and any(
        str(row["name"]).startswith("sqlite_autoindex_resolved_event_members")
        for row in resolved_member_indexes
    ):
        _rebuild_resolved_event_members_for_snapshots()
    _execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_resolved_event_members_unique_snapshot
           ON resolved_event_members (
               COALESCE(snapshot_id, ''),
               match_id,
               bookmaker_id
           )"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_resolved_event_members_event
           ON resolved_event_members (resolved_event_id, status)"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_resolved_event_members_match
           ON resolved_event_members (match_id, bookmaker_id, snapshot_id)"""
    )

    unresolved_columns = _columns("unresolved_odds")
    existing_unresolved = {row["name"] for row in unresolved_columns}
    if unresolved_columns and "snapshot_id" not in existing_unresolved:
        _execute("ALTER TABLE unresolved_odds ADD COLUMN snapshot_id TEXT")
    if unresolved_columns and "sport" not in existing_unresolved:
        _execute("ALTER TABLE unresolved_odds ADD COLUMN sport TEXT NOT NULL DEFAULT 'basketball'")

    _execute("DROP TABLE IF EXISTS discrepancies")

    odds_columns = _columns("odds")
    existing_odds = {row["name"] for row in odds_columns}
    if odds_columns and "snapshot_id" not in existing_odds:
        _execute("ALTER TABLE odds ADD COLUMN snapshot_id TEXT")
        existing_odds.add("snapshot_id")
    odds_indexes = _fetchall("PRAGMA index_list(odds)")
    if odds_columns and any(str(row["name"]).startswith("sqlite_autoindex_odds") for row in odds_indexes):
        _rebuild_odds_for_snapshots()
    _backfill_table_snapshot_metadata("odds")
    _assert_no_duplicate_rows(
        "odds",
        "COALESCE(snapshot_id, ''), match_id, bookmaker_id, market_type, "
        "COALESCE(player_name, ''), threshold",
        "idx_odds_unique_snapshot_line",
    )
    _execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_odds_unique_snapshot_line
           ON odds (
               COALESCE(snapshot_id, ''),
               match_id,
               bookmaker_id,
               market_type,
               COALESCE(player_name, ''),
               threshold
           )"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_odds_snapshot
           ON odds (snapshot_id, scraped_at, match_id, bookmaker_id)"""
    )

    odds_history_columns = _columns("odds_history")
    existing_odds_history = {row["name"] for row in odds_history_columns}
    if odds_history_columns and "snapshot_id" not in existing_odds_history:
        _execute("ALTER TABLE odds_history ADD COLUMN snapshot_id TEXT")

    _execute(
        """CREATE TABLE IF NOT EXISTS outcome_offers (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               snapshot_id TEXT,
               match_id TEXT REFERENCES matches(id),
               bookmaker_id TEXT REFERENCES bookmakers(id),
               market_type TEXT NOT NULL,
               outcome_code TEXT NOT NULL,
               line REAL,
               odds REAL NOT NULL,
               raw_label TEXT,
               scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    outcome_offer_columns = _columns("outcome_offers")
    existing_outcome_offers = {row["name"] for row in outcome_offer_columns}
    if outcome_offer_columns and "snapshot_id" not in existing_outcome_offers:
        _execute("ALTER TABLE outcome_offers ADD COLUMN snapshot_id TEXT")
    _backfill_table_snapshot_metadata("outcome_offers")
    _assert_no_duplicate_rows(
        "outcome_offers",
        "COALESCE(snapshot_id, ''), match_id, bookmaker_id, market_type, "
        "outcome_code, COALESCE(line, -999999.0)",
        "idx_outcome_offers_unique_line",
    )
    if _index_sql_contains(index_name="idx_outcome_offers_unique_line", expected="snapshot_id") is False:
        _execute("DROP INDEX IF EXISTS idx_outcome_offers_unique_line")
    _execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_outcome_offers_unique_line
           ON outcome_offers (
               COALESCE(snapshot_id, ''),
               match_id,
               bookmaker_id,
               market_type,
               outcome_code,
               COALESCE(line, -999999.0)
           )"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_outcome_offers_snapshot
           ON outcome_offers (snapshot_id, scraped_at, match_id, bookmaker_id)"""
    )

    _execute(
        """CREATE TABLE IF NOT EXISTS opportunities (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               publish_id TEXT,
               sport TEXT NOT NULL,
               match_id TEXT REFERENCES matches(id),
               resolved_event_id TEXT REFERENCES resolved_events(id),
               opportunity_type TEXT NOT NULL,
               market_type TEXT NOT NULL,
               subject_type TEXT,
               subject_key TEXT,
               subject_name TEXT,
               line REAL,
               profit_margin REAL,
               middle_profit_margin REAL,
               market_keys TEXT NOT NULL DEFAULT '[]',
               legs TEXT NOT NULL DEFAULT '[]',
               detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               is_active BOOLEAN DEFAULT TRUE
           )"""
    )
    opportunity_columns = _columns("opportunities")
    existing_opportunities = {row["name"] for row in opportunity_columns}
    if opportunity_columns and "publish_id" not in existing_opportunities:
        _execute("ALTER TABLE opportunities ADD COLUMN publish_id TEXT")
        existing_opportunities.add("publish_id")
    if opportunity_columns and "middle_profit_margin" not in existing_opportunities:
        _execute("ALTER TABLE opportunities ADD COLUMN middle_profit_margin REAL")
    if opportunity_columns and "resolved_event_id" not in existing_opportunities:
        _execute("ALTER TABLE opportunities ADD COLUMN resolved_event_id TEXT REFERENCES resolved_events(id)")
    if opportunity_columns and "subject_type" not in existing_opportunities:
        _execute("ALTER TABLE opportunities ADD COLUMN subject_type TEXT")
    if opportunity_columns and "subject_key" not in existing_opportunities:
        _execute("ALTER TABLE opportunities ADD COLUMN subject_key TEXT")
    if opportunity_columns and "subject_name" not in existing_opportunities:
        _execute("ALTER TABLE opportunities ADD COLUMN subject_name TEXT")
    if opportunity_columns and "market_keys" not in existing_opportunities:
        _execute("ALTER TABLE opportunities ADD COLUMN market_keys TEXT NOT NULL DEFAULT '[]'")
    if opportunity_columns and not _table_has_foreign_key(
        "opportunities",
        from_column="resolved_event_id",
        target_table="resolved_events",
    ):
        _rebuild_opportunities()
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_opportunities_active_sport
           ON opportunities (is_active, sport, detected_at)"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_opportunities_resolved_event_active
           ON opportunities (resolved_event_id, is_active)"""
    )
    _execute(
        """CREATE INDEX IF NOT EXISTS idx_opportunities_publish
           ON opportunities (publish_id, sport, detected_at)"""
    )

    team_review_columns = _columns("team_review_cases")
    existing_team_review = {row["name"] for row in team_review_columns}
    if team_review_columns and "snapshot_id" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN snapshot_id TEXT")
        existing_team_review.add("snapshot_id")
    if team_review_columns and "sport" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN sport TEXT NOT NULL DEFAULT 'basketball'")
    if team_review_columns and "similarity_score" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN similarity_score REAL")
    if team_review_columns and "suggested_team_id" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN suggested_team_id INTEGER")
    if team_review_columns and "review_kind" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN review_kind TEXT NOT NULL DEFAULT 'alias_suggestion'")
    if team_review_columns and "candidate_teams" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN candidate_teams TEXT NOT NULL DEFAULT '[]'")
    if team_review_columns and "matched_counterpart_team" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN matched_counterpart_team TEXT")
    if team_review_columns and "canonical_home_team" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN canonical_home_team TEXT")
    if team_review_columns and "canonical_away_team" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN canonical_away_team TEXT")
    if team_review_columns and "declined_at" not in existing_team_review:
        _execute("ALTER TABLE team_review_cases ADD COLUMN declined_at TIMESTAMP")
    if team_review_columns:
        suggested_team_name_column = next(
            (row for row in team_review_columns if row["name"] == "suggested_team_name"),
            None,
        )
        if (
            suggested_team_name_column is not None
            and int(suggested_team_name_column["notnull"]) == 1
        ) or not _table_has_foreign_key(
            "team_review_cases",
            from_column="suggested_team_id",
            target_table="canonical_teams",
        ):
            _rebuild_team_review_cases()

    scrape_state_columns = _columns("scrape_state")
    existing_scrape_state = {row["name"] for row in scrape_state_columns}
    if scrape_state_columns and "current_snapshot_id" not in existing_scrape_state:
        _execute("ALTER TABLE scrape_state ADD COLUMN current_snapshot_id TEXT")
    if scrape_state_columns and "current_opportunity_publish_id" not in existing_scrape_state:
        _execute("ALTER TABLE scrape_state ADD COLUMN current_opportunity_publish_id TEXT")

    _backfill_snapshot_metadata()

    team_merge_history_columns = _columns("team_merge_history")
    existing_team_merge_history = {row["name"] for row in team_merge_history_columns}
    if team_merge_history_columns and "alias_snapshot" not in existing_team_merge_history:
        _execute("ALTER TABLE team_merge_history ADD COLUMN alias_snapshot TEXT")
    if team_merge_history_columns and "review_case_snapshot" not in existing_team_merge_history:
        _execute("ALTER TABLE team_merge_history ADD COLUMN review_case_snapshot TEXT")
    if team_merge_history_columns and "unmerged_at" not in existing_team_merge_history:
        _execute("ALTER TABLE team_merge_history ADD COLUMN unmerged_at TIMESTAMP")


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")
