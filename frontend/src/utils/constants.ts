import type { MarketType } from '../api/types';

export const MARKET_TYPES: readonly MarketType[] = [
  'player_points',
  'player_points_milestones',
  'player_rebounds',
  'player_assists',
  'player_3points',
  'player_steals',
  'player_blocks',
  'player_points_rebounds',
  'player_points_assists',
  'player_rebounds_assists',
  'player_points_rebounds_assists',
  'game_total',
];

export const MARKET_TYPE_LABELS: Record<MarketType, string> = {
  player_points: 'Player Points',
  player_points_milestones: 'Player Points Milestones',
  player_rebounds: 'Player Rebounds',
  player_assists: 'Player Assists',
  player_3points: 'Player 3-Pointers',
  player_steals: 'Player Steals',
  player_blocks: 'Player Blocks',
  player_points_rebounds: 'Points + Rebounds',
  player_points_assists: 'Points + Assists',
  player_rebounds_assists: 'Rebounds + Assists',
  player_points_rebounds_assists: 'Points + Rebounds + Assists',
  game_total: 'Game Total',
};

export const SPORT_LABELS: Record<string, string> = {
  basketball: 'Basketball',
};

export const STATUS_LABELS: Record<string, string> = {
  upcoming: 'Upcoming',
  live: 'Live',
  finished: 'Finished',
};

export const PROFIT_THRESHOLDS = {
  high: 3.0,
  moderate: 1.5,
} as const;

export const DEFAULT_SORT_BY = 'profit_margin';
export const DEFAULT_SORT_ORDER = 'desc' as const;
export const DEFAULT_LIMIT = 50;
