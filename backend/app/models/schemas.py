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
    sport: str = "basketball"
    home_team: str
    away_team: str
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
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
    sport: str = "basketball"
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


class TeamReviewDiagnostic(BaseModel):
    bookmaker_id: str
    raw_league_id: str
    normalized_raw_league_id: str
    sport: str = "basketball"
    scope_league_id: Optional[str] = None
    raw_team_name: str
    normalized_raw_team_name: str
    suggested_team_id: Optional[int] = None
    suggested_team_name: Optional[str] = None
    start_time: Optional[str] = None
    review_kind: str = "alias_suggestion"
    reason_code: str
    confidence: str = "medium"
    similarity_score: Optional[float] = None
    candidate_teams: list["TeamReviewCandidate"] = Field(default_factory=list)
    matched_counterpart_team: Optional[str] = None
    canonical_home_team: Optional[str] = None
    canonical_away_team: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    status: str = "pending"


class TeamReviewOut(TeamReviewDiagnostic):
    id: int
    bookmaker_name: Optional[str] = None
    scope_league_name: Optional[str] = None
    scraped_at: Optional[str] = None


class TeamReviewApprovalOut(BaseModel):
    case_id: int
    status: str
    saved_alias: str
    saved_team_id: int
    saved_team_name: str
    resolved_team_name: Optional[str] = None


class TeamReviewActionOut(BaseModel):
    case_id: int
    status: str


# ── Raw odds from scrapers ─────────────────────────────────
class RawOddsData(BaseModel):
    bookmaker_id: str
    league_id: str
    sport: str = "basketball"
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
    sport: str = "basketball"
    home_team_id: int = 0
    away_team_id: int = 0
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


class TeamReviewCandidate(BaseModel):
    team_id: int
    team_name: str
    score: Optional[float] = None
    matched_alias: Optional[str] = None


class TeamReviewApprovalIn(BaseModel):
    team_id: Optional[int] = None
    create_team_name: Optional[str] = None


class CanonicalTeamOut(BaseModel):
    id: int
    sport: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    alias_count: int = 0
    merged_into_team_id: Optional[int] = None


class CanonicalTeamMergeIn(BaseModel):
    target_team_id: int


class CanonicalTeamMergeOut(BaseModel):
    source_team_id: int
    target_team_id: int
    merged_team_name: str
    matches_scraped: int = 0
    odds_scraped: int = 0
    discrepancies_found: int = 0


# ── Manual match merge ─────────────────────────────────────
class MatchMergeTeamPairing(BaseModel):
    source_team_id: int
    target_team_id: int


class MatchMergeIn(BaseModel):
    target_match_id: str
    source_match_ids: list[str]
    team_pairings: list[MatchMergeTeamPairing] = Field(default_factory=list)


class MatchMergeOut(BaseModel):
    target_match_id: str
    merged_source_match_ids: list[str]
    merged_team_ids: list[MatchMergeTeamPairing] = Field(default_factory=list)
    reassigned_odds: int = 0
    reassigned_odds_history: int = 0
    reassigned_discrepancies: int = 0
    deleted_source_matches: int = 0
