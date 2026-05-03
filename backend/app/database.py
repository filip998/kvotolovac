from __future__ import annotations

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmakers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    website_url TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leagues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sport TEXT NOT NULL,
    country TEXT,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS matches (
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
);

CREATE TABLE IF NOT EXISTS match_bookmaker_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT,
    match_id TEXT NOT NULL REFERENCES matches(id),
    bookmaker_id TEXT NOT NULL REFERENCES bookmakers(id),
    source_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS snapshot_matches (
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
);

CREATE INDEX IF NOT EXISTS idx_snapshot_matches_match
ON snapshot_matches (match_id, snapshot_id);

CREATE TABLE IF NOT EXISTS resolved_events (
    id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    start_time TIMESTAMP NOT NULL,
    primary_match_id TEXT NOT NULL REFERENCES matches(id),
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL,
    method TEXT NOT NULL DEFAULT 'manual',
    display_home_team TEXT,
    display_away_team TEXT,
    display_league_name TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resolved_events_slot
ON resolved_events (sport, start_time, status);

CREATE INDEX IF NOT EXISTS idx_resolved_events_primary_match
ON resolved_events (primary_match_id);

CREATE TABLE IF NOT EXISTS resolved_event_members (
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
);

CREATE INDEX IF NOT EXISTS idx_resolved_event_members_event
ON resolved_event_members (resolved_event_id, status);

CREATE INDEX IF NOT EXISTS idx_resolved_event_members_match
ON resolved_event_members (match_id, bookmaker_id);

CREATE TABLE IF NOT EXISTS event_review_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    sport TEXT NOT NULL DEFAULT 'basketball',
    start_time TIMESTAMP NOT NULL,
    primary_match_id TEXT REFERENCES matches(id),
    candidate_resolved_event_id TEXT REFERENCES resolved_events(id),
    resolved_event_id TEXT REFERENCES resolved_events(id),
    candidate_match_ids TEXT NOT NULL DEFAULT '[]',
    reason_code TEXT NOT NULL,
    confidence REAL,
    method TEXT NOT NULL DEFAULT 'auto_candidate',
    source_bookmaker_ids TEXT NOT NULL DEFAULT '[]',
    source_league_labels TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    accepted_at TIMESTAMP,
    declined_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_event_review_cases_status
ON event_review_cases (status, sport, start_time);

CREATE INDEX IF NOT EXISTS idx_event_review_cases_candidate_event
ON event_review_cases (candidate_resolved_event_id);

CREATE INDEX IF NOT EXISTS idx_event_review_cases_resolved_event
ON event_review_cases (resolved_event_id);

CREATE TABLE IF NOT EXISTS scrape_snapshots (
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
);

CREATE TABLE IF NOT EXISTS odds (
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
);

CREATE TABLE IF NOT EXISTS odds_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT,
    match_id TEXT,
    bookmaker_id TEXT,
    market_type TEXT,
    player_name TEXT,
    threshold REAL,
    over_odds REAL,
    under_odds REAL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outcome_offers (
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
);

CREATE TABLE IF NOT EXISTS opportunity_publishes (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT,
    detected_at TIMESTAMP NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    opportunity_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS opportunities (
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
);

CREATE INDEX IF NOT EXISTS idx_opportunities_active_sport
ON opportunities (is_active, sport, detected_at);

CREATE TABLE IF NOT EXISTS unresolved_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT,
    bookmaker_id TEXT REFERENCES bookmakers(id),
    raw_league_id TEXT NOT NULL,
    league_id TEXT NOT NULL,
    sport TEXT NOT NULL DEFAULT 'basketball',
    market_type TEXT NOT NULL,
    player_name TEXT,
    raw_team_name TEXT NOT NULL,
    normalized_team_name TEXT NOT NULL,
    start_time TIMESTAMP,
    threshold REAL NOT NULL,
    over_odds REAL,
    under_odds REAL,
    reason_code TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    candidate_matchups TEXT NOT NULL DEFAULT '[]',
    available_matchups_same_slot TEXT NOT NULL DEFAULT '[]',
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS team_review_cases (
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
);

CREATE TABLE IF NOT EXISTS canonical_teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sport TEXT NOT NULL,
    display_name TEXT NOT NULL,
    normalized_display_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    merged_into_team_id INTEGER REFERENCES canonical_teams(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (sport, normalized_display_name)
);

CREATE INDEX IF NOT EXISTS idx_canonical_teams_sport_active
ON canonical_teams (sport, is_active);

CREATE TABLE IF NOT EXISTS team_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_team_id INTEGER NOT NULL REFERENCES canonical_teams(id),
    sport TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    bookmaker_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual_review',
    legacy_competition_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (sport, normalized_alias, bookmaker_id)
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_lookup
ON team_aliases (sport, normalized_alias, bookmaker_id);

CREATE TABLE IF NOT EXISTS team_merge_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_team_id INTEGER NOT NULL REFERENCES canonical_teams(id),
    target_team_id INTEGER NOT NULL REFERENCES canonical_teams(id),
    alias_snapshot TEXT,
    review_case_snapshot TEXT,
    unmerged_at TIMESTAMP,
    merged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT,
    data TEXT,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_snapshot_id TEXT,
    current_snapshot_at TIMESTAMP,
    current_opportunity_publish_id TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_db_connection: aiosqlite.Connection | None = None


async def _table_has_foreign_key(
    conn: aiosqlite.Connection,
    *,
    table_name: str,
    from_column: str,
    target_table: str,
) -> bool:
    rows = await conn.execute_fetchall(f"PRAGMA foreign_key_list({table_name})")
    return any(row[2] == target_table and row[3] == from_column for row in rows)


async def _index_sql_contains(
    conn: aiosqlite.Connection,
    *,
    index_name: str,
    expected: str,
) -> bool | None:
    rows = await conn.execute_fetchall(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index_name,),
    )
    if not rows:
        return None
    sql = rows[0][0] or ""
    return expected.lower() in str(sql).lower()


async def _rebuild_matches(conn: aiosqlite.Connection) -> None:
    await conn.execute(
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
    await conn.execute(
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
    await conn.execute("DROP TABLE matches")
    await conn.execute("ALTER TABLE matches__new RENAME TO matches")


async def _rebuild_resolved_event_members_for_snapshots(
    conn: aiosqlite.Connection,
) -> None:
    await conn.execute("DROP INDEX IF EXISTS idx_resolved_event_members_event")
    await conn.execute("DROP INDEX IF EXISTS idx_resolved_event_members_match")
    await conn.execute("DROP INDEX IF EXISTS idx_resolved_event_members_unique_snapshot")
    await conn.execute(
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
    await conn.execute(
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
    await conn.execute("DROP TABLE resolved_event_members")
    await conn.execute(
        "ALTER TABLE resolved_event_members__new RENAME TO resolved_event_members"
    )


async def _rebuild_match_bookmaker_sources_for_snapshots(
    conn: aiosqlite.Connection,
) -> None:
    columns = await conn.execute_fetchall("PRAGMA table_info(match_bookmaker_sources)")
    existing = {row[1] for row in columns}
    snapshot_expr = "snapshot_id" if "snapshot_id" in existing else "NULL"
    await conn.execute("DROP INDEX IF EXISTS idx_match_bookmaker_sources_unique_snapshot")
    await conn.execute("DROP INDEX IF EXISTS idx_match_bookmaker_sources_lookup")
    await conn.execute(
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
    await conn.execute(
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
    await conn.execute("DROP TABLE match_bookmaker_sources")
    await conn.execute(
        "ALTER TABLE match_bookmaker_sources__new RENAME TO match_bookmaker_sources"
    )


async def _rebuild_odds_for_snapshots(conn: aiosqlite.Connection) -> None:
    await conn.execute("DROP INDEX IF EXISTS idx_odds_unique_snapshot_line")
    await conn.execute("DROP INDEX IF EXISTS idx_odds_snapshot")
    await conn.execute(
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
    await conn.execute(
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
    await conn.execute("DROP TABLE odds")
    await conn.execute("ALTER TABLE odds__new RENAME TO odds")


async def _rebuild_opportunities(conn: aiosqlite.Connection) -> None:
    await conn.execute(
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
    await conn.execute(
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
    await conn.execute("DROP TABLE opportunities")
    await conn.execute("ALTER TABLE opportunities__new RENAME TO opportunities")


async def _rebuild_team_review_cases(conn: aiosqlite.Connection) -> None:
    await conn.execute(
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
    await conn.execute(
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
    await conn.execute("DROP TABLE team_review_cases")
    await conn.execute("ALTER TABLE team_review_cases__new RENAME TO team_review_cases")


async def _backfill_snapshot_metadata(conn: aiosqlite.Connection) -> None:
    snapshot_sources = (
        ("odds", "scraped_at"),
        ("odds_history", "scraped_at"),
        ("outcome_offers", "scraped_at"),
        ("unresolved_odds", "scraped_at"),
        ("team_review_cases", "scraped_at"),
    )
    for table_name, column_name in snapshot_sources:
        await conn.execute(
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
        await conn.execute(
            f"""UPDATE {table_name}
                SET snapshot_id = {column_name}
                WHERE snapshot_id IS NULL
                  AND {column_name} IS NOT NULL"""
        )

    await conn.execute(
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
    await conn.execute(
        """UPDATE opportunities
           SET publish_id = detected_at
           WHERE publish_id IS NULL
             AND detected_at IS NOT NULL
             AND is_active = TRUE"""
    )
    await conn.execute(
        """UPDATE scrape_state
           SET current_snapshot_id = COALESCE(current_snapshot_id, current_snapshot_at)
           WHERE id = 1
             AND current_snapshot_at IS NOT NULL"""
    )
    await conn.execute(
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
           WHERE id = 1"""
    )
    await conn.execute(
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
    await conn.execute(
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


async def _ensure_schema_compatibility(conn: aiosqlite.Connection) -> None:
    await conn.execute(
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
    await conn.execute(
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

    match_columns = await conn.execute_fetchall("PRAGMA table_info(matches)")
    existing_matches = {row[1] for row in match_columns}
    if match_columns and "sport" not in existing_matches:
        await conn.execute(
            "ALTER TABLE matches ADD COLUMN sport TEXT NOT NULL DEFAULT 'basketball'"
        )
    if match_columns and "home_team_id" not in existing_matches:
        await conn.execute("ALTER TABLE matches ADD COLUMN home_team_id INTEGER")
    if match_columns and "away_team_id" not in existing_matches:
        await conn.execute("ALTER TABLE matches ADD COLUMN away_team_id INTEGER")
    if match_columns and (
        not await _table_has_foreign_key(
            conn,
            table_name="matches",
            from_column="home_team_id",
            target_table="canonical_teams",
        )
        or not await _table_has_foreign_key(
            conn,
            table_name="matches",
            from_column="away_team_id",
            target_table="canonical_teams",
        )
    ):
        await _rebuild_matches(conn)

    await conn.execute(
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
    source_columns = await conn.execute_fetchall("PRAGMA table_info(match_bookmaker_sources)")
    existing_sources = {row[1] for row in source_columns}
    source_indexes = await conn.execute_fetchall("PRAGMA index_list(match_bookmaker_sources)")
    if source_columns and (
        "snapshot_id" not in existing_sources
        or any(
            str(row[1]).startswith("sqlite_autoindex_match_bookmaker_sources")
            for row in source_indexes
        )
    ):
        await _rebuild_match_bookmaker_sources_for_snapshots(conn)
    await conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_match_bookmaker_sources_unique_snapshot
           ON match_bookmaker_sources (
               COALESCE(snapshot_id, ''),
               match_id,
               bookmaker_id
           )"""
    )
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_match_bookmaker_sources_lookup
           ON match_bookmaker_sources (match_id, bookmaker_id, snapshot_id)"""
    )

    await conn.execute(
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
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_snapshot_matches_match
           ON snapshot_matches (match_id, snapshot_id)"""
    )

    resolved_member_columns = await conn.execute_fetchall(
        "PRAGMA table_info(resolved_event_members)"
    )
    existing_resolved_members = {row[1] for row in resolved_member_columns}
    if resolved_member_columns and "snapshot_id" not in existing_resolved_members:
        await conn.execute("ALTER TABLE resolved_event_members ADD COLUMN snapshot_id TEXT")
        existing_resolved_members.add("snapshot_id")
    resolved_member_indexes = await conn.execute_fetchall(
        "PRAGMA index_list(resolved_event_members)"
    )
    if resolved_member_columns and any(
        str(row[1]).startswith("sqlite_autoindex_resolved_event_members")
        for row in resolved_member_indexes
    ):
        await _rebuild_resolved_event_members_for_snapshots(conn)
    await conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_resolved_event_members_unique_snapshot
           ON resolved_event_members (
               COALESCE(snapshot_id, ''),
               match_id,
               bookmaker_id
           )"""
    )
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_resolved_event_members_event
           ON resolved_event_members (resolved_event_id, status)"""
    )
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_resolved_event_members_match
           ON resolved_event_members (match_id, bookmaker_id, snapshot_id)"""
    )

    unresolved_columns = await conn.execute_fetchall("PRAGMA table_info(unresolved_odds)")
    existing_unresolved = {row[1] for row in unresolved_columns}
    if unresolved_columns and "snapshot_id" not in existing_unresolved:
        await conn.execute("ALTER TABLE unresolved_odds ADD COLUMN snapshot_id TEXT")
    if unresolved_columns and "sport" not in existing_unresolved:
        await conn.execute(
            "ALTER TABLE unresolved_odds ADD COLUMN sport TEXT NOT NULL DEFAULT 'basketball'"
        )

    await conn.execute("DROP TABLE IF EXISTS discrepancies")

    odds_columns = await conn.execute_fetchall("PRAGMA table_info(odds)")
    existing_odds = {row[1] for row in odds_columns}
    if odds_columns and "snapshot_id" not in existing_odds:
        await conn.execute("ALTER TABLE odds ADD COLUMN snapshot_id TEXT")
        existing_odds.add("snapshot_id")
    odds_indexes = await conn.execute_fetchall("PRAGMA index_list(odds)")
    if odds_columns and any(str(row[1]).startswith("sqlite_autoindex_odds") for row in odds_indexes):
        await _rebuild_odds_for_snapshots(conn)
    await conn.execute(
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
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_odds_snapshot
           ON odds (snapshot_id, scraped_at, match_id, bookmaker_id)"""
    )

    odds_history_columns = await conn.execute_fetchall("PRAGMA table_info(odds_history)")
    existing_odds_history = {row[1] for row in odds_history_columns}
    if odds_history_columns and "snapshot_id" not in existing_odds_history:
        await conn.execute("ALTER TABLE odds_history ADD COLUMN snapshot_id TEXT")

    await conn.execute(
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
    outcome_offer_columns = await conn.execute_fetchall("PRAGMA table_info(outcome_offers)")
    existing_outcome_offers = {row[1] for row in outcome_offer_columns}
    if outcome_offer_columns and "snapshot_id" not in existing_outcome_offers:
        await conn.execute("ALTER TABLE outcome_offers ADD COLUMN snapshot_id TEXT")
    outcome_offer_unique_index_has_snapshot = await _index_sql_contains(
        conn,
        index_name="idx_outcome_offers_unique_line",
        expected="snapshot_id",
    )
    if outcome_offer_unique_index_has_snapshot is False:
        await conn.execute("DROP INDEX IF EXISTS idx_outcome_offers_unique_line")
    await conn.execute(
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
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_outcome_offers_snapshot
           ON outcome_offers (snapshot_id, scraped_at, match_id, bookmaker_id)"""
    )
    await conn.execute(
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
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_opportunities_active_sport
           ON opportunities (is_active, sport, detected_at)"""
    )
    opportunity_columns = await conn.execute_fetchall("PRAGMA table_info(opportunities)")
    existing_opportunities = {row[1] for row in opportunity_columns}
    if opportunity_columns and "publish_id" not in existing_opportunities:
        await conn.execute("ALTER TABLE opportunities ADD COLUMN publish_id TEXT")
        existing_opportunities.add("publish_id")
    if opportunity_columns and "middle_profit_margin" not in existing_opportunities:
        await conn.execute("ALTER TABLE opportunities ADD COLUMN middle_profit_margin REAL")
    if opportunity_columns and "resolved_event_id" not in existing_opportunities:
        await conn.execute(
            "ALTER TABLE opportunities ADD COLUMN resolved_event_id TEXT REFERENCES resolved_events(id)"
        )
    if opportunity_columns and "subject_type" not in existing_opportunities:
        await conn.execute("ALTER TABLE opportunities ADD COLUMN subject_type TEXT")
    if opportunity_columns and "subject_key" not in existing_opportunities:
        await conn.execute("ALTER TABLE opportunities ADD COLUMN subject_key TEXT")
    if opportunity_columns and "subject_name" not in existing_opportunities:
        await conn.execute("ALTER TABLE opportunities ADD COLUMN subject_name TEXT")
    if opportunity_columns and "market_keys" not in existing_opportunities:
        await conn.execute(
            "ALTER TABLE opportunities ADD COLUMN market_keys TEXT NOT NULL DEFAULT '[]'"
        )
    if opportunity_columns and not await _table_has_foreign_key(
        conn,
        table_name="opportunities",
        from_column="resolved_event_id",
        target_table="resolved_events",
    ):
        await _rebuild_opportunities(conn)
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_opportunities_active_sport
           ON opportunities (is_active, sport, detected_at)"""
    )
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_opportunities_resolved_event_active
           ON opportunities (resolved_event_id, is_active)"""
    )
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_opportunities_publish
           ON opportunities (publish_id, sport, detected_at)"""
    )

    team_review_columns = await conn.execute_fetchall("PRAGMA table_info(team_review_cases)")
    existing_team_review = {row[1] for row in team_review_columns}
    if team_review_columns and "snapshot_id" not in existing_team_review:
        await conn.execute("ALTER TABLE team_review_cases ADD COLUMN snapshot_id TEXT")
        existing_team_review.add("snapshot_id")
    if team_review_columns and "sport" not in existing_team_review:
        await conn.execute(
            "ALTER TABLE team_review_cases ADD COLUMN sport TEXT NOT NULL DEFAULT 'basketball'"
        )
    if team_review_columns and "similarity_score" not in existing_team_review:
        await conn.execute("ALTER TABLE team_review_cases ADD COLUMN similarity_score REAL")
    if team_review_columns and "suggested_team_id" not in existing_team_review:
        await conn.execute("ALTER TABLE team_review_cases ADD COLUMN suggested_team_id INTEGER")
    if team_review_columns and "review_kind" not in existing_team_review:
        await conn.execute(
            "ALTER TABLE team_review_cases ADD COLUMN review_kind TEXT NOT NULL DEFAULT 'alias_suggestion'"
        )
    if team_review_columns and "candidate_teams" not in existing_team_review:
        await conn.execute(
            "ALTER TABLE team_review_cases ADD COLUMN candidate_teams TEXT NOT NULL DEFAULT '[]'"
        )
    if team_review_columns and "matched_counterpart_team" not in existing_team_review:
        await conn.execute(
            "ALTER TABLE team_review_cases ADD COLUMN matched_counterpart_team TEXT"
        )
    if team_review_columns and "canonical_home_team" not in existing_team_review:
        await conn.execute(
            "ALTER TABLE team_review_cases ADD COLUMN canonical_home_team TEXT"
        )
    if team_review_columns and "canonical_away_team" not in existing_team_review:
        await conn.execute(
            "ALTER TABLE team_review_cases ADD COLUMN canonical_away_team TEXT"
        )
    if team_review_columns and "declined_at" not in existing_team_review:
        await conn.execute("ALTER TABLE team_review_cases ADD COLUMN declined_at TIMESTAMP")
    if team_review_columns:
        suggested_team_name_column = next(
            (row for row in team_review_columns if row[1] == "suggested_team_name"),
            None,
        )
        if (
            suggested_team_name_column is not None
            and int(suggested_team_name_column[3]) == 1
        ) or not await _table_has_foreign_key(
            conn,
            table_name="team_review_cases",
            from_column="suggested_team_id",
            target_table="canonical_teams",
        ):
            await _rebuild_team_review_cases(conn)

    scrape_state_columns = await conn.execute_fetchall("PRAGMA table_info(scrape_state)")
    existing_scrape_state = {row[1] for row in scrape_state_columns}
    if scrape_state_columns and "current_snapshot_id" not in existing_scrape_state:
        await conn.execute("ALTER TABLE scrape_state ADD COLUMN current_snapshot_id TEXT")
    if scrape_state_columns and "current_opportunity_publish_id" not in existing_scrape_state:
        await conn.execute(
            "ALTER TABLE scrape_state ADD COLUMN current_opportunity_publish_id TEXT"
        )

    await _backfill_snapshot_metadata(conn)

    team_merge_history_columns = await conn.execute_fetchall(
        "PRAGMA table_info(team_merge_history)"
    )
    existing_team_merge_history = {row[1] for row in team_merge_history_columns}
    if team_merge_history_columns and "alias_snapshot" not in existing_team_merge_history:
        await conn.execute("ALTER TABLE team_merge_history ADD COLUMN alias_snapshot TEXT")
    if team_merge_history_columns and "review_case_snapshot" not in existing_team_merge_history:
        await conn.execute("ALTER TABLE team_merge_history ADD COLUMN review_case_snapshot TEXT")
    if team_merge_history_columns and "unmerged_at" not in existing_team_merge_history:
        await conn.execute("ALTER TABLE team_merge_history ADD COLUMN unmerged_at TIMESTAMP")


async def get_db() -> aiosqlite.Connection:
    global _db_connection
    if _db_connection is None:
        raise RuntimeError("Database not initialised – call init_db() first")
    return _db_connection


async def init_db(db_path: str = ":memory:") -> aiosqlite.Connection:
    global _db_connection
    _db_connection = await aiosqlite.connect(db_path)
    _db_connection.row_factory = aiosqlite.Row
    await _db_connection.execute("PRAGMA busy_timeout = 5000")
    await _db_connection.execute("PRAGMA foreign_keys = OFF")
    await _db_connection.executescript(_SCHEMA)
    await _ensure_schema_compatibility(_db_connection)
    await _db_connection.commit()
    await _db_connection.execute("PRAGMA foreign_keys = ON")
    return _db_connection


async def close_db() -> None:
    global _db_connection
    if _db_connection is not None:
        await _db_connection.close()
        _db_connection = None
