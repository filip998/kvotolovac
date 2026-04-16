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

CREATE TABLE IF NOT EXISTS odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT REFERENCES matches(id),
    bookmaker_id TEXT REFERENCES bookmakers(id),
    market_type TEXT NOT NULL,
    player_name TEXT,
    threshold REAL NOT NULL,
    over_odds REAL,
    under_odds REAL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, bookmaker_id, market_type, player_name, threshold)
);

CREATE TABLE IF NOT EXISTS odds_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT,
    bookmaker_id TEXT,
    market_type TEXT,
    player_name TEXT,
    threshold REAL,
    over_odds REAL,
    under_odds REAL,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS unresolved_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    merged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discrepancies (
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
    middle_profit_margin REAL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
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
    current_snapshot_at TIMESTAMP,
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


async def _rebuild_team_review_cases(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE team_review_cases__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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


async def _ensure_schema_compatibility(conn: aiosqlite.Connection) -> None:
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

    unresolved_columns = await conn.execute_fetchall("PRAGMA table_info(unresolved_odds)")
    existing_unresolved = {row[1] for row in unresolved_columns}
    if unresolved_columns and "sport" not in existing_unresolved:
        await conn.execute(
            "ALTER TABLE unresolved_odds ADD COLUMN sport TEXT NOT NULL DEFAULT 'basketball'"
        )

    columns = await conn.execute_fetchall("PRAGMA table_info(discrepancies)")
    existing = {row[1] for row in columns}
    if "middle_profit_margin" not in existing:
        await conn.execute("ALTER TABLE discrepancies ADD COLUMN middle_profit_margin REAL")

    team_review_columns = await conn.execute_fetchall("PRAGMA table_info(team_review_cases)")
    existing_team_review = {row[1] for row in team_review_columns}
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


async def get_db() -> aiosqlite.Connection:
    global _db_connection
    if _db_connection is None:
        raise RuntimeError("Database not initialised – call init_db() first")
    return _db_connection


async def init_db(db_path: str = ":memory:") -> aiosqlite.Connection:
    global _db_connection
    _db_connection = await aiosqlite.connect(db_path)
    _db_connection.row_factory = aiosqlite.Row
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
