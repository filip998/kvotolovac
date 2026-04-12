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


async def _ensure_schema_compatibility(conn: aiosqlite.Connection) -> None:
    columns = await conn.execute_fetchall("PRAGMA table_info(discrepancies)")
    existing = {row[1] for row in columns}
    if "middle_profit_margin" not in existing:
        await conn.execute("ALTER TABLE discrepancies ADD COLUMN middle_profit_margin REAL")


async def get_db() -> aiosqlite.Connection:
    global _db_connection
    if _db_connection is None:
        raise RuntimeError("Database not initialised – call init_db() first")
    return _db_connection


async def init_db(db_path: str = ":memory:") -> aiosqlite.Connection:
    global _db_connection
    _db_connection = await aiosqlite.connect(db_path)
    _db_connection.row_factory = aiosqlite.Row
    await _db_connection.executescript(_SCHEMA)
    await _ensure_schema_compatibility(_db_connection)
    await _db_connection.commit()
    return _db_connection


async def close_db() -> None:
    global _db_connection
    if _db_connection is not None:
        await _db_connection.close()
        _db_connection = None
