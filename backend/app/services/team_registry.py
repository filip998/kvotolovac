from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from ..config import settings
from .team_seed_data import SPORT_ALIAS_SEEDS
from .text_normalizer import normalize_identity_text

DEFAULT_SPORT = "basketball"
_GLOBAL_BOOKMAKER_ID = ""

_TEAM_REGISTRY_SCHEMA = """
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
"""

_bootstrap_db_path: str | None = None
_schema_db_path: str | None = None


class CircularAliasError(Exception):
    """Raised when saving an alias would create a cycle."""


@dataclass(frozen=True)
class TeamAliasResolution:
    team_id: int
    team_name: str
    source: str
    sport: str
    bookmaker_id: str = _GLOBAL_BOOKMAKER_ID


@dataclass(frozen=True)
class CanonicalTeamCandidate:
    team_id: int
    team_name: str
    score: float
    matched_alias: str | None = None


@dataclass(frozen=True)
class CanonicalTeamSummary:
    id: int
    sport: str
    display_name: str
    aliases: tuple[str, ...]
    alias_count: int
    merged_into_team_id: int | None = None


@dataclass(frozen=True)
class CanonicalTeamUnmergeResult:
    source_team_id: int
    target_team_id: int
    source_team_name: str


def _registry_path() -> Path:
    return Path(settings.team_registry_path)


def _normalize_bookmaker_key(value: str | None) -> str:
    normalized = normalize_identity_text(value)
    return normalized or _GLOBAL_BOOKMAKER_ID


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_TEAM_REGISTRY_SCHEMA)
    merge_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(team_merge_history)").fetchall()
    }
    if merge_columns and "alias_snapshot" not in merge_columns:
        conn.execute("ALTER TABLE team_merge_history ADD COLUMN alias_snapshot TEXT")
    if merge_columns and "review_case_snapshot" not in merge_columns:
        conn.execute("ALTER TABLE team_merge_history ADD COLUMN review_case_snapshot TEXT")
    if merge_columns and "unmerged_at" not in merge_columns:
        conn.execute("ALTER TABLE team_merge_history ADD COLUMN unmerged_at TIMESTAMP")


def _default_registry_payload() -> dict[str, Any]:
    return {
        "aliases": {},
        "bookmaker_aliases": {},
        "competition_aliases": {},
        "bookmaker_competition_aliases": {},
    }


def _read_registry_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_registry_payload()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return _default_registry_payload()
    return {
        "aliases": data.get("aliases", {}),
        "bookmaker_aliases": data.get("bookmaker_aliases", {}),
        "competition_aliases": data.get("competition_aliases", {}),
        "bookmaker_competition_aliases": data.get("bookmaker_competition_aliases", {}),
    }


def _query_team_by_id(conn: sqlite3.Connection, team_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, sport, display_name, normalized_display_name, merged_into_team_id
        FROM canonical_teams
        WHERE id = ? AND is_active = TRUE
        """,
        (team_id,),
    ).fetchone()


def _query_any_team_by_id(conn: sqlite3.Connection, team_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, sport, display_name, normalized_display_name, is_active, merged_into_team_id
        FROM canonical_teams
        WHERE id = ?
        """,
        (team_id,),
    ).fetchone()


def _query_any_team_by_display_name(
    conn: sqlite3.Connection,
    *,
    sport: str,
    normalized_display_name: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, sport, display_name, normalized_display_name, is_active, merged_into_team_id
        FROM canonical_teams
        WHERE sport = ? AND normalized_display_name = ?
        """,
        (sport, normalized_display_name),
    ).fetchone()


def _resolve_active_team_row(
    conn: sqlite3.Connection,
    team_row: sqlite3.Row,
) -> sqlite3.Row | None:
    current_row: sqlite3.Row | None = team_row
    visited_team_ids: set[int] = set()
    while current_row is not None:
        current_team_id = int(current_row["id"])
        if current_team_id in visited_team_ids:
            return None
        visited_team_ids.add(current_team_id)
        if bool(current_row["is_active"]):
            return current_row
        merged_into_team_id = current_row["merged_into_team_id"]
        if merged_into_team_id is None:
            return None
        current_row = _query_any_team_by_id(conn, int(merged_into_team_id))
    return None


def _query_team_by_display_name(
    conn: sqlite3.Connection,
    *,
    sport: str,
    normalized_display_name: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, sport, display_name, normalized_display_name, merged_into_team_id
        FROM canonical_teams
        WHERE sport = ? AND normalized_display_name = ? AND is_active = TRUE
        """,
        (sport, normalized_display_name),
    ).fetchone()


