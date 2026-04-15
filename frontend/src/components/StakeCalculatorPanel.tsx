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
  balanced: 'Keeps both outside outcomes on the same floor.',
  'aggressive-middle': 'Leans 5% toward the better price for more middle upside.',
  'conservative-rounded': 'Rounds to 0.1u and protects the safer outcome after rounding.',
};

const LABEL_TONE_CLASSES: Record<StakeCalculatorLabel['tone'], string> = {
  accent: 'border-accent/30 bg-accent/[0.12] text-accent',
  warning: 'border-warning/30 bg-warning/[0.12] text-warning',
  muted: 'border-border bg-surface-raised text-text-secondary',
};

interface StakeSplitRowProps {
  bookmaker: string;
  detail: string;
  value: string;
}

interface SummaryPillProps {
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

function StakeSplitRow({ bookmaker, detail, value }: StakeSplitRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-text">{bookmaker}</div>
        <div className="text-[11px] leading-5 text-text-secondary">{detail}</div>
      </div>
      <div className="shrink-0 font-mono text-sm font-semibold text-text">{value}</div>
    </div>
  );
}

function SummaryPill({
  label,
  value,
  detail,
  valueClassName = 'text-text',
}: SummaryPillProps) {
  return (
    <div className="rounded-md border border-border/70 bg-bg/70 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-text-muted">
        {label}
      </div>
      <div className={`mt-1 font-mono text-sm font-semibold ${valueClassName}`}>{value}</div>
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
    <div className="rounded-lg border border-border/70 bg-bg/45 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-text-muted">
            <span>Inline sizing</span>
            <span className="h-1 w-1 rounded-full bg-border" />
            <span className="font-mono tracking-normal text-text-secondary">
              {formatUnits(totalUnits)}u total
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-5 text-text-secondary">
            {MODE_DESCRIPTIONS[activeMode]}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Stake calculator mode">
          {availableModes.map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setSelectedMode(mode)}
              aria-pressed={activeMode === mode}
              className={`rounded-md border px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.16em] transition ${
                activeMode === mode
                  ? 'border-accent/40 bg-accent/[0.14] text-accent'
                  : 'border-border/70 bg-bg text-text-muted hover:border-border-hover hover:text-text-secondary'
              }`}
            >
              {MODE_LABELS[mode]}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-3 divide-y divide-border/70 rounded-md border border-border/70 bg-bg/70 px-3">
        <StakeSplitRow
          bookmaker={d.bookmaker_a_name}
          detail={`Over ${formatThreshold(d.threshold_a)} @ ${formatOdds(d.odds_a)}`}
          value={`${formatUnits(plan.stakeA)}u`}
        />
        <StakeSplitRow
          bookmaker={d.bookmaker_b_name}
          detail={`Under ${formatThreshold(d.threshold_b)} @ ${formatOdds(d.odds_b)}`}
          value={`${formatUnits(plan.stakeB)}u`}
        />
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <SummaryPill
          label="Worst-case P/L"
          value={`${formatSignedUnits(plan.worstCaseProfit)}u`}
          detail={
            plan.isGuaranteed
              ? 'Outside outcomes stay at or above breakeven.'
              : 'Outside outcomes can lose if the middle misses.'
          }
          valueClassName={resultValueClass(plan.worstCaseProfit)}
        />
        <SummaryPill
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
        <div className="mt-2 flex flex-wrap gap-1.5">
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
