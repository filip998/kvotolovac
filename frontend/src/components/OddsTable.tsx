import type { OddsOffer, Discrepancy } from '../api/types';
import { formatOdds, formatThreshold } from '../utils/format';
import BookmakerBadge from './BookmakerBadge';

interface OddsTableProps {
  offers: OddsOffer[];
  discrepancies?: Discrepancy[];
  title: string;
}

export default function OddsTable({ offers, discrepancies = [], title }: OddsTableProps) {
  if (offers.length === 0) return null;

  // Build a set of bookmaker+threshold combos that are part of a discrepancy
  const discrepancyKeys = new Set<string>();
  for (const d of discrepancies) {
    discrepancyKeys.add(`${d.bookmaker_a_id}-${d.threshold_a}`);
    discrepancyKeys.add(`${d.bookmaker_b_id}-${d.threshold_b}`);
  }

  const isHighlighted = (bookId: string, threshold: number) =>
    discrepancyKeys.has(`${bookId}-${threshold}`);

  return (
    <div className="overflow-hidden rounded-xl border border-line-700/70 bg-ink-900">
      <div className="border-b border-line-700/70 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-base font-semibold text-white">{title}</h4>
          <span className="rounded-full border border-line-700/70 bg-ink-950 px-3 py-1 text-xs font-medium text-slate-400">
            {offers.length} offers
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line-700/70 text-left text-xs font-medium text-slate-500">
              <th className="px-4 py-3">Bookmaker</th>
              <th className="px-4 py-3 text-right">Threshold</th>
              <th className="px-4 py-3 text-right">Over</th>
              <th className="px-4 py-3 text-right">Under</th>
            </tr>
          </thead>
          <tbody>
            {offers.map((offer) => {
              const highlighted = isHighlighted(offer.bookmaker_id, offer.threshold);
              return (
                <tr
                  key={offer.id}
                  className={`border-b border-line-700/50 transition ${
                    highlighted
                      ? 'bg-white/[0.04]'
                      : 'hover:bg-white/[0.03]'
                  }`}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <BookmakerBadge name={offer.bookmaker_name} compact />
                      {highlighted && (
                        <span className="rounded-full border border-line-600 bg-white/[0.04] px-2 py-1 text-[10px] font-medium text-slate-200">
                          Opportunity
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-slate-300">
                    {formatThreshold(offer.threshold)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono font-semibold text-white">
                    {formatOdds(offer.over_odds)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono font-semibold text-white">
                    {formatOdds(offer.under_odds)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