def _upsert_alias(
    conn: sqlite3.Connection,
    *,
    sport: str,
    alias: str,
    canonical_team_id: int,
    bookmaker_id: str,
    source: str,
    legacy_competition_id: str | None = None,
) -> None:
    normalized_alias = normalize_identity_text(alias)
    alias_text = alias.strip()
    if not normalized_alias or not alias_text:
        return
    conn.execute(
        """
        INSERT INTO team_aliases (
            canonical_team_id,
            sport,
            alias,
            normalized_alias,
            bookmaker_id,
            source,
            legacy_competition_id,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (sport, normalized_alias, bookmaker_id) DO UPDATE SET
            canonical_team_id = excluded.canonical_team_id,
            alias = excluded.alias,
            source = excluded.source,
            legacy_competition_id = excluded.legacy_competition_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            canonical_team_id,
            sport,
            alias_text,
            normalized_alias,
            bookmaker_id,
            source,
            legacy_competition_id,
        ),
    )


def _create_canonical_team(
    conn: sqlite3.Connection,
    *,
    sport: str,
    display_name: str,
    source: str,
) -> TeamAliasResolution:
    normalized_display_name = normalize_identity_text(display_name)
    existing = _query_any_team_by_display_name(
        conn, sport=sport, normalized_display_name=normalized_display_name
    )
    if existing is not None:
        active_team = _resolve_active_team_row(conn, existing)
        if active_team is None:
            raise RuntimeError(
                f"Canonical team '{display_name.strip()}' already exists for sport "
                f"'{sport}' but does not resolve to an active team"
            )
        return TeamAliasResolution(
            team_id=int(active_team["id"]),
            team_name=str(active_team["display_name"]),
            source="canonical",
            sport=sport,
            bookmaker_id=_GLOBAL_BOOKMAKER_ID,
        )

    cursor = conn.execute(
        """
        INSERT INTO canonical_teams (
            sport,
            display_name,
            normalized_display_name,
            is_active,
            updated_at
        ) VALUES (?, ?, ?, TRUE, CURRENT_TIMESTAMP)
        """,
        (sport, display_name.strip(), normalized_display_name),
    )
    team_id = int(cursor.lastrowid)
    _upsert_alias(
        conn,
        sport=sport,
        alias=display_name,
        canonical_team_id=team_id,
        bookmaker_id=_GLOBAL_BOOKMAKER_ID,
        source="canonical",
    )
    return TeamAliasResolution(
        team_id=team_id,
        team_name=display_name.strip(),
        source=source,
        sport=sport,
        bookmaker_id=_GLOBAL_BOOKMAKER_ID,
    )


def _find_resolution_by_exact_alias(
    conn: sqlite3.Connection,
    *,
    raw_key: str,
    sport: str,
    bookmaker_id: str,
) -> TeamAliasResolution | None:
    row = conn.execute(
        """
        SELECT
            ct.id AS team_id,
            ct.display_name AS team_name,
            ta.source AS source,
            ta.bookmaker_id AS bookmaker_id
        FROM team_aliases ta
        JOIN canonical_teams ct ON ct.id = ta.canonical_team_id
        WHERE ta.sport = ?
          AND ta.normalized_alias = ?
          AND ta.bookmaker_id IN (?, '')
          AND ct.is_active = TRUE
        ORDER BY
            CASE WHEN ta.bookmaker_id = ? THEN 0 ELSE 1 END,
            CASE WHEN ta.source = 'canonical' THEN 0 ELSE 1 END,
            ta.id ASC
        LIMIT 1
        """,
        (sport, raw_key, bookmaker_id, bookmaker_id),
    ).fetchone()
    if row is None:
        return None
    return TeamAliasResolution(
        team_id=int(row["team_id"]),
        team_name=str(row["team_name"]),
        source=str(row["source"]),
        sport=sport,
        bookmaker_id=str(row["bookmaker_id"]),
    )


def _resolve_existing_team(
    conn: sqlite3.Connection,
    *,
    team_name: str,
    sport: str,
    bookmaker_id: str,
) -> TeamAliasResolution | None:
    normalized_team_name = normalize_identity_text(team_name)
    if not normalized_team_name:
        return None
    direct = _query_team_by_display_name(
        conn, sport=sport, normalized_display_name=normalized_team_name
    )
    if direct is not None:
        return TeamAliasResolution(
            team_id=int(direct["id"]),
            team_name=str(direct["display_name"]),
            source="canonical",
            sport=sport,
            bookmaker_id=_GLOBAL_BOOKMAKER_ID,
        )
    return _find_resolution_by_exact_alias(
        conn, raw_key=normalized_team_name, sport=sport, bookmaker_id=bookmaker_id
    )


def _seed_aliases(conn: sqlite3.Connection) -> None:
    for sport, aliases in SPORT_ALIAS_SEEDS.items():
        for raw_alias, target_name in aliases.items():
            target_resolution = _create_canonical_team(
                conn,
                sport=sport,
                display_name=target_name,
                source="seed",
            )
            _upsert_alias(
                conn,
                sport=sport,
                alias=raw_alias,
                canonical_team_id=target_resolution.team_id,
                bookmaker_id=_GLOBAL_BOOKMAKER_ID,
                source="seed",
            )


def _import_legacy_team_registry(conn: sqlite3.Connection) -> None:
    payload = _read_registry_payload(_registry_path())
    if not any(payload.values()):
        return

    def save_imported_alias(
        *,
        raw_alias: str,
        target_name: str,
        bookmaker_id: str,
        source: str,
        legacy_competition_id: str | None = None,
    ) -> None:
        target_resolution = _resolve_existing_team(
            conn,
            team_name=target_name,
            sport=DEFAULT_SPORT,
            bookmaker_id=bookmaker_id,
        )
        if target_resolution is None:
            target_resolution = _create_canonical_team(
                conn,
                sport=DEFAULT_SPORT,
                display_name=target_name,
                source=source,
            )
        _upsert_alias(
            conn,
            sport=DEFAULT_SPORT,
            alias=raw_alias,
            canonical_team_id=target_resolution.team_id,
            bookmaker_id=bookmaker_id,
            source=source,
            legacy_competition_id=legacy_competition_id,
        )

    for raw_alias, target_name in payload["aliases"].items():
        if raw_alias and target_name:
            save_imported_alias(
                raw_alias=str(raw_alias),
                target_name=str(target_name),
                bookmaker_id=_GLOBAL_BOOKMAKER_ID,
                source="legacy_alias",
            )

    for bookmaker_id, alias_map in payload["bookmaker_aliases"].items():
        bookmaker_key = _normalize_bookmaker_key(bookmaker_id)
        if not isinstance(alias_map, dict):
            continue
        for raw_alias, target_name in alias_map.items():
            if raw_alias and target_name:
                save_imported_alias(
                    raw_alias=str(raw_alias),
                    target_name=str(target_name),
                    bookmaker_id=bookmaker_key,
                    source="legacy_bookmaker_alias",
                )

    # Competition-scoped aliases from the legacy matcher are intentionally not
    # imported. They were approved under league-aware semantics and cannot be
    # safely promoted into the new global sport namespace.


def _ensure_bootstrapped() -> None:
    global _bootstrap_db_path, _schema_db_path
    db_path = settings.db_path
    if _bootstrap_db_path == db_path and _schema_db_path == db_path:
        return
    with _connect() as conn:
        if _schema_db_path != db_path:
            _ensure_schema(conn)
            _schema_db_path = db_path
        if _bootstrap_db_path != db_path:
            _seed_aliases(conn)
            _import_legacy_team_registry(conn)
            conn.commit()
            _bootstrap_db_path = db_path


def clear_team_registry_cache(*, reset_bootstrap: bool = True) -> None:
    global _bootstrap_db_path, _schema_db_path
    if reset_bootstrap:
        _bootstrap_db_path = None
        _schema_db_path = None
    _get_canonical_team_cached.cache_clear()
    _resolve_team_alias_cached.cache_clear()
    _load_team_search_rows.cache_clear()
    _load_canonical_team_list_rows.cache_clear()


def resolve_team_alias(
    raw_team_name: str | None,
    *,
    bookmaker_id: str | None = None,
    competition_id: str | None = None,
    sport: str = DEFAULT_SPORT,
) -> TeamAliasResolution | None:
    del competition_id
    raw_key = normalize_identity_text(raw_team_name)
    if not raw_key:
        return None

    _ensure_bootstrapped()
    return _resolve_team_alias_cached(
        settings.db_path,
        raw_key,
        sport,
        _normalize_bookmaker_key(bookmaker_id),
    )


@lru_cache(maxsize=65536)
def _resolve_team_alias_cached(
    db_path: str,
    raw_key: str,
    sport: str,
    bookmaker_id: str,
) -> TeamAliasResolution | None:
    del db_path
    with _connect() as conn:
        return _find_resolution_by_exact_alias(
            conn,
            raw_key=raw_key,
            sport=sport,
            bookmaker_id=bookmaker_id,
        )


def remember_team_alias(
    *,
    bookmaker_id: str,
    raw_team_name: str,
    team_name: str,
    competition_id: str | None = None,
    sport: str = DEFAULT_SPORT,
    source: str = "manual_review",
) -> TeamAliasResolution:
    del competition_id
    raw_key = normalize_identity_text(raw_team_name)
    target_name = team_name.strip()
    bookmaker_key = _normalize_bookmaker_key(bookmaker_id)

    if not raw_key or not target_name:
        raise ValueError("Both raw_team_name and team_name are required")

    _ensure_bootstrapped()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target_resolution = _resolve_existing_team(
            conn,
            team_name=target_name,
            sport=sport,
            bookmaker_id=bookmaker_key,
        )
        if target_resolution is None:
            target_resolution = _create_canonical_team(
                conn,
                sport=sport,
                display_name=target_name,
                source="manual_create",
            )

        target_row = _query_team_by_id(conn, target_resolution.team_id)
        if target_row is None:
            raise RuntimeError("Target canonical team disappeared during alias save")
        target_key = str(target_row["normalized_display_name"])

        if raw_key == target_key and normalize_identity_text(target_name) != raw_key:
            raise CircularAliasError(
                f"Circular alias: '{team_name}' already resolves to "
                f"'{target_resolution.team_name}' which matches '{raw_team_name}'"
            )

        existing_resolution = _find_resolution_by_exact_alias(
            conn,
            raw_key=raw_key,
            sport=sport,
            bookmaker_id=bookmaker_key,
        )
        if existing_resolution is not None and existing_resolution.team_id != target_resolution.team_id:
            if source == "auto_review":
                existing_resolution = None
            else:
                raise CircularAliasError(
                    f"Alias '{raw_team_name}' already resolves to '{existing_resolution.team_name}'"
                )
        if existing_resolution is not None and source == "auto_review":
            conn.rollback()
            return existing_resolution

        _upsert_alias(
            conn,
            sport=sport,
            alias=raw_team_name,
            canonical_team_id=target_resolution.team_id,
            bookmaker_id=bookmaker_key,
            source=source,
        )
        conn.commit()

    clear_team_registry_cache(reset_bootstrap=False)
    resolution = resolve_team_alias(
        raw_team_name,
        bookmaker_id=bookmaker_id,
        sport=sport,
    )
    if resolution is None:
        raise RuntimeError("Saved team alias could not be reloaded")
    return resolution


def forget_team_alias(
    *,
    bookmaker_id: str,
    raw_team_name: str,
    sport: str = DEFAULT_SPORT,
    expected_source: str | None = None,
) -> bool:
    raw_key = normalize_identity_text(raw_team_name)
    bookmaker_key = _normalize_bookmaker_key(bookmaker_id)
    if not raw_key:
        return False

    _ensure_bootstrapped()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        query = """
            DELETE FROM team_aliases
            WHERE sport = ?
              AND normalized_alias = ?
              AND bookmaker_id = ?
        """
        params: list[object] = [sport, raw_key, bookmaker_key]
        if expected_source is not None:
            query += " AND source = ?"
            params.append(expected_source)
        cursor = conn.execute(query, params)
        deleted = cursor.rowcount > 0
        conn.commit()

    if deleted:
        clear_team_registry_cache(reset_bootstrap=False)
    return deleted


def create_canonical_team(
    *,
    display_name: str,
    sport: str = DEFAULT_SPORT,
) -> TeamAliasResolution:
    _ensure_bootstrapped()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        resolution = _create_canonical_team(
            conn,
            sport=sport,
            display_name=display_name,
            source="manual_create",
        )
        conn.commit()
    clear_team_registry_cache(reset_bootstrap=False)
    return resolution


@lru_cache(maxsize=16)
def _load_team_search_rows(
    db_path: str,
    sport: str,
) -> tuple[tuple[int, str, tuple[str, ...]], ...]:
    del db_path
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                ct.id AS team_id,
                ct.display_name AS team_name,
                ta.alias AS alias
            FROM canonical_teams ct
            LEFT JOIN team_aliases ta ON ta.canonical_team_id = ct.id
            WHERE ct.sport = ? AND ct.is_active = TRUE
            ORDER BY ct.display_name ASC, ta.alias ASC
            """,
            (sport,),
        ).fetchall()

    aliases_by_team: dict[int, set[str]] = {}
    team_names: dict[int, str] = {}
    for row in rows:
        team_id = int(row["team_id"])
        team_names[team_id] = str(row["team_name"])
        aliases_by_team.setdefault(team_id, set())
        if row["alias"]:
            aliases_by_team[team_id].add(str(row["alias"]))

    return tuple(
        (
            team_id,
            team_names[team_id],
            tuple(sorted(aliases_by_team.get(team_id, set()))),
        )
        for team_id in sorted(team_names, key=lambda item: team_names[item])
    )


