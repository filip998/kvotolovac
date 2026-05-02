import { MARKET_TYPE_LABELS } from './constants';
import { formatHandicapLine, formatThreshold, isHandicapMarket } from './format';
import type { Edge, EdgeLeg } from '../api/types';

const FOOTBALL_RESULT_LABELS: Record<string, string> = {
  home: '1',
  draw: 'X',
  away: '2',
  home_or_draw: '1X',
  draw_or_away: 'X2',
  home_or_away: '12',
};

export function marketTypeLabel(marketType: string): string {
  return MARKET_TYPE_LABELS[marketType as keyof typeof MARKET_TYPE_LABELS] || marketType;
}

export function bookmakerNameOrFallback(
  bookmakerId: string,
  fallback?: string | null
): string {
  return fallback || bookmakerId;
}

function footballTotalGoalsLabel(outcomeCode: string, line: number | null | undefined): string {
  if (line == null || !Number.isFinite(line)) {
    return outcomeCode;
  }
  const boundary = Math.floor(line);
  if (outcomeCode === 'under') {
    return `0-${boundary}`;
  }
  if (outcomeCode === 'over') {
    return `${boundary + 1}+`;
  }
  return outcomeCode;
}

export function formatOutcomeLabel(
  outcomeCode: string,
  marketType: string,
  line: number | null | undefined
): string {
  if (marketType === 'football_total_goals') {
    return footballTotalGoalsLabel(outcomeCode, line);
  }
  if (marketType === 'football_result' || marketType === 'football_double_chance') {
    return FOOTBALL_RESULT_LABELS[outcomeCode] || outcomeCode;
  }
  if (outcomeCode === 'over' || outcomeCode === 'under') {
    return outcomeCode === 'over' ? 'Over' : 'Under';
  }
  return outcomeCode;
}

export function formatLegLineLabel(leg: EdgeLeg, marketType: string): string {
  if (isHandicapMarket(marketType)) {
    const role = leg.outcome_code === 'over' ? 'home' : 'away';
    return formatHandicapLine(leg.line ?? 0, role);
  }
  if (leg.line == null) {
    return '';
  }
  return formatThreshold(leg.line);
}

function footballOpportunityHeadline(edge: Edge): string {
  if (
    edge.opportunity_type === 'same_line_arbitrage' &&
    edge.market_type === 'football_total_goals'
  ) {
    return `Total goals ${edge.leg_a.line ?? edge.leg_b.line ?? 2.5}`;
  }
  if (edge.opportunity_type === 'middle') {
    if (edge.market_type !== 'football_total_goals') {
      return `${marketTypeLabel(edge.market_type)} middle`;
    }
    const overLeg =
      edge.leg_a.outcome_code === 'over'
        ? edge.leg_a
        : edge.leg_b.outcome_code === 'over'
          ? edge.leg_b
          : null;
    const underLeg =
      edge.leg_a.outcome_code === 'under'
        ? edge.leg_a
        : edge.leg_b.outcome_code === 'under'
          ? edge.leg_b
          : null;
    if (overLeg?.line != null && underLeg?.line != null) {
      const firstGoal = Math.floor(overLeg.line) + 1;
      const lastGoal = Math.floor(underLeg.line);
      const window = firstGoal === lastGoal ? `exactly ${firstGoal} goals` : `${firstGoal}-${lastGoal} goals`;
      return `Goals middle: ${window}`;
    }
    return 'Goals middle';
  }
  if (edge.opportunity_type === 'complementary_outcomes') {
    return 'Result combo';
  }
  return marketTypeLabel(edge.market_type);
}

export function edgeMarketHeadline(edge: Edge): string {
  if (edge.source === 'opportunity') {
    return footballOpportunityHeadline(edge);
  }
  return marketTypeLabel(edge.market_type);
}
