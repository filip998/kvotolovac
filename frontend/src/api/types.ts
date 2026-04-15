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
  home_team: string;
  away_team: string;
  start_time: string;
  status: 'upcoming' | 'live' | 'finished';
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
  | 'player_points_rebounds'
  | 'player_points_assists'
  | 'player_rebounds_assists'
  | 'player_points_rebounds_assists'
  | 'game_total'
  | 'game_total_ot';

export interface OddsOffer {
  id: number;
  match_id: string;
  bookmaker_id: string;
  bookmaker_name: string;
  market_type: MarketType;
  player_name: string | null;
  threshold: number;
  over_odds: number | null;
  under_odds: number | null;
  scraped_at: string;
}

export interface Discrepancy {
  id: number;
  match_id: string;
  home_team: string;
  away_team: string;
  league_name: string;
  market_type: MarketType;
  player_name: string | null;
  bookmaker_a_id: string;
  bookmaker_a_name: string;
  bookmaker_b_id: string;
  bookmaker_b_name: string;
  threshold_a: number;
  threshold_b: number;
  odds_a: number;
  odds_b: number;
  gap: number;
  profit_margin: number;
  middle_profit_margin?: number | null;
  detected_at: string;
  is_active: boolean;
}

export interface UnresolvedOdds {
  id: number;
  bookmaker_id: string;
  bookmaker_name: string | null;
  raw_league_id: string;
  league_id: string;
  league_name: string | null;
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

export interface LeagueMatchingHealth {
  league_id: string;
  league_name: string;
  matched_events: number;
  pending_reviews: number;
  approved_reviews: number;
}

export interface MatchingReviewCase {
  id: number;
  bookmaker_id: string;
  bookmaker_name: string | null;
  raw_league_id: string;
  normalized_raw_league_id: string;
  suggested_league_id: string;
  suggested_league_name: string | null;
  match_id: string;
  home_team: string;
  away_team: string;
  start_time: string | null;
  reason_code: string;
  confidence: string;
  evidence: string[];
  status: 'pending' | 'approved';
  scraped_at: string | null;
}

export interface MatchingReviewSummary {
  total_matches: number;
  leagues_with_matches: number;
  pending_reviews: number;
  approved_reviews: number;
  inferred_events: number;
  leagues: LeagueMatchingHealth[];
}

export interface MatchingReviewApproval {
  case_id: number;
  status: 'approved';
  saved_alias: string;
  saved_league_id: string;
  saved_league_name: string | null;
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
  total_discrepancies: number;
  active_bookmakers: number;
  scheduler_running: boolean;
  scan: ScanProgress;
  // Mock-only fields (optional)
  last_scrape?: string | null;
  active_discrepancies?: number;
  bookmaker_status?: BookmakerStatus[];
}

export interface DiscrepancyFilters {
  sport?: string;
  league?: string;
  bookmaker_ids?: string[];
  min_gap?: number;
  market_type?: MarketType;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

export interface UnresolvedOddsFilters {
  bookmaker_id?: string;
  bookmaker_ids?: string[];
  reason_code?: string;
  market_type?: string;
  league_id?: string;
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export interface MatchingReviewFilters {
  bookmaker_id?: string;
  bookmaker_ids?: string[];
  league_id?: string;
  status?: 'pending' | 'approved';
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}