@lru_cache(maxsize=32)
def _load_canonical_team_list_rows(
    db_path: str,
    sport: str,
    include_merged: bool,
) -> tuple[tuple[int, str, tuple[str, ...], int | None], ...]:
    del db_path
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                ct.id AS team_id,
                ct.display_name AS team_name,
                ct.merged_into_team_id AS merged_into_team_id,
                ta.alias AS alias
            FROM canonical_teams ct
            LEFT JOIN team_aliases ta ON ta.canonical_team_id = ct.id
            WHERE ct.sport = ?
              AND (
                ct.is_active = TRUE
                OR (? = TRUE AND ct.merged_into_team_id IS NOT NULL)
              )
            ORDER BY ct.is_active DESC, ct.display_name ASC, ta.alias ASC
            """,
            (sport, include_merged),
        ).fetchall()

    aliases_by_team: dict[int, set[str]] = {}
    team_names: dict[int, str] = {}
    merged_targets: dict[int, int | None] = {}
    for row in rows:
        team_id = int(row["team_id"])
        team_names[team_id] = str(row["team_name"])
        merged_into_team_id = row["merged_into_team_id"]
        merged_targets[team_id] = (
            int(merged_into_team_id) if merged_into_team_id is not None else None
        )
        aliases_by_team.setdefault(team_id, set())
        if row["alias"]:
            aliases_by_team[team_id].add(str(row["alias"]))

    return tuple(
        (
            team_id,
            team_names[team_id],
            tuple(sorted(aliases_by_team.get(team_id, set()))),
            merged_targets.get(team_id),
        )
        for team_id in sorted(
            team_names,
            key=lambda item: (
                merged_targets.get(item) is not None,
                team_names[item],
            ),
        )
    )


def search_canonical_team_candidates(
    raw_team_name: str,
    *,
    sport: str = DEFAULT_SPORT,
    limit: int = 3,
) -> list[CanonicalTeamCandidate]:
    _ensure_bootstrapped()
    raw_key = normalize_identity_text(raw_team_name)
    if not raw_key:
        return []

    candidates: list[CanonicalTeamCandidate] = []
    for team_id, team_name, aliases in _load_team_search_rows(settings.db_path, sport):
        best_score = 0.0
        best_alias: str | None = None
        for candidate_value in (team_name, *aliases):
            candidate_key = normalize_identity_text(candidate_value)
            if not candidate_key or candidate_key == raw_key:
                continue
            score = float(
                max(
                    fuzz.token_set_ratio(raw_key, candidate_key),
                    fuzz.partial_ratio(raw_key, candidate_key),
                )
            )
            if score > best_score:
                best_score = score
                best_alias = candidate_value
        if best_score <= 0:
            continue
        candidates.append(
            CanonicalTeamCandidate(
                team_id=team_id,
                team_name=team_name,
                score=best_score,
                matched_alias=best_alias if best_alias != team_name else None,
            )
        )

    return sorted(
        candidates,
        key=lambda item: (-item.score, item.team_name),
    )[:limit]


def get_canonical_team(
    team_id: int,
    *,
    follow_merge: bool = False,
) -> CanonicalTeamSummary | None:
    _ensure_bootstrapped()
    return _get_canonical_team_cached(settings.db_path, team_id, follow_merge)


@lru_cache(maxsize=8192)
def _get_canonical_team_cached(
    db_path: str,
    team_id: int,
    follow_merge: bool,
) -> CanonicalTeamSummary | None:
    del db_path
    with _connect() as conn:
        team_row = _query_team_by_id(conn, team_id)
        if team_row is None and follow_merge:
            current_team_id = team_id
            visited: set[int] = set()
            while current_team_id not in visited:
                visited.add(current_team_id)
                raw_row = _query_any_team_by_id(conn, current_team_id)
                if raw_row is None:
                    break
                if bool(raw_row["is_active"]):
                    team_row = raw_row
                    break
                merged_into_team_id = raw_row["merged_into_team_id"]
                if merged_into_team_id is None:
                    break
                current_team_id = int(merged_into_team_id)
        if team_row is None:
            return None
        resolved_team_id = int(team_row["id"])
        alias_rows = conn.execute(
            """
            SELECT alias
            FROM team_aliases
            WHERE canonical_team_id = ?
            ORDER BY alias ASC
            """,
            (resolved_team_id,),
        ).fetchall()
    aliases = tuple(str(row["alias"]) for row in alias_rows if row["alias"])
    return CanonicalTeamSummary(
        id=int(team_row["id"]),
        sport=str(team_row["sport"]),
        display_name=str(team_row["display_name"]),
        aliases=aliases,
        alias_count=len(aliases),
        merged_into_team_id=(
            int(team_row["merged_into_team_id"])
            if team_row["merged_into_team_id"] is not None
            else None
        ),
    )


def _candidate_score(candidate: dict[str, Any]) -> float:
    score = candidate.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    return 0.0


def _pending_team_review_case_rows(
    conn: sqlite3.Connection,
    *,
    source_team_id: int,
    source_team_name: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            id,
            suggested_team_id,
            suggested_team_name,
            candidate_teams,
            canonical_home_team,
            canonical_away_team
        FROM team_review_cases
        WHERE status = 'pending'
          AND (
            suggested_team_id = ?
            OR suggested_team_name = ?
            OR candidate_teams LIKE ?
            OR canonical_home_team = ?
            OR canonical_away_team = ?
          )
        """,
        (
            source_team_id,
            source_team_name,
            f'%"team_id": {source_team_id}%',
            source_team_name,
            source_team_name,
        ),
    ).fetchall()


