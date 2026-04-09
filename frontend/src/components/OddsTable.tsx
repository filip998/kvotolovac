import type { OddsOffer, Discrepancy } from '../api/types';
import { formatOdds, formatThreshold } from '../utils/format';

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
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 overflow-hidden">
      <div className="border-b border-gray-800 px-4 py-3">
        <h4 className="text-sm font-semibold text-white">{title}</h4>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-left text-xs text-gray-500">
              <th className="px-4 py-2.5 font-medium">Bookmaker</th>
              <th className="px-4 py-2.5 font-medium text-right">Threshold</th>
              <th className="px-4 py-2.5 font-medium text-right">Over</th>
              <th className="px-4 py-2.5 font-medium text-right">Under</th>
            </tr>
          </thead>
          <tbody>
            {offers.map((offer) => {
              const highlighted = isHighlighted(offer.bookmaker_id, offer.threshold);
              return (
                <tr
                  key={offer.id}
                  className={`border-b border-gray-800/50 transition ${
                    highlighted
                      ? 'bg-brand-500/5'
                      : 'hover:bg-gray-800/30'
                  }`}
                >
                  <td className="px-4 py-2.5">
                    <span className={`font-medium ${highlighted ? 'text-brand-400' : 'text-gray-300'}`}>
                      {offer.bookmaker_name}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-gray-300">
                    {formatThreshold(offer.threshold)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono font-semibold text-white">
                    {formatOdds(offer.over_odds)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono font-semibold text-white">
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
