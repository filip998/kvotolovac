import { Link, useLocation } from 'react-router-dom';
import type { Discrepancy } from '../api/types';
import { formatOdds, formatGap, formatThreshold, formatPercentage, formatRelativeTime, profitColor, profitBgColor } from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import BookmakerBadge from './BookmakerBadge';

interface DiscrepancyCardProps {
  discrepancy: Discrepancy;
}

export default function DiscrepancyCard({ discrepancy: d }: DiscrepancyCardProps) {
  const location = useLocation();
  const marketLabel = MARKET_TYPE_LABELS[d.market_type] || d.market_type;
  const label = d.player_name ? `${d.player_name} — ${marketLabel}` : marketLabel;

  return (
    <div className={`rounded-lg border p-4 transition hover:border-border-hover ${profitBgColor(d.profit_margin)}`}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className="text-[11px] font-medium uppercase tracking-wider text-text-muted">
            {marketLabel}
          </span>
          <h4 className="mt-1 text-sm font-semibold text-text">{label}</h4>
        </div>
        <div className={`font-mono text-lg font-bold ${profitColor(d.profit_margin)}`}>
          {formatPercentage(d.profit_margin)}
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3">
        <div className="rounded-md bg-surface-raised p-3">
          <div className="mb-2">
            <BookmakerBadge name={d.bookmaker_a_name} />
          </div>
          <span className="text-[11px] text-text-muted">Over</span>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="font-mono text-lg font-semibold text-text">{formatThreshold(d.threshold_a)}</span>
            <span className="font-mono text-base font-semibold text-text">{formatOdds(d.odds_a)}</span>
          </div>
        </div>
        <div className="rounded-md bg-surface-raised p-3">
          <div className="mb-2">
            <BookmakerBadge name={d.bookmaker_b_name} />
          </div>
          <span className="text-[11px] text-text-muted">Under</span>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="font-mono text-lg font-semibold text-text">{formatThreshold(d.threshold_b)}</span>
            <span className="font-mono text-base font-semibold text-text">{formatOdds(d.odds_b)}</span>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 text-xs">
        <div className="flex items-center gap-3">
          <span className="font-mono font-medium text-text-secondary">
            Gap {formatGap(d.gap)} pts
          </span>
          {d.middle_profit_margin !== undefined && d.middle_profit_margin !== null && d.gap > 0 && (
            <span className={`font-mono font-medium ${profitColor(d.middle_profit_margin)}`}>
              Middle {formatPercentage(d.middle_profit_margin)}
            </span>
          )}
          <span className="text-text-muted">Detected {formatRelativeTime(d.detected_at)}</span>
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
