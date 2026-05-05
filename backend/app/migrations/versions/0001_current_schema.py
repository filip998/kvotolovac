"""Create current application schema.

Revision ID: 0001_current_schema
Revises:
"""

from __future__ import annotations

from alembic import op


revision = "0001_current_schema"
down_revision = None
branch_labels = None
depends_on = None


_SCHEMA = r"""
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

CREATE TABLE IF NOT EXISTS runtime_scrape_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    applied_config TEXT NOT NULL,
    pending_config TEXT,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pending_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_snapshot_id TEXT,
    current_snapshot_at TIMESTAMP,
    current_opportunity_publish_id TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _schema_statements() -> list[str]:
    return [statement.strip() for statement in _SCHEMA.split(";") if statement.strip()]


def upgrade() -> None:
    for statement in _schema_statements():
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("Downgrades are not supported for KvotoLovac migrations.")
