import { useMemo, useState } from 'react';
import type { Discrepancy } from '../api/types';
import {
  formatGap,
  formatOdds,
  formatSignedUnits,
  formatThreshold,
  formatUnits,
  roundUnitsDisplayValue,
} from '../utils/format';
import {
  calculateStakePlan,
  getAvailableStakeCalculatorModes,
  type StakeCalculatorLabel,
  type StakeCalculatorMode,
} from '../utils/stakeCalculator';

interface StakeCalculatorPanelProps {
  discrepancy: Discrepancy;
  totalUnits: number;
}

const MODE_LABELS: Record<StakeCalculatorMode, string> = {
  balanced: 'Balanced',
  'aggressive-middle': 'Aggressive',
  'conservative-rounded': 'Rounded 0.1',
};

const MODE_DESCRIPTIONS: Record<StakeCalculatorMode, string> = {
  balanced: 'Balances the outside outcomes so both edge scenarios land on the same floor.',
  'aggressive-middle':
    'Shifts 5% of total exposure to the higher-odds side for more middle upside.',
  'conservative-rounded':
    'Rounds to 0.1-unit sizing and favors the safer post-rounding outcome.',
};

const LABEL_TONE_CLASSES: Record<StakeCalculatorLabel['tone'], string> = {
  accent: 'border-accent/30 bg-accent/[0.12] text-accent',
  warning: 'border-warning/30 bg-warning/[0.12] text-warning',
  muted: 'border-border bg-surface-raised text-text-secondary',
};

interface MetricTileProps {
  label: string;
  value: string;
  detail: string;
  valueClassName?: string;
}

function resultValueClass(value: number) {
  const roundedValue = roundUnitsDisplayValue(value);
  if (roundedValue > 0) return 'text-accent';
  if (roundedValue < 0) return 'text-danger';
  return 'text-text-secondary';
}

function MetricTile({
  label,
  value,
  detail,
  valueClassName = 'text-text',
}: MetricTileProps) {
  return (
    <div className="rounded-2xl border border-border/70 bg-bg/65 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
      <div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-text-muted">
        {label}
      </div>
      <div className={`mt-2 font-mono text-lg font-semibold ${valueClassName}`}>{value}</div>
      <div className="mt-1 text-[11px] leading-5 text-text-secondary">{detail}</div>
    </div>
  );
}

export default function StakeCalculatorPanel({
  discrepancy: d,
  totalUnits,
}: StakeCalculatorPanelProps) {
  const availableModes = useMemo(() => getAvailableStakeCalculatorModes(d), [d]);
  const [selectedMode, setSelectedMode] = useState<StakeCalculatorMode>(availableModes[0]);
  const activeMode = availableModes.includes(selectedMode) ? selectedMode : availableModes[0];
  const plan = useMemo(() => calculateStakePlan(d, totalUnits, activeMode), [d, totalUnits, activeMode]);

  if (!plan) {
    return null;
  }

  const middleValueClass =
    plan.middleProfit !== null ? resultValueClass(plan.middleProfit) : 'text-text-secondary';

  return (
    <div className="rounded-[22px] border border-border/70 bg-[linear-gradient(135deg,rgba(255,255,255,0.04),rgba(255,255,255,0.015))] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-text-muted">
            Inline stake calculator
          </div>
          <div className="mt-1 text-sm font-medium text-text">
            Using {formatUnits(totalUnits)}u across both books
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Stake calculator mode">
          {availableModes.map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setSelectedMode(mode)}
              aria-pressed={activeMode === mode}
              className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] transition ${
                activeMode === mode
                  ? 'border-accent/40 bg-accent/[0.14] text-accent'
                  : 'border-border bg-surface-raised text-text-muted hover:border-border-hover hover:text-text-secondary'
              }`}
            >
              {MODE_LABELS[mode]}
            </button>
          ))}
        </div>
      </div>

      <p className="mt-2 text-[11px] leading-5 text-text-secondary">{MODE_DESCRIPTIONS[activeMode]}</p>

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <MetricTile
          label="Stake A"
          value={`${formatUnits(plan.stakeA)}u`}
          detail={`${d.bookmaker_a_name} · Over ${formatThreshold(d.threshold_a)} @ ${formatOdds(d.odds_a)}`}
        />
        <MetricTile
          label="Stake B"
          value={`${formatUnits(plan.stakeB)}u`}
          detail={`${d.bookmaker_b_name} · Under ${formatThreshold(d.threshold_b)} @ ${formatOdds(d.odds_b)}`}
        />
        <MetricTile
          label="Total stake"
          value={`${formatUnits(plan.totalStake)}u`}
          detail={plan.isGuaranteed ? 'Protected outside the middle' : 'Exposure across both bets'}
        />
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <MetricTile
          label="Worst-case P/L"
          value={`${formatSignedUnits(plan.worstCaseProfit)}u`}
          detail={
            plan.isGuaranteed
              ? 'Outside outcomes stay at or above breakeven.'
              : 'Outside outcomes can lose if the middle misses.'
          }
          valueClassName={resultValueClass(plan.worstCaseProfit)}
        />
        <MetricTile
          label="Middle profit"
          value={plan.middleProfit !== null ? `${formatSignedUnits(plan.middleProfit)}u` : '—'}
          detail={
            plan.middleProfit !== null
              ? `${formatGap(d.gap)} pt middle window when both tickets cash.`
              : 'Unavailable on same-threshold arbitrage.'
          }
          valueClassName={middleValueClass}
        />
      </div>

      {plan.labels.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {plan.labels.map((labelItem) => (
            <span
              key={labelItem.text}
              className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${LABEL_TONE_CLASSES[labelItem.tone]}`}
            >
              {labelItem.text}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
