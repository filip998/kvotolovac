from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

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
    resolved_event_id: Optional[str] = None
    available_bookmakers: list[MatchBookmakerOut] = Field(default_factory=list)


# ── Resolved events ─────────────────────────────────────────
class ResolvedEventIn(BaseModel):
    id: Optional[str] = None
    sport: str = "basketball"
    start_time: str
    primary_match_id: str
    status: str = "active"
    confidence: Optional[float] = None
    method: str = "manual"
    display_home_team: Optional[str] = None
    display_away_team: Optional[str] = None
    display_league_name: Optional[str] = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ResolvedEventMemberIn(BaseModel):
    snapshot_id: Optional[str] = None
    resolved_event_id: str
    match_id: str
    bookmaker_id: str
    orientation: str = "as_listed"
    confidence: Optional[float] = None
    status: str = "active"
    source_url: Optional[str] = None
    source_league_id: Optional[str] = None
    source_league_name: Optional[str] = None
    source_home_team: Optional[str] = None
    source_away_team: Optional[str] = None
    source_start_time: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class ResolvedEventMemberOut(ResolvedEventMemberIn):
    id: int
    bookmaker_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ResolvedEventOut(ResolvedEventIn):
    id: str
    members: list[ResolvedEventMemberOut] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class EventReviewCaseIn(BaseModel):
    fingerprint: str
    sport: str = "basketball"
    start_time: str
    primary_match_id: Optional[str] = None
    candidate_resolved_event_id: Optional[str] = None
    candidate_match_ids: list[str] = Field(default_factory=list)
    reason_code: str
    confidence: Optional[float] = None
    method: str = "auto_candidate"
    source_bookmaker_ids: list[str] = Field(default_factory=list)
    source_league_labels: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    status: str = "pending"


class EventReviewVariantOut(BaseModel):
    match_id: str
    bookmaker_id: Optional[str] = None
    bookmaker_name: Optional[str] = None
    league_id: Optional[str] = None
    league_name: Optional[str] = None
    home_team: str
    away_team: str
    start_time: Optional[str] = None
    source_url: Optional[str] = None
    source_league_id: Optional[str] = None
    source_league_name: Optional[str] = None
    source_home_team: Optional[str] = None
    source_away_team: Optional[str] = None
    source_start_time: Optional[str] = None
    orientation: str = "as_listed"
    confidence: Optional[float] = None
    evidence: list[str] = Field(default_factory=list)


class EventReviewCaseOut(EventReviewCaseIn):
    id: int
    resolved_event_id: Optional[str] = None
    primary_home_team: Optional[str] = None
    primary_away_team: Optional[str] = None
    primary_league_name: Optional[str] = None
    variants: list[EventReviewVariantOut] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    accepted_at: Optional[str] = None
    declined_at: Optional[str] = None


class EventReviewActionOut(BaseModel):
    case_id: int
    status: str
    resolved_event_id: Optional[str] = None


class EventReviewAcceptanceIn(BaseModel):
    primary_match_id: Optional[str] = None


class EventMergeIn(BaseModel):
    primary_match_id: str
    source_match_ids: list[str] = Field(default_factory=list)


class EventMergeOut(BaseModel):
    resolved_event_id: str
    primary_match_id: str
    linked_match_ids: list[str]
    linked_member_count: int
    opportunities_rebuilt: int = 0


# ── Odds ───────────────────────────────────────────────────
class OddsOut(BaseModel):
    id: int
    match_id: str
    bookmaker_id: str
    bookmaker_name: Optional[str] = None
    source_url: Optional[str] = None
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
    merged_source_team_id: Optional[int] = None
    merged_source_team_name: Optional[str] = None


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
    source_url: Optional[str] = None
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
    source_url: Optional[str] = None
    market_type: str
    player_name: Optional[str] = None
    threshold: float
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    start_time: Optional[str] = None
    scraped_at: Optional[str] = None


