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
    team_review_case_id: Optional[int] = None
    team_review_suggested_team_id: Optional[int] = None
    team_review_suggested_team_name: Optional[str] = None
    team_review_confidence: Optional[str] = None
    team_review_status: Optional[str] = None
    team_review_similarity_score: Optional[float] = None


class TeamReviewDiagnostic(BaseModel):
    bookmaker_id: str
    raw_league_id: str
    normalized_raw_league_id: str
    sport: str
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
    middle_hit_probability: Optional[float] = None
    middle_ev: Optional[float] = None
    middle_model_confidence: Optional[str] = None
    middle_model_diagnostics: dict[str, object] = Field(default_factory=dict)
    middle_ev_rank: Optional[float] = None
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


class TelegramNotificationProfileBase(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    chat_id: str = Field(..., min_length=1, max_length=120)
    enabled: bool = True
    min_gap: float = Field(default=0.0, ge=0)
    min_roi_percent: float = Field(default=0.0, ge=0)
    min_middle_ev_percent: float = Field(default=0.0, ge=0)
    bookmaker_ids: list[str] = Field(default_factory=list)


class TelegramNotificationProfileCreate(TelegramNotificationProfileBase):
    pass


class TelegramNotificationProfileUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=120)
    chat_id: Optional[str] = Field(default=None, min_length=1, max_length=120)
    enabled: Optional[bool] = None
    min_gap: Optional[float] = Field(default=None, ge=0)
    min_roi_percent: Optional[float] = Field(default=None, ge=0)
    min_middle_ev_percent: Optional[float] = Field(default=None, ge=0)
    bookmaker_ids: Optional[list[str]] = None


class TelegramNotificationProfileOut(TelegramNotificationProfileBase):
    id: int
    rate_limited_until: Optional[str] = None
    last_delivery_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TelegramSettingsResponse(BaseModel):
    token_configured: bool = False
    api_base_url: str
    profiles: list[TelegramNotificationProfileOut] = Field(default_factory=list)


class TelegramNotificationProfileDeleteResponse(BaseModel):
    profile_id: int
    deleted: bool


class TelegramTestMessageResponse(BaseModel):
    profile_id: int
    ok: bool
    message_id: Optional[int] = None


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
    analysis_markets: list[str] = Field(default_factory=lambda: ["all"])
    scrape_lookahead_hours: int = Field(default=24, ge=0)
    scrape_interval_minutes: int = Field(default=10, ge=1)
    max_middle_opportunities_per_market: int = Field(default=10, ge=1)
    enable_fitted_middles: bool = True
    min_fitted_middle_ev_percent: float = Field(default=0.0, ge=0)
    rate_limit_per_second: float = Field(default=1.0, ge=0)
    meridian_rate_limit_per_second: float = Field(default=2.0, ge=0)
    soccerbet_detail_mode: ScraperDetailMode = "partial"
    merkurxtip_detail_mode: ScraperDetailMode = "partial"
    pinnbet_detail_mode: ScraperDetailMode = "partial"
    betole_detail_mode: ScraperDetailMode = "partial"
    notification_gap_threshold: float = Field(default=1.5, ge=0)
    persist_inapp_notifications: bool = False


class ScrapeRuntimeSettingsUpdate(BaseModel):
    enabled_bookmakers: Optional[list[str]] = None
    enabled_sports: Optional[list[str]] = None
    scrape_market_scope: Optional[ScrapeMarketScope] = None
    analysis_markets: Optional[list[str]] = None
    scrape_lookahead_hours: Optional[int] = Field(default=None, ge=0)
    scrape_interval_minutes: Optional[int] = Field(default=None, ge=1)
    max_middle_opportunities_per_market: Optional[int] = Field(default=None, ge=1)
    enable_fitted_middles: Optional[bool] = None
    min_fitted_middle_ev_percent: Optional[float] = Field(default=None, ge=0)
    rate_limit_per_second: Optional[float] = Field(default=None, ge=0)
    meridian_rate_limit_per_second: Optional[float] = Field(default=None, ge=0)
    soccerbet_detail_mode: Optional[ScraperDetailMode] = None
    merkurxtip_detail_mode: Optional[ScraperDetailMode] = None
    pinnbet_detail_mode: Optional[ScraperDetailMode] = None
    betole_detail_mode: Optional[ScraperDetailMode] = None
    notification_gap_threshold: Optional[float] = Field(default=None, ge=0)
    persist_inapp_notifications: Optional[bool] = None


