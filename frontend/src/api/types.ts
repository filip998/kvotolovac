export interface League {
  id: string;
  name: string;
  sport: string;
  country: string | null;
  is_active: boolean;
}

export interface Match {
  id: string;
  league_id: string;
  league_name: string;
  home_team: string;
  away_team: string;
  start_time: string;
  status: 'upcoming' | 'live' | 'finished';
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
  | 'game_total';

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
  detected_at: string;
  is_active: boolean;
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
  min_gap?: number;
  market_type?: MarketType;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}