# ── Generic outcome offers ─────────────────────────────────
class RawOutcomeOffer(BaseModel):
    bookmaker_id: str
    league_id: str
    sport: str
    home_team: str
    away_team: str
    source_url: Optional[str] = None
    market_type: str
    outcome_code: str
    odds: float
    line: Optional[float] = None
    raw_label: Optional[str] = None
    start_time: Optional[str] = None


class NormalizedOutcomeOffer(BaseModel):
    match_id: str
    bookmaker_id: str
    league_id: str
    sport: str
    home_team_id: int = 0
    away_team_id: int = 0
    home_team: str
    away_team: str
    source_url: Optional[str] = None
    market_type: str
    outcome_code: str
    odds: float
    line: Optional[float] = None
    raw_label: Optional[str] = None
    start_time: Optional[str] = None
    scraped_at: Optional[str] = None


class CanonicalMarket(BaseModel):
    market_key: str
    match_id: str
    event_id: Optional[str] = None
    bookmaker_match_id: Optional[str] = None
    sport: str
    market_type: str
    source_market_type: str
    subject_type: str
    subject_key: Optional[str] = None
    subject_name: Optional[str] = None
    line: Optional[float] = None
    period: Optional[str] = None
    scope: Optional[str] = None


class CanonicalOffer(BaseModel):
    market_key: str
    market: CanonicalMarket
    bookmaker_id: str
    outcome_code: str
    odds: float
    source_url: Optional[str] = None
    raw_label: Optional[str] = None
    scraped_at: Optional[str] = None


class OutcomeOfferOut(BaseModel):
    id: int
    match_id: str
    bookmaker_id: str
    bookmaker_name: Optional[str] = None
    source_url: Optional[str] = None
    market_type: str
    outcome_code: str
    odds: float
    line: Optional[float] = None
    raw_label: Optional[str] = None
    scraped_at: Optional[str] = None


class OpportunityLeg(BaseModel):
    offer_id: Optional[int] = None
    match_id: Optional[str] = None
    bookmaker_id: str
    bookmaker_name: Optional[str] = None
    source_url: Optional[str] = None
    market_type: str
    outcome_code: str
    odds: float
    line: Optional[float] = None
    raw_label: Optional[str] = None


class OpportunityOut(BaseModel):
    id: int
    sport: str
    event_id: Optional[str] = None
    match_id: str
    resolved_event_id: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    league_name: Optional[str] = None
    start_time: Optional[str] = None
    opportunity_type: str
    market_type: str
    subject_type: Optional[str] = None
    subject_key: Optional[str] = None
    subject_name: Optional[str] = None
    line: Optional[float] = None
    profit_margin: Optional[float] = None
    middle_profit_margin: Optional[float] = None
    market_keys: list[str] = Field(default_factory=list)
    legs: list[OpportunityLeg] = Field(default_factory=list)
    detected_at: Optional[str] = None
    is_active: bool = True


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
    total_opportunities: int = 0
    active_bookmakers: int = 0
    scheduler_running: bool = False
    scan: ScanProgressOut = Field(default_factory=ScanProgressOut)


# ── Runtime scrape settings ────────────────────────────────
ScrapeMarketScope = Literal["all", "player_props"]
ScraperDetailMode = Literal["partial", "full"]


class ScrapeRuntimeSettings(BaseModel):
    enabled_bookmakers: list[str] = Field(default_factory=list)
    enabled_sports: list[str] = Field(default_factory=list)
    scrape_market_scope: ScrapeMarketScope = "all"
    scrape_lookahead_hours: int = Field(default=24, ge=0)
    scrape_interval_minutes: int = Field(default=10, ge=1)
    max_middle_opportunities_per_market: int = Field(default=10, ge=1)
    rate_limit_per_second: float = Field(default=1.0, ge=0)
    meridian_rate_limit_per_second: float = Field(default=2.0, ge=0)
    soccerbet_detail_mode: ScraperDetailMode = "partial"
    merkurxtip_detail_mode: ScraperDetailMode = "partial"
    notification_gap_threshold: float = Field(default=1.5, ge=0)
    persist_inapp_notifications: bool = False


