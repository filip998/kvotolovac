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

  const discrepancyKeys = new Set<string>();
  for (const d of discrepancies) {
    discrepancyKeys.add(`${d.bookmaker_a_id}-${d.threshold_a}`);
    discrepancyKeys.add(`${d.bookmaker_b_id}-${d.threshold_b}`);
  }

  const isHighlighted = (bookId: string, threshold: number) =>
    discrepancyKeys.has(`${bookId}-${threshold}`);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between px-4 py-3">
        <h4 className="text-sm font-semibold text-text">{title}</h4>
        <span className="font-mono text-xs text-text-muted">
          {offers.length}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-t border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
              <th className="px-4 py-2.5 text-left">Bookmaker</th>
              <th className="px-4 py-2.5 text-right">Threshold</th>
              <th className="px-4 py-2.5 text-right">Over</th>
              <th className="px-4 py-2.5 text-right">Under</th>
            </tr>
          </thead>
          <tbody>
            {offers.map((offer) => {
              const highlighted = isHighlighted(offer.bookmaker_id, offer.threshold);
              return (
                <tr
                  key={offer.id}
                  className={`border-t border-border transition ${
                    highlighted
                      ? 'bg-accent/[0.06]'
                      : 'hover:bg-surface-raised'
                  }`}
                >
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <BookmakerBadge name={offer.bookmaker_name} compact />
                      {highlighted && (
                        <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
                          OPP
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                    {formatThreshold(offer.threshold)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono font-semibold text-text">
                    {formatOdds(offer.over_odds)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono font-semibold text-text">
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