class ScrapeSettingsBookmakerOption(BaseModel):
    id: str
    name: str
    enabled: bool = False


class ScrapeSettingsMarketOption(BaseModel):
    token: str
    label: str
    sport: Optional[str] = None


class ScrapeSettingsOptions(BaseModel):
    bookmakers: list[ScrapeSettingsBookmakerOption] = Field(default_factory=list)
    sports: list[str] = Field(default_factory=list)
    market_scopes: list[ScrapeMarketScope] = Field(default_factory=lambda: ["all", "player_props"])
    analysis_market_options: list[ScrapeSettingsMarketOption] = Field(
        default_factory=list
    )
    detail_modes: list[ScraperDetailMode] = Field(default_factory=lambda: ["partial", "full"])
    scrape_interval_minutes_min: int = 1
    scrape_interval_minutes_max: int = 24 * 60
    scrape_lookahead_hours_min: int = 0
    scrape_lookahead_hours_max: int = 24 * 14
    max_middle_opportunities_per_market_min: int = 1
    max_middle_opportunities_per_market_max: int = 1000
    min_fitted_middle_ev_percent_min: float = 0.0
    min_fitted_middle_ev_percent_max: float = 100.0
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
class BenchmarkRuntimeMetadataOut(BaseModel):
    """Runtime configuration captured with a benchmark snapshot."""

    scraper_mode: str
    enabled_bookmakers: list[str] = Field(default_factory=list)
    enabled_sports: list[str] = Field(default_factory=list)
    scrape_market_scope: ScrapeMarketScope = "all"
    analysis_markets: list[str] = Field(default_factory=list)
    scrape_lookahead_hours: int = 0
    rate_limit_per_second: float = 0
    meridian_rate_limit_per_second: float = 0
    bookmaker_rate_limits: dict[str, float] = Field(default_factory=dict)
    scrape_type_rate_limits: dict[str, float] = Field(default_factory=dict)
    detail_modes: dict[str, ScraperDetailMode] = Field(default_factory=dict)
    proxies_configured: bool = False
    proxy_count: int = 0
    max_middle_opportunities_per_market: int = 0
    enable_fitted_middles: bool = True
    min_fitted_middle_ev_percent: float = 0.0


class HttpTimingBenchmarkOut(BaseModel):
    """Compact HTTP timing aggregate for benchmark snapshots."""

    logical_requests: int = 0
    attempts: int = 0
    retries: int = 0
    errors: int = 0
    total_elapsed_ms: int = 0
    total_rate_limit_wait_ms: int = 0
    total_network_ms: int = 0
    min_latency_ms: Optional[int] = None
    avg_latency_ms: float = 0.0
    max_latency_ms: Optional[int] = None
    status_classes: dict[str, int] = Field(default_factory=dict)


class PersistenceBenchmarkOut(BaseModel):
    """Subphase timings for persisting a normalized scrape snapshot."""

    wall_ms: int = 0
    begin_transaction_ms: int = 0
    upsert_snapshot_persisting_ms: int = 0
    upsert_leagues_ms: int = 0
    upsert_matches_ms: int = 0
    upsert_snapshot_matches_ms: int = 0
    upsert_sources_ms: int = 0
    upsert_odds_ms: int = 0
    insert_odds_history_ms: int = 0
    upsert_outcome_offers_ms: int = 0
    insert_unresolved_odds_ms: int = 0
    insert_team_review_cases_ms: int = 0
    insert_auto_approved_team_reviews_ms: int = 0
    update_auto_approved_reviews_ms: int = 0
    upsert_snapshot_persisted_ms: int = 0
    commit_ms: int = 0
    row_counts: dict[str, int] = Field(default_factory=dict)


class ScraperRequestBenchmarkOut(HttpTimingBenchmarkOut):
    """HTTP timing aggregate scoped to one scraper capability/method."""

    lane: Optional[str] = None
    sport: Optional[str] = None
    league_id: Optional[str] = None
    endpoint: Optional[str] = None
    method: str


class BenchmarkEventCoverageOut(BaseModel):
    """Matched-event coverage for one bookmaker/sport source-event bucket."""

    bookmaker_id: str
    sport: str
    normalized_events: int = 0
    matched_events: int = 0
    unmatched_events: int = 0
    ungrouped_events: int = 0
    in_review_events: int = 0
    not_matched_events: int = 0
    match_rate: float = 0.0


