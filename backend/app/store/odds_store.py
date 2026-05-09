from __future__ import annotations

import json
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from ..config import settings
from ..database import get_db
from ..models.schemas import (
    BookmakerOut,
    CanonicalOffer,
    CanonicalTeamOut,
    EventDetailOut,
    EventOddsOut,
    EventPlayerOut,
    EventReviewCaseIn,
    EventReviewCaseOut,
    EventReviewVariantOut,
    LeagueOut,
    MatchBookmakerOut,
    MatchOut,
    NormalizedOdds,
    NormalizedOutcomeOffer,
    NotificationOut,
    OddsOut,
    OpportunityLeg,
    OpportunityOut,
    OutcomeOfferOut,
    PersistenceBenchmarkOut,
    ResolvedEventIn,
    ResolvedEventMemberIn,
    ResolvedEventMemberOut,
    ResolvedEventOut,
    ScanProgressOut,
    SystemStatus,
    TeamReviewCandidate,
    TeamReviewDiagnostic,
    TeamReviewOut,
    TelegramNotificationProfileCreate,
    TelegramNotificationProfileOut,
    TelegramNotificationProfileUpdate,
    UnresolvedOddsDiagnostic,
    UnresolvedOddsOut,
)
from ..services.canonical_offers import (
    canonical_offer_from_normalized_outcome_offer,
    canonical_offers_from_normalized_odds,
)
from ..services.event_player_resolver import (
    build_event_scoped_player_identities,
    build_event_scoped_player_odds,
)
from ..services.league_registry import league_country, league_display_name


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(row)


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


async def _open_isolated_db_connection() -> aiosqlite.Connection:
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.execute("PRAGMA foreign_keys = ON")
    return db


def _json_list(value: object) -> list:
    if not value:
        return []
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, list):
        return value
    return []


def _json_dict(value: object) -> dict:
    if not value:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, dict):
        return value
    return {}


def _normalize_search_text(value: object) -> str:
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFD", str(value))
    chars = [
        char.lower() if char.isalnum() else " "
        for char in decomposed
        if unicodedata.category(char) != "Mn"
    ]
    return " ".join("".join(chars).split())


def _row_to_unresolved_odds(row: aiosqlite.Row) -> UnresolvedOddsOut:
    data = _row_to_dict(row)
    for field in ("candidate_matchups", "available_matchups_same_slot"):
        value = data.get(field)
        if not value:
            data[field] = []
            continue
        if isinstance(value, str):
            data[field] = json.loads(value)
    return UnresolvedOddsOut(**data)


def _row_to_team_review(row: aiosqlite.Row) -> TeamReviewOut:
    data = _row_to_dict(row)
    value = data.get("evidence")
    if not value:
        data["evidence"] = []
    elif isinstance(value, str):
        data["evidence"] = json.loads(value)
    candidate_value = data.get("candidate_teams")
    if not candidate_value:
        data["candidate_teams"] = []
    elif isinstance(candidate_value, str):
        data["candidate_teams"] = [
            TeamReviewCandidate(**item) for item in json.loads(candidate_value)
        ]
    return TeamReviewOut(**data)


def _row_to_resolved_event_member(row: aiosqlite.Row) -> ResolvedEventMemberOut:
    data = _row_to_dict(row)
    data["evidence"] = _json_list(data.get("evidence"))
    data["metadata"] = _json_dict(data.get("metadata"))
    return ResolvedEventMemberOut(**data)


def _row_to_resolved_event(
    row: aiosqlite.Row,
    *,
    members: list[ResolvedEventMemberOut] | None = None,
) -> ResolvedEventOut:
    data = _row_to_dict(row)
    data["metadata"] = _json_dict(data.get("metadata"))
    data["members"] = members or []
    return ResolvedEventOut(**data)


def _row_to_event_review_case(row: aiosqlite.Row) -> EventReviewCaseOut:
    data = _row_to_dict(row)
    for field in (
        "candidate_match_ids",
        "source_bookmaker_ids",
        "source_league_labels",
        "evidence",
    ):
        data[field] = _json_list(data.get(field))
    data["metadata"] = _json_dict(data.get("metadata"))
    return EventReviewCaseOut(**data)


def _row_to_event_review_variant(row: aiosqlite.Row) -> EventReviewVariantOut:
    data = _row_to_dict(row)
    evidence = data.get("member_evidence", data.get("evidence"))
    return EventReviewVariantOut(
        match_id=data["match_id"],
        bookmaker_id=data.get("bookmaker_id"),
        bookmaker_name=data.get("bookmaker_name"),
        league_id=data.get("league_id"),
        league_name=data.get("league_name"),
        home_team=data.get("home_team") or data.get("source_home_team") or "Unknown",
        away_team=data.get("away_team") or data.get("source_away_team") or "Unknown",
        start_time=data.get("start_time"),
        source_url=data.get("source_url"),
        source_league_id=data.get("source_league_id"),
        source_league_name=data.get("source_league_name") or data.get("league_name"),
        source_home_team=data.get("source_home_team") or data.get("home_team"),
        source_away_team=data.get("source_away_team") or data.get("away_team"),
        source_start_time=data.get("source_start_time") or data.get("start_time"),
        orientation=data.get("orientation") or "as_listed",
        confidence=data.get("member_confidence", data.get("confidence")),
        evidence=_json_list(evidence),
    )


def _event_review_source_variant_pairs(case: EventReviewCaseOut) -> list[tuple[str, str]]:
    raw_variants = case.metadata.get("source_variants")
    if not isinstance(raw_variants, list):
        return []

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_variant in raw_variants:
        if not isinstance(raw_variant, dict):
            continue
        match_id = raw_variant.get("match_id")
        bookmaker_id = raw_variant.get("bookmaker_id")
        if not isinstance(match_id, str) or not isinstance(bookmaker_id, str):
            continue
        if not match_id or not bookmaker_id:
            continue
        key = (match_id, bookmaker_id)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def _sql_placeholders(values: list[object]) -> str:
    return ", ".join("?" for _ in values)


async def rollback_pending_transaction() -> None:
    db = await get_db()
    await db.rollback()


# ── Bookmakers ─────────────────────────────────────────────

async def upsert_bookmaker(id: str, name: str, website_url: str | None = None) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO bookmakers (id, name, website_url) VALUES (?, ?, ?)",
        (id, name, website_url),
    )
    await db.commit()


async def get_bookmakers(active_only: bool = True) -> list[BookmakerOut]:
    db = await get_db()
    q = "SELECT * FROM bookmakers"
    if active_only:
        q += " WHERE is_active = TRUE"
    rows = await db.execute_fetchall(q)
    return [BookmakerOut(**_row_to_dict(r)) for r in rows]


# ── Leagues ────────────────────────────────────────────────

async def upsert_league(id: str, name: str, sport: str, country: str | None = None) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO leagues (id, name, sport, country) VALUES (?, ?, ?, ?)",
        (id, name, sport, country),
    )
    await db.commit()


async def get_leagues(sport: str | None = None) -> list[LeagueOut]:
    db = await get_db()
    q = "SELECT * FROM leagues WHERE is_active = TRUE"
    params: list = []
    if sport:
        q += " AND sport = ?"
        params.append(sport)
    rows = await db.execute_fetchall(q, params)
    return [LeagueOut(**_row_to_dict(r)) for r in rows]


# ── Snapshot state ──────────────────────────────────────────


def _snapshot_id_from_scraped_at(snapshot_at: str) -> str:
    return snapshot_at


def _new_opportunity_publish_id(detected_at: str) -> str:
    return f"{detected_at}:{uuid.uuid4().hex[:8]}"


async def _upsert_scrape_snapshot_tx(
    db: aiosqlite.Connection,
    *,
    snapshot_id: str,
    scraped_at: str,
    status: str = "persisted",
    matches_count: int = 0,
    odds_count: int = 0,
    outcome_offers_count: int = 0,
    unresolved_odds_count: int = 0,
    team_review_cases_count: int = 0,
    error: str | None = None,
) -> None:
    await db.execute(
        """INSERT INTO scrape_snapshots (
               id,
               scraped_at,
               completed_at,
               status,
               matches_count,
               odds_count,
               outcome_offers_count,
               unresolved_odds_count,
               team_review_cases_count,
               error
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               scraped_at = excluded.scraped_at,
               completed_at = excluded.completed_at,
               status = excluded.status,
               matches_count = excluded.matches_count,
               odds_count = excluded.odds_count,
               outcome_offers_count = excluded.outcome_offers_count,
               unresolved_odds_count = excluded.unresolved_odds_count,
               team_review_cases_count = excluded.team_review_cases_count,
               error = excluded.error,
               updated_at = CURRENT_TIMESTAMP""",
        (
            snapshot_id,
            scraped_at,
            scraped_at if status in {"persisted", "published"} else None,
            status,
            matches_count,
            odds_count,
            outcome_offers_count,
            unresolved_odds_count,
            team_review_cases_count,
            error,
        ),
    )


async def set_current_snapshot(snapshot_at: str) -> None:
    db = await get_db()
    snapshot_id = _snapshot_id_from_scraped_at(snapshot_at)
    await _upsert_scrape_snapshot_tx(
        db,
        snapshot_id=snapshot_id,
        scraped_at=snapshot_at,
        status="published",
    )
    await db.execute(
        """INSERT INTO scrape_state (
               id,
               current_snapshot_id,
               current_snapshot_at,
               updated_at
           )
           VALUES (1, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
               current_snapshot_id = excluded.current_snapshot_id,
               current_snapshot_at = excluded.current_snapshot_at,
               updated_at = CURRENT_TIMESTAMP""",
        (snapshot_id, snapshot_at),
    )
    await db.commit()


async def _get_current_snapshot(
    db: aiosqlite.Connection,
) -> tuple[str | None, str | None]:
    row = await db.execute_fetchall(
        "SELECT current_snapshot_id, current_snapshot_at FROM scrape_state WHERE id = 1"
    )
    if row:
        snapshot_id = row[0]["current_snapshot_id"]
        snapshot_at = row[0]["current_snapshot_at"]
        if snapshot_id:
            return snapshot_id, snapshot_at or snapshot_id
        if snapshot_at:
            return _snapshot_id_from_scraped_at(snapshot_at), snapshot_at
    published_row = await db.execute_fetchall(
        """SELECT id, scraped_at
           FROM scrape_snapshots
           WHERE status = 'published'
           ORDER BY datetime(scraped_at) DESC, scraped_at DESC
           LIMIT 1"""
    )
    if published_row:
        return published_row[0]["id"], published_row[0]["scraped_at"]
    return None, None


async def _get_current_snapshot_id(db: aiosqlite.Connection) -> str | None:
    snapshot_id, _ = await _get_current_snapshot(db)
    return snapshot_id


async def _has_scrape_snapshots(db: aiosqlite.Connection) -> bool:
    row = await db.execute_fetchall("SELECT 1 FROM scrape_snapshots LIMIT 1")
    return bool(row)


async def _get_current_snapshot_at(db: aiosqlite.Connection) -> str | None:
    _, snapshot_at = await _get_current_snapshot(db)
    return snapshot_at


async def _get_current_opportunity_publish_id(
    db: aiosqlite.Connection,
) -> str | None:
    row = await db.execute_fetchall(
        "SELECT current_opportunity_publish_id FROM scrape_state WHERE id = 1"
    )
    if not row or not row[0][0]:
        return None
    return row[0][0]


async def _get_legacy_snapshot_cutoff(db: aiosqlite.Connection) -> tuple[str, str] | None:
    row = await db.execute_fetchall(
        """SELECT MAX(t) AS t
           FROM (
               SELECT MAX(scraped_at) AS t FROM odds
               UNION ALL
               SELECT MAX(scraped_at) AS t FROM outcome_offers
           )"""
    )
    if not row or not row[0][0]:
        return None

    latest_scrape_at = row[0][0]
    latest_dt = datetime.fromisoformat(latest_scrape_at)
    lookback_minutes = max(settings.scrape_interval_minutes, 15)
    cutoff_at = (latest_dt - timedelta(minutes=lookback_minutes)).isoformat()
    return latest_scrape_at, cutoff_at


async def _get_latest_unresolved_snapshot_at(db: aiosqlite.Connection) -> str | None:
    row = await db.execute_fetchall("SELECT MAX(scraped_at) AS t FROM unresolved_odds")
    if not row or not row[0][0]:
        return None
    return row[0][0]


async def _get_latest_team_review_snapshot_at(db: aiosqlite.Connection) -> str | None:
    row = await db.execute_fetchall("SELECT MAX(scraped_at) AS t FROM team_review_cases")
    if not row or not row[0][0]:
        return None
    return row[0][0]


async def _get_visible_diagnostic_snapshot(
    db: aiosqlite.Connection,
    table_name: str,
) -> tuple[str | None, str | None]:
    if table_name not in {"unresolved_odds", "team_review_cases"}:
        raise ValueError(f"unsupported diagnostic table: {table_name}")

    current_snapshot_id, current_snapshot_at = await _get_current_snapshot(db)
    newer_than_current = ""
    params: list[object] = []
    if current_snapshot_at is not None:
        newer_than_current = "AND datetime(ss.scraped_at) > datetime(?)"
        params.append(current_snapshot_at)
    rows = await db.execute_fetchall(
        f"""SELECT ss.id, ss.scraped_at
            FROM scrape_snapshots ss
            WHERE ss.status = 'analysis_failed'
              {newer_than_current}
              AND EXISTS (
                  SELECT 1
                  FROM {table_name} d
                  WHERE d.snapshot_id = ss.id
              )
            ORDER BY datetime(ss.scraped_at) DESC, ss.id DESC
            LIMIT 1""",
        params,
    )
    if rows:
        return rows[0]["id"], rows[0]["scraped_at"]
    if current_snapshot_id is not None:
        return current_snapshot_id, current_snapshot_at
    if await _has_scrape_snapshots(db):
        return None, None
    snapshot_at = (
        await _get_latest_unresolved_snapshot_at(db)
        if table_name == "unresolved_odds"
        else await _get_latest_team_review_snapshot_at(db)
    )
    return _snapshot_id_from_scraped_at(snapshot_at) if snapshot_at else None, snapshot_at


async def _get_team_review_snapshot_at(db: aiosqlite.Connection) -> str | None:
    snapshot_at = await _get_current_snapshot_at(db)
    if snapshot_at is not None:
        return snapshot_at
    return await _get_latest_team_review_snapshot_at(db)


async def _current_or_legacy_snapshot_filter(
    db: aiosqlite.Connection,
    alias: str,
    *,
    snapshot_id: str | None = None,
) -> tuple[str | None, list[object]]:
    if snapshot_id is not None:
        return f"{alias}.snapshot_id = ?", [snapshot_id]

    current_snapshot_id = await _get_current_snapshot_id(db)
    if current_snapshot_id is not None:
        return f"{alias}.snapshot_id = ?", [current_snapshot_id]

    current_snapshot_at = await _get_current_snapshot_at(db)
    if current_snapshot_at is not None:
        return f"{alias}.scraped_at = ?", [current_snapshot_at]

    if await _has_scrape_snapshots(db):
        return None, []

    legacy_window = await _get_legacy_snapshot_cutoff(db)
    if legacy_window is None:
        return None, []
    _, cutoff_at = legacy_window
    return f"{alias}.scraped_at >= ?", [cutoff_at]


# ── Matches ────────────────────────────────────────────────


async def _get_resolved_event_id_for_match(
    db: aiosqlite.Connection,
    match_id: str,
    *,
    snapshot_id: str | None,
) -> str | None:
    if snapshot_id is not None:
        rows = await db.execute_fetchall(
            """SELECT rem.resolved_event_id
               FROM resolved_event_members rem
              WHERE rem.match_id = ?
                AND rem.status = 'active'
                AND (
                    rem.snapshot_id = ?
                    OR rem.snapshot_id IS NULL
                )
              ORDER BY CASE
                  WHEN EXISTS (
                      SELECT 1
                      FROM resolved_events re
                      WHERE re.id = rem.resolved_event_id
                        AND re.method IN ('manual', 'manual_review')
                  ) THEN 0
                  ELSE 1
              END,
              CASE
                  WHEN rem.snapshot_id = ? THEN 0
                  ELSE 1
              END,
              rem.resolved_event_id ASC
              LIMIT 1""",
            (match_id, snapshot_id, snapshot_id),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT rem.resolved_event_id
               FROM resolved_event_members rem
              WHERE rem.match_id = ?
                AND rem.status = 'active'
                AND rem.snapshot_id IS NULL
              ORDER BY rem.resolved_event_id ASC
              LIMIT 1""",
            (match_id,),
        )
    return rows[0]["resolved_event_id"] if rows else None


async def _get_resolved_event_member_match_ids(
    db: aiosqlite.Connection,
    resolved_event_id: str,
    *,
    snapshot_id: str | None,
) -> list[str]:
    if snapshot_id is not None:
        rows = await db.execute_fetchall(
            """SELECT DISTINCT rem.match_id
               FROM resolved_event_members rem
              WHERE rem.resolved_event_id = ?
                AND rem.status = 'active'
                AND (
                    rem.snapshot_id = ?
                    OR rem.snapshot_id IS NULL
                )
              ORDER BY rem.match_id ASC""",
            (resolved_event_id, snapshot_id),
        )
    else:
        rows = await db.execute_fetchall(
            """SELECT DISTINCT rem.match_id
               FROM resolved_event_members rem
              WHERE rem.resolved_event_id = ?
                AND rem.status = 'active'
                AND rem.snapshot_id IS NULL
              ORDER BY rem.match_id ASC""",
            (resolved_event_id,),
        )
    return [row["match_id"] for row in rows]


async def upsert_match(
    id: str,
    league_id: str,
    home_team: str,
    away_team: str,
    sport: str = "basketball",
    home_team_id: int | None = None,
    away_team_id: int | None = None,
    start_time: str | None = None,
    status: str = "upcoming",
) -> None:
    db = await get_db()
    normalized_home_team_id = home_team_id if home_team_id and home_team_id > 0 else None
    normalized_away_team_id = away_team_id if away_team_id and away_team_id > 0 else None
    await db.execute(
        """INSERT OR REPLACE INTO matches (
               id,
               league_id,
               sport,
               home_team_id,
               away_team_id,
               home_team,
               away_team,
               start_time,
               status
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            id,
            league_id,
            sport,
            normalized_home_team_id,
            normalized_away_team_id,
            home_team,
            away_team,
            start_time,
            status,
        ),
    )
    await db.commit()


async def _upsert_match_bookmaker_source_tx(
    db: aiosqlite.Connection,
    *,
    snapshot_id: str | None = None,
    match_id: str,
    bookmaker_id: str,
    source_url: str | None,
) -> None:
    await db.execute(
        """INSERT INTO match_bookmaker_sources (snapshot_id, match_id, bookmaker_id, source_url)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(COALESCE(snapshot_id, ''), match_id, bookmaker_id) DO UPDATE SET
                source_url = COALESCE(excluded.source_url, match_bookmaker_sources.source_url),
                updated_at = CURRENT_TIMESTAMP""",
        (snapshot_id, match_id, bookmaker_id, source_url),
    )


async def upsert_match_bookmaker_source(
    *,
    snapshot_id: str | None = None,
    match_id: str,
    bookmaker_id: str,
    source_url: str | None,
) -> None:
    db = await get_db()
    await _upsert_match_bookmaker_source_tx(
        db,
        snapshot_id=snapshot_id,
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        source_url=source_url,
    )
    await db.commit()