def _team_review_case_snapshot(
    conn: sqlite3.Connection,
    *,
    source_team_id: int,
    source_team_name: str,
) -> list[dict[str, Any]]:
    rows = _pending_team_review_case_rows(
        conn,
        source_team_id=source_team_id,
        source_team_name=source_team_name,
    )
    return [
        {
            "id": int(row["id"]),
            "suggested_team_id": row["suggested_team_id"],
            "suggested_team_name": row["suggested_team_name"],
            "candidate_teams": row["candidate_teams"],
            "canonical_home_team": row["canonical_home_team"],
            "canonical_away_team": row["canonical_away_team"],
        }
        for row in rows
    ]


def _restore_pending_team_review_cases(
    conn: sqlite3.Connection,
    review_case_snapshot: list[Any],
) -> None:
    for item in review_case_snapshot:
        if not isinstance(item, dict):
            raise ValueError("Stored merge review-case rollback metadata is invalid")
        case_id = item.get("id")
        if not isinstance(case_id, int):
            raise ValueError("Stored merge review-case rollback metadata is invalid")

        current_row = conn.execute(
            """
            SELECT status
            FROM team_review_cases
            WHERE id = ?
            """,
            (case_id,),
        ).fetchone()
        if current_row is None or current_row["status"] != "pending":
            continue

        conn.execute(
            """
            UPDATE team_review_cases
            SET suggested_team_id = ?,
                suggested_team_name = ?,
                candidate_teams = ?,
                canonical_home_team = ?,
                canonical_away_team = ?
            WHERE id = ?
            """,
            (
                item.get("suggested_team_id"),
                item.get("suggested_team_name"),
                item.get("candidate_teams") or "[]",
                item.get("canonical_home_team"),
                item.get("canonical_away_team"),
                case_id,
            ),
        )


