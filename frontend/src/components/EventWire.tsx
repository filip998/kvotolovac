import type { MarketCard } from '../utils/oddsAxes';
import { formatImpliedPct, formatRelativeTime } from '../utils/format';

interface EventWireProps {
  cards: MarketCard[];
  lastUpdated: string | null;
}

interface BestBet {
  cardTitle: string;
  axisLabel: string;
  bookA: string;
  bookB: string;
  pct: number;
}

function findStrongestAcrossCards(cards: MarketCard[]): BestBet[] {
  const candidates: BestBet[] = [];
  for (const card of cards) {
    let best: { pct: number; axisIdx: number } | null = null;
    for (let i = 0; i < card.axes.length; i += 1) {
      const axis = card.axes[i];
      if (axis.bestPairImpliedPct == null) continue;
      if (!best || axis.bestPairImpliedPct < best.pct) {
        best = { pct: axis.bestPairImpliedPct, axisIdx: i };
      }
    }
    if (!best) continue;
    const axis = card.axes[best.axisIdx];
    if (!axis.bestBack || !axis.bestLay) continue;
    const axisLabel =
      card.category === 'match'
        ? `${axis.lineTag} ↔ ${axis.layLabel}`
        : `${card.title.split('·')[0].trim()} ${axis.lineTag}`;
    candidates.push({
      cardTitle: card.title,
      axisLabel,
      bookA: axis.bestBack.bookmakerName,
      bookB: axis.bestLay.bookmakerName,
      pct: best.pct,
    });
  }
  candidates.sort((a, b) => a.pct - b.pct);
  return candidates;
}

/**
 * Single-line ticker at the top of the markets area: top arb + best total +
 * last-updated. Trading-terminal-style mono small-caps.
 */
export default function EventWire({ cards, lastUpdated }: EventWireProps) {
  const ranked = findStrongestAcrossCards(cards);
  if (ranked.length === 0) return null;
  const topArb = ranked[0];

  return (
    <div
      role="status"
      className="flex flex-wrap items-baseline gap-x-7 gap-y-1.5 overflow-hidden whitespace-nowrap border-b border-border px-4 py-3 font-mono text-[11px] uppercase tracking-[0.16em] text-text-muted"
    >
      <span>Top combo</span>
      <span className={topArb.pct < 100 ? 'font-bold text-accent' : 'font-bold text-warning'}>
        {topArb.axisLabel} · {topArb.bookA}/{topArb.bookB} · {formatImpliedPct(topArb.pct)}
      </span>
      {ranked[1] && (
        <>
          <span className="text-text-muted">│</span>
          <span>Next</span>
          <span className={ranked[1].pct < 100 ? 'font-bold text-accent' : 'font-bold text-warning'}>
            {ranked[1].axisLabel} · {ranked[1].bookA}/{ranked[1].bookB} · {formatImpliedPct(ranked[1].pct)}
          </span>
        </>
      )}
      <span className="text-text-muted">│</span>
      <span>Updated</span>
      <span className="text-text">{lastUpdated ? formatRelativeTime(lastUpdated).toUpperCase() : 'NEVER'}</span>
    </div>
  );
}
