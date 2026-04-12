from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Bookmaker ──────────────────────────────────────────────
class BookmakerOut(BaseModel):
    id: str
    name: str
    website_url: Optional[str] = None
    is_active: bool = True


# ── League ─────────────────────────────────────────────────
class LeagueOut(BaseModel):
    id: str
    name: str
    sport: str
    country: Optional[str] = None
    is_active: bool = True


# ── Match ──────────────────────────────────────────────────
class MatchOut(BaseModel):
    id: str
    league_id: Optional[str] = None
    home_team: str
    away_team: str
    start_time: Optional[str] = None
    status: str = "upcoming"


# ── Odds ───────────────────────────────────────────────────
class OddsOut(BaseModel):
    id: int
    match_id: str
    bookmaker_id: str
    bookmaker_name: Optional[str] = None
    market_type: str
    player_name: Optional[str] = None
    threshold: float
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    scraped_at: Optional[str] = None


# ── Raw odds from scrapers ─────────────────────────────────
class RawOddsData(BaseModel):
    bookmaker_id: str
    league_id: str
    home_team: str
    away_team: str
    market_type: str
    player_name: Optional[str] = None
    threshold: float
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    start_time: Optional[str] = None


# ── Normalised odds ────────────────────────────────────────
class NormalizedOdds(BaseModel):
    match_id: str
    bookmaker_id: str
    league_id: str
    home_team: str
    away_team: str
    market_type: str
    player_name: Optional[str] = None
    threshold: float
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    start_time: Optional[str] = None


# ── Discrepancy ────────────────────────────────────────────
class DiscrepancyOut(BaseModel):
    id: int
    match_id: str
    market_type: str
    player_name: Optional[str] = None
    bookmaker_a_id: str
    bookmaker_b_id: str
    threshold_a: float
    threshold_b: float
    odds_a: Optional[float] = None
    odds_b: Optional[float] = None
    gap: float
    profit_margin: Optional[float] = None
    middle_profit_margin: Optional[float] = None
    detected_at: Optional[str] = None
    is_active: bool = True


class DiscrepancyDetail(DiscrepancyOut):
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    league_name: Optional[str] = None
    bookmaker_a_name: Optional[str] = None
    bookmaker_b_name: Optional[str] = None


# ── Notification ───────────────────────────────────────────
class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    message: Optional[str] = None
    data: Optional[str] = None
    is_read: bool = False
    created_at: Optional[str] = None


# ── System Status ──────────────────────────────────────────
class ScanProgressOut(BaseModel):
    in_progress: bool = False
    phase: str = "idle"
    started_at: Optional[str] = None
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    active_tasks: int = 0


class SystemStatus(BaseModel):
    status: str = "ok"
    last_scrape_at: Optional[str] = None
    total_matches: int = 0
    total_odds: int = 0
    total_discrepancies: int = 0
    active_bookmakers: int = 0
    scheduler_running: bool = False
    scan: ScanProgressOut = Field(default_factory=ScanProgressOut)


# ── Scrape trigger response ────────────────────────────────
class ScrapeResponse(BaseModel):
    message: str
    matches_scraped: int = 0
    odds_scraped: int = 0
    discrepancies_found: int = 0
