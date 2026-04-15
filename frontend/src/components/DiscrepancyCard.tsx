import { useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Discrepancy } from '../api/types';
import {
  formatGap,
  formatOdds,
  formatPercentage,
  formatRelativeTime,
  formatSignedUnits,
  formatThreshold,
  formatUnits,
  profitBgColor,
  profitColor,
  roundUnitsDisplayValue,
} from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import {
  calculateStakePlan,
  getAvailableStakeCalculatorModes,
  type StakeCalculatorLabel,
  type StakeCalculatorMode,
} from '../utils/stakeCalculator';
import BookmakerBadge from './BookmakerBadge';

interface DiscrepancyCardProps {
  discrepancy: Discrepancy;
  totalUnits: number;
  context?: 'flat' | 'grouped';
  rank?: number;
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

export default function DiscrepancyCard({
  discrepancy: d,
  totalUnits,
  context = 'grouped',
  rank,
}: DiscrepancyCardProps) {
  const location = useLocation();
  const marketLabel = MARKET_TYPE_LABELS[d.market_type] || d.market_type;
  const label = d.player_name ? `${d.player_name} — ${marketLabel}` : marketLabel;
  const availableModes = useMemo(() => getAvailableStakeCalculatorModes(d), [d]);
  const [selectedMode, setSelectedMode] = useState<StakeCalculatorMode>(availableModes[0]);
  const activeMode = availableModes.includes(selectedMode) ? selectedMode : availableModes[0];
  const plan = useMemo(() => calculateStakePlan(d, totalUnits, activeMode), [d, totalUnits, activeMode]);
  const showFlatMeta = context === 'flat';

  if (!plan) {
    return null;
  }

  const middleValueClass =
    plan.middleProfit !== null ? resultValueClass(plan.middleProfit) : 'text-text-secondary';

  return (
    <div
      className={`rounded-[24px] border p-4 shadow-[0_20px_60px_-42px_rgba(0,0,0,0.9)] transition hover:-translate-y-0.5 hover:border-border-hover ${profitBgColor(d.profit_margin)}`}
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {showFlatMeta && rank !== undefined && (
              <span className="rounded-full border border-border/70 bg-bg/75 px-2 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary">
                #{rank}
              </span>
            )}
            <span className="text-[11px] font-medium uppercase tracking-[0.24em] text-text-muted">
              {marketLabel}
            </span>
            {showFlatMeta && (
              <span className="rounded-full border border-border/70 bg-bg/60 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary">
                {d.league_name}
              </span>
            )}
          </div>
          <h4 className="mt-2 text-sm font-semibold text-text">{label}</h4>
          {showFlatMeta && (
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-text-secondary">
              <span>
                {d.home_team} vs {d.away_team}
              </span>
              <span className="h-1 w-1 rounded-full bg-border" />
              <span>Detected {formatRelativeTime(d.detected_at)}</span>
            </div>
          )}
        </div>
        <div className="text-right">
          <div className={`font-mono text-lg font-bold ${profitColor(d.profit_margin)}`}>
            {formatPercentage(d.profit_margin)}
          </div>
          <div className="mt-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-muted">
            {d.gap > 0 ? `${formatGap(d.gap)} pt gap` : 'same threshold'}
          </div>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3">
        <div className="rounded-2xl border border-border/70 bg-bg/55 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
          <div className="mb-2">
            <BookmakerBadge name={d.bookmaker_a_name} />
          </div>
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
            Over
          </span>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="font-mono text-lg font-semibold text-text">
              {formatThreshold(d.threshold_a)}
            </span>
            <span className="font-mono text-base font-semibold text-text">
              {formatOdds(d.odds_a)}
            </span>
          </div>
        </div>
        <div className="rounded-2xl border border-border/70 bg-bg/55 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
          <div className="mb-2">
            <BookmakerBadge name={d.bookmaker_b_name} />
          </div>
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
            Under
          </span>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="font-mono text-lg font-semibold text-text">
              {formatThreshold(d.threshold_b)}
            </span>
            <span className="font-mono text-base font-semibold text-text">
              {formatOdds(d.odds_b)}
            </span>
          </div>
        </div>
      </div>

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

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 text-xs">
        <div className="flex items-center gap-3">
          <span className="font-mono font-medium text-text-secondary">
            {d.gap > 0 ? `Gap ${formatGap(d.gap)} pts` : 'Same threshold'}
          </span>
          {d.middle_profit_margin !== undefined && d.middle_profit_margin !== null && d.gap > 0 && (
            <span className={`font-mono font-medium ${profitColor(d.middle_profit_margin)}`}>
              Middle {formatPercentage(d.middle_profit_margin)}
            </span>
          )}
          {!showFlatMeta && <span className="text-text-muted">Detected {formatRelativeTime(d.detected_at)}</span>}
        </div>
        <Link
          to={{ pathname: `/matches/${d.match_id}`, search: location.search }}
          className="font-medium text-text-secondary transition hover:text-accent"
        >
          View match →
        </Link>
      </div>
    </div>
  );
}