def _reassign_pending_team_review_cases(
    conn: sqlite3.Connection,
    *,
    source_team_id: int,
    target_team_id: int,
    source_team_name: str,
    target_team_name: str,
) -> None:
    rows = _pending_team_review_case_rows(
        conn,
        source_team_id=source_team_id,
        source_team_name=source_team_name,
    )

    for row in rows:
        suggested_team_id = row["suggested_team_id"]
        suggested_team_name = row["suggested_team_name"]
        canonical_home_team = row["canonical_home_team"]
        canonical_away_team = row["canonical_away_team"]
        changed = False

        if suggested_team_id == source_team_id:
            suggested_team_id = target_team_id
            suggested_team_name = target_team_name
            changed = True
        elif suggested_team_name == source_team_name:
            suggested_team_id = target_team_id
            suggested_team_name = target_team_name
            changed = True

        if canonical_home_team == source_team_name:
            canonical_home_team = target_team_name
            changed = True
        if canonical_away_team == source_team_name:
            canonical_away_team = target_team_name
            changed = True

        raw_candidates = json.loads(row["candidate_teams"] or "[]")
        merged_candidates: dict[int, dict[str, Any]] = {}
        for candidate in raw_candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_team_id = candidate.get("team_id")
            candidate_team_name = candidate.get("team_name")
            if not isinstance(candidate_team_id, int) or not isinstance(candidate_team_name, str):
                continue
            normalized_candidate = dict(candidate)
            if candidate_team_id == source_team_id:
                normalized_candidate["team_id"] = target_team_id
                normalized_candidate["team_name"] = target_team_name
                changed = True
            existing_candidate = merged_candidates.get(normalized_candidate["team_id"])
            if existing_candidate is None or _candidate_score(normalized_candidate) > _candidate_score(
                existing_candidate
            ):
                merged_candidates[normalized_candidate["team_id"]] = normalized_candidate
            elif (
                existing_candidate.get("matched_alias") is None
                and normalized_candidate.get("matched_alias") is not None
            ):
                existing_candidate["matched_alias"] = normalized_candidate["matched_alias"]

        candidate_teams = sorted(
            merged_candidates.values(),
            key=lambda item: (-_candidate_score(item), str(item.get("team_name", ""))),
        )

        if changed:
            conn.execute(
                """
                UPDATE team_review_cases
                SET suggested_team_id = ?,
                    suggested_team_name = ?,
                    candidate_teams = ?,
                    canonical_home_team = ?,
                    canonical_away_team = ?
                WHERE id = ?
                """,
                (
                    suggested_team_id,
                    suggested_team_name,
                    json.dumps(candidate_teams),
                    canonical_home_team,
                    canonical_away_team,
                    int(row["id"]),
                ),
            )


