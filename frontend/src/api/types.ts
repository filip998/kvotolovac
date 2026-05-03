export interface League {
  id: string;
  name: string;
  sport: string;
  country: string | null;
  is_active: boolean;
}

export interface Bookmaker {
  id: string;
  name: string;
  website_url?: string | null;
  is_active: boolean;
}

export interface MatchBookmaker {
  id: string;
  name: string;
}

export interface Match {
  id: string;
  league_id: string;
  league_name: string;
  sport: string;
  home_team: string;
  away_team: string;
  home_team_id?: number | null;
  away_team_id?: number | null;
  start_time: string | null;
  status: 'upcoming' | 'live' | 'finished';
  resolved_event_id?: string | null;
  available_bookmakers: MatchBookmaker[];
}

export type MarketType =
  | 'player_points'
  | 'player_points_milestones'
  | 'player_rebounds'
  | 'player_assists'
  | 'player_3points'
  | 'player_steals'
  | 'player_blocks'
  | 'player_turnovers'
  | 'player_points_rebounds'
  | 'player_points_assists'
  | 'player_rebounds_assists'
  | 'player_points_rebounds_assists'
  | 'game_total'
  | 'game_total_ot'
  | 'home_handicap_ot'
  | 'football_total_goals'
  | 'football_result'
  | 'football_double_chance'
  | 'football_result_double_chance'
  | 'match_winner'
  | 'tennis_match_winner';

export type OutcomeMarketType =
  | MarketType
  | 'football_total_goals'
  | 'football_result'
  | 'football_double_chance'
  | 'football_result_double_chance';

export interface OddsOffer {
  id: number;
  match_id: string;
  bookmaker_id: string;
  bookmaker_name: string;
  source_url?: string | null;
  market_type: MarketType;
  player_name: string | null;
  threshold: number;
  over_odds: number | null;
  under_odds: number | null;
  scraped_at: string;
}

export interface OutcomeOffer {
  id: number;
  match_id: string;
  bookmaker_id: string;
  bookmaker_name: string | null;
  source_url?: string | null;
  market_type: OutcomeMarketType;
  outcome_code: string;
  odds: number;
  line: number | null;
  raw_label: string | null;
  scraped_at: string | null;
}

export interface OpportunityLeg {
  offer_id?: number | null;
  match_id?: string | null;
  bookmaker_id: string;
  bookmaker_name?: string | null;
  source_url?: string | null;
  market_type: string;
  outcome_code: string;
  odds: number;
  line: number | null;
  raw_label: string | null;
}

export interface Opportunity {
  id: number;
  sport: string;
  event_id?: string | null;
  match_id: string;
  resolved_event_id?: string | null;
  home_team: string | null;
  away_team: string | null;
  league_name: string | null;
  start_time: string | null;
  opportunity_type: string;
  market_type: string;
  subject_type?: string | null;
  subject_key?: string | null;
  subject_name?: string | null;
  line: number | null;
  profit_margin: number | null;
  middle_profit_margin: number | null;
  market_keys: string[];
  legs: OpportunityLeg[];
  detected_at: string | null;
  is_active: boolean;
}

export type EdgeSport = 'basketball' | 'football' | 'tennis';

export interface EdgeLeg {
  bookmaker_id: string;
  bookmaker_name: string;
  outcome_code: string;
  line: number | null;
  odds: number;
  source_url?: string | null;
  bookmaker_match_id?: string | null;
}

export interface Edge {
  id: string;
  source: 'opportunity';
  source_id: number;
  sport: EdgeSport;
  match_id: string;
  resolved_event_id?: string | null;
  home_team: string | null;
  away_team: string | null;
  league_name: string | null;
  start_time: string | null;
  market_type: string;
  player_name: string | null;
  profit_margin: number | null;
  middle_profit_margin: number | null;
  gap: number | null;
  detected_at: string | null;
  leg_a: EdgeLeg;
  leg_b: EdgeLeg;
  opportunity_type?: string;
}

