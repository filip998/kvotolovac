from __future__ import annotations

import json
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
    DiscrepancyDetail,
    DiscrepancyOut,
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
    ResolvedEventIn,
    ResolvedEventMemberIn,
    ResolvedEventMemberOut,
    ResolvedEventOut,
    ScanProgressOut,
    SystemStatus,
    TeamReviewCandidate,
    TeamReviewDiagnostic,
    TeamReviewOut,
    UnresolvedOddsDiagnostic,
    UnresolvedOddsOut,
)
from ..services.canonical_offers import (
    canonical_offer_from_normalized_outcome_offer,
    canonical_offers_from_normalized_odds,
)
from ..services.event_player_resolver import build_event_scoped_player_odds


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(row)


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


async def set_current_snapshot(snapshot_at: str) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO scrape_state (id, current_snapshot_at, updated_at)
           VALUES (1, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
               current_snapshot_at = excluded.current_snapshot_at,
               updated_at = CURRENT_TIMESTAMP""",
        (snapshot_at,),
    )
    await db.commit()


async def _get_current_snapshot_at(db: aiosqlite.Connection) -> str | None:
    row = await db.execute_fetchall(
        "SELECT current_snapshot_at FROM scrape_state WHERE id = 1"
    )
    if not row or not row[0][0]:
        return None
    return row[0][0]


async def _get_legacy_snapshot_cutoff(db: aiosqlite.Connection) -> tuple[str, str] | None:
    row = await db.execute_fetchall("SELECT MAX(scraped_at) AS t FROM odds")
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


async def _get_team_review_snapshot_at(db: aiosqlite.Connection) -> str | None:
    snapshot_at = await _get_current_snapshot_at(db)
    if snapshot_at is not None:
        return snapshot_at
    return await _get_latest_team_review_snapshot_at(db)


async def _current_or_legacy_snapshot_filter(
    db: aiosqlite.Connection,
    alias: str,
) -> tuple[str | None, list[object]]:
    current_snapshot_at = await _get_current_snapshot_at(db)
    if current_snapshot_at is not None:
        return f"{alias}.scraped_at = ?", [current_snapshot_at]

    legacy_window = await _get_legacy_snapshot_cutoff(db)
    if legacy_window is None:
        return None, []
    _, cutoff_at = legacy_window
    return f"{alias}.scraped_at >= ?", [cutoff_at]


# ── Matches ────────────────────────────────────────────────

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
    match_id: str,
    bookmaker_id: str,
    source_url: str | None,
) -> None:
    await db.execute(
        """INSERT INTO match_bookmaker_sources (match_id, bookmaker_id, source_url)
           VALUES (?, ?, ?)
           ON CONFLICT(match_id, bookmaker_id) DO UPDATE SET
                source_url = COALESCE(excluded.source_url, match_bookmaker_sources.source_url),
                updated_at = CURRENT_TIMESTAMP""",
        (match_id, bookmaker_id, source_url),
    )


async def upsert_match_bookmaker_source(
    *,
    match_id: str,
    bookmaker_id: str,
    source_url: str | None,
) -> None:
    db = await get_db()
    await _upsert_match_bookmaker_source_tx(
        db,
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        source_url=source_url,
    )
    await db.commit()


async def get_matches(
    league_id: str | None = None,
    sport: str | None = None,
    status: str | None = None,
    bookmaker_ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MatchOut]:
    db = await get_db()
    current_snapshot_at = await _get_current_snapshot_at(db)
    snapshot_at: str | None = current_snapshot_at
    cutoff_at: str | None = None
    bookmaker_filter = ""
    if bookmaker_ids:
        bookmaker_filter = f" AND {{alias}}.bookmaker_id IN ({_sql_placeholders(bookmaker_ids)})"

    if current_snapshot_at is not None:
        odds_filter = "o.scraped_at = ?" + bookmaker_filter.format(alias="o")
        offers_filter = "oo.scraped_at = ?" + bookmaker_filter.format(alias="oo")
        params: list[object] = [current_snapshot_at]
        if bookmaker_ids:
            params.extend(bookmaker_ids)
        params.append(current_snapshot_at)
        if bookmaker_ids:
            params.extend(bookmaker_ids)
        q = f"""SELECT m.*, l.name as league_name,
                      (
                          SELECT rem.resolved_event_id
                          FROM resolved_event_members rem
                          WHERE rem.match_id = m.id AND rem.status = 'active'
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
    else:
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
                          WHERE rem.match_id = m.id AND rem.status = 'active'
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
        q += " AND m.league_id = ?"
        params.append(league_id)
    if sport:
        q += " AND m.sport = ?"
        params.append(sport)
    if status:
        q += " AND m.status = ?"
        params.append(status)
    q += " ORDER BY m.start_time ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)

    match_rows = [_row_to_dict(r) for r in rows]
    bookmaker_map = await _get_match_bookmaker_map(
        db,
        [row["id"] for row in match_rows],
        snapshot_at=snapshot_at,
        cutoff_at=cutoff_at,
    )

    for row in match_rows:
        row["available_bookmakers"] = bookmaker_map.get(row["id"], [])

    return [MatchOut(**row) for row in match_rows]


async def get_match(match_id: str) -> MatchOut | None:
    db = await get_db()
    row = await db.execute_fetchall(
        """SELECT m.*, l.name as league_name,
                  (
                      SELECT rem.resolved_event_id
                      FROM resolved_event_members rem
                      WHERE rem.match_id = m.id AND rem.status = 'active'
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
    return MatchOut(**_row_to_dict(row[0]))


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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id, bookmaker_id) DO UPDATE SET
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
            match_id=member.match_id,
            bookmaker_id=member.bookmaker_id,
            source_url=member.source_url,
        )
        rows = await db.execute_fetchall(
            """SELECT id
               FROM resolved_event_members
               WHERE match_id = ? AND bookmaker_id = ?""",
            (member.match_id, member.bookmaker_id),
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
) -> list[ResolvedEventMemberOut]:
    db = await get_db()
    q = """SELECT m.*, b.name AS bookmaker_name
           FROM resolved_event_members m
           LEFT JOIN bookmakers b ON b.id = m.bookmaker_id
           WHERE m.resolved_event_id = ?"""
    params: list[object] = [resolved_event_id]
    if status:
        q += " AND m.status = ?"
        params.append(status)
    q += " ORDER BY m.id ASC"
    rows = await db.execute_fetchall(q, params)
    return [_row_to_resolved_event_member(row) for row in rows]


async def get_eligible_resolved_event_members_for_matches(
    match_ids: list[str],
    *,
    bookmaker_ids: list[str] | None = None,
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
    match_placeholders = _sql_placeholders(match_ids)
    method_placeholders = _sql_placeholders(list(event_methods))
    q = f"""SELECT rem.*, b.name AS bookmaker_name
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

    q += " ORDER BY re.start_time ASC, rem.resolved_event_id ASC, rem.id ASC"
    rows = await db.execute_fetchall(q, params)
    return [_row_to_resolved_event_member(row) for row in rows]


async def get_eligible_resolved_event_members_for_odds(
    odds_list: list[NormalizedOdds],
    *,
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
        event_methods=event_methods,
    )


async def get_eligible_resolved_event_members_for_outcome_offers(
    offers: list[NormalizedOutcomeOffer],
    *,
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
) -> ResolvedEventMemberOut | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT m.*, b.name AS bookmaker_name
           FROM resolved_event_members m
           LEFT JOIN bookmakers b ON b.id = m.bookmaker_id
           WHERE m.match_id = ? AND m.bookmaker_id = ?""",
        (match_id, bookmaker_id),
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

    resolved_event_ids = list(
        dict.fromkeys(
            event_id
            for event_id in (case.resolved_event_id, case.candidate_resolved_event_id)
            if event_id
        )
    )
    for resolved_event_id in resolved_event_ids:
        rows = await db.execute_fetchall(
            """SELECT rem.match_id,
                      rem.bookmaker_id,
                      b.name AS bookmaker_name,
                      m.league_id,
                      l.name AS league_name,
                      m.home_team,
                      m.away_team,
                      m.start_time,
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
               LEFT JOIN leagues l ON l.id = m.league_id
               WHERE rem.resolved_event_id = ?
               ORDER BY rem.id ASC""",
            (resolved_event_id,),
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
    if candidate_match_ids:
        placeholders = _sql_placeholders(candidate_match_ids)
        rows = await db.execute_fetchall(
            f"""SELECT m.id AS match_id,
                       s.bookmaker_id,
                       b.name AS bookmaker_name,
                       m.league_id,
                       l.name AS league_name,
                       m.home_team,
                       m.away_team,
                       m.start_time,
                       s.source_url,
                       NULL AS source_league_id,
                       l.name AS source_league_name,
                       m.home_team AS source_home_team,
                       m.away_team AS source_away_team,
                       m.start_time AS source_start_time,
                       'as_listed' AS orientation,
                       NULL AS member_confidence,
                       '[]' AS member_evidence
                FROM matches m
                LEFT JOIN leagues l ON l.id = m.league_id
                LEFT JOIN match_bookmaker_sources s ON s.match_id = m.id
                LEFT JOIN bookmakers b ON b.id = s.bookmaker_id
                WHERE m.id IN ({placeholders})
                ORDER BY m.start_time ASC, m.id ASC, s.bookmaker_id ASC""",
            candidate_match_ids,
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
                           m.league_id,
                           l.name AS league_name,
                           m.home_team,
                           m.away_team,
                           m.start_time
                    FROM matches m
                    LEFT JOIN leagues l ON l.id = m.league_id
                    WHERE m.id IN ({placeholders})""",
                candidate_match_ids,
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
            "reassigned_discrepancies": 0,
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
            SELECT id, bookmaker_id, market_type, player_name, threshold
            FROM odds
            WHERE match_id IN ({all_placeholders})
            """,
            all_match_ids,
        )

        groups: dict[tuple, list[int]] = {}
        for row in rows:
            key = (
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
            SELECT id, bookmaker_id, market_type, outcome_code,
                   COALESCE(line, -999999.0) AS line_key
            FROM outcome_offers
            WHERE match_id IN ({all_placeholders})
            """,
            all_match_ids,
        )

        outcome_groups: dict[tuple, list[int]] = {}
        for row in outcome_rows:
            key = (
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

        # 5. match_bookmaker_sources has UNIQUE(match_id, bookmaker_id), so it
        #    needs the same dedupe-before-update treatment as odds.
        source_rows = await db.execute_fetchall(
            f"""
            SELECT id, match_id, bookmaker_id, source_url
            FROM match_bookmaker_sources
            WHERE match_id IN ({all_placeholders})
            """,
            all_match_ids,
        )

        source_groups: dict[str, list[aiosqlite.Row]] = {}
        for row in source_rows:
            source_groups.setdefault(str(row["bookmaker_id"]), []).append(row)

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

        # 6. discrepancies: bulk update; no UNIQUE constraint. The duplicate
        #    rows will be deactivated on the next discrepancy detection cycle.
        reassigned_disc_cur = await db.execute(
            f"UPDATE discrepancies SET match_id = ? WHERE match_id IN ({placeholders})",
            [target_match_id, *params],
        )
        reassigned_disc = reassigned_disc_cur.rowcount or 0

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
        "reassigned_discrepancies": reassigned_disc,
        "reassigned_opportunities": reassigned_opportunities,
        "deleted_source_matches": deleted_matches,
    }


async def _get_match_bookmaker_map(
    db: aiosqlite.Connection,
    match_ids: list[str],
    *,
    snapshot_at: str | None,
    cutoff_at: str | None,
) -> dict[str, list[MatchBookmakerOut]]:
    if not match_ids:
        return {}

    placeholders = _sql_placeholders(match_ids)
    params: list[object] = list(match_ids)

    odds_filter = ""
    offers_filter = ""
    if snapshot_at is not None:
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

    if snapshot_at is not None:
        params.append(snapshot_at)
    elif cutoff_at is not None:
        params.append(cutoff_at)
    params.extend(match_ids)
    if snapshot_at is not None:
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
) -> list[NormalizedOdds]:
    selected_match_ids = list(dict.fromkeys(match_ids))
    if not selected_match_ids:
        return []

    db = await get_db()
    snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    if snapshot_filter is None:
        return []

    return await _get_normalized_odds_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
    )


