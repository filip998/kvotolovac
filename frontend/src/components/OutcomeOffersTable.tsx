import type { OutcomeOffer } from '../api/types';
import { formatOdds } from '../utils/format';
import BookmakerBadge from './BookmakerBadge';

const OUTCOME_LABELS: Record<string, string> = {
  home: 'Home',
  draw: 'Draw',
  away: 'Away',
  home_or_draw: 'Home / Draw',
  home_or_away: 'Home / Away',
  draw_or_away: 'Draw / Away',
  over: 'Over',
  under: 'Under',
};

interface OutcomeOffersTableProps {
  offers: OutcomeOffer[];
  title: string;
}

function formatLine(line: number | null): string {
  return line === null ? '—' : line.toFixed(1);
}

function formatOutcome(offer: OutcomeOffer): string {
  return offer.raw_label || OUTCOME_LABELS[offer.outcome_code] || offer.outcome_code;
}

export default function OutcomeOffersTable({ offers, title }: OutcomeOffersTableProps) {
  if (offers.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between px-4 py-3">
        <h4 className="text-sm font-semibold text-text">{title}</h4>
        <span className="font-mono text-xs text-text-muted">{offers.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-t border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
              <th className="px-4 py-2.5 text-left">Bookmaker</th>
              <th className="px-4 py-2.5 text-left">Outcome</th>
              <th className="px-4 py-2.5 text-right">Line</th>
              <th className="px-4 py-2.5 text-right">Odds</th>
            </tr>
          </thead>
          <tbody>
            {offers.map((offer) => (
              <tr
                key={offer.id}
                className="border-t border-border transition hover:bg-surface-raised"
              >
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <BookmakerBadge
                      name={offer.bookmaker_name || offer.bookmaker_id}
                      compact
                      href={offer.source_url}
                      ariaLabel={`Open ${offer.bookmaker_name || offer.bookmaker_id} match page`}
                    />
                  </div>
                </td>
                <td className="px-4 py-2.5 text-text-secondary">{formatOutcome(offer)}</td>
                <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                  {formatLine(offer.line)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono font-semibold text-text">
                  {formatOdds(offer.odds)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