class BenchmarkSplitEventFragmentOut(BaseModel):
    """One resolved-event fragment participating in a split/over-merge diagnostic."""

    resolved_event_id: str
    primary_match_id: str
    display_home_team: str
    display_away_team: str
    display_league_name: Optional[str] = None
    start_time: str
    method: str
    confidence: float = 0.0
    bookmaker_ids: list[str] = Field(default_factory=list)
    match_ids: list[str] = Field(default_factory=list)
    member_count: int = 0


class BenchmarkSplitMemberFragmentOut(BaseModel):
    """One source member participating in an over-merge diagnostic."""

    bookmaker_id: str
    match_id: str
    home_team: str
    away_team: str
    source_home_team: Optional[str] = None
    source_away_team: Optional[str] = None
    source_kind: str


class BenchmarkSplitWeakestMemberPairOut(BaseModel):
    """Weakest source-member pair inside a possible over-merged event."""

    left: BenchmarkSplitMemberFragmentOut
    right: BenchmarkSplitMemberFragmentOut
    orientation: str
    average_score: float = 0.0
    weak_side_score: float = 0.0


class BenchmarkSplitClusterOut(BaseModel):
    """Diagnostic candidate for logical event splitting or over-merging."""

    sport: str
    reason_code: str
    score: float = 0.0
    shared_side: Optional[str] = None
    start_time: str
    max_start_delta_minutes: float = 0.0
    events: list[BenchmarkSplitEventFragmentOut] = Field(default_factory=list)
    weakest_member_pair: Optional[BenchmarkSplitWeakestMemberPairOut] = None


class BenchmarkSplitSportDiagnosticsOut(BaseModel):
    """Per-sport split/over-merge diagnostic aggregate."""

    sport: str
    split_candidate_count: int = 0
    events_in_split_candidates: int = 0
    members_in_split_candidates: int = 0
    overmerge_candidate_count: int = 0
    events_in_overmerge_candidates: int = 0
    members_in_overmerge_candidates: int = 0


class BenchmarkSplitDiagnosticsOut(BaseModel):
    """Cycle-level diagnostics for split logical events and possible over-merges."""

    split_candidate_count: int = 0
    events_in_split_candidates: int = 0
    members_in_split_candidates: int = 0
    overmerge_candidate_count: int = 0
    events_in_overmerge_candidates: int = 0
    members_in_overmerge_candidates: int = 0
    top_split_candidates: list[BenchmarkSplitClusterOut] = Field(default_factory=list)
    top_overmerge_candidates: list[BenchmarkSplitClusterOut] = Field(
        default_factory=list
    )
    sports: list[BenchmarkSplitSportDiagnosticsOut] = Field(default_factory=list)


class SportBenchmarkOut(BaseModel):
    """Per-sport benchmark aggregates within a cycle or scraper row."""

    sport: str
    duration_ms: int = 0
    raw_items: int = 0
    matches_after_normalization: int = 0
    odds_count: int = 0
    leagues_attempted: int = 0
    leagues_failed: int = 0
    failure_rate: float = 0.0
    matched_events: int = 0
    unmatched_events: int = 0
    ungrouped_events: int = 0
    in_review_events: int = 0
    not_matched_events: int = 0
    match_rate: float = 0.0


class OutcomeFootballEventBucketBenchmarkOut(BaseModel):
    """Top football event-resolution time buckets for outcome normalization."""

    sport: str
    start_time: str
    event_count: int = 0
    bookmaker_count: int = 0
    candidate_pair_count: int = 0


class OutcomeNormalizationBookmakerBenchmarkOut(BaseModel):
    """Per-bookmaker row counters for one outcome-normalization pass."""

    bookmaker_id: str
    raw_rows: int = 0
    normalized_rows: int = 0
    event_resolution_rows: int = 0
    direct_resolution_rows: int = 0
    skipped_unresolved_rows: int = 0
    unresolved_diagnostic_count: int = 0
    missing_start_time_rows: int = 0
    unsupported_reversed_rows: int = 0