async def _get_normalized_odds_for_matches_snapshot(
    db: aiosqlite.Connection,
    selected_match_ids: list[str],
    *,
    snapshot_filter: str,
    snapshot_params: list[object],
) -> list[NormalizedOdds]:
    placeholders = _sql_placeholders(selected_match_ids)
    rows = await db.execute_fetchall(
        f"""SELECT o.match_id,
                   o.bookmaker_id,
                   m.league_id,
                   m.sport,
                   COALESCE(m.home_team_id, 0) AS home_team_id,
                   COALESCE(m.away_team_id, 0) AS away_team_id,
                   m.home_team,
                   m.away_team,
                   s.source_url,
                   o.market_type,
                   o.player_name,
                   o.threshold,
                   o.over_odds,
                   o.under_odds,
                   m.start_time,
                   o.scraped_at
            FROM odds o
            JOIN matches m ON m.id = o.match_id
            LEFT JOIN match_bookmaker_sources s
              ON s.match_id = o.match_id AND s.bookmaker_id = o.bookmaker_id
            WHERE o.match_id IN ({placeholders})
              AND {snapshot_filter}
            ORDER BY m.start_time ASC, o.match_id ASC, o.bookmaker_id ASC,
                     o.market_type ASC, o.player_name ASC, o.threshold ASC""",
        [*selected_match_ids, *snapshot_params],
    )
    return [NormalizedOdds(**_row_to_dict(row)) for row in rows]


