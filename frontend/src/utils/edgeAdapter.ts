import type { Edge, EdgeLeg, EdgeSport, Opportunity, OpportunityLeg } from '../api/types';

function inferSportFromMarketType(marketType: string): EdgeSport {
  if (marketType.startsWith('football_')) {
    return 'football';
  }
  if (marketType === 'match_winner' || marketType.startsWith('tennis_')) {
    return 'tennis';
  }
  return 'basketball';
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
    middle_hit_probability: o.middle_hit_probability,
    middle_ev: o.middle_ev,
    middle_model_confidence: o.middle_model_confidence,
    middle_model_diagnostics: o.middle_model_diagnostics ?? {},
    middle_ev_rank: o.middle_ev_rank,
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