export interface UnresolvedOdds {
  id: number;
  bookmaker_id: string;
  bookmaker_name: string | null;
  raw_league_id: string;
  league_id: string;
  league_name: string | null;
  sport: string;
  market_type: string;
  player_name: string | null;
  raw_team_name: string;
  normalized_team_name: string;
  start_time: string | null;
  threshold: number;
  over_odds: number | null;
  under_odds: number | null;
  reason_code: string;
  candidate_count: number;
  candidate_matchups: string[];
  available_matchups_same_slot: string[];
  scraped_at: string | null;
}

export interface TeamReviewCase {
  id: number;
  bookmaker_id: string;
  bookmaker_name: string | null;
  raw_league_id: string;
  normalized_raw_league_id: string;
  sport: string;
  scope_league_id: string | null;
  scope_league_name: string | null;
  raw_team_name: string;
  normalized_raw_team_name: string;
  suggested_team_id: number | null;
  suggested_team_name: string | null;
  start_time: string | null;
  review_kind: string;
  reason_code: string;
  confidence: string;
  similarity_score: number | null;
  candidate_teams: TeamReviewCandidate[];
  matched_counterpart_team: string | null;
  canonical_home_team: string | null;
  canonical_away_team: string | null;
  evidence: string[];
  status: 'pending' | 'approved' | 'declined';
  scraped_at: string | null;
}

export interface TeamReviewCandidate {
  team_id: number;
  team_name: string;
  score: number | null;
  matched_alias: string | null;
  slot_support?: number | null;
  canonical_home_team?: string | null;
  canonical_away_team?: string | null;
}

export interface TeamReviewApproval {
  case_id: number;
  status: 'approved';
  saved_alias: string;
  saved_team_id: number;
  saved_team_name: string;
  resolved_team_name: string | null;
  merged_source_team_id?: number | null;
  merged_source_team_name?: string | null;
}

export interface TeamReviewAction {
  case_id: number;
  status: 'declined';
}

export type EventReviewStatus = 'pending' | 'accepted' | 'declined';

export interface EventReviewVariant {
  match_id: string;
  bookmaker_id: string | null;
  bookmaker_name: string | null;
  league_id: string | null;
  league_name: string | null;
  home_team: string;
  away_team: string;
  start_time: string | null;
  source_url: string | null;
  source_league_id: string | null;
  source_league_name: string | null;
  source_home_team: string | null;
  source_away_team: string | null;
  source_start_time: string | null;
  orientation: string;
  confidence: number | null;
  evidence: string[];
}

export interface EventReviewCase {
  id: number;
  fingerprint: string;
  sport: string;
  start_time: string;
  primary_match_id: string | null;
  candidate_resolved_event_id: string | null;
  candidate_match_ids: string[];
  reason_code: string;
  confidence: number | null;
  method: string;
  source_bookmaker_ids: string[];
  source_league_labels: string[];
  evidence: string[];
  metadata: Record<string, unknown>;
  status: EventReviewStatus;
  resolved_event_id: string | null;
  primary_home_team: string | null;
  primary_away_team: string | null;
  primary_league_name: string | null;
  variants: EventReviewVariant[];
  created_at: string | null;
  updated_at: string | null;
  accepted_at: string | null;
  declined_at: string | null;
}

export interface EventReviewAction {
  case_id: number;
  status: EventReviewStatus;
  resolved_event_id: string | null;
}

export interface BookmakerStatus {
  id: string;
  name: string;
  last_scrape: string | null;
  is_active: boolean;
}

export interface ScanProgress {
  in_progress: boolean;
  phase: string;
  started_at: string | null;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  active_tasks: number;
}

export interface SystemStatus {
  status: string;
  last_scrape_at: string | null;
  total_matches: number;
  total_odds: number;
  total_opportunities: number;
  active_bookmakers: number;
  scheduler_running: boolean;
  scan: ScanProgress;
  // Mock-only fields (optional)
  last_scrape?: string | null;
  bookmaker_status?: BookmakerStatus[];
}

export type ScrapeMarketScope = 'all' | 'player_props';
export type ScraperDetailMode = 'partial' | 'full';

