export const MARKET_TYPE_LABELS: Record<string, string> = {
  player_points: 'Player Points',
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