async def get_current_normalized_outcome_offers_for_matches(
    match_ids: list[str],
) -> list[NormalizedOutcomeOffer]:
    selected_match_ids = list(dict.fromkeys(match_ids))
    if not selected_match_ids:
        return []

    db = await get_db()
    snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    if snapshot_filter is None:
        return []

    return await _get_normalized_outcome_offers_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
    )


async def _get_normalized_outcome_offers_for_matches_snapshot(
    db: aiosqlite.Connection,
    selected_match_ids: list[str],
    *,
    snapshot_filter: str,
    snapshot_params: list[object],
) -> list[NormalizedOutcomeOffer]:
    placeholders = _sql_placeholders(selected_match_ids)
    rows = await db.execute_fetchall(
        f"""SELECT o.match_id,
                   o.bookmaker_id,
                   m.league_id,
                   m.sport,
                   COALESCE(m.home_team_id, 0) AS home_team_id,
                   COALESCE(m.away_team_id, 0) AS away_team_id,
                   m.home_team,
                   m.away_team,
                   s.source_url,
                   o.market_type,
                   o.outcome_code,
                   o.odds,
                   o.line,
                   o.raw_label,
                   m.start_time,
                   o.scraped_at
            FROM outcome_offers o
            JOIN matches m ON m.id = o.match_id
            LEFT JOIN match_bookmaker_sources s
              ON s.match_id = o.match_id AND s.bookmaker_id = o.bookmaker_id
            WHERE o.match_id IN ({placeholders})
              AND {snapshot_filter}
            ORDER BY m.start_time ASC, o.match_id ASC, o.bookmaker_id ASC,
                     o.market_type ASC, o.line ASC, o.outcome_code ASC""",
        [*selected_match_ids, *snapshot_params],
    )
    return [NormalizedOutcomeOffer(**_row_to_dict(row)) for row in rows]