class ScrapeRuntimeSettingsUpdate(BaseModel):
    enabled_bookmakers: Optional[list[str]] = None
    enabled_sports: Optional[list[str]] = None
    scrape_market_scope: Optional[ScrapeMarketScope] = None
    scrape_lookahead_hours: Optional[int] = Field(default=None, ge=0)
    scrape_interval_minutes: Optional[int] = Field(default=None, ge=1)
    max_middle_opportunities_per_market: Optional[int] = Field(default=None, ge=1)
    rate_limit_per_second: Optional[float] = Field(default=None, ge=0)
    meridian_rate_limit_per_second: Optional[float] = Field(default=None, ge=0)
    soccerbet_detail_mode: Optional[ScraperDetailMode] = None
    merkurxtip_detail_mode: Optional[ScraperDetailMode] = None
    notification_gap_threshold: Optional[float] = Field(default=None, ge=0)
    persist_inapp_notifications: Optional[bool] = None


class ScrapeSettingsBookmakerOption(BaseModel):
    id: str
    name: str
    enabled: bool = False


class ScrapeSettingsOptions(BaseModel):
    bookmakers: list[ScrapeSettingsBookmakerOption] = Field(default_factory=list)
    sports: list[str] = Field(default_factory=list)
    market_scopes: list[ScrapeMarketScope] = Field(default_factory=lambda: ["all", "player_props"])
    detail_modes: list[ScraperDetailMode] = Field(default_factory=lambda: ["partial", "full"])
    scrape_interval_minutes_min: int = 1
    scrape_interval_minutes_max: int = 24 * 60
    scrape_lookahead_hours_min: int = 0
    scrape_lookahead_hours_max: int = 24 * 14
    max_middle_opportunities_per_market_min: int = 1
    max_middle_opportunities_per_market_max: int = 1000
    rate_limit_per_second_min: float = 0
    rate_limit_per_second_max: float = 20


class ScrapeSettingsResponse(BaseModel):
    applied: ScrapeRuntimeSettings
    pending: Optional[ScrapeRuntimeSettings] = None
    defaults: ScrapeRuntimeSettings
    has_pending_changes: bool = False
    applied_at: Optional[str] = None
    pending_at: Optional[str] = None
    applied_immediately: bool = False
    options: ScrapeSettingsOptions = Field(default_factory=ScrapeSettingsOptions)


# ── Scrape trigger response ────────────────────────────────
class ScrapeResponse(BaseModel):
    message: str
    matches_scraped: int = 0
    odds_scraped: int = 0
    opportunities_found: int = 0


# ── Scraper benchmarks ─────────────────────────────────────
class ScraperBenchmarkOut(BaseModel):
    """Per-scraper aggregates for the most recent scrape cycle."""

    bookmaker_id: str
    duration_ms: int
    raw_items: int
    matches_after_normalization: int
    odds_count: int
    leagues_attempted: int
    leagues_failed: int
    failure_rate: float


class CycleBenchmarkOut(BaseModel):
    """Latest cycle benchmark snapshot."""

    cycle_started_at: Optional[str] = None
    cycle_finished_at: Optional[str] = None
    scrape_duration_ms: int = 0
    cycle_duration_ms: int = 0
    total_raw_items: int = 0
    total_matches: int = 0
    total_odds: int = 0
    scrapers: list[ScraperBenchmarkOut] = Field(default_factory=list)


class TeamReviewCandidate(BaseModel):
    team_id: int
    team_name: str
    score: Optional[float] = None
    matched_alias: Optional[str] = None
    slot_support: Optional[int] = None
    canonical_home_team: Optional[str] = None
    canonical_away_team: Optional[str] = None


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
    opportunities_found: int = 0


class CanonicalTeamUnmergeOut(BaseModel):
    source_team_id: int
    target_team_id: int
    restored_team_name: str


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
    reassigned_outcome_offers: int = 0
    reassigned_opportunities: int = 0
    deleted_source_matches: int = 0
