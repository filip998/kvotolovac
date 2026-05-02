import type { Discrepancy } from '../api/types';

/**
 * A group of discrepancies that share a (match_id, market_type, player_name)
 * tuple. Threshold-based markets (handicap, totals, player props) emit one
 * analyzer row per (threshold_a, threshold_b, bookmaker_a, bookmaker_b)
 * cross-pair, which can produce thousands of rows for a single market on a
 * single match. Grouping collapses that ladder into a single summary row,
 * with the full ladder available on demand for the user who wants to pick
 * a specific line.
 */
export interface MarketGroup {
  /** Stable string key suitable for React keys / Set membership. */
  key: string;
  matchId: string;
  marketType: Discrepancy['market_type'];
  playerName: string | null;
  /** All discrepancies in the group, sorted by profit_margin descending. */
  lines: Discrepancy[];
  /** Best (highest profit_margin) discrepancy — the summary row. */
  best: Discrepancy;
}

/**
 * Build a stable, collision-free key for a market group. Includes
 * market_type + player_name so player props and game-level markets on the
 * same match remain separate. ``player_name`` may be ``null`` for
 * game-level markets — we mark it explicitly to avoid a collision with a
 * hypothetical player whose name is the literal string "null".
 */
export function buildMarketGroupKey(
  matchId: string,
  marketType: string,
  playerName: string | null,
): string {
  const player = playerName === null ? '\u0000' : playerName;
  return `${matchId}\u001f${marketType}\u001f${player}`;
}

/**
 * Group discrepancies by (match_id, market_type, player_name).
 *
 * Within each group, lines are sorted by profit_margin descending so the
 * first entry is always the most attractive offer (the "best line").
 *
 * Group iteration order preserves the order in which each group was first
 * seen in the input. Callers that want a specific order (e.g., best-margin
 * desc) should sort the returned array themselves; we deliberately don't
 * impose one because different views want different orders.
 */
export function groupDiscrepanciesByMarket(
  discrepancies: readonly Discrepancy[],
): MarketGroup[] {
  const byKey = new Map<string, Discrepancy[]>();
  const order: string[] = [];

  for (const d of discrepancies) {
    const key = buildMarketGroupKey(d.match_id, d.market_type, d.player_name);
    const existing = byKey.get(key);
    if (existing) {
      existing.push(d);
    } else {
      byKey.set(key, [d]);
      order.push(key);
    }
  }

  const groups: MarketGroup[] = [];
  for (const key of order) {
    const lines = byKey.get(key)!;
    lines.sort((a, b) => b.profit_margin - a.profit_margin);
    const best = lines[0];
    groups.push({
      key,
      matchId: best.match_id,
      marketType: best.market_type,
      playerName: best.player_name,
      lines,
      best,
    });
  }
  return groups;
}
