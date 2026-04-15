import type { Discrepancy } from '../api/types';

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

function hasMiddleWindow(discrepancy: Discrepancy) {
  return discrepancy.gap > 0 && discrepancy.middle_profit_margin != null;
}

function hasNonNegativeFloor(discrepancy: Discrepancy) {
  return discrepancy.profit_margin >= 0;
}

function calculateBalancedStakes(discrepancy: Discrepancy, totalUnits: number) {
  const totalOdds = discrepancy.odds_a + discrepancy.odds_b;
  const stakeA = totalUnits * (discrepancy.odds_b / totalOdds);
  const stakeB = totalUnits - stakeA;

  return {
    stakeA: normalizeFloat(stakeA),
    stakeB: normalizeFloat(stakeB),
  };
}

function calculateScenarioProfits(
  discrepancy: Discrepancy,
  stakeA: number,
  stakeB: number,
  totalStake: number
) {
  const profitIfAWins = normalizeFloat(stakeA * discrepancy.odds_a - totalStake);
  const profitIfBWins = normalizeFloat(stakeB * discrepancy.odds_b - totalStake);
  const middleProfit = hasMiddleWindow(discrepancy)
    ? normalizeFloat(stakeA * discrepancy.odds_a + stakeB * discrepancy.odds_b - totalStake)
    : null;

  return {
    profitIfAWins,
    profitIfBWins,
    middleProfit,
  };
}

function buildLabels(
  discrepancy: Discrepancy,
  mode: StakeCalculatorMode,
  worstCaseProfit: number
): StakeCalculatorLabel[] {
  const labels: StakeCalculatorLabel[] = [];

  if (!hasMiddleWindow(discrepancy)) {
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
  } else if (hasMiddleWindow(discrepancy) && worstCaseProfit === 0) {
    labels.push({ text: 'Breakeven floor', tone: 'muted' });
  }

  return labels;
}

function buildPlan(
  discrepancy: Discrepancy,
  mode: StakeCalculatorMode,
  stakeA: number,
  stakeB: number
): StakeCalculatorPlan {
  const totalStake = normalizeFloat(stakeA + stakeB);
  const { profitIfAWins, profitIfBWins, middleProfit } = calculateScenarioProfits(
    discrepancy,
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
    labels: buildLabels(discrepancy, mode, worstCaseProfit),
  };
}

export function getAvailableStakeCalculatorModes(
  discrepancy: Discrepancy
): StakeCalculatorMode[] {
  if (!hasMiddleWindow(discrepancy)) {
    return ['balanced'];
  }

  if (hasNonNegativeFloor(discrepancy)) {
    return ['balanced', 'aggressive-middle', 'conservative-rounded'];
  }

  return ['aggressive-middle'];
}

export function calculateStakePlan(
  discrepancy: Discrepancy,
  totalUnits: number,
  mode: StakeCalculatorMode
): StakeCalculatorPlan | null {
  if (!Number.isFinite(totalUnits) || totalUnits <= 0) {
    return null;
  }

  if (!getAvailableStakeCalculatorModes(discrepancy).includes(mode)) {
    return null;
  }

  const balanced = calculateBalancedStakes(discrepancy, totalUnits);

  if (mode === 'balanced') {
    return buildPlan(discrepancy, mode, balanced.stakeA, balanced.stakeB);
  }

  if (mode === 'aggressive-middle') {
    const shift = normalizeFloat(totalUnits * AGGRESSIVE_SHIFT_RATIO);

    if (discrepancy.odds_a > discrepancy.odds_b + EPSILON) {
      const stakeA = clamp(balanced.stakeA + shift, 0, totalUnits);
      return buildPlan(discrepancy, mode, stakeA, totalUnits - stakeA);
    }

    if (discrepancy.odds_b > discrepancy.odds_a + EPSILON) {
      const stakeB = clamp(balanced.stakeB + shift, 0, totalUnits);
      return buildPlan(discrepancy, mode, totalUnits - stakeB, stakeB);
    }

    return buildPlan(discrepancy, mode, balanced.stakeA, balanced.stakeB);
  }

  const candidateStakeAs = new Set<number>([
    clamp(roundToStep(balanced.stakeA, ROUNDING_STEP), 0, totalUnits),
    clamp(floorToStep(balanced.stakeA, ROUNDING_STEP), 0, totalUnits),
    clamp(ceilToStep(balanced.stakeA, ROUNDING_STEP), 0, totalUnits),
  ]);

  const candidates = Array.from(candidateStakeAs).map((candidateStakeA) =>
    buildPlan(
      discrepancy,
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

export const getAvailableModesForDiscrepancy = getAvailableStakeCalculatorModes;
export const getStakeCalculatorModesForDiscrepancy = getAvailableStakeCalculatorModes;
export const calculateStakePlanForDiscrepancy = calculateStakePlan;
