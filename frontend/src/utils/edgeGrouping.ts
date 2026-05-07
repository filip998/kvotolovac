import type { Edge, EdgeSport } from '../api/types';

/**
 * A group of Edges that share a (match_id, market_type, player_name) tuple.
 *
 * Threshold-based markets (handicap, totals, player props) emit one analyzer
 * row per (threshold_a, threshold_b, bookmaker_a, bookmaker_b) cross-pair —
 * a single match's ladder can fan out to thousands of rows. Grouping
 * collapses that ladder into a single summary row, with the full ladder
 * available on demand.
 *
 * Football opportunity edges typically have one Edge per (match, market,
 * outcome-pair) so most groups have lines.length === 1 and render as a
 * plain row without an expandable ladder.
 */
export interface EdgeGroup {
  /** Stable string key suitable for React keys / Set membership. */
  key: string;
  source: Edge['source'];
  sport: EdgeSport;
  matchId: string;
  marketType: string;
  playerName: string | null;
  homeTeam: string | null;
  awayTeam: string | null;
  leagueName: string | null;
  /** All edges in the group, sorted by the market-appropriate value descending. */
  lines: Edge[];
  /** Best edge for the summary row. Middies prefer EV; non-middles prefer profit margin. */
  best: Edge;
}

/**
 * Build a stable, collision-free key for an edge group. Includes
 * market_type + player_name so player props and game-level markets on the
 * same match remain separate. ``player_name`` may be ``null`` for
 * game-level markets — we mark it explicitly with NUL to avoid a collision
 * with a hypothetical player whose name is the literal string "null".
 */
export function buildEdgeGroupKey(
  matchId: string,
  marketType: string,
  playerName: string | null,
): string {
  const player = playerName === null ? '\u0000' : playerName;
  return `${matchId}\u001f${marketType}\u001f${player}`;
}

/**
 * Group edges by (match_id, market_type, player_name).
 *
 * Within each group, lines are sorted by the market-appropriate value so the
 * first entry is always the most attractive offer (the "best line").
 *
 * Group iteration order preserves the order in which each group was first
 * seen in the input. Callers that want a specific order (e.g., best-margin
 * desc) should sort the returned array themselves.
 */
export function groupEdgesByMarket(edges: readonly Edge[]): EdgeGroup[] {
  const byKey = new Map<string, Edge[]>();
  const order: string[] = [];

  for (const edge of edges) {
    const key = buildEdgeGroupKey(edge.match_id, edge.market_type, edge.player_name);
    const existing = byKey.get(key);
    if (existing) {
      existing.push(edge);
    } else {
      byKey.set(key, [edge]);
      order.push(key);
    }
  }

  const groups: EdgeGroup[] = [];
  for (const key of order) {
    const lines = byKey.get(key)!;
    lines.sort((a, b) => edgeRankValue(b) - edgeRankValue(a));
    const best = lines[0];
    groups.push({
      key,
      source: best.source,
      sport: best.sport,
      matchId: best.match_id,
      marketType: best.market_type,
      playerName: best.player_name,
      homeTeam: best.home_team,
      awayTeam: best.away_team,
      leagueName: best.league_name,
      lines,
      best,
    });
  }
  return groups;
}

function edgeRankValue(edge: Edge): number {
  if (edge.opportunity_type === 'middle') {
    return edge.middle_ev_rank ?? edge.middle_ev ?? edge.profit_margin ?? Number.NEGATIVE_INFINITY;
  }
  return edge.profit_margin ?? Number.NEGATIVE_INFINITY;
}
