import { Link, useLocation } from 'react-router-dom';
import type { Discrepancy } from '../api/types';
import {
  formatGap,
  formatOdds,
  formatPercentage,
  formatRelativeTime,
  formatThreshold,
  profitBgColor,
  profitColor,
} from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import BookmakerBadge from './BookmakerBadge';
import StakeCalculatorPanel from './StakeCalculatorPanel';

interface DiscrepancyCardProps {
  discrepancy: Discrepancy;
  totalUnits: number;
  context?: 'flat' | 'grouped';
  rank?: number;
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
  const showFlatMeta = context === 'flat';

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

      <StakeCalculatorPanel discrepancy={d} totalUnits={totalUnits} />

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
