import type { AxisBookmakerRow, AxisRow, MarketCard } from '../utils/oddsAxes';
import { formatRelativeTime, formatRoi, roiColor, roiFromImpliedPct } from '../utils/format';

interface EventWireProps {
  cards: MarketCard[];
  lastUpdated: string | null;
}

interface BestBet {
  cardTitle: string;
  marketLabel: string;
  axisLabel: string;
  bookA: string;
  bookB: string;
  impliedPct: number;
}

interface CrossBookPair {
  backBmId: string;
  backBmName: string;
  backOdds: number;
  layBmId: string;
  layBmName: string;
  layOdds: number;
  impliedPct: number;
}

/**
 * Find the strongest pair across an axis's bookmaker rows where the back
 * leg and the lay leg are at *different* bookmakers — i.e. a real
 * tradable arb candidate. Falls back to null when no cross-book pair
 * exists.
 */
function findBestCrossBookPair(rows: AxisBookmakerRow[]): CrossBookPair | null {
  let best: CrossBookPair | null = null;
  for (const a of rows) {
    if (a.backOdds == null || a.backOdds <= 0) continue;
    for (const b of rows) {
      if (b.layOdds == null || b.layOdds <= 0) continue;
      if (a.bookmakerId === b.bookmakerId) continue;
      const impliedPct = (1 / a.backOdds + 1 / b.layOdds) * 100;
      if (best === null || impliedPct < best.impliedPct) {
        best = {
          backBmId: a.bookmakerId,
          backBmName: a.bookmakerName,
          backOdds: a.backOdds,
          layBmId: b.bookmakerId,
          layBmName: b.bookmakerName,
          layOdds: b.layOdds,
          impliedPct,
        };
      }
    }
  }
  return best;
}

function pickBestPairFromAxis(axis: AxisRow): CrossBookPair | null {
  const cross = findBestCrossBookPair(axis.bookmakerRows);
  return cross;
}

function findStrongestAcrossCards(cards: MarketCard[]): BestBet[] {
  const candidates: BestBet[] = [];
  for (const card of cards) {
    let bestForCard: { pair: CrossBookPair; axis: AxisRow } | null = null;
    for (const axis of card.axes) {
      const pair = pickBestPairFromAxis(axis);
      if (!pair) continue;
      if (!bestForCard || pair.impliedPct < bestForCard.pair.impliedPct) {
        bestForCard = { pair, axis };
      }
    }
    if (!bestForCard) continue;
    const { pair, axis } = bestForCard;

    // Pick a market label that reads cleanly in a small table cell.
    let marketLabel = card.title;
    let axisLabel = axis.lineTag;
    if (card.category === 'match') {
      axisLabel = `${axis.backLabel}↔${axis.layLabel}`;
    } else if (card.category === 'totals' || card.category === 'handicap') {
      marketLabel = `${card.title} ${axis.lineTag}`;
      axisLabel = `${axis.backLabel}↔${axis.layLabel}`;
    }

    candidates.push({
      cardTitle: card.title,
      marketLabel,
      axisLabel,
      bookA: pair.backBmName,
      bookB: pair.layBmName,
      impliedPct: pair.impliedPct,
    });
  }
  candidates.sort((a, b) => a.impliedPct - b.impliedPct);
  return candidates;
}

const RANK_LABELS = ['Top combo', 'Next', '3rd'] as const;

/**
 * Compact summary table at the top of the markets area: top 2-3 combos
 * across all visible cards, ranked by ROI. Uses the page's system-font
 * scope (set by the parent), so it's noticeably easier to read than the
 * mono-uppercase ticker we had before.
 */
export default function EventWire({ cards, lastUpdated }: EventWireProps) {
  const ranked = findStrongestAcrossCards(cards);
  if (ranked.length === 0) return null;
  const visible = ranked.slice(0, 3);

  return (
    <div
      role="status"
      aria-label="Top arbitrage combos for this event"
      className="border-b border-border px-4 py-3"
    >
      <div className="mb-2 flex items-baseline gap-3 text-[11px] uppercase tracking-[0.1em] text-text-muted">
        <span>Best combos</span>
        <span aria-hidden className="h-0 flex-1 self-center border-b border-border" />
        <span>
          Updated{' '}
          <span className="text-text">
            {lastUpdated ? formatRelativeTime(lastUpdated) : 'never'}
          </span>
        </span>
      </div>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="text-[10px] font-medium uppercase tracking-[0.08em] text-text-muted">
            <th className="pb-1.5 pr-3 text-left font-medium">Rank</th>
            <th className="pb-1.5 pr-3 text-left font-medium">Market</th>
            <th className="pb-1.5 pr-3 text-left font-medium">Axis</th>
            <th className="pb-1.5 pr-3 text-left font-medium">Books</th>
            <th className="pb-1.5 text-right font-medium">ROI</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((row, idx) => {
            const roi = roiFromImpliedPct(row.impliedPct);
            return (
              <tr
                key={`${row.cardTitle}-${idx}`}
                className="border-t border-dotted border-border"
              >
                <td className="py-1.5 pr-3 text-text-secondary">{RANK_LABELS[idx] ?? `#${idx + 1}`}</td>
                <td className="py-1.5 pr-3 font-medium text-text">{row.marketLabel}</td>
                <td className="py-1.5 pr-3 text-text-secondary" style={{ fontFamily: 'var(--font-mono)' }}>
                  {row.axisLabel}
                </td>
                <td className="py-1.5 pr-3 text-text-secondary">
                  {row.bookA === row.bookB ? row.bookA : `${row.bookA} / ${row.bookB}`}
                </td>
                <td
                  className={`py-1.5 text-right font-semibold ${roiColor(roi)}`}
                  style={{ fontFamily: 'var(--font-mono)' }}
                  title={`Implied ${row.impliedPct.toFixed(1)}%`}
                >
                  {formatRoi(roi)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
