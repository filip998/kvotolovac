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


class MatchBookmakerOut(BaseModel):
    id: str
    name: str


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
    league_name: Optional[str] = None
    home_team: str
    away_team: str
    start_time: Optional[str] = None
    status: str = "upcoming"
    available_bookmakers: list[MatchBookmakerOut] = Field(default_factory=list)


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


class UnresolvedOddsDiagnostic(BaseModel):
    bookmaker_id: str
    raw_league_id: str
    league_id: str
    market_type: str
    player_name: Optional[str] = None
    raw_team_name: str
    normalized_team_name: str
    start_time: Optional[str] = None
    threshold: float
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    reason_code: str
    candidate_count: int = 0
    candidate_matchups: list[str] = Field(default_factory=list)
    available_matchups_same_slot: list[str] = Field(default_factory=list)


class UnresolvedOddsOut(UnresolvedOddsDiagnostic):
    id: int
    bookmaker_name: Optional[str] = None
    league_name: Optional[str] = None
    scraped_at: Optional[str] = None


class MatchingReviewDiagnostic(BaseModel):
    bookmaker_id: str
    raw_league_id: str
    normalized_raw_league_id: str
    suggested_league_id: str
    match_id: str
    home_team: str
    away_team: str
    start_time: Optional[str] = None
    reason_code: str
    confidence: str = "medium"
    evidence: list[str] = Field(default_factory=list)
    status: str = "pending"


class MatchingReviewOut(MatchingReviewDiagnostic):
    id: int
    bookmaker_name: Optional[str] = None
    suggested_league_name: Optional[str] = None
    scraped_at: Optional[str] = None


class MatchingReviewApprovalIn(BaseModel):
    league_id: Optional[str] = None


class MatchingReviewApprovalOut(BaseModel):
    case_id: int
    status: str
    saved_alias: str
    saved_league_id: str
    saved_league_name: Optional[str] = None


class LeagueMatchingHealthOut(BaseModel):
    league_id: str
    league_name: str
    matched_events: int = 0
    pending_reviews: int = 0
    approved_reviews: int = 0


class MatchingReviewSummaryOut(BaseModel):
    total_matches: int = 0
    leagues_with_matches: int = 0
    pending_reviews: int = 0
    approved_reviews: int = 0
    inferred_events: int = 0
    leagues: list[LeagueMatchingHealthOut] = Field(default_factory=list)


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