def _match_row_from_offer_row(row: NormalizedOdds | NormalizedOutcomeOffer) -> tuple:
    normalized_home_team_id = row.home_team_id if row.home_team_id and row.home_team_id > 0 else None
    normalized_away_team_id = row.away_team_id if row.away_team_id and row.away_team_id > 0 else None
    return (
        row.match_id,
        row.league_id,
        row.sport,
        normalized_home_team_id,
        normalized_away_team_id,
        row.home_team,
        row.away_team,
        row.start_time,
        "upcoming",
    )


def _team_review_values(
    case: TeamReviewDiagnostic,
    *,
    snapshot_id: str,
    scraped_at: str,
) -> tuple:
    return (
        snapshot_id,
        case.bookmaker_id,
        case.raw_league_id,
        case.normalized_raw_league_id,
        case.sport,
        case.scope_league_id,
        case.raw_team_name,
        case.normalized_raw_team_name,
        case.suggested_team_id,
        case.suggested_team_name,
        case.start_time,
        case.review_kind,
        case.reason_code,
        case.confidence,
        case.similarity_score,
        json.dumps([candidate.model_dump() for candidate in case.candidate_teams]),
        case.matched_counterpart_team,
        case.canonical_home_team,
        case.canonical_away_team,
        json.dumps(case.evidence),
        case.status,
        scraped_at,
    )