def list_canonical_teams(
    *,
    sport: str = DEFAULT_SPORT,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_merged: bool = False,
) -> list[CanonicalTeamSummary]:
    _ensure_bootstrapped()
    search_key = normalize_identity_text(search)
    rows = _load_canonical_team_list_rows(settings.db_path, sport, include_merged)
    summaries: list[CanonicalTeamSummary] = []
    for team_id, team_name, aliases, merged_into_team_id in rows:
        haystack = " ".join((team_name, *aliases))
        if search_key and search_key not in normalize_identity_text(haystack):
            continue
        summaries.append(
            CanonicalTeamSummary(
                id=team_id,
                sport=sport,
                display_name=team_name,
                aliases=aliases,
                alias_count=len(aliases),
                merged_into_team_id=merged_into_team_id,
            )
        )
    return summaries[offset : offset + limit]


def _team_alias_snapshot(
    conn: sqlite3.Connection,
    *,
    source_team_id: int,
    target_team_id: int,
) -> list[dict[str, Any]]:
    conflict_rows = conn.execute(
        """
        SELECT normalized_alias, bookmaker_id
        FROM team_aliases
        WHERE canonical_team_id = ?
        INTERSECT
        SELECT normalized_alias, bookmaker_id
        FROM team_aliases
        WHERE canonical_team_id = ?
        """,
        (source_team_id, target_team_id),
    ).fetchall()
    conflicts = {
        (str(row["normalized_alias"]), str(row["bookmaker_id"]))
        for row in conflict_rows
    }
    alias_rows = conn.execute(
        """
        SELECT
            alias,
            normalized_alias,
            bookmaker_id,
            source,
            legacy_competition_id
        FROM team_aliases
        WHERE canonical_team_id = ?
        ORDER BY normalized_alias ASC, bookmaker_id ASC
        """,
        (source_team_id,),
    ).fetchall()
    return [
        {
            "alias": str(row["alias"]),
            "normalized_alias": str(row["normalized_alias"]),
            "bookmaker_id": str(row["bookmaker_id"]),
            "source": str(row["source"]),
            "legacy_competition_id": row["legacy_competition_id"],
            "was_conflict": (
                str(row["normalized_alias"]),
                str(row["bookmaker_id"]),
            )
            in conflicts,
        }
        for row in alias_rows
    ]


