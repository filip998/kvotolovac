import { useMemo, useState } from 'react';
import type { AxisRow, MarketCard } from '../utils/oddsAxes';
import { formatImpliedPct, formatOdds, impliedPctColor, impliedPctTier } from '../utils/format';
import BookmakerBadge from './BookmakerBadge';

interface OddsLadderCardProps {
  card: MarketCard;
  /** Bankroll value in units (controlled by the parent so it persists across cards). */
  bankrollUnits: number;
  /** Called when the user edits the bankroll input. */
  onBankrollChange: (units: number) => void;
  /** Optional anchor id override (for jump-bar). */
  anchorId?: string;
}

function pickStrongestAxisIndex(axes: AxisRow[]): number {
  let bestIdx = 0;
  let bestPct = Number.POSITIVE_INFINITY;
  for (let i = 0; i < axes.length; i += 1) {
    const pct = axes[i].bestPairImpliedPct ?? Number.POSITIVE_INFINITY;
    if (pct < bestPct) {
      bestPct = pct;
      bestIdx = i;
    }
  }
  return bestIdx;
}

function StatusPill({ pct, count }: { pct: number | null | undefined; count: number }) {
  const tier = impliedPctTier(pct);
  if (count > 0 && tier === 'arb') {
    return (
      <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-accent">
        {count} ARB{count > 1 ? 'S' : ''}
      </span>
    );
  }
  if (tier === 'knife-edge') {
    return <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-accent/70">KNIFE-EDGE</span>;
  }
  if (tier === 'margin') {
    return <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-warning">MARGIN</span>;
  }
  if (tier === 'high-margin') {
    return <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-danger">HIGH MARGIN</span>;
  }
  return null;
}

function calcStakeSplit(backOdds: number, layOdds: number, bankroll: number) {
  if (backOdds <= 0 || layOdds <= 0 || bankroll <= 0) {
    return { stakeBack: 0, stakeLay: 0, profit: 0, roi: 0, returnAmount: 0 };
  }
  const totalOdds = backOdds + layOdds;
  const stakeBack = bankroll * (layOdds / totalOdds);
  const stakeLay = bankroll - stakeBack;
  // Both legs return ~ same payout under balanced split.
  const returnIfBack = stakeBack * backOdds;
  const returnIfLay = stakeLay * layOdds;
  const minReturn = Math.min(returnIfBack, returnIfLay);
  const profit = minReturn - bankroll;
  const roi = bankroll > 0 ? (profit / bankroll) * 100 : 0;
  return {
    stakeBack: Number(stakeBack.toFixed(2)),
    stakeLay: Number(stakeLay.toFixed(2)),
    profit: Number(profit.toFixed(2)),
    roi: Number(roi.toFixed(2)),
    returnAmount: Number(minReturn.toFixed(2)),
  };
}

