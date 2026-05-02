import type { Edge } from '../api/types';

export type StakeCalculatorMode =
  | 'balanced'
  | 'aggressive-middle'
  | 'conservative-rounded';

type StakeCalculatorLabelTone = 'accent' | 'warning' | 'muted';

export interface StakeCalculatorLabel {
  text: string;
  tone: StakeCalculatorLabelTone;
}

export interface StakeCalculatorPlan {
  mode: StakeCalculatorMode;
  stakeA: number;
  stakeB: number;
  totalStake: number;
  profitIfAWins: number;
  profitIfBWins: number;
  worstCaseProfit: number;
  middleProfit: number | null;
  isGuaranteed: boolean;
  labels: StakeCalculatorLabel[];
}

const AGGRESSIVE_SHIFT_RATIO = 0.05;
const ROUNDING_STEP = 0.1;
const EPSILON = 1e-9;

function normalizeFloat(value: number) {
  return Math.abs(value) < EPSILON ? 0 : Number(value.toFixed(4));
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function roundToStep(value: number, step: number) {
  return Math.round((value + Number.EPSILON) / step) * step;
}

function floorToStep(value: number, step: number) {
  return Math.floor((value + Number.EPSILON) / step) * step;
}

function ceilToStep(value: number, step: number) {
  return Math.ceil((value - Number.EPSILON) / step) * step;
}

function hasMiddleWindow(edge: Edge) {
  return (edge.gap ?? 0) > 0 && edge.middle_profit_margin != null;
}

function hasNonNegativeFloor(edge: Edge) {
  return (edge.profit_margin ?? Number.NEGATIVE_INFINITY) >= 0;
}

function calculateBalancedStakes(edge: Edge, totalUnits: number) {
  const oddsA = edge.leg_a.odds;
  const oddsB = edge.leg_b.odds;
  const totalOdds = oddsA + oddsB;
  const stakeA = totalUnits * (oddsB / totalOdds);
  const stakeB = totalUnits - stakeA;

  return {
    stakeA: normalizeFloat(stakeA),
    stakeB: normalizeFloat(stakeB),
  };
}

function calculateScenarioProfits(
  edge: Edge,
  stakeA: number,
  stakeB: number,
  totalStake: number
) {
  const profitIfAWins = normalizeFloat(stakeA * edge.leg_a.odds - totalStake);
  const profitIfBWins = normalizeFloat(stakeB * edge.leg_b.odds - totalStake);
  const middleProfit = hasMiddleWindow(edge)
    ? normalizeFloat(stakeA * edge.leg_a.odds + stakeB * edge.leg_b.odds - totalStake)
    : null;

  return {
    profitIfAWins,
    profitIfBWins,
    middleProfit,
  };
}

function buildLabels(
  edge: Edge,
  mode: StakeCalculatorMode,
  worstCaseProfit: number
): StakeCalculatorLabel[] {
  const labels: StakeCalculatorLabel[] = [];

  if (!hasMiddleWindow(edge)) {
    labels.push({ text: 'Same-threshold arb', tone: 'accent' });
  }

  if (mode === 'aggressive-middle') {
    labels.push({ text: 'Middle-focused', tone: 'accent' });
  }

  if (mode === 'conservative-rounded') {
    labels.push({ text: 'Rounded to 0.1u', tone: 'muted' });
  }

  if (worstCaseProfit < 0) {
    labels.push({ text: 'Not guaranteed', tone: 'warning' });
  } else if (hasMiddleWindow(edge) && worstCaseProfit === 0) {
    labels.push({ text: 'Breakeven floor', tone: 'muted' });
  }

  return labels;
}

function buildPlan(
  edge: Edge,
  mode: StakeCalculatorMode,
  stakeA: number,
  stakeB: number
): StakeCalculatorPlan {
  const totalStake = normalizeFloat(stakeA + stakeB);
  const { profitIfAWins, profitIfBWins, middleProfit } = calculateScenarioProfits(
    edge,
    stakeA,
    stakeB,
    totalStake
  );
  const worstCaseProfit = normalizeFloat(Math.min(profitIfAWins, profitIfBWins));

  return {
    mode,
    stakeA: normalizeFloat(stakeA),
    stakeB: normalizeFloat(stakeB),
    totalStake,
    profitIfAWins,
    profitIfBWins,
    worstCaseProfit,
    middleProfit,
    isGuaranteed: worstCaseProfit >= 0,
    labels: buildLabels(edge, mode, worstCaseProfit),
  };
}

export function getAvailableStakeCalculatorModes(edge: Edge): StakeCalculatorMode[] {
  if (!hasMiddleWindow(edge)) {
    return ['balanced'];
  }

  if (hasNonNegativeFloor(edge)) {
    return ['balanced', 'aggressive-middle', 'conservative-rounded'];
  }

  return ['aggressive-middle'];
}

export function calculateStakePlan(
  edge: Edge,
  totalUnits: number,
  mode: StakeCalculatorMode
): StakeCalculatorPlan | null {
  if (!Number.isFinite(totalUnits) || totalUnits <= 0) {
    return null;
  }

  if (!getAvailableStakeCalculatorModes(edge).includes(mode)) {
    return null;
  }

  const balanced = calculateBalancedStakes(edge, totalUnits);

  if (mode === 'balanced') {
    return buildPlan(edge, mode, balanced.stakeA, balanced.stakeB);
  }

  if (mode === 'aggressive-middle') {
    const shift = normalizeFloat(totalUnits * AGGRESSIVE_SHIFT_RATIO);

    if (edge.leg_a.odds > edge.leg_b.odds + EPSILON) {
      const stakeA = clamp(balanced.stakeA + shift, 0, totalUnits);
      return buildPlan(edge, mode, stakeA, totalUnits - stakeA);
    }

    if (edge.leg_b.odds > edge.leg_a.odds + EPSILON) {
      const stakeB = clamp(balanced.stakeB + shift, 0, totalUnits);
      return buildPlan(edge, mode, totalUnits - stakeB, stakeB);
    }

    return buildPlan(edge, mode, balanced.stakeA, balanced.stakeB);
  }

  const candidateStakeAs = new Set<number>([
    clamp(roundToStep(balanced.stakeA, ROUNDING_STEP), 0, totalUnits),
    clamp(floorToStep(balanced.stakeA, ROUNDING_STEP), 0, totalUnits),
    clamp(ceilToStep(balanced.stakeA, ROUNDING_STEP), 0, totalUnits),
  ]);

  const candidates = Array.from(candidateStakeAs).map((candidateStakeA) =>
    buildPlan(
      edge,
      mode,
      normalizeFloat(candidateStakeA),
      normalizeFloat(totalUnits - candidateStakeA)
    )
  );

  candidates.sort((planA, planB) => {
    if (planB.worstCaseProfit !== planA.worstCaseProfit) {
      return planB.worstCaseProfit - planA.worstCaseProfit;
    }

    const balancedDeltaA = Math.abs(planA.stakeA - balanced.stakeA);
    const balancedDeltaB = Math.abs(planB.stakeA - balanced.stakeA);
    return balancedDeltaA - balancedDeltaB;
  });

  return candidates[0] ?? null;
}

export const getAvailableModesForEdge = getAvailableStakeCalculatorModes;
export const calculateStakePlanForEdge = calculateStakePlan;