class OutcomeNormalizationRunBenchmarkOut(BaseModel):
    """Per-pass outcome-normalization counters and timings."""

    run_index: int = 0
    wall_ms: int = 0
    raw_outcome_offer_count: int = 0
    normalized_outcome_offer_count: int = 0
    unresolved_outcome_offer_count: int = 0
    football_unique_event_count: int = 0
    football_event_pair_candidate_count: int = 0
    football_event_fuzzy_score_count: int = 0
    football_event_canonical_conflict_skip_count: int = 0
    football_event_canonical_conflict_fuzzy_score_avoided_count: int = 0
    football_team_review_case_count: int = 0
    auto_create_football_teams_ms: int = 0
    football_event_resolution_ms: int = 0
    football_event_pair_ranking_ms: int = 0
    football_event_slot_lookup_ms: int = 0
    row_normalization_ms: int = 0
    team_review_proxy_rows: int = 0
    team_review_proxy_ms: int = 0
    team_review_proxy_slot_resolution_ms: int = 0
    team_review_proxy_case_build_ms: int = 0
    team_review_proxy_resolve_league_ms: int = 0
    team_review_proxy_resolve_team_ms: int = 0
    team_review_proxy_slot_candidate_ms: int = 0
    team_review_proxy_global_candidate_ms: int = 0
    team_review_proxy_duplicate_suppression_ms: int = 0
    team_review_proxy_resolve_team_cache_hits: int = 0
    team_review_proxy_slot_candidate_search_count: int = 0
    team_review_proxy_slot_candidate_cache_hits: int = 0
    team_review_proxy_global_candidate_search_count: int = 0
    team_review_proxy_global_candidate_cache_hits: int = 0
    team_review_proxy_duplicate_suppression_count: int = 0
    row_iteration_ms: int = 0
    missing_start_time_count: int = 0
    event_resolution_offer_count: int = 0
    direct_resolution_attempt_count: int = 0
    direct_resolution_success_count: int = 0
    skipped_unresolved_row_count: int = 0
    unsupported_reversed_offer_count: int = 0
    league_resolution_ms: int = 0
    event_resolution_offer_build_ms: int = 0
    direct_team_resolution_ms: int = 0
    unresolved_context_ms: int = 0
    direct_offer_build_ms: int = 0


class OutcomeNormalizationBenchmarkOut(BaseModel):
    """Subphase metrics for football outcome-offer normalization."""

    runs: int = 0
    raw_outcome_offer_count: int = 0
    normalized_outcome_offer_count: int = 0
    unresolved_outcome_offer_count: int = 0
    football_unique_event_count: int = 0
    football_event_pair_candidate_count: int = 0
    football_event_fuzzy_score_count: int = 0
    football_event_canonical_conflict_skip_count: int = 0
    football_event_canonical_conflict_fuzzy_score_avoided_count: int = 0
    auto_created_football_team_count: int = 0
    football_team_review_case_count: int = 0
    football_team_review_alias_miss_count: int = 0
    football_team_review_unknown_count: int = 0
    football_team_review_same_slot_alias_miss_count: int = 0
    football_team_review_global_alias_miss_count: int = 0
    auto_create_football_teams_ms: int = 0
    football_event_resolution_ms: int = 0
    football_event_pair_ranking_ms: int = 0
    football_event_slot_lookup_ms: int = 0
    football_event_slot_mutation_ms: int = 0
    row_normalization_ms: int = 0
    team_review_proxy_rows: int = 0
    team_review_proxy_ms: int = 0
    team_review_proxy_slot_resolution_ms: int = 0
    team_review_proxy_case_build_ms: int = 0
    team_review_proxy_resolve_league_ms: int = 0
    team_review_proxy_resolve_team_ms: int = 0
    team_review_proxy_slot_candidate_ms: int = 0
    team_review_proxy_global_candidate_ms: int = 0
    team_review_proxy_duplicate_suppression_ms: int = 0
    team_review_proxy_resolve_team_cache_hits: int = 0
    team_review_proxy_slot_candidate_search_count: int = 0
    team_review_proxy_slot_candidate_cache_hits: int = 0
    team_review_proxy_global_candidate_search_count: int = 0
    team_review_proxy_global_candidate_cache_hits: int = 0
    team_review_proxy_duplicate_suppression_count: int = 0
    row_iteration_ms: int = 0
    missing_start_time_count: int = 0
    event_resolution_offer_count: int = 0
    direct_resolution_attempt_count: int = 0
    direct_resolution_success_count: int = 0
    skipped_unresolved_row_count: int = 0
    unsupported_reversed_offer_count: int = 0
    league_resolution_ms: int = 0
    event_resolution_offer_build_ms: int = 0
    direct_team_resolution_ms: int = 0
    unresolved_context_ms: int = 0
    direct_offer_build_ms: int = 0
    football_event_time_slot_count: int = 0
    football_event_max_events_per_slot: int = 0
    run_details: list[OutcomeNormalizationRunBenchmarkOut] = Field(
        default_factory=list
    )
    bookmakers: list[OutcomeNormalizationBookmakerBenchmarkOut] = Field(
        default_factory=list
    )
    top_football_event_buckets: list[OutcomeFootballEventBucketBenchmarkOut] = Field(
        default_factory=list
    )