async def get_current_canonical_offers_for_matches(
    match_ids: list[str],
) -> list[CanonicalOffer]:
    selected_match_ids = list(dict.fromkeys(match_ids))
    if not selected_match_ids:
        return []

    db = await get_db()
    snapshot_filter, snapshot_params = await _current_or_legacy_snapshot_filter(db, "o")
    if snapshot_filter is None:
        return []

    odds_rows = await _get_normalized_odds_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
    )
    outcome_offer_rows = await _get_normalized_outcome_offers_for_matches_snapshot(
        db,
        selected_match_ids,
        snapshot_filter=snapshot_filter,
        snapshot_params=snapshot_params,
    )
    resolved_event_members = await _eligible_resolved_event_members_for_offer_rows(
        [*odds_rows, *outcome_offer_rows]
    )
    resolved_event_ids = _resolved_event_ids_for_offer_rows(
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
            )
        )
    return canonical_offers


async def _eligible_resolved_event_members_for_offer_rows(
    rows: list[NormalizedOdds | NormalizedOutcomeOffer],
) -> list[ResolvedEventMemberOut]:
    row_keys = {(row.match_id, row.bookmaker_id) for row in rows}
    if not row_keys:
        return []

    return await get_eligible_resolved_event_members_for_matches(
        sorted({match_id for match_id, _ in row_keys}),
        bookmaker_ids=sorted({bookmaker_id for _, bookmaker_id in row_keys}),
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


# ── Odds ───────────────────────────────────────────────────

async def upsert_odds(odds: NormalizedOdds, *, scraped_at: str) -> int:
    db = await get_db()
    await db.execute(
        """INSERT OR REPLACE INTO odds
           (match_id, bookmaker_id, market_type, player_name, threshold, over_odds, under_odds, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
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
           (match_id, bookmaker_id, market_type, player_name, threshold, over_odds, under_odds, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
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
    current_snapshot_at = await _get_current_snapshot_at(db)
    if current_snapshot_at is not None:
        rows = await db.execute_fetchall(
            """SELECT o.*, b.name as bookmaker_name, s.source_url as source_url
               FROM odds o
               LEFT JOIN bookmakers b ON o.bookmaker_id = b.id
               LEFT JOIN match_bookmaker_sources s
                 ON s.match_id = o.match_id AND s.bookmaker_id = o.bookmaker_id
               WHERE o.match_id = ? AND o.scraped_at = ?
               ORDER BY o.market_type, o.player_name, o.threshold""",
            (match_id, current_snapshot_at),
        )
    else:
        legacy_window = await _get_legacy_snapshot_cutoff(db)
        if legacy_window is None:
            return []
        _, cutoff_at = legacy_window
        rows = await db.execute_fetchall(
            """SELECT o.*, b.name as bookmaker_name, s.source_url as source_url
               FROM odds o
               LEFT JOIN bookmakers b ON o.bookmaker_id = b.id
               LEFT JOIN match_bookmaker_sources s
                 ON s.match_id = o.match_id AND s.bookmaker_id = o.bookmaker_id
               WHERE o.match_id = ? AND o.scraped_at >= ?
               ORDER BY o.market_type, o.player_name, o.threshold""",
            (match_id, cutoff_at),
        )
    return [OddsOut(**_row_to_dict(r)) for r in rows]


async def get_odds_history_for_match(match_id: str) -> list[OddsOut]:
    db = await get_db()
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
    await db.execute(
        """INSERT OR REPLACE INTO outcome_offers
           (match_id, bookmaker_id, market_type, outcome_code, line, odds, raw_label, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
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
        match_id=offer.match_id,
        bookmaker_id=offer.bookmaker_id,
        source_url=offer.source_url,
    )
    await db.commit()
    row = await db.execute_fetchall(
        """SELECT id FROM outcome_offers
           WHERE match_id = ?
             AND bookmaker_id = ?
             AND market_type = ?
             AND outcome_code = ?
             AND COALESCE(line, -999999.0) = COALESCE(?, -999999.0)""",
        (
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
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[OutcomeOfferOut]:
    db = await get_db()
    snapshot_at = await _get_current_snapshot_at(db)
    if snapshot_at is None:
        return []

    q = """SELECT o.*, b.name AS bookmaker_name, s.source_url AS source_url
           FROM outcome_offers o
           JOIN matches m ON m.id = o.match_id
           LEFT JOIN bookmakers b ON b.id = o.bookmaker_id
           LEFT JOIN match_bookmaker_sources s
             ON s.match_id = o.match_id AND s.bookmaker_id = o.bookmaker_id"""
    conditions = ["o.scraped_at = ?"]
    params: list[object] = [snapshot_at]

    if sport:
        conditions.append("m.sport = ?")
        params.append(sport)
    if match_id:
        conditions.append("o.match_id = ?")
        params.append(match_id)
    if bookmaker_ids:
        placeholders = _sql_placeholders(bookmaker_ids)
        conditions.append(f"o.bookmaker_id IN ({placeholders})")
        params.extend(bookmaker_ids)
    if market_type:
        conditions.append("o.market_type = ?")
        params.append(market_type)

    q += " WHERE " + " AND ".join(conditions)
    q += " ORDER BY m.start_time ASC, o.market_type ASC, o.line ASC, o.outcome_code ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    return [OutcomeOfferOut(**_row_to_dict(row)) for row in rows]


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
           (sport, match_id, resolved_event_id, opportunity_type, market_type, line, profit_margin,
            middle_profit_margin, legs, detected_at, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)""",
        (
            opportunity.sport,
            opportunity.match_id,
            opportunity.resolved_event_id,
            opportunity.opportunity_type,
            opportunity.market_type,
            opportunity.line,
            opportunity.profit_margin,
            opportunity.middle_profit_margin,
            json.dumps([leg.model_dump() for leg in opportunity.legs]),
            detected_at,
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


def _row_to_opportunity(row: aiosqlite.Row) -> OpportunityOut:
    data = _row_to_dict(row)
    raw_legs = data.get("legs")
    legs_payload = json.loads(raw_legs) if isinstance(raw_legs, str) and raw_legs else []
    legs: list[OpportunityLeg] = []
    for leg_data in legs_payload:
        legs.append(OpportunityLeg(**leg_data))
    data["legs"] = legs
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
    q = """SELECT op.*, m.home_team, m.away_team, m.start_time, l.name AS league_name
           FROM opportunities op
           LEFT JOIN matches m ON m.id = op.match_id
           LEFT JOIN leagues l ON l.id = m.league_id"""
    conditions = ["op.is_active = TRUE"]
    params: list[object] = []
    if sport:
        conditions.append("op.sport = ?")
        params.append(sport)
    if not include_legacy_discrepancy_overlap:
        conditions.append("op.sport != ?")
        params.append("basketball")
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
                    m.start_time ASC,
                    op.id ASC
             LIMIT ? OFFSET ?"""
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    opportunities = [_row_to_opportunity(row) for row in rows]
    await _enrich_opportunity_legs(db, opportunities)
    return opportunities


async def _enrich_opportunity_legs(
    db: aiosqlite.Connection,
    opportunities: list[OpportunityOut],
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
        source_rows = await db.execute_fetchall(
            f"""SELECT match_id, bookmaker_id, source_url
                FROM match_bookmaker_sources
                WHERE match_id IN ({match_placeholders})
                  AND bookmaker_id IN ({bookmaker_placeholders})""",
            [*match_ids, *bookmaker_ids],
        )
        source_urls = {
            (row["match_id"], row["bookmaker_id"]): row["source_url"]
            for row in source_rows
            if row["source_url"] is not None
        }

    for opportunity in opportunities:
        for leg in opportunity.legs:
            leg.bookmaker_name = leg.bookmaker_name or bookmaker_names.get(leg.bookmaker_id)
            source_match_id = leg.match_id or opportunity.match_id
            leg.source_url = leg.source_url or source_urls.get(
                (source_match_id, leg.bookmaker_id)
            )


# ── Unresolved odds ────────────────────────────────────────

async def insert_unresolved_odds(
    unresolved: UnresolvedOddsDiagnostic,
    *,
    scraped_at: str,
) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO unresolved_odds
           (bookmaker_id, raw_league_id, league_id, sport, market_type, player_name,
            raw_team_name, normalized_team_name, start_time, threshold, over_odds,
            under_odds, reason_code, candidate_count, candidate_matchups,
            available_matchups_same_slot, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
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
    snapshot_at = await _get_current_snapshot_at(db)
    if snapshot_at is None:
        snapshot_at = await _get_latest_unresolved_snapshot_at(db)
    if snapshot_at is None:
        return []

    q = """SELECT u.*, b.name as bookmaker_name, l.name as league_name
           FROM unresolved_odds u
           LEFT JOIN bookmakers b ON u.bookmaker_id = b.id
           LEFT JOIN leagues l ON u.league_id = l.id"""
    conditions = ["u.scraped_at = ?"]
    params: list = [snapshot_at]

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
    cursor = await db.execute(
        """INSERT INTO team_review_cases
           (
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
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
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
    snapshot_at = await _get_team_review_snapshot_at(db)
    if snapshot_at is None:
        return []

    q = """SELECT c.*, b.name AS bookmaker_name, l.name AS scope_league_name
           FROM team_review_cases c
           LEFT JOIN bookmakers b ON c.bookmaker_id = b.id
           LEFT JOIN leagues l ON c.scope_league_id = l.id"""
    conditions = ["c.scraped_at = ?"]
    params: list[object] = [snapshot_at]

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


# ── Discrepancies ──────────────────────────────────────────

async def insert_discrepancy(
    match_id: str,
    market_type: str,
    player_name: str | None,
    bookmaker_a_id: str,
    bookmaker_b_id: str,
    threshold_a: float,
    threshold_b: float,
    odds_a: float | None,
    odds_b: float | None,
    gap: float,
    profit_margin: float | None,
    middle_profit_margin: float | None = None,
    resolved_event_id: str | None = None,
    bookmaker_a_match_id: str | None = None,
    bookmaker_b_match_id: str | None = None,
) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO discrepancies
           (match_id, resolved_event_id, market_type, player_name,
            bookmaker_a_id, bookmaker_a_match_id, bookmaker_b_id, bookmaker_b_match_id,
            threshold_a, threshold_b, odds_a, odds_b, gap, profit_margin, middle_profit_margin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            match_id, resolved_event_id, market_type, player_name,
            bookmaker_a_id, bookmaker_a_match_id or match_id,
            bookmaker_b_id, bookmaker_b_match_id or match_id,
            threshold_a, threshold_b,
            odds_a, odds_b, gap, profit_margin, middle_profit_margin,
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def deactivate_all_discrepancies() -> None:
    db = await get_db()
    await db.execute("UPDATE discrepancies SET is_active = FALSE")
    await db.commit()


async def get_discrepancies(
    sport: str | None = None,
    league_id: str | None = None,
    bookmaker_ids: list[str] | None = None,
    market_type: str | None = None,
    search: str | None = None,
    min_gap: float | None = None,
    sort_by: str = "profit_margin",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    active_only: bool = True,
) -> list[DiscrepancyDetail]:
    db = await get_db()
    q = """SELECT d.*, m.home_team, m.away_team, l.name as league_name,
                  ba.name as bookmaker_a_name, sa.source_url as bookmaker_a_source_url,
                  bb.name as bookmaker_b_name, sb.source_url as bookmaker_b_source_url
           FROM discrepancies d
           LEFT JOIN matches m ON d.match_id = m.id
           LEFT JOIN leagues l ON m.league_id = l.id
            LEFT JOIN bookmakers ba ON d.bookmaker_a_id = ba.id
            LEFT JOIN bookmakers bb ON d.bookmaker_b_id = bb.id
            LEFT JOIN match_bookmaker_sources sa
              ON sa.match_id = COALESCE(d.bookmaker_a_match_id, d.match_id)
             AND sa.bookmaker_id = d.bookmaker_a_id
            LEFT JOIN match_bookmaker_sources sb
              ON sb.match_id = COALESCE(d.bookmaker_b_match_id, d.match_id)
             AND sb.bookmaker_id = d.bookmaker_b_id"""
    conditions = []
    params: list = []

    if active_only:
        conditions.append("d.is_active = TRUE")
    if market_type:
        conditions.append("d.market_type = ?")
        params.append(market_type)
    if bookmaker_ids:
        placeholders = _sql_placeholders(bookmaker_ids)
        conditions.append(
            f"(d.bookmaker_a_id IN ({placeholders}) OR d.bookmaker_b_id IN ({placeholders}))"
        )
        params.extend(bookmaker_ids)
        params.extend(bookmaker_ids)
    if min_gap is not None:
        conditions.append("d.gap >= ?")
        params.append(min_gap)
    if league_id:
        conditions.append("m.league_id = ?")
        params.append(league_id)
    if sport:
        conditions.append("l.sport = ?")
        params.append(sport)
    normalized_search = _normalize_search_text(search)
    if normalized_search:
        await db.create_function("normalize_search_text", 1, _normalize_search_text)
        search_like = f"%{normalized_search}%"
        conditions.append(
            """normalize_search_text(printf(
                '%s %s %s %s',
                COALESCE(m.home_team, ''),
                COALESCE(m.away_team, ''),
                printf('%s %s', COALESCE(m.home_team, ''), COALESCE(m.away_team, '')),
                COALESCE(d.player_name, '')
            )) LIKE ?"""
        )
        params.append(search_like)

    if conditions:
        q += " WHERE " + " AND ".join(conditions)

    allowed_sort = {"profit_margin", "middle_profit_margin", "gap", "detected_at", "odds_a", "odds_b"}
    col = sort_by if sort_by in allowed_sort else "profit_margin"
    order = "DESC" if sort_order.lower() == "desc" else "ASC"
    q += f" ORDER BY d.{col} {order} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = await db.execute_fetchall(q, params)
    return [DiscrepancyDetail(**_row_to_dict(r)) for r in rows]


async def get_discrepancy(disc_id: int) -> DiscrepancyDetail | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT d.*, m.home_team, m.away_team,
                  ba.name as bookmaker_a_name, sa.source_url as bookmaker_a_source_url,
                  bb.name as bookmaker_b_name, sb.source_url as bookmaker_b_source_url
           FROM discrepancies d
           LEFT JOIN matches m ON d.match_id = m.id
            LEFT JOIN bookmakers ba ON d.bookmaker_a_id = ba.id
            LEFT JOIN bookmakers bb ON d.bookmaker_b_id = bb.id
            LEFT JOIN match_bookmaker_sources sa
              ON sa.match_id = COALESCE(d.bookmaker_a_match_id, d.match_id)
             AND sa.bookmaker_id = d.bookmaker_a_id
            LEFT JOIN match_bookmaker_sources sb
              ON sb.match_id = COALESCE(d.bookmaker_b_match_id, d.match_id)
             AND sb.bookmaker_id = d.bookmaker_b_id
            WHERE d.id = ?""",
        (disc_id,),
    )
    if not rows:
        return None
    return DiscrepancyDetail(**_row_to_dict(rows[0]))


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


def _retention_cutoff(snapshot_at: str, days: int) -> str:
    return (datetime.fromisoformat(snapshot_at) - timedelta(days=days)).isoformat()


async def cleanup_retained_data(current_snapshot_at: str) -> dict[str, int]:
    db = await aiosqlite.connect(settings.db_path)
    try:
        await db.execute("BEGIN IMMEDIATE")
        deleted_stale_odds_cur = await db.execute(
            "DELETE FROM odds WHERE scraped_at IS NULL OR scraped_at != ?",
            (current_snapshot_at,),
        )
        deleted_unresolved_cur = await db.execute(
            "DELETE FROM unresolved_odds WHERE scraped_at IS NULL OR scraped_at != ?",
            (current_snapshot_at,),
        )
        deleted_inactive_discrepancies_cur = await db.execute(
            "DELETE FROM discrepancies WHERE is_active = FALSE"
        )

        if settings.odds_history_retention_days > 0:
            odds_history_cutoff = _retention_cutoff(
                current_snapshot_at, settings.odds_history_retention_days
            )
            deleted_odds_history_cur = await db.execute(
                """
                DELETE FROM odds_history
                WHERE scraped_at IS NOT NULL
                  AND datetime(scraped_at) < datetime(?)
                """,
                (odds_history_cutoff,),
            )
        else:
            deleted_odds_history_cur = await db.execute(
                "DELETE FROM odds_history WHERE scraped_at IS NOT NULL"
            )

        if settings.team_review_retention_days > 0:
            team_review_cutoff = _retention_cutoff(
                current_snapshot_at, settings.team_review_retention_days
            )
            deleted_team_reviews_cur = await db.execute(
                """
                DELETE FROM team_review_cases
                WHERE scraped_at IS NOT NULL
                  AND datetime(scraped_at) < datetime(?)
                """,
                (team_review_cutoff,),
            )
        else:
            deleted_team_reviews_cur = await db.execute(
                "DELETE FROM team_review_cases WHERE scraped_at IS NOT NULL"
            )

        if settings.persist_inapp_notifications and settings.notification_retention_days > 0:
            notification_cutoff = _retention_cutoff(
                current_snapshot_at, settings.notification_retention_days
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
        "deleted_inactive_discrepancies": deleted_inactive_discrepancies_cur.rowcount or 0,
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
    current_snapshot_at = await _get_current_snapshot_at(db)
    if current_snapshot_at is not None:
        matches_row = await db.execute_fetchall(
            "SELECT COUNT(DISTINCT match_id) as c FROM odds WHERE scraped_at = ?",
            (current_snapshot_at,),
        )
        odds_row = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM odds WHERE scraped_at = ?",
            (current_snapshot_at,),
        )
        matches_count = matches_row[0][0]
        odds_count = odds_row[0][0]
        last_scrape_at = current_snapshot_at
    else:
        legacy_window = await _get_legacy_snapshot_cutoff(db)
        if legacy_window is None:
            matches_count = 0
            odds_count = 0
            last_scrape_at = None
        else:
            last_scrape_at, cutoff_at = legacy_window
            matches_row = await db.execute_fetchall(
                "SELECT COUNT(DISTINCT match_id) as c FROM odds WHERE scraped_at >= ?",
                (cutoff_at,),
            )
            odds_row = await db.execute_fetchall(
                "SELECT COUNT(*) as c FROM odds WHERE scraped_at >= ?",
                (cutoff_at,),
            )
            matches_count = matches_row[0][0]
            odds_count = odds_row[0][0]
    disc_row = await db.execute_fetchall("SELECT COUNT(*) as c FROM discrepancies WHERE is_active = TRUE")
    bm_row = await db.execute_fetchall("SELECT COUNT(*) as c FROM bookmakers WHERE is_active = TRUE")

    return SystemStatus(
        status="ok",
        last_scrape_at=last_scrape_at,
        total_matches=matches_count,
        total_odds=odds_count,
        total_discrepancies=disc_row[0][0],
        active_bookmakers=bm_row[0][0],
        scheduler_running=scheduler_running,
        scan=scan_progress or ScanProgressOut(),
    )
