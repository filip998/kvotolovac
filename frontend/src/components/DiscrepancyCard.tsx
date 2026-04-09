import { Link } from 'react-router-dom';
import type { Discrepancy } from '../api/types';
import { formatOdds, formatGap, formatThreshold, formatPercentage, formatRelativeTime, profitColor, profitBgColor } from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';

interface DiscrepancyCardProps {
  discrepancy: Discrepancy;
}

export default function DiscrepancyCard({ discrepancy: d }: DiscrepancyCardProps) {
  const marketLabel = MARKET_TYPE_LABELS[d.market_type] || d.market_type;
  const label = d.player_name ? `${d.player_name} — ${marketLabel}` : marketLabel;

  return (
    <div className={`rounded-xl border p-4 transition hover:border-gray-600 ${profitBgColor(d.profit_margin)}`}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <span className="mb-1 inline-block rounded-md bg-gray-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
            {marketLabel}
          </span>
          <h4 className="text-sm font-semibold text-white">{label}</h4>
        </div>
        <div className="text-right">
          <div className={`font-mono text-lg font-bold ${profitColor(d.profit_margin)}`}>
            {formatPercentage(d.profit_margin)}
          </div>
          <div className="text-[10px] text-gray-500">profit margin</div>
        </div>
      </div>

      {/* Bookmaker comparison */}
      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div className="rounded-lg bg-gray-900/60 p-3">
          <div className="mb-1 text-xs font-medium text-brand-400">{d.bookmaker_a_name}</div>
          <div className="flex items-baseline gap-2">
            <span className="text-xs text-gray-500">
              {d.market_type === 'player_points' ? 'Over' : 'Over'} {formatThreshold(d.threshold_a)}
            </span>
            <span className="font-mono text-lg font-semibold text-white">@ {formatOdds(d.odds_a)}</span>
          </div>
        </div>
        <div className="rounded-lg bg-gray-900/60 p-3">
          <div className="mb-1 text-xs font-medium text-amber-400">{d.bookmaker_b_name}</div>
          <div className="flex items-baseline gap-2">
            <span className="text-xs text-gray-500">Under {formatThreshold(d.threshold_b)}</span>
            <span className="font-mono text-lg font-semibold text-white">@ {formatOdds(d.odds_b)}</span>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500">
        <div className="flex items-center gap-3">
          <span>
            Gap: <span className="font-mono font-medium text-gray-300">{formatGap(d.gap)} pts</span>
          </span>
          <span>Detected {formatRelativeTime(d.detected_at)}</span>
        </div>
        <Link
          to={`/matches/${d.match_id}`}
          className="font-medium text-brand-400 transition hover:text-brand-300"
        >
          View Match →
        </Link>
      </div>
    </div>
  );
}
