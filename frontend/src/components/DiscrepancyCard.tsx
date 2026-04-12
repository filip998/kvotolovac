import { Link } from 'react-router-dom';
import type { Discrepancy } from '../api/types';
import { formatOdds, formatGap, formatThreshold, formatPercentage, formatRelativeTime, profitColor, profitBgColor } from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import BookmakerBadge from './BookmakerBadge';

interface DiscrepancyCardProps {
  discrepancy: Discrepancy;
}

export default function DiscrepancyCard({ discrepancy: d }: DiscrepancyCardProps) {
  const marketLabel = MARKET_TYPE_LABELS[d.market_type] || d.market_type;
  const label = d.player_name ? `${d.player_name} — ${marketLabel}` : marketLabel;

  return (
    <div
      className={`rounded-xl border p-4 transition hover:border-line-500 ${profitBgColor(d.profit_margin)}`}
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className="mb-2 inline-flex rounded-full border border-line-700/75 bg-ink-950 px-2.5 py-1 text-xs font-medium text-slate-400">
            {marketLabel}
          </span>
          <h4 className="text-base font-semibold text-white">{label}</h4>
        </div>
        <div className="text-right">
          <div
            className={`inline-flex items-center rounded-full px-3 py-1.5 font-mono text-sm font-semibold ${profitColor(d.profit_margin)} ${profitBgColor(d.profit_margin)}`}
          >
            {formatPercentage(d.profit_margin)}
          </div>
          <div className="mt-1 text-[10px] text-slate-500">
            edge ROI
          </div>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-line-700/70 bg-ink-950/60 p-4">
          <div className="mb-4">
            <BookmakerBadge name={d.bookmaker_a_name} />
          </div>
          <div className="text-xs text-slate-500">Over</div>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="text-xl font-semibold text-white">{formatThreshold(d.threshold_a)}</span>
            <span className="font-mono text-lg font-semibold text-white">{formatOdds(d.odds_a)}</span>
          </div>
        </div>
        <div className="rounded-lg border border-line-700/70 bg-ink-950/60 p-4">
          <div className="mb-4">
            <BookmakerBadge name={d.bookmaker_b_name} />
          </div>
          <div className="text-xs text-slate-500">Under</div>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="text-xl font-semibold text-white">{formatThreshold(d.threshold_b)}</span>
            <span className="font-mono text-lg font-semibold text-white">{formatOdds(d.odds_b)}</span>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-700/60 pt-4 text-xs text-slate-400">
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-full border border-line-600 bg-white/[0.03] px-3 py-1 font-medium text-slate-200">
            Gap {formatGap(d.gap)} pts
          </span>
          {d.middle_profit_margin !== undefined && d.middle_profit_margin !== null && d.gap > 0 && (
            <span className={`rounded-full border px-3 py-1 font-medium ${profitColor(d.middle_profit_margin)} ${profitBgColor(d.middle_profit_margin)}`}>
              Middle ROI {formatPercentage(d.middle_profit_margin)}
            </span>
          )}
          <span className="text-slate-500">Detected {formatRelativeTime(d.detected_at)}</span>
        </div>
        <Link
          to={`/matches/${d.match_id}`}
          className="font-medium text-slate-200 transition hover:text-white"
        >
          View match →
        </Link>
      </div>
    </div>
  );
}