def merge_canonical_teams(
    *,
    source_team_id: int,
    target_team_id: int,
) -> CanonicalTeamSummary:
    if source_team_id == target_team_id:
        raise ValueError("Cannot merge a canonical team into itself")

    _ensure_bootstrapped()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        source_row = _query_team_by_id(conn, source_team_id)
        target_row = _query_team_by_id(conn, target_team_id)
        if source_row is None or target_row is None:
            raise ValueError("Both canonical teams must exist before merging")
        if str(source_row["sport"]) != str(target_row["sport"]):
            raise ValueError("Only canonical teams from the same sport can be merged")

        alias_snapshot = _team_alias_snapshot(
            conn,
            source_team_id=source_team_id,
            target_team_id=target_team_id,
        )
        review_case_snapshot = _team_review_case_snapshot(
            conn,
            source_team_id=source_team_id,
            source_team_name=str(source_row["display_name"]),
        )
        conflict_rows = conn.execute(
            """
            SELECT normalized_alias, bookmaker_id
            FROM team_aliases
            WHERE canonical_team_id = ?
            INTERSECT
            SELECT normalized_alias, bookmaker_id
            FROM team_aliases
            WHERE canonical_team_id = ?
            """,
            (source_team_id, target_team_id),
        ).fetchall()
        for row in conflict_rows:
            conn.execute(
                """
                DELETE FROM team_aliases
                WHERE canonical_team_id = ?
                  AND normalized_alias = ?
                  AND bookmaker_id = ?
                """,
                (
                    source_team_id,
                    str(row["normalized_alias"]),
                    str(row["bookmaker_id"]),
                ),
            )

        conn.execute(
            """
            UPDATE team_aliases
            SET canonical_team_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE canonical_team_id = ?
            """,
            (target_team_id, source_team_id),
        )
        _upsert_alias(
            conn,
            sport=str(target_row["sport"]),
            alias=str(source_row["display_name"]),
            canonical_team_id=target_team_id,
            bookmaker_id=_GLOBAL_BOOKMAKER_ID,
            source="merge",
        )
        conn.execute(
            """
            UPDATE canonical_teams
            SET is_active = FALSE,
                merged_into_team_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (target_team_id, source_team_id),
        )
        conn.execute(
            """
            INSERT INTO team_merge_history (
                source_team_id,
                target_team_id,
                alias_snapshot,
                review_case_snapshot
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                source_team_id,
                target_team_id,
                json.dumps(alias_snapshot),
                json.dumps(review_case_snapshot),
            ),
        )
        _reassign_pending_team_review_cases(
            conn,
            source_team_id=source_team_id,
            target_team_id=target_team_id,
            source_team_name=str(source_row["display_name"]),
            target_team_name=str(target_row["display_name"]),
        )
        conn.commit()

    clear_team_registry_cache(reset_bootstrap=False)
    merged = get_canonical_team(target_team_id)
    if merged is None:
        raise RuntimeError("Merged canonical team could not be reloaded")
    return merged