class EventResolverSourceMatchSlotBenchmarkOut(BaseModel):
    """Top raw-source slots scanned while matching normalized event candidates."""

    bookmaker_id: str
    sport: str
    start_time: str
    lookup_count: int = 0
    source_count: int = 0
    average_sources_per_lookup: float = 0.0


class EventResolverBenchmarkOut(BaseModel):
    """Subphase metrics for resolved-event extraction/grouping/persistence."""

    extract_event_candidates_ms: int = 0
    extract_raw_odds_sources_ms: int = 0
    extract_raw_outcome_sources_ms: int = 0
    extract_normalized_odds_candidates_ms: int = 0
    extract_normalized_outcome_candidates_ms: int = 0
    extract_source_match_ms: int = 0
    football_raw_resolution_candidates_ms: int = 0
    reused_football_event_resolution_count: int = 0
    build_event_resolution_groups_ms: int = 0
    persist_event_resolution_groups_ms: int = 0
    raw_odds_rows_scanned: int = 0
    raw_odds_sources_emitted: int = 0
    raw_outcome_offer_rows_scanned: int = 0
    raw_outcome_sources_emitted: int = 0
    normalized_odds_rows_scanned: int = 0
    normalized_odds_candidates_emitted: int = 0
    normalized_outcome_offer_rows_scanned: int = 0
    normalized_outcome_candidates_emitted: int = 0
    stored_outcome_match_bookmaker_count: int = 0
    source_match_lookup_count: int = 0
    source_match_source_count: int = 0
    source_match_scored_source_count: int = 0
    source_match_index_candidate_count: int = 0
    source_match_exact_url_hit_count: int = 0
    source_match_listed_pair_hit_count: int = 0
    source_match_unordered_pair_hit_count: int = 0
    source_match_fallback_scan_count: int = 0
    source_match_max_sources_per_lookup: int = 0
    source_match_truncated_slot_count: int = 0
    football_raw_candidate_count: int = 0
    candidate_count: int = 0
    exact_group_count: int = 0
    pair_check_count: int = 0
    fuzzy_score_count: int = 0
    accepted_fuzzy_pair_count: int = 0
    review_case_count: int = 0
    persisted_resolved_event_count: int = 0
    persisted_member_count: int = 0
    persisted_review_case_count: int = 0
    top_source_match_slots: list[EventResolverSourceMatchSlotBenchmarkOut] = Field(
        default_factory=list
    )


class AutoResolutionRerunBatchCountsOut(BaseModel):
    """Normalized row counts captured before/after same-cycle auto-resolution."""

    normalized_threshold_odds: int = 0
    normalized_outcome_offers: int = 0
    unresolved_diagnostics: int = 0
    team_review_cases: int = 0


AutoResolutionRerunDecision = Literal[
    "not_needed",
    "performed_canonical_merge",
    "performed_canonical_merge_yield_met",
    "performed_alias_yield_met",
    "skipped_canonical_merge_low_yield",
    "skipped_alias_low_yield",
    "skipped_no_registry_change",
]


