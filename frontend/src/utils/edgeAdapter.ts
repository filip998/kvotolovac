import type { Discrepancy, Edge, EdgeLeg, EdgeSport, Opportunity, OpportunityLeg } from '../api/types';

function inferSportFromMarketType(marketType: string): EdgeSport {
  if (marketType.startsWith('football_')) {
    return 'football';
  }
  if (marketType === 'match_winner' || marketType.startsWith('tennis_')) {
    return 'tennis';
  }
  return 'basketball';
}

export function discrepancyToEdge(d: Discrepancy): Edge {
  const sport = inferSportFromMarketType(d.market_type);
  const legA: EdgeLeg = {
    bookmaker_id: d.bookmaker_a_id,
    bookmaker_name: d.bookmaker_a_name,
    outcome_code: 'over',
    line: d.threshold_a,
    odds: d.odds_a,
    source_url: d.bookmaker_a_source_url ?? null,
    bookmaker_match_id: d.bookmaker_a_match_id ?? null,
  };
  const legB: EdgeLeg = {
    bookmaker_id: d.bookmaker_b_id,
    bookmaker_name: d.bookmaker_b_name,
    outcome_code: 'under',
    line: d.threshold_b,
    odds: d.odds_b,
    source_url: d.bookmaker_b_source_url ?? null,
    bookmaker_match_id: d.bookmaker_b_match_id ?? null,
  };
  return {
    id: `discrepancy-${d.id}`,
    source: 'discrepancy',
    source_id: d.id,
    sport,
    match_id: d.match_id,
    resolved_event_id: d.resolved_event_id ?? null,
    home_team: d.home_team,
    away_team: d.away_team,
    league_name: d.league_name,
    start_time: d.detected_at,
    market_type: d.market_type,
    player_name: d.player_name,
    profit_margin: d.profit_margin,
    middle_profit_margin: d.middle_profit_margin ?? null,
    gap: d.gap,
    detected_at: d.detected_at,
    leg_a: legA,
    leg_b: legB,
  };
}

function opportunityLegToEdgeLeg(leg: OpportunityLeg): EdgeLeg {
  return {
    bookmaker_id: leg.bookmaker_id,
    bookmaker_name: leg.bookmaker_name ?? leg.bookmaker_id,
    outcome_code: leg.outcome_code,
    line: leg.line,
    odds: leg.odds,
    source_url: leg.source_url ?? null,
    bookmaker_match_id: leg.match_id ?? null,
  };
}

function gapFromOpportunity(o: Opportunity): number | null {
  if (o.opportunity_type !== 'middle' || o.legs.length !== 2) {
    return null;
  }
  const [a, b] = o.legs;
  if (a.line == null || b.line == null) {
    return null;
  }
  return Math.abs(a.line - b.line);
}

export function opportunityToEdge(o: Opportunity): Edge | null {
  if (o.legs.length !== 2) {
    return null;
  }
  const sport: EdgeSport =
    o.sport === 'football' || o.sport === 'basketball' || o.sport === 'tennis'
      ? o.sport
      : inferSportFromMarketType(o.market_type);
  return {
    id: `opportunity-${o.id}`,
    source: 'opportunity',
    source_id: o.id,
    sport,
    match_id: o.match_id,
    resolved_event_id: o.resolved_event_id ?? null,
    home_team: o.home_team,
    away_team: o.away_team,
    league_name: o.league_name,
    start_time: o.start_time,
    market_type: o.market_type,
    player_name: o.subject_name ?? null,
    profit_margin: o.profit_margin,
    middle_profit_margin: o.middle_profit_margin,
    gap: gapFromOpportunity(o),
    detected_at: o.detected_at,
    leg_a: opportunityLegToEdgeLeg(o.legs[0]),
    leg_b: opportunityLegToEdgeLeg(o.legs[1]),
    opportunity_type: o.opportunity_type,
  };
}

export function buildOpportunityEdges(
  opportunities: Opportunity[] | undefined,
  sportFilter?: EdgeSport | 'both'
): Edge[] {
  const edges: Edge[] = [];
  for (const o of opportunities ?? []) {
    const edge = opportunityToEdge(o);
    if (edge) edges.push(edge);
  }
  if (sportFilter && sportFilter !== 'both') {
    return edges.filter((edge) => edge.sport === sportFilter);
  }
  return edges;
}

export function buildEdges(
  discrepancies: Discrepancy[] | undefined,
  opportunities: Opportunity[] | undefined,
  sportFilter?: EdgeSport | 'both'
): Edge[] {
  const edges: Edge[] = [];
  for (const d of discrepancies ?? []) {
    edges.push(discrepancyToEdge(d));
  }
  edges.push(...buildOpportunityEdges(opportunities));
  if (sportFilter && sportFilter !== 'both') {
    return edges.filter((edge) => edge.sport === sportFilter);
  }
  return edges;
}