def unmerge_canonical_team(
    *,
    source_team_id: int,
) -> CanonicalTeamUnmergeResult:
    _ensure_bootstrapped()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        history_row = conn.execute(
            """
            SELECT
                id,
                source_team_id,
                target_team_id,
                alias_snapshot,
                review_case_snapshot
            FROM team_merge_history
            WHERE source_team_id = ? AND unmerged_at IS NULL
            ORDER BY merged_at DESC, id DESC
            LIMIT 1
            """,
            (source_team_id,),
        ).fetchone()
        if history_row is None:
            raise ValueError("No active merge history exists for this canonical team")
        if not history_row["alias_snapshot"]:
            raise ValueError(
                "This merge cannot be unmerged safely because it was recorded before alias rollback metadata existed"
            )
        if history_row["review_case_snapshot"] is None:
            raise ValueError(
                "This merge cannot be unmerged safely because it was recorded before review-case rollback metadata existed"
            )

        target_team_id = int(history_row["target_team_id"])
        source_row = _query_any_team_by_id(conn, source_team_id)
        target_row = _query_any_team_by_id(conn, target_team_id)
        if source_row is None or target_row is None:
            raise ValueError("Both canonical teams must exist before unmerging")
        if bool(source_row["is_active"]):
            raise ValueError("Canonical team is already active")
        if source_row["merged_into_team_id"] != target_team_id:
            raise ValueError("Canonical team is not currently merged into the recorded target")

        try:
            alias_snapshot = json.loads(str(history_row["alias_snapshot"]))
        except json.JSONDecodeError as exc:
            raise ValueError("Stored merge rollback metadata is invalid") from exc
        if not isinstance(alias_snapshot, list):
            raise ValueError("Stored merge rollback metadata is invalid")
        try:
            review_case_snapshot = json.loads(str(history_row["review_case_snapshot"]))
        except json.JSONDecodeError as exc:
            raise ValueError("Stored merge review-case rollback metadata is invalid") from exc
        if not isinstance(review_case_snapshot, list):
            raise ValueError("Stored merge review-case rollback metadata is invalid")

        for item in alias_snapshot:
            if not isinstance(item, dict) or item.get("was_conflict"):
                continue
            normalized_alias = str(item.get("normalized_alias") or "")
            bookmaker_id = str(item.get("bookmaker_id") or _GLOBAL_BOOKMAKER_ID)
            existing_alias = conn.execute(
                """
                SELECT canonical_team_id
                FROM team_aliases
                WHERE sport = ? AND normalized_alias = ? AND bookmaker_id = ?
                """,
                (str(source_row["sport"]), normalized_alias, bookmaker_id),
            ).fetchone()
            if (
                existing_alias is not None
                and int(existing_alias["canonical_team_id"]) not in {target_team_id, source_team_id}
            ):
                raise ValueError(
                    "Cannot unmerge safely because an alias from the merge snapshot now belongs to another canonical team"
                )

        source_display_key = str(source_row["normalized_display_name"])
        snapshot_keys = {
            (str(item.get("normalized_alias") or ""), str(item.get("bookmaker_id") or _GLOBAL_BOOKMAKER_ID))
            for item in alias_snapshot
            if isinstance(item, dict) and not item.get("was_conflict")
        }
        if (source_display_key, _GLOBAL_BOOKMAKER_ID) not in snapshot_keys:
            conn.execute(
                """
                DELETE FROM team_aliases
                WHERE canonical_team_id = ?
                  AND sport = ?
                  AND normalized_alias = ?
                  AND bookmaker_id = ?
                  AND source = 'merge'
                """,
                (
                    target_team_id,
                    str(source_row["sport"]),
                    source_display_key,
                    _GLOBAL_BOOKMAKER_ID,
                ),
            )

        for item in alias_snapshot:
            if not isinstance(item, dict) or item.get("was_conflict"):
                continue
            _upsert_alias(
                conn,
                sport=str(source_row["sport"]),
                alias=str(item.get("alias") or ""),
                canonical_team_id=source_team_id,
                bookmaker_id=str(item.get("bookmaker_id") or _GLOBAL_BOOKMAKER_ID),
                source=str(item.get("source") or "manual_review"),
                legacy_competition_id=item.get("legacy_competition_id"),
            )

        conn.execute(
            """
            UPDATE canonical_teams
            SET is_active = TRUE,
                merged_into_team_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (source_team_id,),
        )
        _restore_pending_team_review_cases(conn, review_case_snapshot)
        conn.execute(
            """
            UPDATE team_merge_history
            SET unmerged_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(history_row["id"]),),
        )
        conn.commit()

    clear_team_registry_cache(reset_bootstrap=False)
    return CanonicalTeamUnmergeResult(
        source_team_id=source_team_id,
        target_team_id=target_team_id,
        source_team_name=str(source_row["display_name"]),
    )