class AutoResolutionRerunBenchmarkOut(BaseModel):
    """Same-cycle auto-resolution rerun trigger and yield metrics."""

    rerun_performed: bool = False
    rerun_skipped: bool = False
    decision: AutoResolutionRerunDecision = "not_needed"
    decision_reason: str = ""
    estimated_affected_row_count: int = 0
    affected_row_rerun_threshold: int = 0
    merge_affected_row_count: int = 0
    merge_affected_row_rerun_threshold: int = 0
    reasons: list[str] = Field(default_factory=list)
    team_review_cases_seen_count: int = 0
    auto_review_cases_approved_count: int = 0
    same_time_auto_review_count: int = 0
    anchored_auto_review_count: int = 0
    aliases_requested_count: int = 0
    aliases_applied_count: int = 0
    same_time_pending_merge_count: int = 0
    anchored_pending_merge_count: int = 0
    pending_merge_count: int = 0
    applied_merge_count: int = 0
    before: AutoResolutionRerunBatchCountsOut = Field(
        default_factory=AutoResolutionRerunBatchCountsOut
    )
    after: AutoResolutionRerunBatchCountsOut = Field(
        default_factory=AutoResolutionRerunBatchCountsOut
    )
    delta: AutoResolutionRerunBatchCountsOut = Field(
        default_factory=AutoResolutionRerunBatchCountsOut
    )


class OpportunityAnalysisRuleBenchmarkOut(BaseModel):
    """Per-rule opportunity-analysis counters for one sport/market bucket."""

    sport: str
    market_type: str
    rule: str
    duration_ms: int = 0
    group_count: int = 0
    offer_count: int = 0
    candidate_pair_count: int = 0
    publishable_candidate_count: int = 0
    opportunity_count: int = 0


class OpportunityDetailModeYieldOut(BaseModel):
    """Opportunity yield for one detail-mode-capable bookmaker."""

    bookmaker_id: str
    detail_mode: Optional[ScraperDetailMode] = None
    opportunity_count: int = 0
    opportunity_leg_count: int = 0
    market_counts: dict[str, int] = Field(default_factory=dict)


class OpportunityAnalysisBenchmarkOut(BaseModel):
    """Subphase metrics for canonical opportunity analysis."""

    canonical_offer_load_ms: int = 0
    primary_match_lookup_ms: int = 0
    grouping_ms: int = 0
    two_way_arbitrage_ms: int = 0
    line_middle_ms: int = 0
    complementary_outcomes_ms: int = 0
    dedupe_sort_ms: int = 0
    output_build_ms: int = 0
    loaded_offer_count: int = 0
    same_market_group_count: int = 0
    line_market_group_count: int = 0
    event_market_family_group_count: int = 0
    candidate_pair_count: int = 0
    publishable_candidate_count: int = 0
    opportunity_count: int = 0
    rules: list[OpportunityAnalysisRuleBenchmarkOut] = Field(default_factory=list)
    detail_mode_yield: list[OpportunityDetailModeYieldOut] = Field(default_factory=list)


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
    http: HttpTimingBenchmarkOut = Field(default_factory=HttpTimingBenchmarkOut)
    requests: list[ScraperRequestBenchmarkOut] = Field(default_factory=list)
    sports: list[SportBenchmarkOut] = Field(default_factory=list)


class CycleBenchmarkOut(BaseModel):
    """Latest cycle benchmark snapshot."""

    cycle_started_at: Optional[str] = None
    cycle_finished_at: Optional[str] = None
    scrape_duration_ms: int = 0
    cycle_duration_ms: int = 0
    total_raw_items: int = 0
    total_matches: int = 0
    total_odds: int = 0
    metadata: Optional[BenchmarkRuntimeMetadataOut] = None
    phase_durations_ms: dict[str, int] = Field(default_factory=dict)
    outcome_normalization: OutcomeNormalizationBenchmarkOut = Field(
        default_factory=OutcomeNormalizationBenchmarkOut
    )
    event_resolver: EventResolverBenchmarkOut = Field(
        default_factory=EventResolverBenchmarkOut
    )
    auto_resolution_rerun: AutoResolutionRerunBenchmarkOut = Field(
        default_factory=AutoResolutionRerunBenchmarkOut
    )
    persistence: PersistenceBenchmarkOut = Field(
        default_factory=PersistenceBenchmarkOut
    )
    opportunity_analysis: OpportunityAnalysisBenchmarkOut = Field(
        default_factory=OpportunityAnalysisBenchmarkOut
    )
    event_coverage: list[BenchmarkEventCoverageOut] = Field(default_factory=list)
    event_split_diagnostics: BenchmarkSplitDiagnosticsOut = Field(
        default_factory=BenchmarkSplitDiagnosticsOut
    )
    sports: list[SportBenchmarkOut] = Field(default_factory=list)
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