async def persist_scrape_snapshot_batch(
    *,
    snapshot_at: str,
    odds: list[NormalizedOdds],
    outcome_offers: list[NormalizedOutcomeOffer],
    unresolved_odds: list[UnresolvedOddsDiagnostic],
    team_review_cases: list[TeamReviewDiagnostic],
    auto_approved_team_reviews: list[TeamReviewDiagnostic] | None = None,
) -> dict[str, object]:
    wall_started_at = time.perf_counter()
    snapshot_id = _snapshot_id_from_scraped_at(snapshot_at)
    auto_approved_team_reviews = auto_approved_team_reviews or []
    rows: list[NormalizedOdds | NormalizedOutcomeOffer] = [*odds, *outcome_offers]
    seen_match_rows = {row.match_id: row for row in rows}
    league_ids = sorted({row.league_id for row in rows})
    benchmark = PersistenceBenchmarkOut(
        row_counts={
            "leagues": len(league_ids),
            "matches": len(seen_match_rows),
            "snapshot_matches": len(seen_match_rows),
            "sources": 0,
            "odds": len(odds),
            "odds_history": len(odds),
            "outcome_offers": len(outcome_offers),
            "unresolved_odds": len(unresolved_odds),
            "team_review_cases": len(team_review_cases),
            "auto_approved_team_reviews": len(auto_approved_team_reviews),
        }
    )

    db = await get_db()
    auto_approved_case_ids: list[int] = []
    try:
        subphase_started_at = time.perf_counter()
        await db.execute("BEGIN IMMEDIATE")
        benchmark.begin_transaction_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await _upsert_scrape_snapshot_tx(
            db,
            snapshot_id=snapshot_id,
            scraped_at=snapshot_at,
            status="persisting",
            matches_count=len(seen_match_rows),
            odds_count=len(odds),
            outcome_offers_count=len(outcome_offers),
            unresolved_odds_count=len(unresolved_odds),
            team_review_cases_count=len(team_review_cases)
            + len(auto_approved_team_reviews),
        )
        benchmark.upsert_snapshot_persisting_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.executemany(
            "INSERT OR REPLACE INTO leagues (id, name, sport, country) VALUES (?, ?, ?, ?)",
            [
                (
                    league_id,
                    league_display_name(league_id),
                    next(row.sport for row in rows if row.league_id == league_id),
                    league_country(league_id),
                )
                for league_id in league_ids
            ],
        )
        benchmark.upsert_leagues_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.executemany(
            """INSERT OR REPLACE INTO matches (
                   id,
                   league_id,
                   sport,
                   home_team_id,
                   away_team_id,
                   home_team,
                   away_team,
                   start_time,
                   status
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [_match_row_from_offer_row(row) for row in seen_match_rows.values()],
        )
        benchmark.upsert_matches_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.executemany(
            """INSERT OR REPLACE INTO snapshot_matches (
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    *_match_row_from_offer_row(row),
                )
                for row in seen_match_rows.values()
            ],
        )
        benchmark.upsert_snapshot_matches_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        source_rows = [
            (snapshot_id, row.match_id, row.bookmaker_id, row.source_url)
            for row in rows
            if row.source_url is not None
        ]
        benchmark.row_counts["sources"] = len(source_rows)
        await db.executemany(
            """INSERT INTO match_bookmaker_sources (snapshot_id, match_id, bookmaker_id, source_url)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(COALESCE(snapshot_id, ''), match_id, bookmaker_id) DO UPDATE SET
                    source_url = COALESCE(excluded.source_url, match_bookmaker_sources.source_url),
                    updated_at = CURRENT_TIMESTAMP""",
            source_rows,
        )
        benchmark.upsert_sources_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.executemany(
            """INSERT OR REPLACE INTO odds
               (snapshot_id, match_id, bookmaker_id, market_type, player_name, threshold,
                over_odds, under_odds, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    item.match_id,
                    item.bookmaker_id,
                    item.market_type,
                    item.player_name,
                    item.threshold,
                    item.over_odds,
                    item.under_odds,
                    snapshot_at,
                )
                for item in odds
            ],
        )
        benchmark.upsert_odds_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.executemany(
            """INSERT INTO odds_history
               (snapshot_id, match_id, bookmaker_id, market_type, player_name, threshold,
                over_odds, under_odds, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    item.match_id,
                    item.bookmaker_id,
                    item.market_type,
                    item.player_name,
                    item.threshold,
                    item.over_odds,
                    item.under_odds,
                    snapshot_at,
                )
                for item in odds
            ],
        )
        benchmark.insert_odds_history_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.executemany(
            """INSERT OR REPLACE INTO outcome_offers
               (snapshot_id, match_id, bookmaker_id, market_type, outcome_code, line,
                odds, raw_label, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    item.match_id,
                    item.bookmaker_id,
                    item.market_type,
                    item.outcome_code,
                    item.line,
                    item.odds,
                    item.raw_label,
                    snapshot_at,
                )
                for item in outcome_offers
            ],
        )
        benchmark.upsert_outcome_offers_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.executemany(
            """INSERT INTO unresolved_odds
               (snapshot_id, bookmaker_id, raw_league_id, league_id, sport, market_type,
                player_name, raw_team_name, normalized_team_name, start_time, threshold,
                over_odds, under_odds, reason_code, candidate_count, candidate_matchups,
                available_matchups_same_slot, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    item.bookmaker_id,
                    item.raw_league_id,
                    item.league_id,
                    item.sport,
                    item.market_type,
                    item.player_name,
                    item.raw_team_name,
                    item.normalized_team_name,
                    item.start_time,
                    item.threshold,
                    item.over_odds,
                    item.under_odds,
                    item.reason_code,
                    item.candidate_count,
                    json.dumps(item.candidate_matchups),
                    json.dumps(item.available_matchups_same_slot),
                    snapshot_at,
                )
                for item in unresolved_odds
            ],
        )
        benchmark.insert_unresolved_odds_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        team_review_sql = """INSERT INTO team_review_cases
               (snapshot_id, bookmaker_id, raw_league_id, normalized_raw_league_id, sport,
                scope_league_id, raw_team_name, normalized_raw_team_name, suggested_team_id,
                suggested_team_name, start_time, review_kind, reason_code, confidence,
                similarity_score, candidate_teams, matched_counterpart_team,
                canonical_home_team, canonical_away_team, evidence, status, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        await db.executemany(
            team_review_sql,
            [
                _team_review_values(item, snapshot_id=snapshot_id, scraped_at=snapshot_at)
                for item in team_review_cases
            ],
        )
        benchmark.insert_team_review_cases_ms = _elapsed_ms(subphase_started_at)
        for item in auto_approved_team_reviews:
            subphase_started_at = time.perf_counter()
            cursor = await db.execute(
                team_review_sql,
                _team_review_values(item, snapshot_id=snapshot_id, scraped_at=snapshot_at),
            )
            benchmark.insert_auto_approved_team_reviews_ms += _elapsed_ms(
                subphase_started_at
            )
            case_id = cursor.lastrowid or 0
            auto_approved_case_ids.append(case_id)
            subphase_started_at = time.perf_counter()
            await db.execute(
                """UPDATE team_review_cases
                   SET status = 'approved',
                       approved_at = COALESCE(approved_at, CURRENT_TIMESTAMP)
                   WHERE id = ?""",
                (case_id,),
            )
            benchmark.update_auto_approved_reviews_ms += _elapsed_ms(
                subphase_started_at
            )
        subphase_started_at = time.perf_counter()
        await _upsert_scrape_snapshot_tx(
            db,
            snapshot_id=snapshot_id,
            scraped_at=snapshot_at,
            status="persisted",
            matches_count=len(seen_match_rows),
            odds_count=len(odds),
            outcome_offers_count=len(outcome_offers),
            unresolved_odds_count=len(unresolved_odds),
            team_review_cases_count=len(team_review_cases)
            + len(auto_approved_team_reviews),
        )
        benchmark.upsert_snapshot_persisted_ms = _elapsed_ms(subphase_started_at)
        subphase_started_at = time.perf_counter()
        await db.commit()
        benchmark.commit_ms = _elapsed_ms(subphase_started_at)
        benchmark.wall_ms = _elapsed_ms(wall_started_at)
    except Exception:
        await db.rollback()
        raise

    return {
        "snapshot_id": snapshot_id,
        "snapshot_at": snapshot_at,
        "seen_match_ids": set(seen_match_rows),
        "auto_approved_team_review_case_ids": auto_approved_case_ids,
        "matches_count": len(seen_match_rows),
        "odds_count": len(odds),
        "outcome_offers_count": len(outcome_offers),
        "unresolved_odds_count": len(unresolved_odds),
        "team_review_cases_count": len(team_review_cases)
        + len(auto_approved_team_reviews),
        "benchmark": benchmark,
    }


async def publish_scrape_snapshot(*, snapshot_id: str, snapshot_at: str) -> None:
    db = await _open_isolated_db_connection()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """UPDATE scrape_snapshots
               SET status = 'published',
                   completed_at = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (snapshot_at, snapshot_id),
        )
        await db.execute(
            """INSERT INTO scrape_state (
                   id,
                   current_snapshot_id,
                   current_snapshot_at,
                   updated_at
               )
               VALUES (1, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                   current_snapshot_id = excluded.current_snapshot_id,
                   current_snapshot_at = excluded.current_snapshot_at,
                   updated_at = CURRENT_TIMESTAMP""",
            (snapshot_id, snapshot_at),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def mark_scrape_snapshot_analysis_failed(
    *, snapshot_id: str, snapshot_at: str, error: str | None = None
) -> None:
    db = await _open_isolated_db_connection()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """UPDATE scrape_snapshots
               SET status = 'analysis_failed',
                   completed_at = ?,
                   error = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (snapshot_at, error, snapshot_id),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_matches(
    league_id: str | None = None,
    sport: str | None = None,
    status: str | None = None,
    bookmaker_ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MatchOut]:
    db = await get_db()
    current_snapshot_id = await _get_current_snapshot_id(db)
    snapshot_at: str | None = await _get_current_snapshot_at(db)
    cutoff_at: str | None = None
    bookmaker_filter = ""
    if bookmaker_ids:
        bookmaker_filter = f" AND {{alias}}.bookmaker_id IN ({_sql_placeholders(bookmaker_ids)})"

    if current_snapshot_id is not None:
        odds_filter = "o.snapshot_id = ?" + bookmaker_filter.format(alias="o")
        offers_filter = "oo.snapshot_id = ?" + bookmaker_filter.format(alias="oo")
        params: list[object] = [
            current_snapshot_id,
            current_snapshot_id,
            current_snapshot_id,
            current_snapshot_id,
        ]
        if bookmaker_ids:
            params.extend(bookmaker_ids)
        params.append(current_snapshot_id)
        if bookmaker_ids:
            params.extend(bookmaker_ids)
        q = f"""SELECT m.id,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END AS league_id,
                       l.name as league_name,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.sport ELSE m.sport END AS sport,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team_id ELSE m.home_team_id END AS home_team_id,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team_id ELSE m.away_team_id END AS away_team_id,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.status ELSE m.status END AS status,
                        (
                            SELECT rem.resolved_event_id
                            FROM resolved_event_members rem
                           WHERE rem.match_id = m.id
                             AND rem.status = 'active'
                             AND (
                                 rem.snapshot_id = ?
                                 OR rem.snapshot_id IS NULL
                             )
                           ORDER BY CASE
                               WHEN EXISTS (
                                   SELECT 1
                                   FROM resolved_events re
                                   WHERE re.id = rem.resolved_event_id
                                     AND re.method IN ('manual', 'manual_review')
                               ) THEN 0
                               ELSE 1
                           END,
                           CASE
                               WHEN rem.snapshot_id = ? THEN 0
                               ELSE 1
                           END,
                           rem.resolved_event_id ASC
                           LIMIT 1
                       ) AS resolved_event_id
                FROM matches m
                LEFT JOIN snapshot_matches sm
                  ON sm.snapshot_id = ? AND sm.match_id = m.id
                LEFT JOIN leagues l
                  ON CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END = l.id
               WHERE m.id IN (
                   SELECT o.match_id
                   FROM odds o
                   WHERE {odds_filter}
                   UNION
                   SELECT oo.match_id
                   FROM outcome_offers oo
                   WHERE {offers_filter}
               )"""
    else:
        if await _has_scrape_snapshots(db):
            return []
        legacy_window = await _get_legacy_snapshot_cutoff(db)
        if legacy_window is None:
            return []
        _, cutoff_at = legacy_window
        snapshot_at = None
        odds_filter = "o.scraped_at >= ?" + bookmaker_filter.format(alias="o")
        offers_filter = "oo.scraped_at >= ?" + bookmaker_filter.format(alias="oo")
        params = [cutoff_at]
        if bookmaker_ids:
            params.extend(bookmaker_ids)
        params.append(cutoff_at)
        if bookmaker_ids:
            params.extend(bookmaker_ids)
        q = f"""SELECT m.*, l.name as league_name,
                      (
                           SELECT rem.resolved_event_id
                           FROM resolved_event_members rem
                           WHERE rem.match_id = m.id
                             AND rem.status = 'active'
                             AND rem.snapshot_id IS NULL
                           ORDER BY rem.resolved_event_id ASC
                           LIMIT 1
                      ) AS resolved_event_id
               FROM matches m
               LEFT JOIN leagues l ON m.league_id = l.id
               WHERE m.id IN (
                   SELECT o.match_id
                   FROM odds o
                   WHERE {odds_filter}
                   UNION
                   SELECT oo.match_id
                   FROM outcome_offers oo
                   WHERE {offers_filter}
               )"""
    if league_id:
        if current_snapshot_id is not None:
            q += " AND CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END = ?"
        else:
            q += " AND m.league_id = ?"
        params.append(league_id)
    if sport:
        if current_snapshot_id is not None:
            q += " AND CASE WHEN sm.match_id IS NOT NULL THEN sm.sport ELSE m.sport END = ?"
        else:
            q += " AND m.sport = ?"
        params.append(sport)
    if status:
        if current_snapshot_id is not None:
            q += " AND CASE WHEN sm.match_id IS NOT NULL THEN sm.status ELSE m.status END = ?"
        else:
            q += " AND m.status = ?"
        params.append(status)
    if current_snapshot_id is not None:
        q += (
            " ORDER BY CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END ASC "
            "LIMIT ? OFFSET ?"
        )
    else:
        q += " ORDER BY m.start_time ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)

    match_rows = [_row_to_dict(r) for r in rows]
    bookmaker_map = await _get_match_bookmaker_map(
        db,
        [row["id"] for row in match_rows],
        snapshot_id=current_snapshot_id,
        snapshot_at=snapshot_at,
        cutoff_at=cutoff_at,
    )

    for row in match_rows:
        row["available_bookmakers"] = bookmaker_map.get(row["id"], [])

    return [MatchOut(**row) for row in match_rows]


async def get_match(
    match_id: str,
    *,
    require_current_snapshot: bool = False,
) -> MatchOut | None:
    db = await get_db()
    current_snapshot_id = await _get_current_snapshot_id(db)
    snapshot_at: str | None = None
    cutoff_at: str | None = None
    if current_snapshot_id is not None:
        snapshot_at = await _get_current_snapshot_at(db)
        visibility_clause = ""
        params: list[object] = [
            current_snapshot_id,
            current_snapshot_id,
            current_snapshot_id,
            match_id,
        ]
        if require_current_snapshot:
            visibility_clause = """
                 AND (
                     EXISTS (
                         SELECT 1 FROM odds o
                         WHERE o.snapshot_id = ? AND o.match_id = m.id
                     )
                     OR EXISTS (
                         SELECT 1 FROM outcome_offers oo
                         WHERE oo.snapshot_id = ? AND oo.match_id = m.id
                     )
                 )"""
            params.extend([current_snapshot_id, current_snapshot_id])
        row = await db.execute_fetchall(
            f"""SELECT m.id,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END AS league_id,
                       l.name as league_name,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.sport ELSE m.sport END AS sport,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team_id ELSE m.home_team_id END AS home_team_id,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team_id ELSE m.away_team_id END AS away_team_id,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.status ELSE m.status END AS status,
                      (
                          SELECT rem.resolved_event_id
                          FROM resolved_event_members rem
                          WHERE rem.match_id = m.id
                            AND rem.status = 'active'
                            AND (
                                rem.snapshot_id = ?
                                OR rem.snapshot_id IS NULL
                            )
                          ORDER BY CASE
                              WHEN EXISTS (
                                  SELECT 1
                                  FROM resolved_events re
                                  WHERE re.id = rem.resolved_event_id
                                    AND re.method IN ('manual', 'manual_review')
                              ) THEN 0
                              ELSE 1
                          END,
                          CASE
                              WHEN rem.snapshot_id = ? THEN 0
                              ELSE 1
                          END,
                          rem.resolved_event_id ASC
                          LIMIT 1
                      ) AS resolved_event_id
               FROM matches m
               LEFT JOIN snapshot_matches sm
                 ON sm.snapshot_id = ? AND sm.match_id = m.id
                LEFT JOIN leagues l
                  ON CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END = l.id
                WHERE m.id = ?{visibility_clause}""",
            params,
        )
    else:
        if require_current_snapshot and await _has_scrape_snapshots(db):
            return None
        legacy_window = None if await _has_scrape_snapshots(db) else await _get_legacy_snapshot_cutoff(db)
        if legacy_window is not None:
            _, cutoff_at = legacy_window
        row = await db.execute_fetchall(
            """SELECT m.*, l.name as league_name,
                      (
                           SELECT rem.resolved_event_id
                          FROM resolved_event_members rem
                          WHERE rem.match_id = m.id
                            AND rem.status = 'active'
                            AND rem.snapshot_id IS NULL
                          ORDER BY rem.resolved_event_id ASC
                          LIMIT 1
                      ) AS resolved_event_id
               FROM matches m
               LEFT JOIN leagues l ON m.league_id = l.id
               WHERE m.id = ?""",
            (match_id,),
        )
    if not row:
        return None
    data = _row_to_dict(row[0])
    match_ids_for_bookmakers = [data["id"]]
    if data.get("resolved_event_id"):
        event_member_ids = await _get_resolved_event_member_match_ids(
            db,
            data["resolved_event_id"],
            snapshot_id=current_snapshot_id,
        )
        if event_member_ids:
            match_ids_for_bookmakers = event_member_ids
    bookmaker_map = await _get_match_bookmaker_map(
        db,
        match_ids_for_bookmakers,
        snapshot_id=current_snapshot_id,
        snapshot_at=snapshot_at,
        cutoff_at=cutoff_at,
    )
    bookmakers_by_id: dict[str, MatchBookmakerOut] = {}
    for scoped_match_id in match_ids_for_bookmakers:
        for bookmaker in bookmaker_map.get(scoped_match_id, []):
            bookmakers_by_id.setdefault(bookmaker.id, bookmaker)
    data["available_bookmakers"] = sorted(
        bookmakers_by_id.values(),
        key=lambda bookmaker: bookmaker.name.lower(),
    )
    return MatchOut(**data)


# ── Resolved events ─────────────────────────────────────────

async def upsert_resolved_event(event: ResolvedEventIn) -> str:
    db = await get_db()
    event_id = event.id or f"evt_{uuid.uuid4().hex}"
    await db.execute(
        """INSERT INTO resolved_events (
               id,
               sport,
               start_time,
               primary_match_id,
               status,
               confidence,
               method,
               display_home_team,
               display_away_team,
               display_league_name,
               metadata
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               sport = excluded.sport,
               start_time = excluded.start_time,
               primary_match_id = excluded.primary_match_id,
               status = excluded.status,
               confidence = excluded.confidence,
               method = excluded.method,
               display_home_team = excluded.display_home_team,
               display_away_team = excluded.display_away_team,
               display_league_name = excluded.display_league_name,
               metadata = excluded.metadata,
               updated_at = CURRENT_TIMESTAMP""",
        (
            event_id,
            event.sport,
            event.start_time,
            event.primary_match_id,
            event.status,
            event.confidence,
            event.method,
            event.display_home_team,
            event.display_away_team,
            event.display_league_name,
            json.dumps(event.metadata),
        ),
    )
    await db.commit()
    return event_id


async def link_resolved_event_member(member: ResolvedEventMemberIn) -> int:
    db = await get_db()
    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(
            """INSERT INTO resolved_event_members (
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
                   metadata
                )
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(
                     COALESCE(snapshot_id, ''),
                     match_id,
                     bookmaker_id
                 ) DO UPDATE SET
                     resolved_event_id = excluded.resolved_event_id,
                     orientation = excluded.orientation,
                    confidence = excluded.confidence,
                   status = excluded.status,
                   source_url = COALESCE(excluded.source_url, resolved_event_members.source_url),
                   source_league_id = COALESCE(
                       excluded.source_league_id,
                       resolved_event_members.source_league_id
                   ),
                   source_league_name = COALESCE(
                       excluded.source_league_name,
                       resolved_event_members.source_league_name
                   ),
                   source_home_team = COALESCE(
                       excluded.source_home_team,
                       resolved_event_members.source_home_team
                   ),
                   source_away_team = COALESCE(
                       excluded.source_away_team,
                       resolved_event_members.source_away_team
                   ),
                   source_start_time = COALESCE(
                       excluded.source_start_time,
                       resolved_event_members.source_start_time
                    ),
                    evidence = excluded.evidence,
                    metadata = excluded.metadata,
                    updated_at = CURRENT_TIMESTAMP
                WHERE NOT (
                    EXISTS (
                        SELECT 1
                        FROM resolved_events existing_event
                        WHERE existing_event.id = resolved_event_members.resolved_event_id
                          AND existing_event.status = 'active'
                          AND existing_event.method IN ('manual', 'manual_review')
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM resolved_events incoming_event
                        WHERE incoming_event.id = excluded.resolved_event_id
                          AND incoming_event.method IN ('manual', 'manual_review')
                    )
                )""",
            (
                member.snapshot_id,
                member.resolved_event_id,
                member.match_id,
                member.bookmaker_id,
                member.orientation,
                member.confidence,
                member.status,
                member.source_url,
                member.source_league_id,
                member.source_league_name,
                member.source_home_team,
                member.source_away_team,
                member.source_start_time,
                json.dumps(member.evidence),
                json.dumps(member.metadata),
            ),
        )
        await _upsert_match_bookmaker_source_tx(
            db,
            snapshot_id=member.snapshot_id,
            match_id=member.match_id,
            bookmaker_id=member.bookmaker_id,
            source_url=member.source_url,
        )
        rows = await db.execute_fetchall(
            """SELECT id
               FROM resolved_event_members
               WHERE COALESCE(snapshot_id, '') = COALESCE(?, '')
                 AND match_id = ?
                 AND bookmaker_id = ?""",
            (member.snapshot_id, member.match_id, member.bookmaker_id),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return int(rows[0]["id"]) if rows else 0


async def get_resolved_event_members(
    resolved_event_id: str,
    *,
    status: str | None = None,
    snapshot_id: str | None = None,
) -> list[ResolvedEventMemberOut]:
    db = await get_db()
    effective_snapshot_id = (
        snapshot_id if snapshot_id is not None else await _get_current_snapshot_id(db)
    )
    q = """SELECT m.*, b.name AS bookmaker_name
           FROM resolved_event_members m
           LEFT JOIN bookmakers b ON b.id = m.bookmaker_id
           WHERE m.resolved_event_id = ?"""
    params: list[object] = [resolved_event_id]
    if status:
        q += " AND m.status = ?"
        params.append(status)
    order_params: list[object] = []
    if effective_snapshot_id is not None:
        q += " AND (m.snapshot_id = ? OR m.snapshot_id IS NULL)"
        params.append(effective_snapshot_id)
        snapshot_order = "CASE WHEN m.snapshot_id = ? THEN 0 ELSE 1 END, "
        order_params.append(effective_snapshot_id)
    else:
        q += " AND m.snapshot_id IS NULL"
        snapshot_order = ""
    q += f" ORDER BY {snapshot_order}m.id ASC"
    params.extend(order_params)
    rows = await db.execute_fetchall(q, params)
    members: list[ResolvedEventMemberOut] = []
    seen_keys: set[tuple[str, str]] = set()
    for row in rows:
        member = _row_to_resolved_event_member(row)
        key = (member.match_id, member.bookmaker_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        members.append(member)
    return members


async def get_eligible_resolved_event_members_for_matches(
    match_ids: list[str],
    *,
    bookmaker_ids: list[str] | None = None,
    snapshot_id: str | None = None,
    event_methods: tuple[str, ...] = (
        "exact",
        "auto_fuzzy_high",
        "manual",
        "manual_review",
    ),
) -> list[ResolvedEventMemberOut]:
    if not match_ids or not event_methods:
        return []

    db = await get_db()
    effective_snapshot_id = (
        snapshot_id if snapshot_id is not None else await _get_current_snapshot_id(db)
    )
    match_placeholders = _sql_placeholders(match_ids)
    method_placeholders = _sql_placeholders(list(event_methods))
    q = f"""SELECT rem.*, b.name AS bookmaker_name, re.method AS event_method
            FROM resolved_event_members rem
            JOIN resolved_events re ON re.id = rem.resolved_event_id
            LEFT JOIN bookmakers b ON b.id = rem.bookmaker_id
            WHERE rem.match_id IN ({match_placeholders})
              AND rem.status = 'active'
              AND re.status = 'active'
              AND re.method IN ({method_placeholders})"""
    params: list[object] = [*match_ids, *event_methods]

    if bookmaker_ids:
        bookmaker_placeholders = _sql_placeholders(bookmaker_ids)
        q += f" AND rem.bookmaker_id IN ({bookmaker_placeholders})"
        params.extend(bookmaker_ids)

    order_params: list[object] = []
    if effective_snapshot_id is not None:
        q += " AND (rem.snapshot_id = ? OR rem.snapshot_id IS NULL)"
        params.append(effective_snapshot_id)
        snapshot_order = "CASE WHEN rem.snapshot_id = ? THEN 0 ELSE 1 END, "
        order_params.append(effective_snapshot_id)
    else:
        q += " AND rem.snapshot_id IS NULL"
        snapshot_order = ""

    q += f" ORDER BY {snapshot_order}re.start_time ASC, rem.resolved_event_id ASC, rem.id ASC"
    params.extend(order_params)
    rows = await db.execute_fetchall(q, params)
    chosen_by_key: dict[tuple[str, str], tuple[tuple[int, int, int], ResolvedEventMemberOut]] = {}
    key_order: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows):
        member = _row_to_resolved_event_member(row)
        key = (member.match_id, member.bookmaker_id)
        key_order.setdefault(key, index)
        rank = (
            0 if row["event_method"] in {"manual", "manual_review"} else 1,
            0
            if effective_snapshot_id is not None
            and member.snapshot_id == effective_snapshot_id
            else 1,
            index,
        )
        current = chosen_by_key.get(key)
        if current is None or rank < current[0]:
            chosen_by_key[key] = (rank, member)
    return [
        chosen_by_key[key][1]
        for key in sorted(key_order, key=lambda item: key_order[item])
    ]


async def get_eligible_resolved_event_members_for_odds(
    odds_list: list[NormalizedOdds],
    *,
    snapshot_id: str | None = None,
    event_methods: tuple[str, ...] = (
        "exact",
        "auto_fuzzy_high",
        "manual",
        "manual_review",
    ),
) -> list[ResolvedEventMemberOut]:
    match_ids = sorted({odds.match_id for odds in odds_list})
    bookmaker_ids = sorted({odds.bookmaker_id for odds in odds_list})
    return await get_eligible_resolved_event_members_for_matches(
        match_ids,
        bookmaker_ids=bookmaker_ids,
        snapshot_id=snapshot_id,
        event_methods=event_methods,
    )


async def get_eligible_resolved_event_members_for_outcome_offers(
    offers: list[NormalizedOutcomeOffer],
    *,
    snapshot_id: str | None = None,
    event_methods: tuple[str, ...] = (
        "exact",
        "auto_fuzzy_high",
        "manual",
        "manual_review",
    ),
) -> list[ResolvedEventMemberOut]:
    match_ids = sorted({offer.match_id for offer in offers})
    bookmaker_ids = sorted({offer.bookmaker_id for offer in offers})
    return await get_eligible_resolved_event_members_for_matches(
        match_ids,
        bookmaker_ids=bookmaker_ids,
        snapshot_id=snapshot_id,
        event_methods=event_methods,
    )


async def get_resolved_event_primary_match_ids(
    resolved_event_ids: list[str],
) -> dict[str, str]:
    event_ids = sorted(set(resolved_event_ids))
    if not event_ids:
        return {}

    db = await get_db()
    placeholders = _sql_placeholders(event_ids)
    rows = await db.execute_fetchall(
        f"""SELECT id, primary_match_id
            FROM resolved_events
            WHERE id IN ({placeholders})""",
        event_ids,
    )
    return {row["id"]: row["primary_match_id"] for row in rows}


async def get_resolved_event_member(
    *,
    match_id: str,
    bookmaker_id: str,
    snapshot_id: str | None = None,
) -> ResolvedEventMemberOut | None:
    db = await get_db()
    effective_snapshot_id = (
        snapshot_id if snapshot_id is not None else await _get_current_snapshot_id(db)
    )
    order_params: list[object] = []
    if effective_snapshot_id is not None:
        snapshot_filter = "AND (m.snapshot_id = ? OR m.snapshot_id IS NULL)"
        snapshot_params: list[object] = [effective_snapshot_id]
        snapshot_order = "CASE WHEN m.snapshot_id = ? THEN 0 ELSE 1 END, "
        order_params.append(effective_snapshot_id)
    else:
        snapshot_filter = "AND m.snapshot_id IS NULL"
        snapshot_params = []
        snapshot_order = ""
    rows = await db.execute_fetchall(
        f"""SELECT m.*, b.name AS bookmaker_name
           FROM resolved_event_members m
           JOIN resolved_events re ON re.id = m.resolved_event_id
           LEFT JOIN bookmakers b ON b.id = m.bookmaker_id
           WHERE m.match_id = ? AND m.bookmaker_id = ?
             {snapshot_filter}
           ORDER BY CASE WHEN re.method IN ('manual', 'manual_review') THEN 0 ELSE 1 END,
                    {snapshot_order}m.id ASC
           LIMIT 1""",
        [match_id, bookmaker_id, *snapshot_params, *order_params],
    )
    if not rows:
        return None
    return _row_to_resolved_event_member(rows[0])


async def get_resolved_event(
    resolved_event_id: str,
    *,
    include_members: bool = True,
) -> ResolvedEventOut | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM resolved_events WHERE id = ?",
        (resolved_event_id,),
    )
    if not rows:
        return None
    members = (
        await get_resolved_event_members(resolved_event_id)
        if include_members
        else []
    )
    return _row_to_resolved_event(rows[0], members=members)


async def _get_current_event_context(
    db: aiosqlite.Connection,
    resolved_event_id: str,
) -> tuple[ResolvedEventOut, list[ResolvedEventMemberOut], str | None] | None:
    event_rows = await db.execute_fetchall(
        "SELECT * FROM resolved_events WHERE id = ? AND status = 'active'",
        (resolved_event_id,),
    )
    if not event_rows:
        return None

    current_snapshot_id = await _get_current_snapshot_id(db)
    if current_snapshot_id is not None:
        if event_rows[0]["method"] in {"manual", "manual_review"}:
            snapshot_clause = "(m.snapshot_id = ? OR m.snapshot_id IS NULL)"
            snapshot_params: list[object] = [current_snapshot_id]
            snapshot_order = "CASE WHEN m.snapshot_id = ? THEN 0 ELSE 1 END, "
            order_params: list[object] = [current_snapshot_id]
        else:
            snapshot_clause = "m.snapshot_id = ?"
            snapshot_params = [current_snapshot_id]
            snapshot_order = ""
            order_params = []
    elif await _has_scrape_snapshots(db):
        return None
    else:
        snapshot_clause = "m.snapshot_id IS NULL"
        snapshot_params = []
        snapshot_order = ""
        order_params = []

    member_rows = await db.execute_fetchall(
        f"""SELECT m.*, b.name AS bookmaker_name
           FROM resolved_event_members m
           LEFT JOIN bookmakers b ON b.id = m.bookmaker_id
           WHERE m.resolved_event_id = ?
             AND m.status = 'active'
             AND {snapshot_clause}
           ORDER BY {snapshot_order}m.match_id ASC, b.name ASC, m.bookmaker_id ASC, m.id ASC""",
        [resolved_event_id, *snapshot_params, *order_params],
    )
    members: list[ResolvedEventMemberOut] = []
    seen_member_keys: set[tuple[str, str]] = set()
    for row in member_rows:
        member = _row_to_resolved_event_member(row)
        key = (member.match_id, member.bookmaker_id)
        if key in seen_member_keys:
            continue
        seen_member_keys.add(key)
        members.append(member)
    if not members:
        return None
    event = _row_to_resolved_event(event_rows[0], members=members)
    return event, members, current_snapshot_id


async def _get_event_odds_rows(
    db: aiosqlite.Connection,
    members: list[ResolvedEventMemberOut],
    *,
    current_snapshot_id: str | None,
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[aiosqlite.Row]:
    odds_filter, odds_params = await _current_or_legacy_snapshot_filter(
        db,
        "o",
        snapshot_id=current_snapshot_id,
    )
    if odds_filter is None:
        return []
    if not members:
        return []

    if current_snapshot_id is not None:
        source_snapshot_clause = "mbs.snapshot_id = ?"
        source_snapshot_params: list[object] = [current_snapshot_id]
    else:
        source_snapshot_clause = "mbs.snapshot_id IS NULL"
        source_snapshot_params = []

    pair_conditions: list[str] = []
    pair_params: list[object] = []
    for member in members:
        pair_conditions.append("(o.match_id = ? AND o.bookmaker_id = ?)")
        pair_params.extend([member.match_id, member.bookmaker_id])

    conditions = [odds_filter, f"({' OR '.join(pair_conditions)})"]
    params: list[object] = [
        current_snapshot_id,
        *source_snapshot_params,
        *odds_params,
        *pair_params,
    ]
    if bookmaker_ids:
        conditions.append(f"o.bookmaker_id IN ({_sql_placeholders(bookmaker_ids)})")
        params.extend(bookmaker_ids)
    if market_type:
        conditions.append("o.market_type = ?")
        params.append(market_type)

    pagination = ""
    if limit is not None:
        pagination = " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    where_clause = " AND ".join(conditions)
    return await db.execute_fetchall(
        f"""SELECT o.id,
                  o.match_id,
                  o.bookmaker_id,
                  b.name AS bookmaker_name,
                  mbs.source_url AS source_url,
                  o.market_type,
                  o.player_name,
                  o.threshold,
                  o.over_odds,
                  o.under_odds,
                  o.scraped_at,
                  COALESCE(sm.league_id, m.league_id, '') AS league_id,
                  COALESCE(sm.sport, m.sport, 'basketball') AS sport,
                  COALESCE(sm.home_team_id, m.home_team_id, 0) AS home_team_id,
                  COALESCE(sm.away_team_id, m.away_team_id, 0) AS away_team_id,
                  COALESCE(sm.home_team, m.home_team) AS home_team,
                  COALESCE(sm.away_team, m.away_team) AS away_team,
                  COALESCE(sm.start_time, m.start_time) AS start_time
           FROM odds o
           JOIN matches m ON m.id = o.match_id
           LEFT JOIN snapshot_matches sm
             ON sm.snapshot_id = ? AND sm.match_id = o.match_id
           LEFT JOIN bookmakers b ON b.id = o.bookmaker_id
           LEFT JOIN match_bookmaker_sources mbs
             ON mbs.match_id = o.match_id
            AND mbs.bookmaker_id = o.bookmaker_id
            AND {source_snapshot_clause}
          WHERE {where_clause}
          ORDER BY COALESCE(sm.start_time, m.start_time) ASC,
                   o.match_id ASC,
                   b.name ASC,
                   o.bookmaker_id ASC,
                   o.market_type ASC,
                   o.player_name ASC,
                   o.threshold ASC,
                   o.id ASC{pagination}""",
        params,
    )


def _event_odds_normalized(row: aiosqlite.Row) -> NormalizedOdds:
    data = _row_to_dict(row)
    return NormalizedOdds(
        match_id=data["match_id"],
        bookmaker_id=data["bookmaker_id"],
        league_id=data["league_id"],
        sport=data["sport"],
        home_team_id=data["home_team_id"],
        away_team_id=data["away_team_id"],
        home_team=data["home_team"],
        away_team=data["away_team"],
        source_url=data.get("source_url"),
        market_type=data["market_type"],
        player_name=data.get("player_name"),
        threshold=data["threshold"],
        over_odds=data.get("over_odds"),
        under_odds=data.get("under_odds"),
        start_time=data.get("start_time"),
        scraped_at=data.get("scraped_at"),
    )


def _build_event_scoped_odds(
    rows: list[aiosqlite.Row],
    members: list[ResolvedEventMemberOut],
    *,
    identity_rows: list[aiosqlite.Row] | None = None,
) -> tuple[list[EventOddsOut], list[EventPlayerOut]]:
    member_source_by_key = {
        (member.match_id, member.bookmaker_id): member.source_url for member in members
    }
    identity_normalized_rows: list[tuple[aiosqlite.Row, NormalizedOdds]] = [
        (row, _event_odds_normalized(row)) for row in (identity_rows or rows)
    ]
    output_normalized_rows: list[tuple[aiosqlite.Row, NormalizedOdds]] = [
        (row, _event_odds_normalized(row)) for row in rows
    ]
    row_id_by_normalized_id = {
        id(normalized): row["id"] for row, normalized in identity_normalized_rows
    }
    scoped_odds = build_event_scoped_player_odds(
        [normalized for _, normalized in identity_normalized_rows],
        members,
    )
    scoped_by_row_id = {
        row_id_by_normalized_id[id(scoped.odds)]: scoped for scoped in scoped_odds
    }

    odds_out: list[EventOddsOut] = []
    for row, _normalized in output_normalized_rows:
        data = _row_to_dict(row)
        source_url = data.get("source_url") or member_source_by_key.get(
            (data["match_id"], data["bookmaker_id"])
        )
        scoped = scoped_by_row_id.get(data["id"])
        odds_out.append(
            EventOddsOut(
                id=data["id"],
                match_id=data["match_id"],
                bookmaker_id=data["bookmaker_id"],
                bookmaker_name=data.get("bookmaker_name"),
                source_url=source_url,
                market_type=data["market_type"],
                player_name=data.get("player_name"),
                threshold=data["threshold"],
                over_odds=data.get("over_odds"),
                under_odds=data.get("under_odds"),
                scraped_at=data.get("scraped_at"),
                event_scoped_player_key=scoped.event_scoped_player_key
                if scoped
                else None,
                event_player_display_name=scoped.event_player_display_name
                if scoped
                else None,
            )
        )

    players = [
        EventPlayerOut(
            key=identity.event_scoped_player_key,
            display_name=identity.display_name,
            source_variants=list(identity.source_variants),
        )
        for identity in build_event_scoped_player_identities(scoped_odds)
    ]
    return odds_out, players


async def get_event_detail(resolved_event_id: str) -> EventDetailOut | None:
    db = await get_db()
    context = await _get_current_event_context(db, resolved_event_id)
    if context is None:
        return None
    event, members, current_snapshot_id = context
    odds_rows = await _get_event_odds_rows(
        db,
        members,
        current_snapshot_id=current_snapshot_id,
    )
    _, players = _build_event_scoped_odds(odds_rows, members)
    return EventDetailOut(**event.model_dump(), players=players)


async def get_event_odds(
    resolved_event_id: str,
    *,
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    limit: int = 5000,
    offset: int = 0,
) -> list[EventOddsOut] | None:
    db = await get_db()
    context = await _get_current_event_context(db, resolved_event_id)
    if context is None:
        return None
    _event, members, current_snapshot_id = context
    rows = await _get_event_odds_rows(
        db,
        members,
        current_snapshot_id=current_snapshot_id,
        bookmaker_ids=bookmaker_ids,
        market_type=market_type,
        limit=limit,
        offset=offset,
    )
    identity_rows = await _get_event_odds_rows(
        db,
        members,
        current_snapshot_id=current_snapshot_id,
    )
    odds, _players = _build_event_scoped_odds(
        rows,
        members,
        identity_rows=identity_rows,
    )
    return odds


async def get_event_outcome_offers(
    resolved_event_id: str,
    *,
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    limit: int = 5000,
    offset: int = 0,
) -> list[OutcomeOfferOut] | None:
    db = await get_db()
    context = await _get_current_event_context(db, resolved_event_id)
    if context is None:
        return None
    _event, members, current_snapshot_id = context

    offer_filter, offer_params = await _current_or_legacy_snapshot_filter(
        db,
        "oo",
        snapshot_id=current_snapshot_id,
    )
    if offer_filter is None:
        return []

    if current_snapshot_id is not None:
        source_snapshot_clause = "mbs.snapshot_id = ?"
        source_snapshot_params: list[object] = [current_snapshot_id]
    else:
        source_snapshot_clause = "mbs.snapshot_id IS NULL"
        source_snapshot_params = []

    pair_conditions: list[str] = []
    pair_params: list[object] = []
    member_source_by_key = {
        (member.match_id, member.bookmaker_id): member.source_url for member in members
    }
    for member in members:
        pair_conditions.append("(oo.match_id = ? AND oo.bookmaker_id = ?)")
        pair_params.extend([member.match_id, member.bookmaker_id])

    conditions = [offer_filter, f"({' OR '.join(pair_conditions)})"]
    params: list[object] = [
        *source_snapshot_params,
        *offer_params,
        *pair_params,
    ]
    if bookmaker_ids:
        conditions.append(f"oo.bookmaker_id IN ({_sql_placeholders(bookmaker_ids)})")
        params.extend(bookmaker_ids)
    if market_type:
        conditions.append("oo.market_type = ?")
        params.append(market_type)
    params.extend([limit, offset])
    where_clause = " AND ".join(conditions)
    rows = await db.execute_fetchall(
        f"""SELECT oo.id,
                  oo.match_id,
                  oo.bookmaker_id,
                  b.name AS bookmaker_name,
                  mbs.source_url AS source_url,
                  oo.market_type,
                  oo.outcome_code,
                  oo.odds,
                  oo.line,
                  oo.raw_label,
                  oo.scraped_at
           FROM outcome_offers oo
           JOIN matches m ON m.id = oo.match_id
           LEFT JOIN bookmakers b ON b.id = oo.bookmaker_id
           LEFT JOIN match_bookmaker_sources mbs
             ON mbs.match_id = oo.match_id
            AND mbs.bookmaker_id = oo.bookmaker_id
            AND {source_snapshot_clause}
          WHERE {where_clause}
          ORDER BY oo.match_id ASC,
                   b.name ASC,
                   oo.bookmaker_id ASC,
                   oo.market_type ASC,
                   oo.line ASC,
                   oo.outcome_code ASC,
                   oo.id ASC
          LIMIT ? OFFSET ?""",
        params,
    )
    offers: list[OutcomeOfferOut] = []
    for row in rows:
        data = _row_to_dict(row)
        data["source_url"] = data.get("source_url") or member_source_by_key.get(
            (data["match_id"], data["bookmaker_id"])
        )
        offers.append(OutcomeOfferOut(**data))
    return offers


async def list_resolved_events(
    *,
    sport: str | None = None,
    status: str | None = None,
    start_time: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ResolvedEventOut]:
    db = await get_db()
    q = "SELECT * FROM resolved_events"
    conditions: list[str] = []
    params: list[object] = []
    if sport:
        conditions.append("sport = ?")
        params.append(sport)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if start_time:
        conditions.append("start_time = ?")
        params.append(start_time)
    if conditions:
        q += " WHERE " + " AND ".join(conditions)
    q += " ORDER BY start_time ASC, id ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    return [_row_to_resolved_event(row, members=[]) for row in rows]


async def upsert_event_review_case(case: EventReviewCaseIn) -> int:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO event_review_cases (
                   fingerprint,
                   sport,
                   start_time,
                   primary_match_id,
                   candidate_resolved_event_id,
                   candidate_match_ids,
                   reason_code,
                   confidence,
                   method,
                   source_bookmaker_ids,
                   source_league_labels,
                   evidence,
                   metadata,
                   status
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                   sport = excluded.sport,
                   start_time = excluded.start_time,
                   primary_match_id = excluded.primary_match_id,
                   candidate_resolved_event_id = excluded.candidate_resolved_event_id,
                   candidate_match_ids = excluded.candidate_match_ids,
                   reason_code = excluded.reason_code,
                   confidence = excluded.confidence,
                   method = excluded.method,
                   source_bookmaker_ids = excluded.source_bookmaker_ids,
                   source_league_labels = excluded.source_league_labels,
                   evidence = excluded.evidence,
                   metadata = excluded.metadata,
                   status = CASE
                       WHEN event_review_cases.status IN ('accepted', 'declined')
                       THEN event_review_cases.status
                       ELSE excluded.status
                   END,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                case.fingerprint,
                case.sport,
                case.start_time,
                case.primary_match_id,
                case.candidate_resolved_event_id,
                json.dumps(case.candidate_match_ids),
                case.reason_code,
                case.confidence,
                case.method,
                json.dumps(case.source_bookmaker_ids),
                json.dumps(case.source_league_labels),
                json.dumps(case.evidence),
                json.dumps(case.metadata),
                case.status,
            ),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    rows = await db.execute_fetchall(
        "SELECT id FROM event_review_cases WHERE fingerprint = ?",
        (case.fingerprint,),
    )
    return int(rows[0]["id"]) if rows else 0


def _event_review_case_values(case: EventReviewCaseIn) -> tuple:
    return (
        case.fingerprint,
        case.sport,
        case.start_time,
        case.primary_match_id,
        case.candidate_resolved_event_id,
        json.dumps(case.candidate_match_ids),
        case.reason_code,
        case.confidence,
        case.method,
        json.dumps(case.source_bookmaker_ids),
        json.dumps(case.source_league_labels),
        json.dumps(case.evidence),
        json.dumps(case.metadata),
        case.status,
    )


async def _delete_stale_generated_football_ambiguous_review_cases_tx(
    db: aiosqlite.Connection,
    *,
    events: list[ResolvedEventIn],
    review_cases: list[EventReviewCaseIn],
) -> int:
    has_current_football_resolution = any(event.sport == "football" for event in events) or any(
        case.sport == "football" for case in review_cases
    )
    if not has_current_football_resolution:
        return 0

    current_fingerprints = [
        case.fingerprint
        for case in review_cases
        if case.sport == "football"
        and case.method == "auto_candidate"
        and case.reason_code == "ambiguous_event_orientation"
    ]
    params: list[object] = []
    query = """DELETE FROM event_review_cases
               WHERE sport = 'football'
                 AND status = 'pending'
                 AND method = 'auto_candidate'
                 AND reason_code = 'ambiguous_event_orientation'"""
    if current_fingerprints:
        placeholders = _sql_placeholders(current_fingerprints)
        query += f" AND fingerprint NOT IN ({placeholders})"
        params.extend(current_fingerprints)

    cursor = await db.execute(query, params)
    return cursor.rowcount or 0


async def persist_event_resolution_batch(
    *,
    snapshot_id: str | None = None,
    events: list[ResolvedEventIn],
    members: list[ResolvedEventMemberIn],
    review_cases: list[EventReviewCaseIn],
) -> dict[str, int]:
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.executemany(
            """INSERT INTO resolved_events (
                   id,
                   sport,
                   start_time,
                   primary_match_id,
                   status,
                   confidence,
                   method,
                   display_home_team,
                   display_away_team,
                   display_league_name,
                   metadata
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   sport = excluded.sport,
                   start_time = excluded.start_time,
                   primary_match_id = excluded.primary_match_id,
                   status = excluded.status,
                   confidence = excluded.confidence,
                   method = excluded.method,
                   display_home_team = excluded.display_home_team,
                   display_away_team = excluded.display_away_team,
                   display_league_name = excluded.display_league_name,
                   metadata = excluded.metadata,
                   updated_at = CURRENT_TIMESTAMP""",
            [
                (
                    event.id or f"evt_{uuid.uuid4().hex}",
                    event.sport,
                    event.start_time,
                    event.primary_match_id,
                    event.status,
                    event.confidence,
                    event.method,
                    event.display_home_team,
                    event.display_away_team,
                    event.display_league_name,
                    json.dumps(event.metadata),
                )
                for event in events
            ],
        )
        await db.executemany(
            """INSERT INTO resolved_event_members (
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
                   metadata
                )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(
                   COALESCE(snapshot_id, ''),
                   match_id,
                   bookmaker_id
               ) DO UPDATE SET
                   resolved_event_id = excluded.resolved_event_id,
                   orientation = excluded.orientation,
                   confidence = excluded.confidence,
                   status = excluded.status,
                   source_url = COALESCE(excluded.source_url, resolved_event_members.source_url),
                   source_league_id = COALESCE(
                       excluded.source_league_id,
                       resolved_event_members.source_league_id
                   ),
                   source_league_name = COALESCE(
                       excluded.source_league_name,
                       resolved_event_members.source_league_name
                   ),
                   source_home_team = COALESCE(
                       excluded.source_home_team,
                       resolved_event_members.source_home_team
                   ),
                   source_away_team = COALESCE(
                       excluded.source_away_team,
                       resolved_event_members.source_away_team
                   ),
                   source_start_time = COALESCE(
                       excluded.source_start_time,
                       resolved_event_members.source_start_time
                   ),
                   evidence = excluded.evidence,
                   metadata = excluded.metadata,
                   updated_at = CURRENT_TIMESTAMP
               WHERE NOT (
                   EXISTS (
                       SELECT 1
                       FROM resolved_events existing_event
                       WHERE existing_event.id = resolved_event_members.resolved_event_id
                         AND existing_event.status = 'active'
                         AND existing_event.method IN ('manual', 'manual_review')
                   )
                   AND NOT EXISTS (
                       SELECT 1
                       FROM resolved_events incoming_event
                       WHERE incoming_event.id = excluded.resolved_event_id
                         AND incoming_event.method IN ('manual', 'manual_review')
                   )
               )""",
            [
                (
                    member.snapshot_id or snapshot_id,
                    member.resolved_event_id,
                    member.match_id,
                    member.bookmaker_id,
                    member.orientation,
                    member.confidence,
                    member.status,
                    member.source_url,
                    member.source_league_id,
                    member.source_league_name,
                    member.source_home_team,
                    member.source_away_team,
                    member.source_start_time,
                    json.dumps(member.evidence),
                    json.dumps(member.metadata),
                )
                for member in members
            ],
        )
        await db.executemany(
            """INSERT INTO match_bookmaker_sources (snapshot_id, match_id, bookmaker_id, source_url)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(COALESCE(snapshot_id, ''), match_id, bookmaker_id) DO UPDATE SET
                    source_url = COALESCE(excluded.source_url, match_bookmaker_sources.source_url),
                    updated_at = CURRENT_TIMESTAMP""",
            [
                (
                    member.snapshot_id or snapshot_id,
                    member.match_id,
                    member.bookmaker_id,
                    member.source_url,
                )
                for member in members
                if member.source_url is not None
            ],
        )
        await db.executemany(
            """INSERT INTO event_review_cases (
                   fingerprint,
                   sport,
                   start_time,
                   primary_match_id,
                   candidate_resolved_event_id,
                   candidate_match_ids,
                   reason_code,
                   confidence,
                   method,
                   source_bookmaker_ids,
                   source_league_labels,
                   evidence,
                   metadata,
                   status
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                   sport = excluded.sport,
                   start_time = excluded.start_time,
                   primary_match_id = excluded.primary_match_id,
                   candidate_resolved_event_id = excluded.candidate_resolved_event_id,
                   candidate_match_ids = excluded.candidate_match_ids,
                   reason_code = excluded.reason_code,
                   confidence = excluded.confidence,
                   method = excluded.method,
                   source_bookmaker_ids = excluded.source_bookmaker_ids,
                   source_league_labels = excluded.source_league_labels,
                   evidence = excluded.evidence,
                   metadata = excluded.metadata,
                   status = CASE
                       WHEN event_review_cases.status IN ('accepted', 'declined')
                       THEN event_review_cases.status
                       ELSE excluded.status
                   END,
                   updated_at = CURRENT_TIMESTAMP""",
            [_event_review_case_values(case) for case in review_cases],
        )
        await _delete_stale_generated_football_ambiguous_review_cases_tx(
            db,
            events=events,
            review_cases=review_cases,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {
        "resolved_events": len(events),
        "resolved_event_members": len(members),
        "review_cases": len(review_cases),
    }


async def get_event_review_case(
    case_id: int,
    *,
    include_variants: bool = False,
) -> EventReviewCaseOut | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM event_review_cases WHERE id = ?",
        (case_id,),
    )
    if not rows:
        return None
    case = _row_to_event_review_case(rows[0])
    if include_variants:
        return await _hydrate_event_review_case(db, case)
    return case


async def get_event_review_case_by_fingerprint(
    fingerprint: str,
    *,
    statuses: list[str] | None = None,
) -> EventReviewCaseOut | None:
    db = await get_db()
    q = "SELECT * FROM event_review_cases WHERE fingerprint = ?"
    params: list[object] = [fingerprint]
    if statuses:
        placeholders = _sql_placeholders(statuses)
        q += f" AND status IN ({placeholders})"
        params.extend(statuses)
    rows = await db.execute_fetchall(q, params)
    if not rows:
        return None
    return _row_to_event_review_case(rows[0])


async def get_event_review_case_variants(
    case: EventReviewCaseOut,
) -> list[EventReviewVariantOut]:
    db = await get_db()
    return await _get_event_review_case_variants_tx(db, case)


async def _get_event_review_case_variants_tx(
    db: aiosqlite.Connection,
    case: EventReviewCaseOut,
) -> list[EventReviewVariantOut]:
    variants: list[EventReviewVariantOut] = []
    seen: set[tuple[str, str | None]] = set()
    current_snapshot_id = await _get_current_snapshot_id(db)
    has_snapshot_mode = await _has_scrape_snapshots(db)

    resolved_event_ids = list(
        dict.fromkeys(
            event_id
            for event_id in (case.resolved_event_id, case.candidate_resolved_event_id)
            if event_id
        )
    )
    for resolved_event_id in resolved_event_ids:
        if current_snapshot_id is not None:
            member_snapshot_clause = "AND rem.snapshot_id = ?"
            member_params: list[object] = [current_snapshot_id]
            member_order = ""
            member_order_params: list[object] = []
            member_metadata_clause = "AND sm.match_id IS NOT NULL"
        elif has_snapshot_mode:
            member_snapshot_clause = "AND 1 = 0"
            member_params = []
            member_order = ""
            member_order_params = []
            member_metadata_clause = ""
        else:
            member_snapshot_clause = "AND rem.snapshot_id IS NULL"
            member_params = []
            member_order = ""
            member_order_params = []
            member_metadata_clause = ""
        rows = await db.execute_fetchall(
            f"""SELECT rem.match_id,
                      rem.bookmaker_id,
                      b.name AS bookmaker_name,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END AS league_id,
                       l.name AS league_name,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                       CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time,
                       rem.source_url,
                      rem.source_league_id,
                      rem.source_league_name,
                      rem.source_home_team,
                      rem.source_away_team,
                      rem.source_start_time,
                      rem.orientation,
                      rem.confidence AS member_confidence,
                      rem.evidence AS member_evidence
               FROM resolved_event_members rem
               LEFT JOIN bookmakers b ON b.id = rem.bookmaker_id
               LEFT JOIN matches m ON m.id = rem.match_id
               LEFT JOIN snapshot_matches sm
                 ON sm.snapshot_id = ? AND sm.match_id = m.id
               LEFT JOIN leagues l
                 ON l.id = CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END
               WHERE rem.resolved_event_id = ?
                 {member_snapshot_clause}
                 {member_metadata_clause}
               ORDER BY CASE
                   WHEN EXISTS (
                       SELECT 1
                       FROM resolved_events re
                       WHERE re.id = rem.resolved_event_id
                         AND re.method IN ('manual', 'manual_review')
                   ) THEN 0
                   ELSE 1
               END,
               {member_order}
               rem.id ASC""",
            [
                current_snapshot_id,
                resolved_event_id,
                *member_params,
                *member_order_params,
            ],
        )
        for row in rows:
            key = (row["match_id"], row["bookmaker_id"])
            if key in seen:
                continue
            seen.add(key)
            variants.append(_row_to_event_review_variant(row))

    candidate_match_ids = list(
        dict.fromkeys(
            match_id
            for match_id in ([case.primary_match_id] if case.primary_match_id else [])
            + case.candidate_match_ids
            if match_id
        )
    )
    if candidate_match_ids and not (has_snapshot_mode and current_snapshot_id is None):
        placeholders = _sql_placeholders(candidate_match_ids)
        if current_snapshot_id is not None:
            source_snapshot_clause = "AND s.snapshot_id = ?"
            source_params: list[object] = [current_snapshot_id]
            match_metadata_clause = "AND sm.match_id IS NOT NULL"
        elif await _has_scrape_snapshots(db):
            source_snapshot_clause = "AND 1 = 0"
            source_params = []
            match_metadata_clause = ""
        else:
            source_snapshot_clause = "AND s.snapshot_id IS NULL"
            source_params = []
            match_metadata_clause = ""
        rows = await db.execute_fetchall(
            f"""SELECT m.id AS match_id,
                        s.bookmaker_id,
                        b.name AS bookmaker_name,
                        CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END AS league_id,
                        l.name AS league_name,
                        CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                        CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                        CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time,
                        s.source_url,
                        NULL AS source_league_id,
                        l.name AS source_league_name,
                        CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS source_home_team,
                        CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS source_away_team,
                        CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS source_start_time,
                        'as_listed' AS orientation,
                        NULL AS member_confidence,
                        '[]' AS member_evidence
                 FROM matches m
                 LEFT JOIN snapshot_matches sm
                   ON sm.snapshot_id = ? AND sm.match_id = m.id
                 LEFT JOIN leagues l
                   ON l.id = CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END
                 LEFT JOIN match_bookmaker_sources s
                   ON s.match_id = m.id
                  {source_snapshot_clause}
                 LEFT JOIN bookmakers b ON b.id = s.bookmaker_id
                 WHERE m.id IN ({placeholders})
                   {match_metadata_clause}
                 ORDER BY CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END ASC,
                          m.id ASC, s.bookmaker_id ASC,
                          s.id ASC""",
            [current_snapshot_id, *source_params, *candidate_match_ids],
        )
        for row in rows:
            key = (row["match_id"], row["bookmaker_id"])
            if key in seen:
                continue
            seen.add(key)
            variants.append(_row_to_event_review_variant(row))

        source_bookmaker_ids = list(dict.fromkeys(case.source_bookmaker_ids))
        metadata_pairs = [
            pair
            for pair in _event_review_source_variant_pairs(case)
            if pair[0] in candidate_match_ids
        ]
        fallback_pairs: list[tuple[str, str]] = []
        if metadata_pairs:
            fallback_pairs = metadata_pairs
        elif len(candidate_match_ids) == 1:
            fallback_pairs = [
                (candidate_match_ids[0], bookmaker_id)
                for bookmaker_id in source_bookmaker_ids
            ]

        if fallback_pairs:
            fallback_bookmaker_ids = list(
                dict.fromkeys([bookmaker_id for _, bookmaker_id in fallback_pairs])
            )
            bookmaker_placeholders = _sql_placeholders(fallback_bookmaker_ids)
            bookmaker_rows = await db.execute_fetchall(
                f"SELECT id, name FROM bookmakers WHERE id IN ({bookmaker_placeholders})",
                fallback_bookmaker_ids,
            )
            bookmaker_names = {row["id"]: row["name"] for row in bookmaker_rows}
            match_rows = await db.execute_fetchall(
                f"""SELECT m.id AS match_id,
                           CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END AS league_id,
                           l.name AS league_name,
                           CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                           CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                           CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time
                    FROM matches m
                    LEFT JOIN snapshot_matches sm
                      ON sm.snapshot_id = ? AND sm.match_id = m.id
                    LEFT JOIN leagues l
                      ON l.id = CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END
                    WHERE m.id IN ({placeholders})
                      {match_metadata_clause}""",
                [current_snapshot_id, *candidate_match_ids],
            )
            match_map = {row["match_id"]: _row_to_dict(row) for row in match_rows}

            for match_id, bookmaker_id in fallback_pairs:
                key = (match_id, bookmaker_id)
                if key in seen or match_id not in match_map:
                    continue
                seen.add(key)
                match_data = match_map[match_id]
                variants.append(
                    EventReviewVariantOut(
                        match_id=match_id,
                        bookmaker_id=bookmaker_id,
                        bookmaker_name=bookmaker_names.get(bookmaker_id),
                        league_id=match_data.get("league_id"),
                        league_name=match_data.get("league_name"),
                        home_team=match_data["home_team"],
                        away_team=match_data["away_team"],
                        start_time=match_data.get("start_time"),
                        source_league_name=match_data.get("league_name"),
                        source_home_team=match_data["home_team"],
                        source_away_team=match_data["away_team"],
                        source_start_time=match_data.get("start_time"),
                    )
                )

    if candidate_match_ids:
        match_order = {match_id: index for index, match_id in enumerate(candidate_match_ids)}
        variants.sort(
            key=lambda variant: (
                match_order.get(variant.match_id, len(match_order)),
                variant.bookmaker_id or "",
            )
        )

    return variants


async def _hydrate_event_review_case(
    db: aiosqlite.Connection,
    case: EventReviewCaseOut,
) -> EventReviewCaseOut:
    variants = await _get_event_review_case_variants_tx(db, case)
    primary_variant = next(
        (variant for variant in variants if variant.match_id == case.primary_match_id),
        variants[0] if variants else None,
    )
    return case.model_copy(
        update={
            "variants": variants,
            "primary_home_team": (
                primary_variant.source_home_team or primary_variant.home_team
                if primary_variant
                else None
            ),
            "primary_away_team": (
                primary_variant.source_away_team or primary_variant.away_team
                if primary_variant
                else None
            ),
            "primary_league_name": (
                primary_variant.source_league_name or primary_variant.league_name
                if primary_variant
                else None
            ),
        }
    )


async def list_event_review_cases(
    *,
    sport: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_variants: bool = False,
) -> list[EventReviewCaseOut]:
    db = await get_db()
    q = "SELECT * FROM event_review_cases"
    conditions: list[str] = []
    params: list[object] = []
    if sport:
        conditions.append("sport = ?")
        params.append(sport)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if conditions:
        q += " WHERE " + " AND ".join(conditions)
    q += " ORDER BY status ASC, start_time ASC, id ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    cases = [_row_to_event_review_case(row) for row in rows]
    if not include_variants:
        return cases
    return [await _hydrate_event_review_case(db, case) for case in cases]


async def mark_event_review_case_accepted(
    case_id: int,
    *,
    resolved_event_id: str | None = None,
) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE event_review_cases
           SET status = 'accepted',
               resolved_event_id = COALESCE(?, resolved_event_id),
               accepted_at = COALESCE(accepted_at, CURRENT_TIMESTAMP),
               declined_at = NULL,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (resolved_event_id, case_id),
    )
    await db.commit()


async def mark_event_review_case_declined(case_id: int) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE event_review_cases
           SET status = 'declined',
               resolved_event_id = NULL,
               accepted_at = NULL,
               declined_at = COALESCE(declined_at, CURRENT_TIMESTAMP),
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (case_id,),
    )
    await db.commit()


async def merge_matches(
    *,
    target_match_id: str,
    source_match_ids: list[str],
) -> dict[str, int]:
    """Reassign match-scoped rows from source matches to target, deduping
    UNIQUE-constrained tables before deleting source match rows. All in a single
    transaction."""
    if not source_match_ids:
        return {
            "reassigned_odds": 0,
            "reassigned_odds_history": 0,
            "reassigned_outcome_offers": 0,
            "reassigned_opportunities": 0,
            "deleted_source_matches": 0,
        }
    if target_match_id in source_match_ids:
        raise ValueError("target_match_id cannot be in source_match_ids")

    db = await get_db()
    placeholders = _sql_placeholders(source_match_ids)
    params: list[object] = list(source_match_ids)

    await db.execute("BEGIN IMMEDIATE")
    try:
        # 1. Find every odds row across (target + sources) that would collide on the
        #    post-merge UNIQUE(match_id, bookmaker_id, market_type, player_name, threshold)
        #    key once all source rows are reassigned to target_match_id. This must
        #    detect collisions both source↔target AND source↔source (otherwise the
        #    UPDATE in step 2 trips the UNIQUE constraint).
        all_match_ids = [target_match_id, *source_match_ids]
        all_placeholders = _sql_placeholders(all_match_ids)
        rows = await db.execute_fetchall(
            f"""
            SELECT id,
                   COALESCE(snapshot_id, '') AS snapshot_key,
                   bookmaker_id,
                   market_type,
                   player_name,
                   threshold
            FROM odds
            WHERE match_id IN ({all_placeholders})
            """,
            all_match_ids,
        )

        groups: dict[tuple, list[int]] = {}
        for row in rows:
            key = (
                row["snapshot_key"],
                row["bookmaker_id"],
                row["market_type"],
                row["player_name"],
                row["threshold"],
            )
            groups.setdefault(key, []).append(row["id"])

        ids_to_delete: list[int] = []
        for ids in groups.values():
            if len(ids) <= 1:
                continue
            winner = max(ids)
            ids_to_delete.extend(i for i in ids if i != winner)

        if ids_to_delete:
            del_placeholders = _sql_placeholders(ids_to_delete)
            await db.execute(
                f"DELETE FROM odds WHERE id IN ({del_placeholders})",
                ids_to_delete,
            )

        # 2. Reassign remaining source odds rows to the target match_id.
        reassigned_odds_cur = await db.execute(
            f"UPDATE odds SET match_id = ? WHERE match_id IN ({placeholders})",
            [target_match_id, *params],
        )
        reassigned_odds = reassigned_odds_cur.rowcount or 0

        # 3. odds_history has no UNIQUE constraint - bulk update.
        reassigned_history_cur = await db.execute(
            f"UPDATE odds_history SET match_id = ? WHERE match_id IN ({placeholders})",
            [target_match_id, *params],
        )
        reassigned_history = reassigned_history_cur.rowcount or 0

        # 4. outcome_offers has a UNIQUE expression index on
        #    (match_id, bookmaker_id, market_type, outcome_code, COALESCE(line)).
        #    Dedupe source↔target and source↔source collisions before updating.
        outcome_rows = await db.execute_fetchall(
            f"""
            SELECT id,
                   COALESCE(snapshot_id, '') AS snapshot_key,
                   bookmaker_id,
                   market_type,
                   outcome_code,
                   COALESCE(line, -999999.0) AS line_key
            FROM outcome_offers
            WHERE match_id IN ({all_placeholders})
            """,
            all_match_ids,
        )

        outcome_groups: dict[tuple, list[int]] = {}
        for row in outcome_rows:
            key = (
                row["snapshot_key"],
                row["bookmaker_id"],
                row["market_type"],
                row["outcome_code"],
                row["line_key"],
            )
            outcome_groups.setdefault(key, []).append(row["id"])

        outcome_ids_to_delete: list[int] = []
        for ids in outcome_groups.values():
            if len(ids) <= 1:
                continue
            winner = max(ids)
            outcome_ids_to_delete.extend(i for i in ids if i != winner)

        if outcome_ids_to_delete:
            outcome_del_placeholders = _sql_placeholders(outcome_ids_to_delete)
            await db.execute(
                f"DELETE FROM outcome_offers WHERE id IN ({outcome_del_placeholders})",
                outcome_ids_to_delete,
            )

        reassigned_outcome_cur = await db.execute(
            f"UPDATE outcome_offers SET match_id = ? WHERE match_id IN ({placeholders})",
            [target_match_id, *params],
        )
        reassigned_outcome_offers = reassigned_outcome_cur.rowcount or 0

        # 5. match_bookmaker_sources is keyed by snapshot, match, and bookmaker,
        #    so it needs the same dedupe-before-update treatment as odds.
        source_rows = await db.execute_fetchall(
            f"""
            SELECT id, snapshot_id, match_id, bookmaker_id, source_url
            FROM match_bookmaker_sources
            WHERE match_id IN ({all_placeholders})
            """,
            all_match_ids,
        )

        source_groups: dict[tuple[str, str], list[aiosqlite.Row]] = {}
        for row in source_rows:
            source_groups.setdefault(
                (
                    str(row["snapshot_id"] or ""),
                    str(row["bookmaker_id"]),
                ),
                [],
            ).append(row)

        source_ids_to_delete: list[int] = []
        for grouped_rows in source_groups.values():
            if len(grouped_rows) <= 1:
                continue
            winner = max(
                grouped_rows,
                key=lambda row: (
                    1 if row["source_url"] else 0,
                    1 if row["match_id"] == target_match_id else 0,
                    int(row["id"]),
                ),
            )
            source_ids_to_delete.extend(
                int(row["id"]) for row in grouped_rows if row["id"] != winner["id"]
            )

        if source_ids_to_delete:
            source_del_placeholders = _sql_placeholders(source_ids_to_delete)
            await db.execute(
                f"DELETE FROM match_bookmaker_sources WHERE id IN ({source_del_placeholders})",
                source_ids_to_delete,
            )

        await db.execute(
            f"UPDATE match_bookmaker_sources SET match_id = ? WHERE match_id IN ({placeholders})",
            [target_match_id, *params],
        )

        # 6. Snapshot match metadata is keyed by (snapshot_id, match_id), so source
        #    rows need the same dedupe-before-update treatment before deleting
        #    source matches.
        await db.execute(
            f"""DELETE FROM snapshot_matches
                WHERE match_id IN ({placeholders})
                  AND EXISTS (
                      SELECT 1
                      FROM snapshot_matches target
                      WHERE target.snapshot_id = snapshot_matches.snapshot_id
                        AND target.match_id = ?
                  )""",
            [*params, target_match_id],
        )
        await db.execute(
            f"""DELETE FROM snapshot_matches
                WHERE match_id IN ({placeholders})
                  AND rowid NOT IN (
                      SELECT MAX(rowid)
                      FROM snapshot_matches
                      WHERE match_id IN ({placeholders})
                      GROUP BY snapshot_id
                  )""",
            [*params, *params],
        )
        await db.execute(
            f"""UPDATE snapshot_matches
                SET match_id = ?,
                    league_id = (SELECT league_id FROM matches WHERE id = ?),
                    sport = (SELECT sport FROM matches WHERE id = ?),
                    home_team_id = (SELECT home_team_id FROM matches WHERE id = ?),
                    away_team_id = (SELECT away_team_id FROM matches WHERE id = ?),
                    home_team = (SELECT home_team FROM matches WHERE id = ?),
                    away_team = (SELECT away_team FROM matches WHERE id = ?),
                    start_time = (SELECT start_time FROM matches WHERE id = ?),
                    status = (SELECT status FROM matches WHERE id = ?)
                WHERE match_id IN ({placeholders})""",
            [
                target_match_id,
                target_match_id,
                target_match_id,
                target_match_id,
                target_match_id,
                target_match_id,
                target_match_id,
                target_match_id,
                target_match_id,
                *params,
            ],
        )

        # 7. opportunities: bulk update; no UNIQUE constraint. Active duplicates
        #    are deactivated on the next opportunity analysis cycle.
        reassigned_opportunities_cur = await db.execute(
            f"UPDATE opportunities SET match_id = ? WHERE match_id IN ({placeholders})",
            [target_match_id, *params],
        )
        reassigned_opportunities = reassigned_opportunities_cur.rowcount or 0

        # 8. Delete the now-empty source match rows.
        deleted_cur = await db.execute(
            f"DELETE FROM matches WHERE id IN ({placeholders})",
            params,
        )
        deleted_matches = deleted_cur.rowcount or 0

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "reassigned_odds": reassigned_odds,
        "reassigned_odds_history": reassigned_history,
        "reassigned_outcome_offers": reassigned_outcome_offers,
        "reassigned_opportunities": reassigned_opportunities,
        "deleted_source_matches": deleted_matches,
    }


async def _get_match_bookmaker_map(
    db: aiosqlite.Connection,
    match_ids: list[str],
    *,
    snapshot_id: str | None,
    snapshot_at: str | None,
    cutoff_at: str | None,
) -> dict[str, list[MatchBookmakerOut]]:
    if not match_ids:
        return {}

    placeholders = _sql_placeholders(match_ids)
    params: list[object] = list(match_ids)

    odds_filter = ""
    offers_filter = ""
    if snapshot_id is not None:
        odds_filter = " AND o.snapshot_id = ?"
        offers_filter = " AND oo.snapshot_id = ?"
    elif snapshot_at is not None:
        odds_filter = " AND o.scraped_at = ?"
        offers_filter = " AND oo.scraped_at = ?"
    elif cutoff_at is not None:
        odds_filter = " AND o.scraped_at >= ?"
        offers_filter = " AND oo.scraped_at >= ?"

    q = f"""SELECT DISTINCT src.match_id, b.id AS bookmaker_id, b.name AS bookmaker_name
            FROM (
                SELECT o.match_id, o.bookmaker_id
                FROM odds o
                WHERE o.match_id IN ({placeholders}){odds_filter}
                UNION
                SELECT oo.match_id, oo.bookmaker_id
                FROM outcome_offers oo
                WHERE oo.match_id IN ({placeholders}){offers_filter}
            ) src
            LEFT JOIN bookmakers b ON src.bookmaker_id = b.id
            ORDER BY b.name ASC"""

    if snapshot_id is not None:
        params.append(snapshot_id)
    elif snapshot_at is not None:
        params.append(snapshot_at)
    elif cutoff_at is not None:
        params.append(cutoff_at)
    params.extend(match_ids)
    if snapshot_id is not None:
        params.append(snapshot_id)
    elif snapshot_at is not None:
        params.append(snapshot_at)
    elif cutoff_at is not None:
        params.append(cutoff_at)
    rows = await db.execute_fetchall(q, params)

    bookmaker_map: dict[str, list[MatchBookmakerOut]] = {}
    for row in rows:
        match_id = row["match_id"]
        bookmaker_map.setdefault(match_id, []).append(
            MatchBookmakerOut(
                id=row["bookmaker_id"],
                name=row["bookmaker_name"],
            )
        )
    return bookmaker_map


async def get_current_normalized_odds_for_matches(
    match_ids: list[str],
    *,
    snapshot_id: str | None = None,
) -> list[NormalizedOdds]:
    selected_match_ids = list(dict.fromkeys(match_ids))
    if not selected_match_ids:
        return []

    db = await get_db()
    metadata_snapshot_id = snapshot_id or await _get_current_snapshot_id(db)
    if snapshot_id is None:
        snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    else:
        snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(
            db, "o", snapshot_id=snapshot_id
        )
    if snapshot_filter is None:
        return []

    return await _get_normalized_odds_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
        metadata_snapshot_id=metadata_snapshot_id,
    )


async def _get_normalized_odds_for_matches_snapshot(
    db: aiosqlite.Connection,
    selected_match_ids: list[str],
    *,
    snapshot_filter: str,
    snapshot_params: list[object],
    metadata_snapshot_id: str | None = None,
) -> list[NormalizedOdds]:
    placeholders = _sql_placeholders(selected_match_ids)
    metadata_params: list[object] = [metadata_snapshot_id]
    rows = await db.execute_fetchall(
        f"""SELECT o.match_id,
                   o.bookmaker_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END AS league_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.sport ELSE m.sport END AS sport,
                   CASE WHEN sm.match_id IS NOT NULL THEN COALESCE(sm.home_team_id, 0) ELSE COALESCE(m.home_team_id, 0) END AS home_team_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN COALESCE(sm.away_team_id, 0) ELSE COALESCE(m.away_team_id, 0) END AS away_team_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                   s.source_url AS source_url,
                   o.market_type,
                   o.player_name,
                   o.threshold,
                   o.over_odds,
                   o.under_odds,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time,
                   o.scraped_at
            FROM odds o
            JOIN matches m ON m.id = o.match_id
            LEFT JOIN snapshot_matches sm ON sm.snapshot_id = ? AND sm.match_id = m.id
            LEFT JOIN match_bookmaker_sources s
              ON s.match_id = o.match_id
             AND s.bookmaker_id = o.bookmaker_id
             AND COALESCE(s.snapshot_id, '') = COALESCE(o.snapshot_id, '')
            WHERE o.match_id IN ({placeholders})
              AND {snapshot_filter}
            ORDER BY CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END ASC,
                     o.match_id ASC, o.bookmaker_id ASC,
                     o.market_type ASC, o.player_name ASC, o.threshold ASC""",
        [*metadata_params, *selected_match_ids, *snapshot_params],
    )
    return [NormalizedOdds(**_row_to_dict(row)) for row in rows]


async def get_current_normalized_outcome_offers_for_matches(
    match_ids: list[str],
    *,
    snapshot_id: str | None = None,
) -> list[NormalizedOutcomeOffer]:
    selected_match_ids = list(dict.fromkeys(match_ids))
    if not selected_match_ids:
        return []

    db = await get_db()
    metadata_snapshot_id = snapshot_id or await _get_current_snapshot_id(db)
    if snapshot_id is None:
        snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    else:
        snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(
            db, "o", snapshot_id=snapshot_id
        )
    if snapshot_filter is None:
        return []

    return await _get_normalized_outcome_offers_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
        metadata_snapshot_id=metadata_snapshot_id,
    )


async def _get_normalized_outcome_offers_for_matches_snapshot(
    db: aiosqlite.Connection,
    selected_match_ids: list[str],
    *,
    snapshot_filter: str,
    snapshot_params: list[object],
    metadata_snapshot_id: str | None = None,
) -> list[NormalizedOutcomeOffer]:
    placeholders = _sql_placeholders(selected_match_ids)
    metadata_params: list[object] = [metadata_snapshot_id]
    rows = await db.execute_fetchall(
        f"""SELECT o.match_id,
                   o.bookmaker_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END AS league_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.sport ELSE m.sport END AS sport,
                   CASE WHEN sm.match_id IS NOT NULL THEN COALESCE(sm.home_team_id, 0) ELSE COALESCE(m.home_team_id, 0) END AS home_team_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN COALESCE(sm.away_team_id, 0) ELSE COALESCE(m.away_team_id, 0) END AS away_team_id,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                   s.source_url AS source_url,
                   o.market_type,
                   o.outcome_code,
                   o.odds,
                   o.line,
                   o.raw_label,
                   CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time,
                   o.scraped_at
            FROM outcome_offers o
            JOIN matches m ON m.id = o.match_id
            LEFT JOIN snapshot_matches sm ON sm.snapshot_id = ? AND sm.match_id = m.id
            LEFT JOIN match_bookmaker_sources s
              ON s.match_id = o.match_id
             AND s.bookmaker_id = o.bookmaker_id
             AND COALESCE(s.snapshot_id, '') = COALESCE(o.snapshot_id, '')
            WHERE o.match_id IN ({placeholders})
              AND {snapshot_filter}
            ORDER BY CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END ASC,
                     o.match_id ASC, o.bookmaker_id ASC,
                     o.market_type ASC, o.line ASC, o.outcome_code ASC""",
        [*metadata_params, *selected_match_ids, *snapshot_params],
    )
    return [NormalizedOutcomeOffer(**_row_to_dict(row)) for row in rows]


async def get_current_canonical_offers_for_matches(
    match_ids: list[str],
    *,
    snapshot_id: str | None = None,
) -> list[CanonicalOffer]:
    selected_match_ids = list(dict.fromkeys(match_ids))
    if not selected_match_ids:
        return []

    db = await get_db()
    metadata_snapshot_id = snapshot_id or await _get_current_snapshot_id(db)
    if snapshot_id is None:
        snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    else:
        snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(
            db, "o", snapshot_id=snapshot_id
        )
    if snapshot_filter is None:
        return []

    odds_rows = await _get_normalized_odds_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
        metadata_snapshot_id=metadata_snapshot_id,
    )
    outcome_offer_rows = await _get_normalized_outcome_offers_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
        metadata_snapshot_id=metadata_snapshot_id,
    )
    resolved_event_members = await _eligible_resolved_event_members_for_offer_rows(
        [*odds_rows, *outcome_offer_rows],
        snapshot_id=metadata_snapshot_id,
    )
    resolved_event_ids = _resolved_event_ids_for_offer_rows(
        [*odds_rows, *outcome_offer_rows],
        resolved_event_members,
    )
    resolved_event_orientations = _resolved_event_orientations_for_offer_rows(
        [*odds_rows, *outcome_offer_rows],
        resolved_event_members,
    )
    event_scoped_player_odds = {
        id(item.odds): item
        for item in build_event_scoped_player_odds(odds_rows, resolved_event_members)
    }
    canonical_offers: list[CanonicalOffer] = []
    for odds in odds_rows:
        player_identity = event_scoped_player_odds.get(id(odds))
        canonical_offers.extend(
            canonical_offers_from_normalized_odds(
                odds,
                event_id=resolved_event_ids.get((odds.match_id, odds.bookmaker_id)),
                subject_key_override=(
                    player_identity.event_scoped_player_key
                    if player_identity is not None
                    else None
                ),
                subject_name_override=(
                    player_identity.event_player_display_name
                    if player_identity is not None
                    else None
                ),
            )
        )
    for offer in outcome_offer_rows:
        canonical_offers.append(
            canonical_offer_from_normalized_outcome_offer(
                offer,
                event_id=resolved_event_ids.get((offer.match_id, offer.bookmaker_id)),
                event_orientation=resolved_event_orientations.get(
                    (offer.match_id, offer.bookmaker_id)
                ),
            )
        )
    return canonical_offers


async def _eligible_resolved_event_members_for_offer_rows(
    rows: list[NormalizedOdds | NormalizedOutcomeOffer],
    *,
    snapshot_id: str | None,
) -> list[ResolvedEventMemberOut]:
    row_keys = {(row.match_id, row.bookmaker_id) for row in rows}
    if not row_keys:
        return []

    return await get_eligible_resolved_event_members_for_matches(
        sorted({match_id for match_id, _ in row_keys}),
        bookmaker_ids=sorted({bookmaker_id for _, bookmaker_id in row_keys}),
        snapshot_id=snapshot_id,
    )


def _resolved_event_ids_for_offer_rows(
    rows: list[NormalizedOdds | NormalizedOutcomeOffer],
    members: list[ResolvedEventMemberOut],
) -> dict[tuple[str, str], str]:
    row_keys = {(row.match_id, row.bookmaker_id) for row in rows}
    event_ids: dict[tuple[str, str], str] = {}
    for member in members:
        key = (member.match_id, member.bookmaker_id)
        if key in row_keys and key not in event_ids:
            event_ids[key] = member.resolved_event_id
    return event_ids


def _resolved_event_orientations_for_offer_rows(
    rows: list[NormalizedOdds | NormalizedOutcomeOffer],
    members: list[ResolvedEventMemberOut],
) -> dict[tuple[str, str], str]:
    row_keys = {(row.match_id, row.bookmaker_id) for row in rows}
    orientations: dict[tuple[str, str], str] = {}
    for member in members:
        key = (member.match_id, member.bookmaker_id)
        if key in row_keys and key not in orientations:
            orientations[key] = member.orientation
    return orientations


# ── Odds ───────────────────────────────────────────────────

async def upsert_odds(odds: NormalizedOdds, *, scraped_at: str) -> int:
    db = await get_db()
    snapshot_id = _snapshot_id_from_scraped_at(scraped_at)
    await _upsert_scrape_snapshot_tx(
        db,
        snapshot_id=snapshot_id,
        scraped_at=scraped_at,
    )
    await db.execute(
        """INSERT OR REPLACE INTO odds
           (snapshot_id, match_id, bookmaker_id, market_type, player_name, threshold,
            over_odds, under_odds, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            odds.match_id,
            odds.bookmaker_id,
            odds.market_type,
            odds.player_name,
            odds.threshold,
            odds.over_odds,
            odds.under_odds,
            scraped_at,
        ),
    )
    # Also insert into history
    await db.execute(
        """INSERT INTO odds_history
           (snapshot_id, match_id, bookmaker_id, market_type, player_name, threshold,
            over_odds, under_odds, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            odds.match_id,
            odds.bookmaker_id,
            odds.market_type,
            odds.player_name,
            odds.threshold,
            odds.over_odds,
            odds.under_odds,
            scraped_at,
        ),
    )
    await _upsert_match_bookmaker_source_tx(
        db,
        snapshot_id=snapshot_id,
        match_id=odds.match_id,
        bookmaker_id=odds.bookmaker_id,
        source_url=odds.source_url,
    )
    await db.commit()
    cursor = await db.execute("SELECT last_insert_rowid()")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_odds_for_match(match_id: str) -> list[OddsOut]:
    db = await get_db()
    snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    if snapshot_filter is None:
        return []
    rows = await db.execute_fetchall(
        f"""SELECT o.*,
                   b.name as bookmaker_name,
                   s.source_url as source_url
            FROM odds o
            LEFT JOIN bookmakers b ON o.bookmaker_id = b.id
            LEFT JOIN match_bookmaker_sources s
               ON s.match_id = o.match_id AND s.bookmaker_id = o.bookmaker_id
              AND COALESCE(s.snapshot_id, '') = COALESCE(o.snapshot_id, '')
            WHERE o.match_id = ? AND {snapshot_filter}
            ORDER BY o.market_type, o.player_name, o.threshold""",
        [match_id, *snapshot_params],
    )
    return [OddsOut(**_row_to_dict(r)) for r in rows]


async def get_odds_history_for_match(match_id: str) -> list[OddsOut]:
    db = await get_db()
    if await _has_scrape_snapshots(db):
        rows = await db.execute_fetchall(
            """SELECT h.*
               FROM odds_history h
               JOIN scrape_snapshots ss
                 ON ss.id = COALESCE(h.snapshot_id, h.scraped_at)
                AND ss.status = 'published'
               WHERE h.match_id = ?
               ORDER BY h.scraped_at DESC""",
            (match_id,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM odds_history WHERE match_id = ? ORDER BY scraped_at DESC",
            (match_id,),
        )
    return [OddsOut(**_row_to_dict(r)) for r in rows]


# ── Generic outcome offers ─────────────────────────────────

async def upsert_outcome_offer(
    offer: NormalizedOutcomeOffer,
    *,
    scraped_at: str,
) -> int:
    db = await get_db()
    snapshot_id = _snapshot_id_from_scraped_at(scraped_at)
    await _upsert_scrape_snapshot_tx(
        db,
        snapshot_id=snapshot_id,
        scraped_at=scraped_at,
    )
    await db.execute(
        """INSERT OR REPLACE INTO outcome_offers
           (snapshot_id, match_id, bookmaker_id, market_type, outcome_code, line,
            odds, raw_label, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            offer.match_id,
            offer.bookmaker_id,
            offer.market_type,
            offer.outcome_code,
            offer.line,
            offer.odds,
            offer.raw_label,
            scraped_at,
        ),
    )
    await _upsert_match_bookmaker_source_tx(
        db,
        snapshot_id=snapshot_id,
        match_id=offer.match_id,
        bookmaker_id=offer.bookmaker_id,
        source_url=offer.source_url,
    )
    await db.commit()
    row = await db.execute_fetchall(
        """SELECT id FROM outcome_offers
           WHERE snapshot_id = ?
             AND match_id = ?
             AND bookmaker_id = ?
             AND market_type = ?
             AND outcome_code = ?
             AND COALESCE(line, -999999.0) = COALESCE(?, -999999.0)""",
        (
            snapshot_id,
            offer.match_id,
            offer.bookmaker_id,
            offer.market_type,
            offer.outcome_code,
            offer.line,
        ),
    )
    return int(row[0]["id"]) if row else 0


async def get_outcome_offers(
    *,
    sport: str | None = None,
    match_id: str | None = None,
    match_ids: list[str] | None = None,
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[OutcomeOfferOut]:
    db = await get_db()
    current_snapshot_id = await _get_current_snapshot_id(db)
    snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    if snapshot_filter is None:
        return []

    q = """SELECT o.*,
                  b.name AS bookmaker_name,
                  s.source_url AS source_url
           FROM outcome_offers o
           JOIN matches m ON m.id = o.match_id
           LEFT JOIN snapshot_matches sm ON sm.snapshot_id = ? AND sm.match_id = m.id
           LEFT JOIN bookmakers b ON b.id = o.bookmaker_id
           LEFT JOIN match_bookmaker_sources s
              ON s.match_id = o.match_id
             AND s.bookmaker_id = o.bookmaker_id
             AND COALESCE(s.snapshot_id, '') = COALESCE(o.snapshot_id, '')
           """
    conditions = [snapshot_filter]
    params: list[object] = [current_snapshot_id, *snapshot_params]
    selected_match_ids = list(dict.fromkeys(match_ids or ([] if match_id is None else [match_id])))

    if sport:
        conditions.append("CASE WHEN sm.match_id IS NOT NULL THEN sm.sport ELSE m.sport END = ?")
        params.append(sport)
    if selected_match_ids:
        placeholders = _sql_placeholders(selected_match_ids)
        conditions.append(f"o.match_id IN ({placeholders})")
        params.extend(selected_match_ids)
    if bookmaker_ids:
        placeholders = _sql_placeholders(bookmaker_ids)
        conditions.append(f"o.bookmaker_id IN ({placeholders})")
        params.extend(bookmaker_ids)
    if market_type:
        conditions.append("o.market_type = ?")
        params.append(market_type)

    q += " WHERE " + " AND ".join(conditions)
    q += (
        " ORDER BY CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END ASC, "
        "o.market_type ASC, o.line ASC, o.outcome_code ASC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    return [OutcomeOfferOut(**_row_to_dict(row)) for row in rows]


async def get_outcome_offers_for_match(
    match_id: str,
    *,
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[OutcomeOfferOut]:
    return await get_outcome_offers(
        match_id=match_id,
        bookmaker_ids=bookmaker_ids,
        market_type=market_type,
        limit=limit,
        offset=offset,
    )


async def deactivate_opportunities(*, sport: str | None = None) -> None:
    db = await get_db()
    if sport:
        await db.execute("UPDATE opportunities SET is_active = FALSE WHERE sport = ?", (sport,))
    else:
        await db.execute("UPDATE opportunities SET is_active = FALSE")
    await db.commit()


async def deactivate_opportunities_for_scope(
    *,
    match_ids: list[str] | None = None,
    resolved_event_ids: list[str] | None = None,
) -> None:
    selected_match_ids = list(dict.fromkeys(match_ids or []))
    selected_event_ids = list(dict.fromkeys(resolved_event_ids or []))
    if not selected_match_ids and not selected_event_ids:
        return

    conditions: list[str] = []
    params: list[object] = []
    if selected_match_ids:
        placeholders = _sql_placeholders(selected_match_ids)
        conditions.append(f"match_id IN ({placeholders})")
        params.extend(selected_match_ids)
    if selected_event_ids:
        placeholders = _sql_placeholders(selected_event_ids)
        conditions.append(f"resolved_event_id IN ({placeholders})")
        params.extend(selected_event_ids)

    db = await get_db()
    await db.execute(
        f"""UPDATE opportunities
            SET is_active = FALSE
            WHERE is_active = TRUE
              AND ({" OR ".join(conditions)})""",
        params,
    )
    await db.commit()


async def insert_opportunity(opportunity, *, detected_at: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO opportunities
           (sport, match_id, resolved_event_id, opportunity_type, market_type, subject_type,
            subject_key, subject_name, line, profit_margin, middle_profit_margin,
            middle_hit_probability, middle_ev, middle_model_confidence,
            middle_model_diagnostics, middle_ev_rank, market_keys, legs, detected_at,
            is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)""",
        (
            opportunity.sport,
            opportunity.match_id,
            opportunity.resolved_event_id,
            opportunity.opportunity_type,
            opportunity.market_type,
            opportunity.subject_type,
            opportunity.subject_key,
            opportunity.subject_name,
            opportunity.line,
            opportunity.profit_margin,
            opportunity.middle_profit_margin,
            getattr(opportunity, "middle_hit_probability", None),
            getattr(opportunity, "middle_ev", None),
            getattr(opportunity, "middle_model_confidence", None),
            json.dumps(getattr(opportunity, "middle_model_diagnostics", {}) or {}),
            getattr(opportunity, "middle_ev_rank", None),
            json.dumps(list(opportunity.market_keys)),
            json.dumps([leg.model_dump() for leg in opportunity.legs]),
            detected_at,
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def publish_opportunities(
    *,
    snapshot_id: str,
    snapshot_at: str,
    opportunities: list,
    detected_at: str,
) -> str:
    publish_id = _new_opportunity_publish_id(detected_at)
    db = await _open_isolated_db_connection()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO opportunity_publishes (
                   id,
                   snapshot_id,
                   detected_at,
                   status,
                   opportunity_count
               )
               VALUES (?, ?, ?, 'publishing', ?)""",
            (publish_id, snapshot_id, detected_at, len(opportunities)),
        )
        await db.executemany(
            """INSERT INTO opportunities
                (publish_id, sport, match_id, resolved_event_id, opportunity_type,
                 market_type, subject_type, subject_key, subject_name, line,
                 profit_margin, middle_profit_margin, middle_hit_probability,
                 middle_ev, middle_model_confidence, middle_model_diagnostics,
                 middle_ev_rank, market_keys, legs, detected_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)""",
            [
                (
                    publish_id,
                    opportunity.sport,
                    opportunity.match_id,
                    opportunity.resolved_event_id,
                    opportunity.opportunity_type,
                    opportunity.market_type,
                    opportunity.subject_type,
                    opportunity.subject_key,
                    opportunity.subject_name,
                    opportunity.line,
                    opportunity.profit_margin,
                    opportunity.middle_profit_margin,
                    getattr(opportunity, "middle_hit_probability", None),
                    getattr(opportunity, "middle_ev", None),
                    getattr(opportunity, "middle_model_confidence", None),
                    json.dumps(getattr(opportunity, "middle_model_diagnostics", {}) or {}),
                    getattr(opportunity, "middle_ev_rank", None),
                    json.dumps(list(opportunity.market_keys)),
                    json.dumps([leg.model_dump() for leg in opportunity.legs]),
                    detected_at,
                )
                for opportunity in opportunities
            ],
        )
        await db.execute(
            """UPDATE opportunity_publishes
               SET status = 'published',
                   opportunity_count = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (len(opportunities), publish_id),
        )
        await db.execute(
            """UPDATE opportunities
               SET is_active = FALSE
               WHERE publish_id IS NOT NULL
                 AND publish_id != ?""",
            (publish_id,),
        )
        await db.execute(
            """INSERT INTO scrape_state (
                   id,
                   current_snapshot_id,
                   current_snapshot_at,
                   current_opportunity_publish_id,
                   updated_at
               )
               VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                   current_snapshot_id = excluded.current_snapshot_id,
                   current_snapshot_at = excluded.current_snapshot_at,
                   current_opportunity_publish_id = excluded.current_opportunity_publish_id,
                   updated_at = CURRENT_TIMESTAMP""",
            (snapshot_id, snapshot_at, publish_id),
        )
        await db.execute(
            """UPDATE scrape_snapshots
               SET status = 'published',
                   completed_at = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (snapshot_at, snapshot_id),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()
    return publish_id


def _row_to_opportunity(row: aiosqlite.Row) -> OpportunityOut:
    data = _row_to_dict(row)
    raw_legs = data.get("legs")
    legs_payload = json.loads(raw_legs) if isinstance(raw_legs, str) and raw_legs else []
    legs: list[OpportunityLeg] = []
    for leg_data in legs_payload:
        legs.append(OpportunityLeg(**leg_data))
    data["legs"] = legs
    data["event_id"] = data.get("resolved_event_id")
    raw_market_keys = data.get("market_keys")
    data["market_keys"] = (
        json.loads(raw_market_keys)
        if isinstance(raw_market_keys, str) and raw_market_keys
        else []
    )
    raw_middle_diagnostics = data.get("middle_model_diagnostics")
    data["middle_model_diagnostics"] = (
        json.loads(raw_middle_diagnostics)
        if isinstance(raw_middle_diagnostics, str) and raw_middle_diagnostics
        else {}
    )
    return OpportunityOut(**data)


async def get_opportunities(
    *,
    sport: str | None = None,
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    include_legacy_discrepancy_overlap: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[OpportunityOut]:
    db = await get_db()
    current_publish_id = await _get_current_opportunity_publish_id(db)
    current_snapshot_id = await _get_current_snapshot_id(db)
    q = """SELECT op.*,
                  CASE WHEN sm.match_id IS NOT NULL THEN sm.home_team ELSE m.home_team END AS home_team,
                  CASE WHEN sm.match_id IS NOT NULL THEN sm.away_team ELSE m.away_team END AS away_team,
                  CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END AS start_time,
                  l.name AS league_name
           FROM opportunities op
           LEFT JOIN opportunity_publishes pub ON pub.id = op.publish_id
           LEFT JOIN matches m ON m.id = op.match_id
           LEFT JOIN snapshot_matches sm
             ON sm.snapshot_id = COALESCE(pub.snapshot_id, ?)
            AND sm.match_id = op.match_id
           LEFT JOIN leagues l
             ON l.id = CASE WHEN sm.match_id IS NOT NULL THEN sm.league_id ELSE m.league_id END"""
    if current_publish_id is not None:
        conditions = ["op.publish_id = ?", "op.is_active = TRUE"]
        params: list[object] = [current_snapshot_id, current_publish_id]
    else:
        conditions = ["op.is_active = TRUE"]
        params = [current_snapshot_id]
    if sport:
        conditions.append("op.sport = ?")
        params.append(sport)
    _ = include_legacy_discrepancy_overlap
    if market_type:
        conditions.append("op.market_type = ?")
        params.append(market_type)
    if bookmaker_ids:
        placeholders = _sql_placeholders(bookmaker_ids)
        conditions.append(
            f"""EXISTS (
                   SELECT 1
                   FROM json_each(op.legs) AS leg
                   WHERE json_extract(leg.value, '$.bookmaker_id') IN ({placeholders})
               )"""
        )
        params.extend(bookmaker_ids)
    q += " WHERE " + " AND ".join(conditions)
    q += """ ORDER BY COALESCE(op.profit_margin, -999) DESC,
                    CASE WHEN sm.match_id IS NOT NULL THEN sm.start_time ELSE m.start_time END ASC,
                    op.id ASC
             LIMIT ? OFFSET ?"""
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    opportunities = [_row_to_opportunity(row) for row in rows]
    await _enrich_opportunity_legs(
        db,
        opportunities,
        snapshot_id=current_snapshot_id,
    )
    return opportunities


async def _enrich_opportunity_legs(
    db: aiosqlite.Connection,
    opportunities: list[OpportunityOut],
    *,
    snapshot_id: str | None = None,
) -> None:
    bookmaker_ids = sorted(
        {
            leg.bookmaker_id
            for opportunity in opportunities
            for leg in opportunity.legs
            if leg.bookmaker_id
        }
    )
    match_ids = sorted(
        {
            match_id
            for opportunity in opportunities
            for match_id in [
                opportunity.match_id,
                *[
                    leg.match_id
                    for leg in opportunity.legs
                    if leg.match_id is not None
                ],
            ]
            if match_id is not None
        }
    )
    if not bookmaker_ids:
        return

    bookmaker_placeholders = _sql_placeholders(bookmaker_ids)
    bookmaker_rows = await db.execute_fetchall(
        f"SELECT id, name FROM bookmakers WHERE id IN ({bookmaker_placeholders})",
        bookmaker_ids,
    )
    bookmaker_names = {row["id"]: row["name"] for row in bookmaker_rows}

    source_urls: dict[tuple[str, str], str] = {}
    if match_ids:
        match_placeholders = _sql_placeholders(match_ids)
        if snapshot_id is not None:
            source_rows = await db.execute_fetchall(
                f"""SELECT match_id, bookmaker_id, source_url
                    FROM match_bookmaker_sources
                    WHERE match_id IN ({match_placeholders})
                      AND bookmaker_id IN ({bookmaker_placeholders})
                      AND snapshot_id = ?
                    ORDER BY id ASC""",
                [*match_ids, *bookmaker_ids, snapshot_id],
            )
        elif not await _has_scrape_snapshots(db):
            source_rows = await db.execute_fetchall(
                f"""SELECT match_id, bookmaker_id, source_url
                    FROM match_bookmaker_sources
                    WHERE match_id IN ({match_placeholders})
                      AND bookmaker_id IN ({bookmaker_placeholders})
                      AND snapshot_id IS NULL
                    ORDER BY id ASC""",
                [*match_ids, *bookmaker_ids],
            )
        else:
            source_rows = []
        for row in source_rows:
            if row["source_url"] is None:
                continue
            source_urls.setdefault(
                (row["match_id"], row["bookmaker_id"]),
                row["source_url"],
            )

    for opportunity in opportunities:
        for leg in opportunity.legs:
            leg.bookmaker_name = leg.bookmaker_name or bookmaker_names.get(leg.bookmaker_id)
            source_match_id = leg.match_id or opportunity.match_id
            leg.source_url = leg.source_url or source_urls.get(
                (source_match_id, leg.bookmaker_id)
            )


def _telegram_display_context(
    *,
    home_team: object = None,
    away_team: object = None,
    league_name: object = None,
    start_time: object = None,
    fallback_label: object = None,
) -> dict[str, str | None]:
    return {
        "home_team": str(home_team) if home_team else None,
        "away_team": str(away_team) if away_team else None,
        "league_name": str(league_name) if league_name else None,
        "start_time": str(start_time) if start_time else None,
        "fallback_label": str(fallback_label) if fallback_label else None,
    }


async def get_telegram_opportunity_display_contexts(
    opportunity_keys: list[tuple[str | None, str]],
) -> dict[tuple[str | None, str], dict[str, str | None]]:
    """Resolve display context for live Telegram opportunity notifications.

    Keys are `(resolved_event_id, match_id)` pairs. Resolved event display labels
    win first, canonical match labels second, and snapshot match labels last.
    """
    unique_keys = [
        key
        for key in dict.fromkeys(opportunity_keys)
        if key[1]
    ]
    contexts = {
        key: _telegram_display_context(fallback_label=key[0] or key[1])
        for key in unique_keys
    }
    if not unique_keys:
        return contexts

    db = await get_db()
    current_snapshot_id = await _get_current_snapshot_id(db)
    event_ids = sorted({event_id for event_id, _match_id in unique_keys if event_id})
    direct_match_ids = sorted({match_id for _event_id, match_id in unique_keys})

    event_rows: dict[str, aiosqlite.Row] = {}
    event_primary_match_ids: list[str] = []
    if event_ids:
        placeholders = _sql_placeholders(event_ids)
        rows = await db.execute_fetchall(
            f"""SELECT re.id AS resolved_event_id,
                       re.primary_match_id,
                       re.display_home_team,
                       re.display_away_team,
                       re.display_league_name,
                       re.start_time AS event_start_time,
                       m.home_team AS match_home_team,
                       m.away_team AS match_away_team,
                       m.start_time AS match_start_time,
                       l.name AS match_league_name
                FROM resolved_events re
                LEFT JOIN matches m ON m.id = re.primary_match_id
                LEFT JOIN leagues l ON l.id = m.league_id
                WHERE re.id IN ({placeholders})""",
            event_ids,
        )
        event_rows = {row["resolved_event_id"]: row for row in rows}
        event_primary_match_ids = [
            row["primary_match_id"] for row in rows if row["primary_match_id"]
        ]

    all_match_ids = sorted(set(direct_match_ids) | set(event_primary_match_ids))
    match_contexts: dict[str, dict[str, str | None]] = {}
    snapshot_contexts: dict[str, dict[str, str | None]] = {}
    if all_match_ids:
        placeholders = _sql_placeholders(all_match_ids)
        match_rows = await db.execute_fetchall(
            f"""SELECT m.id AS match_id,
                       m.home_team,
                       m.away_team,
                       m.start_time,
                       l.name AS league_name
                FROM matches m
                LEFT JOIN leagues l ON l.id = m.league_id
                WHERE m.id IN ({placeholders})""",
            all_match_ids,
        )
        match_contexts = {
            row["match_id"]: _telegram_display_context(
                home_team=row["home_team"],
                away_team=row["away_team"],
                league_name=row["league_name"],
                start_time=row["start_time"],
                fallback_label=row["match_id"],
            )
            for row in match_rows
        }

        snapshot_params: list[object] = list(all_match_ids)
        snapshot_filter = ""
        if current_snapshot_id is not None:
            snapshot_filter = "AND sm.snapshot_id = ?"
            snapshot_params.append(current_snapshot_id)
        snapshot_rows = await db.execute_fetchall(
            f"""SELECT sm.match_id,
                       sm.home_team,
                       sm.away_team,
                       sm.start_time,
                       l.name AS league_name
                FROM snapshot_matches sm
                LEFT JOIN leagues l ON l.id = sm.league_id
                WHERE sm.match_id IN ({placeholders})
                  {snapshot_filter}
                ORDER BY sm.snapshot_id DESC, sm.rowid ASC""",
            snapshot_params,
        )
        for row in snapshot_rows:
            snapshot_contexts.setdefault(
                row["match_id"],
                _telegram_display_context(
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    league_name=row["league_name"],
                    start_time=row["start_time"],
                    fallback_label=row["match_id"],
                ),
            )

    for key in unique_keys:
        event_id, match_id = key
        if event_id and event_id in event_rows:
            event = event_rows[event_id]
            primary_match_id = event["primary_match_id"]
            match_context = match_contexts.get(primary_match_id, {})
            snapshot_context = snapshot_contexts.get(primary_match_id, {})
            contexts[key] = _telegram_display_context(
                home_team=(
                    event["display_home_team"]
                    or match_context.get("home_team")
                    or snapshot_context.get("home_team")
                ),
                away_team=(
                    event["display_away_team"]
                    or match_context.get("away_team")
                    or snapshot_context.get("away_team")
                ),
                league_name=(
                    event["display_league_name"]
                    or match_context.get("league_name")
                    or snapshot_context.get("league_name")
                ),
                start_time=(
                    event["event_start_time"]
                    or match_context.get("start_time")
                    or snapshot_context.get("start_time")
                ),
                fallback_label=event_id,
            )
            continue

        contexts[key] = (
            match_contexts.get(match_id)
            or snapshot_contexts.get(match_id)
            or contexts[key]
        )
    return contexts


# ── Unresolved odds ────────────────────────────────────────

async def insert_unresolved_odds(
    unresolved: UnresolvedOddsDiagnostic,
    *,
    scraped_at: str,
) -> int:
    db = await get_db()
    snapshot_id = _snapshot_id_from_scraped_at(scraped_at)
    await _upsert_scrape_snapshot_tx(
        db,
        snapshot_id=snapshot_id,
        scraped_at=scraped_at,
    )
    cursor = await db.execute(
        """INSERT INTO unresolved_odds
           (snapshot_id, bookmaker_id, raw_league_id, league_id, sport, market_type, player_name,
            raw_team_name, normalized_team_name, start_time, threshold, over_odds,
            under_odds, reason_code, candidate_count, candidate_matchups,
            available_matchups_same_slot, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            unresolved.bookmaker_id,
            unresolved.raw_league_id,
            unresolved.league_id,
            unresolved.sport,
            unresolved.market_type,
            unresolved.player_name,
            unresolved.raw_team_name,
            unresolved.normalized_team_name,
            unresolved.start_time,
            unresolved.threshold,
            unresolved.over_odds,
            unresolved.under_odds,
            unresolved.reason_code,
            unresolved.candidate_count,
            json.dumps(unresolved.candidate_matchups),
            json.dumps(unresolved.available_matchups_same_slot),
            scraped_at,
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def get_unresolved_odds(
    bookmaker_ids: list[str] | None = None,
    reason_code: str | None = None,
    sport: str | None = None,
    market_type: str | None = None,
    league_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[UnresolvedOddsOut]:
    db = await get_db()
    snapshot_id, snapshot_at = await _get_visible_diagnostic_snapshot(db, "unresolved_odds")
    if snapshot_at is None:
        return []

    q = """SELECT u.*,
                  b.name as bookmaker_name,
                  l.name as league_name,
                  tr.id AS team_review_case_id,
                  tr.suggested_team_id AS team_review_suggested_team_id,
                  tr.suggested_team_name AS team_review_suggested_team_name,
                  tr.confidence AS team_review_confidence,
                  tr.status AS team_review_status,
                  tr.similarity_score AS team_review_similarity_score
           FROM unresolved_odds u
           LEFT JOIN bookmakers b ON u.bookmaker_id = b.id
           LEFT JOIN leagues l ON u.league_id = l.id
           LEFT JOIN team_review_cases tr ON tr.id = (
                SELECT tr2.id
                FROM team_review_cases tr2
                WHERE tr2.snapshot_id = u.snapshot_id
                  AND tr2.bookmaker_id = u.bookmaker_id
                  AND tr2.sport = u.sport
                  AND tr2.raw_team_name = u.raw_team_name
                  AND (
                      tr2.start_time = u.start_time
                      OR (tr2.start_time IS NULL AND u.start_time IS NULL)
                  )
                ORDER BY CASE tr2.status WHEN 'pending' THEN 0 ELSE 1 END,
                         tr2.id DESC
                LIMIT 1
           )"""
    conditions = ["u.snapshot_id = ?" if snapshot_id else "u.scraped_at = ?"]
    params: list = [snapshot_id or snapshot_at]

    if bookmaker_ids:
        placeholders = _sql_placeholders(bookmaker_ids)
        conditions.append(f"u.bookmaker_id IN ({placeholders})")
        params.extend(bookmaker_ids)
    if reason_code:
        conditions.append("u.reason_code = ?")
        params.append(reason_code)
    if sport:
        conditions.append("u.sport = ?")
        params.append(sport)
    if market_type:
        conditions.append("u.market_type = ?")
        params.append(market_type)
    if league_id:
        conditions.append("u.league_id = ?")
        params.append(league_id)

    q += " WHERE " + " AND ".join(conditions)
    q += """ ORDER BY u.reason_code ASC, u.bookmaker_id ASC, u.start_time ASC,
                    u.raw_team_name ASC, u.player_name ASC
             LIMIT ? OFFSET ?"""
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    return [_row_to_unresolved_odds(r) for r in rows]


async def insert_team_review_case(
    case: TeamReviewDiagnostic,
    *,
    scraped_at: str,
) -> int:
    db = await get_db()
    snapshot_id = _snapshot_id_from_scraped_at(scraped_at)
    await _upsert_scrape_snapshot_tx(
        db,
        snapshot_id=snapshot_id,
        scraped_at=scraped_at,
    )
    cursor = await db.execute(
        """INSERT INTO team_review_cases
           (
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
               scraped_at
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            case.bookmaker_id,
            case.raw_league_id,
            case.normalized_raw_league_id,
            case.sport,
            case.scope_league_id,
            case.raw_team_name,
            case.normalized_raw_team_name,
            case.suggested_team_id,
            case.suggested_team_name,
            case.start_time,
            case.review_kind,
            case.reason_code,
            case.confidence,
            case.similarity_score,
            json.dumps([candidate.model_dump() for candidate in case.candidate_teams]),
            case.matched_counterpart_team,
            case.canonical_home_team,
            case.canonical_away_team,
            json.dumps(case.evidence),
            case.status,
            scraped_at,
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def get_team_review_cases(
    bookmaker_ids: list[str] | None = None,
    sport: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TeamReviewOut]:
    db = await get_db()
    snapshot_id, snapshot_at = await _get_visible_diagnostic_snapshot(db, "team_review_cases")
    if snapshot_at is None:
        return []

    q = """SELECT c.*, b.name AS bookmaker_name, l.name AS scope_league_name
           FROM team_review_cases c
           LEFT JOIN bookmakers b ON c.bookmaker_id = b.id
           LEFT JOIN leagues l ON c.scope_league_id = l.id"""
    conditions = ["c.snapshot_id = ?" if snapshot_id else "c.scraped_at = ?"]
    params: list[object] = [snapshot_id or snapshot_at]

    if bookmaker_ids:
        placeholders = _sql_placeholders(bookmaker_ids)
        conditions.append(f"c.bookmaker_id IN ({placeholders})")
        params.extend(bookmaker_ids)
    if sport:
        conditions.append("c.sport = ?")
        params.append(sport)
    if status:
        conditions.append("c.status = ?")
        params.append(status)

    q += " WHERE " + " AND ".join(conditions)
    q += """ ORDER BY c.status ASC, c.start_time ASC, c.suggested_team_name ASC,
                    c.raw_team_name ASC
             LIMIT ? OFFSET ?"""
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    return [_row_to_team_review(r) for r in rows]


async def get_team_review_case(case_id: int) -> TeamReviewOut | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT c.*, b.name AS bookmaker_name, l.name AS scope_league_name
           FROM team_review_cases c
           LEFT JOIN bookmakers b ON c.bookmaker_id = b.id
           LEFT JOIN leagues l ON c.scope_league_id = l.id
           WHERE c.id = ?""",
        (case_id,),
    )
    if not rows:
        return None
    return _row_to_team_review(rows[0])


async def get_team_review_case_history_summary(
    *,
    sport: str,
    normalized_raw_team_name: str,
    suggested_team_id: int,
    start_time: str,
    canonical_home_team: str,
    canonical_away_team: str,
) -> tuple[set[str], bool]:
    db = await get_db()
    snapshot_at = await _get_current_snapshot_at(db)
    if snapshot_at is None:
        return set(), False
    rows = await db.execute_fetchall(
        """SELECT bookmaker_id, status
           FROM team_review_cases
           WHERE review_kind IN (
                'alias_suggestion',
                'candidate_search',
                'auto_alias_suggestion',
                'auto_canonical_merge_suggestion'
           )
             AND sport = ?
             AND normalized_raw_team_name = ?
             AND suggested_team_id = ?
             AND start_time = ?
             AND canonical_home_team = ?
             AND canonical_away_team = ?
             AND scraped_at IS NOT NULL
             AND scraped_at <= ?""",
        (
            sport,
            normalized_raw_team_name,
            suggested_team_id,
            start_time,
            canonical_home_team,
            canonical_away_team,
            snapshot_at,
        ),
    )
    confirming_bookmakers = {
        str(row["bookmaker_id"])
        for row in rows
        if row["bookmaker_id"] and row["status"] != "declined"
    }
    has_declined = any(row["status"] == "declined" for row in rows)
    return confirming_bookmakers, has_declined


async def mark_team_review_case_approved(
    case_id: int,
) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE team_review_cases
           SET status = 'approved',
                approved_at = COALESCE(approved_at, CURRENT_TIMESTAMP)
           WHERE id = ?""",
        (case_id,),
    )
    await db.commit()


async def mark_team_review_case_declined(case_id: int) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE team_review_cases
           SET status = 'declined',
               declined_at = COALESCE(declined_at, CURRENT_TIMESTAMP)
           WHERE id = ?""",
        (case_id,),
    )
    await db.commit()


async def delete_team_review_cases(
    case_ids: list[int],
    *,
    statuses: list[str] | None = None,
    review_kinds: list[str] | None = None,
) -> int:
    if not case_ids:
        return 0
    db = await get_db()
    conditions = [f"id IN ({_sql_placeholders(case_ids)})"]
    params: list[object] = list(case_ids)
    if statuses:
        conditions.append(f"status IN ({_sql_placeholders(statuses)})")
        params.extend(statuses)
    if review_kinds:
        conditions.append(f"review_kind IN ({_sql_placeholders(review_kinds)})")
        params.extend(review_kinds)
    cursor = await db.execute(
        "DELETE FROM team_review_cases WHERE " + " AND ".join(conditions),
        tuple(params),
    )
    await db.commit()
    return cursor.rowcount or 0


# ── Notifications ──────────────────────────────────────────

async def insert_notification(
    type: str, title: str, message: str | None = None, data: dict | None = None
) -> int:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO notifications (type, title, message, data) VALUES (?, ?, ?, ?)",
        (type, title, message, json.dumps(data) if data else None),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def get_notifications(unread_only: bool = False, limit: int = 50) -> list[NotificationOut]:
    db = await get_db()
    q = "SELECT * FROM notifications"
    if unread_only:
        q += " WHERE is_read = FALSE"
    q += " ORDER BY created_at DESC LIMIT ?"
    rows = await db.execute_fetchall(q, (limit,))
    return [NotificationOut(**_row_to_dict(r)) for r in rows]


# ── Telegram notification settings ─────────────────────────

def _normalize_telegram_bookmaker_ids(bookmaker_ids: list[str] | None) -> list[str]:
    if not bookmaker_ids:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for bookmaker_id in bookmaker_ids:
        item = str(bookmaker_id).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _row_to_telegram_profile(row: aiosqlite.Row) -> TelegramNotificationProfileOut:
    data = _row_to_dict(row)
    data["bookmaker_ids"] = _json_list(data.get("bookmaker_ids"))
    data["enabled"] = bool(data.get("enabled"))
    return TelegramNotificationProfileOut(**data)


async def list_telegram_notification_profiles(
    *,
    enabled_only: bool = False,
) -> list[TelegramNotificationProfileOut]:
    db = await get_db()
    q = "SELECT * FROM telegram_notification_profiles"
    params: list[object] = []
    if enabled_only:
        q += " WHERE enabled = TRUE"
    q += " ORDER BY id ASC"
    rows = await db.execute_fetchall(q, params)
    return [_row_to_telegram_profile(row) for row in rows]


async def get_telegram_notification_profile(
    profile_id: int,
) -> TelegramNotificationProfileOut | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM telegram_notification_profiles WHERE id = ?",
        (profile_id,),
    )
    if not rows:
        return None
    return _row_to_telegram_profile(rows[0])


async def create_telegram_notification_profile(
    profile: TelegramNotificationProfileCreate,
) -> TelegramNotificationProfileOut:
    db = await get_db()
    bookmaker_ids = _normalize_telegram_bookmaker_ids(profile.bookmaker_ids)
    cursor = await db.execute(
        """INSERT INTO telegram_notification_profiles (
               label,
               chat_id,
               enabled,
               min_gap,
               min_roi_percent,
               min_middle_ev_percent,
               bookmaker_ids
           )
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            profile.label.strip(),
            profile.chat_id.strip(),
            profile.enabled,
            profile.min_gap,
            profile.min_roi_percent,
            profile.min_middle_ev_percent,
            json.dumps(bookmaker_ids),
        ),
    )
    await db.commit()
    created = await get_telegram_notification_profile(cursor.lastrowid or 0)
    if created is None:
        raise RuntimeError("Created Telegram notification profile could not be loaded")
    return created


async def update_telegram_notification_profile(
    profile_id: int,
    patch: TelegramNotificationProfileUpdate,
) -> TelegramNotificationProfileOut | None:
    current = await get_telegram_notification_profile(profile_id)
    if current is None:
        return None

    values = current.model_dump()
    updates = patch.model_dump(exclude_unset=True)
    values.update(updates)
    bookmaker_ids = _normalize_telegram_bookmaker_ids(values.get("bookmaker_ids"))

    db = await get_db()
    await db.execute(
        """UPDATE telegram_notification_profiles
           SET label = ?,
               chat_id = ?,
               enabled = ?,
               min_gap = ?,
               min_roi_percent = ?,
               min_middle_ev_percent = ?,
               bookmaker_ids = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            str(values["label"]).strip(),
            str(values["chat_id"]).strip(),
            bool(values["enabled"]),
            values["min_gap"],
            values["min_roi_percent"],
            values["min_middle_ev_percent"],
            json.dumps(bookmaker_ids),
            profile_id,
        ),
    )
    await db.commit()
    return await get_telegram_notification_profile(profile_id)


async def delete_telegram_notification_profile(profile_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM telegram_notification_profiles WHERE id = ?",
        (profile_id,),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0


async def get_telegram_delivery_status(
    *,
    profile_id: int,
    opportunity_fingerprint: str,
) -> str | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT status
           FROM telegram_notification_deliveries
           WHERE profile_id = ?
             AND opportunity_fingerprint = ?""",
        (profile_id, opportunity_fingerprint),
    )
    if not rows:
        return None
    return str(rows[0]["status"])


async def begin_telegram_delivery_attempt(
    *,
    profile_id: int,
    opportunity_fingerprint: str,
    publish_id: str | None,
) -> bool:
    db = await get_db()
    await db.execute(
        """INSERT OR IGNORE INTO telegram_notification_deliveries (
               profile_id,
               opportunity_fingerprint,
               publish_id,
               status
           )
           VALUES (?, ?, ?, 'pending')""",
        (profile_id, opportunity_fingerprint, publish_id),
    )
    status = await get_telegram_delivery_status(
        profile_id=profile_id,
        opportunity_fingerprint=opportunity_fingerprint,
    )
    if status == "sent":
        await db.commit()
        return False
    await db.execute(
        """UPDATE telegram_notification_deliveries
           SET publish_id = COALESCE(?, publish_id),
               status = 'pending',
               attempt_count = attempt_count + 1,
               error = NULL,
               last_attempt_at = CURRENT_TIMESTAMP,
               updated_at = CURRENT_TIMESTAMP
           WHERE profile_id = ?
             AND opportunity_fingerprint = ?
             AND status != 'sent'""",
        (publish_id, profile_id, opportunity_fingerprint),
    )
    await db.commit()
    return True


async def mark_telegram_delivery_sent(
    *,
    profile_id: int,
    opportunity_fingerprint: str,
    telegram_message_id: int | None,
) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE telegram_notification_deliveries
           SET status = 'sent',
               telegram_message_id = ?,
               error = NULL,
               sent_at = CURRENT_TIMESTAMP,
               updated_at = CURRENT_TIMESTAMP
           WHERE profile_id = ?
             AND opportunity_fingerprint = ?""",
        (telegram_message_id, profile_id, opportunity_fingerprint),
    )
    await db.commit()


async def mark_telegram_delivery_failed(
    *,
    profile_id: int,
    opportunity_fingerprint: str,
    error: str,
) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE telegram_notification_deliveries
           SET status = 'failed',
               error = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE profile_id = ?
             AND opportunity_fingerprint = ?""",
        (error[:1000], profile_id, opportunity_fingerprint),
    )
    await db.commit()


async def mark_telegram_profile_delivery_error(
    *,
    profile_id: int,
    error: str,
) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE telegram_notification_profiles
           SET last_delivery_error = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (error[:1000], profile_id),
    )
    await db.commit()


async def mark_telegram_profile_rate_limited(
    *,
    profile_id: int,
    retry_after_seconds: int,
    error: str,
) -> None:
    until = (datetime.utcnow() + timedelta(seconds=retry_after_seconds)).isoformat(
        timespec="seconds"
    )
    db = await get_db()
    await db.execute(
        """UPDATE telegram_notification_profiles
           SET rate_limited_until = ?,
               last_delivery_error = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (until, error[:1000], profile_id),
    )
    await db.commit()


async def clear_telegram_profile_rate_limit(profile_id: int) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE telegram_notification_profiles
           SET rate_limited_until = NULL,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (profile_id,),
    )
    await db.commit()


async def clear_telegram_profile_delivery_error(profile_id: int) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE telegram_notification_profiles
           SET rate_limited_until = NULL,
               last_delivery_error = NULL,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (profile_id,),
    )
    await db.commit()


def _retention_cutoff(snapshot_at: str, days: int) -> str:
    return (datetime.fromisoformat(snapshot_at) - timedelta(days=days)).isoformat()


async def cleanup_retained_data(current_snapshot_at: str) -> dict[str, int]:
    db = await _open_isolated_db_connection()
    try:
        await db.execute("BEGIN IMMEDIATE")
        published_snapshot_id, published_snapshot_at = await _get_current_snapshot(db)
        current_publish_id = await _get_current_opportunity_publish_id(db)
        preserved_snapshot_id = published_snapshot_id or _snapshot_id_from_scraped_at(
            current_snapshot_at
        )
        preserved_snapshot_ids = [preserved_snapshot_id]
        newer_failure_params: list[object] = []
        newer_failure_clause = ""
        if published_snapshot_at is not None:
            newer_failure_clause = "AND datetime(scraped_at) > datetime(?)"
            newer_failure_params.append(published_snapshot_at)
        failed_snapshot_rows = await db.execute_fetchall(
            f"""SELECT id
                FROM scrape_snapshots
                WHERE status = 'analysis_failed'
                  {newer_failure_clause}
                ORDER BY datetime(scraped_at) DESC, id DESC
                LIMIT 1""",
            newer_failure_params,
        )
        if failed_snapshot_rows and failed_snapshot_rows[0]["id"] not in preserved_snapshot_ids:
            preserved_snapshot_ids.append(failed_snapshot_rows[0]["id"])
        preserved_snapshot_placeholders = _sql_placeholders(preserved_snapshot_ids)
        retention_anchor_at = published_snapshot_at or current_snapshot_at
        deleted_stale_odds_cur = await db.execute(
            f"""DELETE FROM odds
               WHERE COALESCE(snapshot_id, scraped_at) IS NULL
                    OR COALESCE(snapshot_id, scraped_at) NOT IN ({preserved_snapshot_placeholders})""",
            preserved_snapshot_ids,
        )
        await db.execute(
            f"""DELETE FROM outcome_offers
               WHERE COALESCE(snapshot_id, scraped_at) IS NULL
                   OR COALESCE(snapshot_id, scraped_at) NOT IN ({preserved_snapshot_placeholders})""",
            preserved_snapshot_ids,
        )
        deleted_unresolved_cur = await db.execute(
            f"""DELETE FROM unresolved_odds
               WHERE COALESCE(snapshot_id, scraped_at) IS NULL
                   OR COALESCE(snapshot_id, scraped_at) NOT IN ({preserved_snapshot_placeholders})""",
            preserved_snapshot_ids,
        )
        await db.execute(
            f"""DELETE FROM snapshot_matches
               WHERE snapshot_id IS NULL
                   OR snapshot_id NOT IN ({preserved_snapshot_placeholders})""",
            preserved_snapshot_ids,
        )
        deleted_match_sources_cur = await db.execute(
            f"""DELETE FROM match_bookmaker_sources
               WHERE snapshot_id IS NOT NULL
                  AND snapshot_id NOT IN ({preserved_snapshot_placeholders})""",
            preserved_snapshot_ids,
        )
        deleted_resolved_event_members_cur = await db.execute(
            f"""DELETE FROM resolved_event_members
               WHERE snapshot_id IS NOT NULL
                  AND snapshot_id NOT IN ({preserved_snapshot_placeholders})""",
            preserved_snapshot_ids,
        )
        if current_publish_id is not None:
            deleted_opportunities_cur = await db.execute(
                """DELETE FROM opportunities
                   WHERE publish_id IS NOT NULL
                     AND publish_id != ?""",
                (current_publish_id,),
            )
            deleted_opportunity_publishes_cur = await db.execute(
                "DELETE FROM opportunity_publishes WHERE id != ?",
                (current_publish_id,),
            )
        else:
            deleted_opportunities_cur = await db.execute(
                "DELETE FROM opportunities WHERE publish_id IS NOT NULL"
            )
            deleted_opportunity_publishes_cur = await db.execute(
                "DELETE FROM opportunity_publishes"
            )
        if settings.odds_history_retention_days > 0:
            odds_history_cutoff = _retention_cutoff(
                retention_anchor_at, settings.odds_history_retention_days
            )
            deleted_odds_history_cur = await db.execute(
                f"""
                DELETE FROM odds_history
                WHERE scraped_at IS NOT NULL
                  AND datetime(scraped_at) < datetime(?)
                  AND (
                       COALESCE(snapshot_id, scraped_at) IS NULL
                       OR COALESCE(snapshot_id, scraped_at) NOT IN ({preserved_snapshot_placeholders})
                   )
                """,
                [odds_history_cutoff, *preserved_snapshot_ids],
            )
        else:
            deleted_odds_history_cur = await db.execute(
                f"""DELETE FROM odds_history
                   WHERE COALESCE(snapshot_id, scraped_at) IS NULL
                       OR COALESCE(snapshot_id, scraped_at) NOT IN ({preserved_snapshot_placeholders})""",
                preserved_snapshot_ids,
            )
        deleted_scrape_snapshots_cur = await db.execute(
            f"""DELETE FROM scrape_snapshots
               WHERE id NOT IN ({preserved_snapshot_placeholders})
                 AND NOT EXISTS (
                     SELECT 1
                     FROM odds_history h
                     WHERE COALESCE(h.snapshot_id, h.scraped_at) = scrape_snapshots.id
                 )""",
            preserved_snapshot_ids,
        )

        if settings.team_review_retention_days > 0:
            team_review_cutoff = _retention_cutoff(
                retention_anchor_at, settings.team_review_retention_days
            )
            deleted_team_reviews_cur = await db.execute(
                f"""
                DELETE FROM team_review_cases
                WHERE scraped_at IS NOT NULL
                  AND datetime(scraped_at) < datetime(?)
                  AND (
                       COALESCE(snapshot_id, scraped_at) IS NULL
                       OR COALESCE(snapshot_id, scraped_at) NOT IN ({preserved_snapshot_placeholders})
                   )
                """,
                [team_review_cutoff, *preserved_snapshot_ids],
            )
        else:
            deleted_team_reviews_cur = await db.execute(
                f"""DELETE FROM team_review_cases
                   WHERE COALESCE(snapshot_id, scraped_at) IS NULL
                       OR COALESCE(snapshot_id, scraped_at) NOT IN ({preserved_snapshot_placeholders})""",
                preserved_snapshot_ids,
            )

        if settings.persist_inapp_notifications and settings.notification_retention_days > 0:
            notification_cutoff = _retention_cutoff(
                retention_anchor_at, settings.notification_retention_days
            )
            deleted_notifications_cur = await db.execute(
                """
                DELETE FROM notifications
                WHERE datetime(created_at) < datetime(?)
                """,
                (notification_cutoff,),
            )
        else:
            deleted_notifications_cur = await db.execute("DELETE FROM notifications")

        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    return {
        "deleted_stale_odds": deleted_stale_odds_cur.rowcount or 0,
        "deleted_stale_unresolved_odds": deleted_unresolved_cur.rowcount or 0,
        "deleted_stale_resolved_event_members": (
            deleted_resolved_event_members_cur.rowcount or 0
        ),
        "deleted_stale_match_bookmaker_sources": deleted_match_sources_cur.rowcount or 0,
        "deleted_stale_opportunities": deleted_opportunities_cur.rowcount or 0,
        "deleted_stale_opportunity_publishes": (
            deleted_opportunity_publishes_cur.rowcount or 0
        ),
        "deleted_stale_scrape_snapshots": deleted_scrape_snapshots_cur.rowcount or 0,
        "deleted_odds_history": deleted_odds_history_cur.rowcount or 0,
        "deleted_team_review_cases": deleted_team_reviews_cur.rowcount or 0,
        "deleted_notifications": deleted_notifications_cur.rowcount or 0,
    }


# ── System Status ──────────────────────────────────────────

async def get_system_status(
    scheduler_running: bool = False,
    scan_progress: ScanProgressOut | None = None,
) -> SystemStatus:
    db = await get_db()
    current_snapshot_id, current_snapshot_at = await _get_current_snapshot(db)
    if current_snapshot_id is not None:
        matches_row = await db.execute_fetchall(
            """SELECT COUNT(DISTINCT match_id) as c
               FROM (
                   SELECT match_id FROM odds WHERE snapshot_id = ?
                   UNION
                   SELECT match_id FROM outcome_offers WHERE snapshot_id = ?
               )""",
            (current_snapshot_id, current_snapshot_id),
        )
        odds_row = await db.execute_fetchall(
            """SELECT (
                   (SELECT COUNT(*) FROM odds WHERE snapshot_id = ?)
                   + (SELECT COUNT(*) FROM outcome_offers WHERE snapshot_id = ?)
               ) as c""",
            (current_snapshot_id, current_snapshot_id),
        )
        matches_count = matches_row[0][0]
        odds_count = odds_row[0][0]
        last_scrape_at = current_snapshot_at or current_snapshot_id
    else:
        if await _has_scrape_snapshots(db):
            matches_count = 0
            odds_count = 0
            last_scrape_at = None
        else:
            legacy_window = await _get_legacy_snapshot_cutoff(db)
            if legacy_window is None:
                matches_count = 0
                odds_count = 0
                last_scrape_at = None
            else:
                last_scrape_at, cutoff_at = legacy_window
                matches_row = await db.execute_fetchall(
                    """SELECT COUNT(DISTINCT match_id) as c
                       FROM (
                           SELECT match_id FROM odds WHERE scraped_at >= ?
                           UNION
                           SELECT match_id FROM outcome_offers WHERE scraped_at >= ?
                       )""",
                    (cutoff_at, cutoff_at),
                )
                odds_row = await db.execute_fetchall(
                    """SELECT (
                           (SELECT COUNT(*) FROM odds WHERE scraped_at >= ?)
                           + (SELECT COUNT(*) FROM outcome_offers WHERE scraped_at >= ?)
                       ) as c""",
                    (cutoff_at, cutoff_at),
                )
                matches_count = matches_row[0][0]
                odds_count = odds_row[0][0]
    current_publish_id = await _get_current_opportunity_publish_id(db)
    if current_publish_id is not None:
        opportunity_row = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM opportunities WHERE publish_id = ? AND is_active = TRUE",
            (current_publish_id,),
        )
    else:
        opportunity_row = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM opportunities WHERE is_active = TRUE"
        )
    bm_row = await db.execute_fetchall("SELECT COUNT(*) as c FROM bookmakers WHERE is_active = TRUE")

    return SystemStatus(
        status="ok",
        last_scrape_at=last_scrape_at,
        total_matches=matches_count,
        total_odds=odds_count,
        total_opportunities=opportunity_row[0][0],
        active_bookmakers=bm_row[0][0],
        scheduler_running=scheduler_running,
        scan=scan_progress or ScanProgressOut(),
    )