export default function OddsLadderCard({ card, bankrollUnits, onBankrollChange, anchorId }: OddsLadderCardProps) {
  const [selectedAxisKey, setSelectedAxisKey] = useState<string | null>(null);

  // Derive the effective selection at render time so it survives data refreshes
  // and so the same value drives both the rendered selected axis and `aria-pressed`.
  // (React 19's eslint rule forbids setState inside useEffect.)
  const strongestIdx = useMemo(() => pickStrongestAxisIndex(card.axes), [card.axes]);
  const explicitIdx =
    selectedAxisKey !== null ? card.axes.findIndex((a) => a.axisKey === selectedAxisKey) : -1;
  const effectiveIdx = explicitIdx >= 0 ? explicitIdx : strongestIdx;
  const selectedAxis = card.axes[effectiveIdx] ?? card.axes[0];
  const effectiveAxisKey = selectedAxis?.axisKey ?? null;

  const stakeSplit = useMemo(() => {
    if (!selectedAxis?.bestBack || !selectedAxis?.bestLay) return null;
    return calcStakeSplit(selectedAxis.bestBack.odds, selectedAxis.bestLay.odds, bankrollUnits);
  }, [selectedAxis, bankrollUnits]);

  const arbCountPill = card.arbCount > 0 ? (
    <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-accent">
      {card.arbCount} ARB{card.arbCount > 1 ? 'S' : ''}
    </span>
  ) : null;

  const sectionId = anchorId ?? card.cardKey;

  return (
    <article
      id={sectionId}
      className="border-b border-border px-4 pb-6 pt-4 last:border-b-0"
      style={{ fontFeatureSettings: '"tnum" 1' }}
    >
      {/* Card head — no box, just typography */}
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2 px-1 pb-3">
        <h3 className="m-0 text-[17px] font-medium tracking-[-0.005em] text-text">{card.title}</h3>
        <span className="font-mono text-[10.5px] uppercase tracking-[0.16em] text-text-muted">
          {card.pairEyebrow}
        </span>
        <span className="ml-auto flex items-baseline gap-4 font-mono text-[10.5px] uppercase tracking-[0.1em] text-text-muted">
          {arbCountPill}
          <span>{card.bookmakerCount} BOOKS</span>
          {card.axes.length > 1 && (
            <span>
              {card.axes.length} {card.category === 'match' ? 'AXES' : 'LINES'}
            </span>
          )}
        </span>
      </header>

      {/* 3-column body separated by vertical rules */}
      <div className="grid grid-cols-1 border-b border-t border-border md:grid-cols-[minmax(280px,1.1fr)_minmax(360px,1.7fr)] xl:grid-cols-[minmax(300px,1.1fr)_minmax(400px,2fr)_minmax(260px,1fr)]">
        {/* Ladder */}
        <div className="min-w-0 border-r-0 border-border px-5 py-4 xl:border-r">
          <div className="mb-3 flex items-baseline gap-2 border-b border-border pb-2 font-mono text-[9.5px] uppercase tracking-[0.18em] text-text-muted">
            <span>Axis</span>
            <strong className="font-medium normal-case tracking-[0.04em] text-text">
              {card.category === 'match' ? 'back outcome → complement' : 'line · back ↔ lay'}
            </strong>
          </div>
          <div className="flex flex-col font-mono">
            {card.axes.map((axis) => {
              const isSelected = axis.axisKey === effectiveAxisKey;
              const tier = impliedPctTier(axis.bestPairImpliedPct);
              const pctClass = impliedPctColor(axis.bestPairImpliedPct);
              return (
                <button
                  key={axis.axisKey}
                  type="button"
                  onClick={() => setSelectedAxisKey(axis.axisKey)}
                  aria-pressed={isSelected}
                  className={`relative grid w-full grid-cols-[auto_1fr_1fr_auto] items-baseline gap-3 border-t border-dotted border-border bg-transparent py-2 pl-3.5 pr-1 text-left text-[12px] transition-colors first:border-t-0 ${
                    isSelected ? 'text-text' : 'text-text-secondary hover:text-text'
                  }`}
                  style={{ fontFamily: 'inherit' }}
                >
                  <span
                    aria-hidden
                    className="pointer-events-none absolute left-0 top-2 bottom-2 w-0.5 transition-colors"
                    style={{
                      background: isSelected
                        ? 'var(--color-accent)'
                        : tier === 'arb'
                          ? 'transparent'
                          : 'transparent',
                    }}
                  />
                  <span className="min-w-[42px] text-[12px] font-bold text-text">{axis.lineTag}</span>
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate text-[10.5px] uppercase tracking-[0.04em] text-text-secondary">
                      {axis.bestBack ? axis.bestBack.bookmakerName : '—'}
                    </span>
                    <span className="ml-auto font-bold text-text">
                      {axis.bestBack ? formatOdds(axis.bestBack.odds) : '—'}
                    </span>
                  </span>
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate text-[10.5px] uppercase tracking-[0.04em] text-text-secondary">
                      {axis.bestLay ? `${axis.bestLay.bookmakerName} · ${axis.layLabel}` : '—'}
                    </span>
                    <span className="ml-auto font-bold text-text">
                      {axis.bestLay ? formatOdds(axis.bestLay.odds) : '—'}
                    </span>
                  </span>
                  <span className={`text-right text-[12px] font-bold ${pctClass}`}>
                    {formatImpliedPct(axis.bestPairImpliedPct)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Pivot */}
        <div className="min-w-0 border-t border-border bg-surface px-5 py-4 md:border-l xl:border-l xl:border-r xl:border-t-0">
          <div className="mb-3 flex items-baseline gap-2 pb-2 font-mono text-[9.5px] uppercase tracking-[0.18em] text-text-muted">
            <span>All books · selected</span>
            {selectedAxis && (
              <strong className="font-medium normal-case tracking-[0.04em] text-text">
                {selectedAxis.lineTag}
                {card.category !== 'match' ? '' : ` · ${selectedAxis.backLabel}↔${selectedAxis.layLabel}`}
              </strong>
            )}
          </div>
          <div className="overflow-x-auto font-mono">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr className="border-b border-border text-[9.5px] font-medium uppercase tracking-[0.14em] text-text-muted">
                  <th className="pb-2 pr-3 text-left">Bookmaker</th>
                  <th className="pb-2 pr-3 text-right">{selectedAxis?.backColumnLabel ?? 'Back'}</th>
                  <th className="pb-2 pr-3 text-right">{selectedAxis?.layColumnLabel ?? 'Lay'}</th>
                  <th className="pb-2 text-right">Implied %</th>
                </tr>
              </thead>
              <tbody>
                {selectedAxis?.bookmakerRows.map((row) => {
                  const pctClass = impliedPctColor(row.impliedPct);
                  return (
                    <tr key={row.bookmakerId} className="border-b border-dotted border-border last:border-0">
                      <td className="py-2 pr-3 text-left">
                        <BookmakerBadge name={row.bookmakerName} compact href={row.sourceUrl ?? undefined} />
                      </td>
                      <td
                        className={`py-2 pr-3 text-right font-bold ${
                          row.isBestBack ? 'text-accent' : 'text-text'
                        }`}
                      >
                        {row.isBestBack && row.backOdds != null ? '◆ ' : ''}
                        {row.backOdds != null ? formatOdds(row.backOdds) : '—'}
                      </td>
                      <td
                        className={`py-2 pr-3 text-right font-bold ${
                          row.isBestLay ? 'text-accent' : 'text-text'
                        }`}
                      >
                        {row.isBestLay && row.layOdds != null ? '◆ ' : ''}
                        {row.layOdds != null ? formatOdds(row.layOdds) : '—'}
                      </td>
                      <td className={`py-2 text-right font-bold ${pctClass}`}>
                        {formatImpliedPct(row.impliedPct)}
                      </td>
                    </tr>
                  );
                })}
                {(!selectedAxis || selectedAxis.bookmakerRows.length === 0) && (
                  <tr>
                    <td colSpan={4} className="py-3 text-center text-[11px] text-text-muted">
                      No bookmaker data for this axis
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Stake calc */}
        <div className="min-w-0 border-t border-border bg-bg px-5 py-4 xl:border-l xl:border-t-0">
          <div className="mb-3 pb-2 font-mono text-[9.5px] uppercase tracking-[0.18em] text-text-muted">
            <span>Stake split · </span>
            <strong className="font-medium normal-case tracking-[0.04em] text-text">best combo</strong>
          </div>
          {selectedAxis?.bestBack && selectedAxis?.bestLay ? (
            <>
              <div className="space-y-1.5 border-b border-border pb-3 font-mono text-[11px] leading-snug text-text-secondary">
                <div className="flex items-baseline gap-1.5">
                  <span>►</span>
                  <strong className="font-bold text-text">{selectedAxis.bestBack.bookmakerName}</strong>
                  <span>· {selectedAxis.backLabel} · {formatOdds(selectedAxis.bestBack.odds)}</span>
                </div>
                <div className="flex items-baseline gap-1.5">
                  <span>+</span>
                  <strong className="font-bold text-text">{selectedAxis.bestLay.bookmakerName}</strong>
                  <span>· {selectedAxis.layLabel} · {formatOdds(selectedAxis.bestLay.odds)}</span>
                </div>
                <div
                  className={`mt-2 font-bold tracking-wider ${impliedPctColor(selectedAxis.bestPairImpliedPct)}`}
                >
                  → {formatImpliedPct(selectedAxis.bestPairImpliedPct)}
                  <StatusPillSeparator pct={selectedAxis.bestPairImpliedPct} />
                </div>
              </div>

              <div className="mb-3 flex items-baseline gap-2 border-b border-border pb-3">
                <input
                  type="number"
                  min={0}
                  step={1}
                  value={Number.isFinite(bankrollUnits) ? bankrollUnits : 0}
                  onChange={(e) => {
                    const next = Number(e.target.value);
                    if (Number.isFinite(next) && next >= 0) onBankrollChange(next);
                  }}
                  aria-label="Bankroll in units"
                  className="w-[90px] border-0 border-b border-text bg-transparent pb-1 text-right font-mono text-[22px] font-bold text-text outline-none focus:border-accent"
                />
                <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-muted">units</span>
              </div>

              {stakeSplit && (
                <div className="space-y-1 font-mono text-[11.5px]">
                  <StakeRow
                    label={`Stake ${selectedAxis.backLabel} · ${selectedAxis.bestBack.bookmakerName}`}
                    value={`${stakeSplit.stakeBack.toFixed(2)}u`}
                  />
                  <StakeRow
                    label={`Stake ${selectedAxis.layLabel} · ${selectedAxis.bestLay.bookmakerName}`}
                    value={`${stakeSplit.stakeLay.toFixed(2)}u`}
                  />
                  <div className="mt-2 grid grid-cols-[1fr_auto] gap-3 border-t border-border pt-2.5">
                    <span className="text-text-secondary">Worst-case profit</span>
                    <span
                      className={`font-bold ${stakeSplit.profit >= 0 ? 'text-accent' : 'text-warning'}`}
                    >
                      {stakeSplit.profit >= 0 ? '+' : ''}
                      {stakeSplit.profit.toFixed(2)}u
                    </span>
                  </div>
                  <StakeRow
                    label="ROI"
                    value={`${stakeSplit.roi >= 0 ? '+' : ''}${stakeSplit.roi.toFixed(2)}%`}
                    valueClassName={stakeSplit.roi >= 0 ? 'text-accent' : 'text-warning'}
                  />
                </div>
              )}

              {selectedAxis.bestBack.sourceUrl && selectedAxis.bestLay.sourceUrl ? (
                <div className="mt-4 flex flex-col gap-2">
                  <a
                    href={selectedAxis.bestBack.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[11px] uppercase tracking-[0.22em] text-accent hover:text-text"
                  >
                    ▸ Open back leg
                  </a>
                  <a
                    href={selectedAxis.bestLay.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[11px] uppercase tracking-[0.22em] text-accent hover:text-text"
                  >
                    ▸ Open lay leg
                  </a>
                </div>
              ) : (
                <div className="mt-4 font-mono text-[11px] uppercase tracking-[0.22em] text-text-muted">
                  —  Source URLs unavailable
                </div>
              )}
            </>
          ) : (
            <div className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-muted">
              Insufficient odds to compute pair
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

function StakeRow({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-baseline gap-3 py-1">
      <span className="text-text-secondary">{label}</span>
      <span className={`font-bold ${valueClassName ?? 'text-text'}`}>{value}</span>
    </div>
  );
}

function StatusPillSeparator({ pct }: { pct: number | null | undefined }) {
  const tier = impliedPctTier(pct);
  if (tier === 'arb') return <> · arb</>;
  if (tier === 'knife-edge') return <> · knife-edge</>;
  if (tier === 'margin') return <> · margin</>;
  if (tier === 'high-margin') return <> · high margin</>;
  return null;
}

// Re-export only used externally via the named function. StatusPill below is unused
// but kept for future top-of-event ticker — typed import to silence linter.
export { StatusPill };
