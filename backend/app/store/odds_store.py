from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

import aiosqlite

from ..database import get_db
from ..models.schemas import (
    BookmakerOut,
    DiscrepancyDetail,
    DiscrepancyOut,
    LeagueOut,
    MatchOut,
    NormalizedOdds,
    NotificationOut,
    OddsOut,
    SystemStatus,
)


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(row)


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


# ── Matches ────────────────────────────────────────────────

async def upsert_match(
    id: str,
    league_id: str,
    home_team: str,
    away_team: str,
    start_time: str | None = None,
    status: str = "upcoming",
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT OR REPLACE INTO matches (id, league_id, home_team, away_team, start_time, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, league_id, home_team, away_team, start_time, status),
    )
    await db.commit()


async def get_matches(
    league_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[MatchOut]:
    db = await get_db()
    q = "SELECT * FROM matches WHERE 1=1"
    params: list = []
    if league_id:
        q += " AND league_id = ?"
        params.append(league_id)
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY start_time ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(q, params)
    return [MatchOut(**_row_to_dict(r)) for r in rows]


async def get_match(match_id: str) -> MatchOut | None:
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT * FROM matches WHERE id = ?", (match_id,)
    )
    if not row:
        return None
    return MatchOut(**_row_to_dict(row[0]))


# ── Odds ───────────────────────────────────────────────────

async def upsert_odds(odds: NormalizedOdds) -> int:
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
            datetime.utcnow().isoformat(),
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
            datetime.utcnow().isoformat(),
        ),
    )
    await db.commit()
    cursor = await db.execute("SELECT last_insert_rowid()")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_odds_for_match(match_id: str) -> list[OddsOut]:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT o.*, b.name as bookmaker_name
           FROM odds o
           LEFT JOIN bookmakers b ON o.bookmaker_id = b.id
           WHERE o.match_id = ?
           ORDER BY o.market_type, o.player_name, o.threshold""",
        (match_id,),
    )
    return [OddsOut(**_row_to_dict(r)) for r in rows]


async def get_odds_history_for_match(match_id: str) -> list[OddsOut]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM odds_history WHERE match_id = ? ORDER BY scraped_at DESC",
        (match_id,),
    )
    return [OddsOut(**_row_to_dict(r)) for r in rows]


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
) -> int:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO discrepancies
           (match_id, market_type, player_name, bookmaker_a_id, bookmaker_b_id,
            threshold_a, threshold_b, odds_a, odds_b, gap, profit_margin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            match_id, market_type, player_name,
            bookmaker_a_id, bookmaker_b_id,
            threshold_a, threshold_b,
            odds_a, odds_b, gap, profit_margin,
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
    market_type: str | None = None,
    min_gap: float | None = None,
    sort_by: str = "profit_margin",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    active_only: bool = True,
) -> list[DiscrepancyDetail]:
    db = await get_db()
    q = """SELECT d.*, m.home_team, m.away_team, l.name as league_name,
                  ba.name as bookmaker_a_name, bb.name as bookmaker_b_name
           FROM discrepancies d
           LEFT JOIN matches m ON d.match_id = m.id
           LEFT JOIN leagues l ON m.league_id = l.id
           LEFT JOIN bookmakers ba ON d.bookmaker_a_id = ba.id
           LEFT JOIN bookmakers bb ON d.bookmaker_b_id = bb.id"""
    conditions = []
    params: list = []

    if active_only:
        conditions.append("d.is_active = TRUE")
    if market_type:
        conditions.append("d.market_type = ?")
        params.append(market_type)
    if min_gap is not None:
        conditions.append("d.gap >= ?")
        params.append(min_gap)
    if league_id:
        conditions.append("m.league_id = ?")
        params.append(league_id)
    if sport:
        conditions.append("l.sport = ?")
        params.append(sport)

    if conditions:
        q += " WHERE " + " AND ".join(conditions)

    allowed_sort = {"profit_margin", "gap", "detected_at", "odds_a", "odds_b"}
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
                  ba.name as bookmaker_a_name, bb.name as bookmaker_b_name
           FROM discrepancies d
           LEFT JOIN matches m ON d.match_id = m.id
           LEFT JOIN bookmakers ba ON d.bookmaker_a_id = ba.id
           LEFT JOIN bookmakers bb ON d.bookmaker_b_id = bb.id
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


# ── System Status ──────────────────────────────────────────

async def get_system_status(scheduler_running: bool = False) -> SystemStatus:
    db = await get_db()
    matches_row = await db.execute_fetchall("SELECT COUNT(*) as c FROM matches")
    odds_row = await db.execute_fetchall("SELECT COUNT(*) as c FROM odds")
    disc_row = await db.execute_fetchall("SELECT COUNT(*) as c FROM discrepancies WHERE is_active = TRUE")
    bm_row = await db.execute_fetchall("SELECT COUNT(*) as c FROM bookmakers WHERE is_active = TRUE")
    last_scrape = await db.execute_fetchall("SELECT MAX(scraped_at) as t FROM odds")

    return SystemStatus(
        status="ok",
        last_scrape_at=last_scrape[0][0] if last_scrape and last_scrape[0][0] else None,
        total_matches=matches_row[0][0],
        total_odds=odds_row[0][0],
        total_discrepancies=disc_row[0][0],
        active_bookmakers=bm_row[0][0],
        scheduler_running=scheduler_running,
    )