export interface ScrapeRuntimeSettings {
  enabled_bookmakers: string[];
  enabled_sports: string[];
  scrape_market_scope: ScrapeMarketScope;
  scrape_lookahead_hours: number;
  scrape_interval_minutes: number;
  max_middle_opportunities_per_market: number;
  rate_limit_per_second: number;
  meridian_rate_limit_per_second: number;
  soccerbet_detail_mode: ScraperDetailMode;
  merkurxtip_detail_mode: ScraperDetailMode;
  notification_gap_threshold: number;
  persist_inapp_notifications: boolean;
}

export type ScrapeRuntimeSettingsUpdate = Partial<ScrapeRuntimeSettings>;

export interface ScrapeSettingsBookmakerOption {
  id: string;
  name: string;
  enabled: boolean;
}

export interface ScrapeSettingsOptions {
  bookmakers: ScrapeSettingsBookmakerOption[];
  sports: string[];
  market_scopes: ScrapeMarketScope[];
  detail_modes: ScraperDetailMode[];
  scrape_interval_minutes_min: number;
  scrape_interval_minutes_max: number;
  scrape_lookahead_hours_min: number;
  scrape_lookahead_hours_max: number;
  max_middle_opportunities_per_market_min: number;
  max_middle_opportunities_per_market_max: number;
  rate_limit_per_second_min: number;
  rate_limit_per_second_max: number;
}

export interface ScrapeSettingsResponse {
  applied: ScrapeRuntimeSettings;
  pending: ScrapeRuntimeSettings | null;
  defaults: ScrapeRuntimeSettings;
  has_pending_changes: boolean;
  applied_at: string | null;
  pending_at: string | null;
  applied_immediately: boolean;
  options: ScrapeSettingsOptions;
}

export interface OpportunityBoardFilters {
  sport?: string;
  league?: string;
  bookmaker_ids?: string[];
  min_gap?: number;
  market_type?: MarketType;
  search?: string;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export interface OpportunityFilters {
  sport?: string;
  bookmaker_ids?: string[];
  market_type?: OutcomeMarketType;
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export interface OutcomeOfferFilters {
  sport?: string;
  match_id?: string;
  bookmaker_ids?: string[];
  market_type?: OutcomeMarketType;
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export interface UnresolvedOddsFilters {
  bookmaker_id?: string;
  bookmaker_ids?: string[];
  sport?: string;
  reason_code?: string;
  market_type?: string;
  league_id?: string;
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export interface TeamReviewFilters {
  bookmaker_id?: string;
  bookmaker_ids?: string[];
  sport?: string;
  status?: 'pending' | 'approved' | 'declined';
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export interface EventReviewFilters {
  bookmaker_id?: string;
  bookmaker_ids?: string[];
  sport?: string;
  status?: EventReviewStatus;
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export interface TeamReviewApprovalInput {
  team_id?: number;
  create_team_name?: string;
}

export interface CanonicalTeam {
  id: number;
  sport: string;
  display_name: string;
  aliases: string[];
  alias_count: number;
  merged_into_team_id: number | null;
}

export interface CanonicalTeamFilters {
  sport?: string;
  search?: string;
  limit?: number;
  offset?: number;
  include_merged?: boolean;
}

export interface CanonicalTeamMerge {
  source_team_id: number;
  target_team_id: number;
  merged_team_name: string;
  matches_scraped: number;
  odds_scraped: number;
  opportunities_found: number;
}

export interface CanonicalTeamUnmerge {
  source_team_id: number;
  target_team_id: number;
  restored_team_name: string;
}

export interface MatchMergeTeamPairing {
  source_team_id: number;
  target_team_id: number;
}

export interface MatchMergeInput {
  target_match_id: string;
  source_match_ids: string[];
  team_pairings: MatchMergeTeamPairing[];
}

export interface MatchMergeResult {
  target_match_id: string;
  merged_source_match_ids: string[];
  merged_team_ids: MatchMergeTeamPairing[];
  reassigned_odds: number;
  reassigned_odds_history: number;
  reassigned_outcome_offers: number;
  reassigned_opportunities: number;
  deleted_source_matches: number;
}

export interface EventMergeInput {
  primary_match_id: string;
  source_match_ids: string[];
}

export interface EventMergeResult {
  resolved_event_id: string;
  primary_match_id: string;
  linked_match_ids: string[];
  linked_member_count: number;
  opportunities_rebuilt: number;
}
