import type { OddsOffer } from '../api/types';
import { formatHandicapLine, formatOdds, formatThreshold, isHandicapMarket } from '../utils/format';
import BookmakerBadge from './BookmakerBadge';

interface OddsTableProps {
  offers: OddsOffer[];
  title: string;
}

export default function OddsTable({ offers, title }: OddsTableProps) {
  if (offers.length === 0) return null;

  // Handicap rows have a different mental model: the "threshold" is the
  // home team's expected margin (signed) and the over/under odds map to
  // "home covers" / "away covers" rather than total points over/under.
  const handicapMode = offers.every((o) => isHandicapMarket(o.market_type));
  const lineHeader = handicapMode ? 'Home line' : 'Threshold';
  const overHeader = handicapMode ? 'Home' : 'Over';
  const underHeader = handicapMode ? 'Away' : 'Under';

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
              <th className="px-4 py-2.5 text-right">{lineHeader}</th>
              <th className="px-4 py-2.5 text-right">{overHeader}</th>
              <th className="px-4 py-2.5 text-right">{underHeader}</th>
            </tr>
          </thead>
          <tbody>
            {offers.map((offer) => {
              const lineDisplay = isHandicapMarket(offer.market_type)
                ? formatHandicapLine(offer.threshold, 'home')
                : formatThreshold(offer.threshold);
              return (
                <tr
                  key={offer.id}
                  className="border-t border-border transition hover:bg-surface-raised"
                >
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <BookmakerBadge
                          name={offer.bookmaker_name}
                          compact
                          href={offer.source_url}
                          ariaLabel={`Open ${offer.bookmaker_name} match page`}
                        />
                     </div>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                    {lineDisplay}
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
