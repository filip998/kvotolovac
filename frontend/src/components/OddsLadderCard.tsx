import { useMemo, useState } from 'react';
import type { AxisBookmakerRow, AxisLeg, AxisRow, MarketCard } from '../utils/oddsAxes';
import {
  formatOdds,
  formatRoi,
  impliedPctTier,
  roiColor,
  roiFromImpliedPct,
} from '../utils/format';
import BookmakerBadge from './BookmakerBadge';

interface OddsLadderCardProps {
  card: MarketCard;
  /** Optional anchor id override (for jump-bar). */
  anchorId?: string;
}

/**
 * Trading-style market card.
 *
 * Body has three regions separated by 1px rules:
 *   1. Ladder       — clickable axis selector (line + best back + best lay + ROI)
 *   2. Pivot        — per-bookmaker odds for the selected axis. Clicking the
 *                     "back" cell of any row sets that bookmaker as the
 *                     selected back leg; clicking the "lay" cell sets the lay
 *                     leg. The stake calculator on the right uses these
 *                     selections (defaulting to the auto-best when nothing
 *                     was clicked).
 *   3. Stake calc   — three inputs: Back stake, Lay stake, Total. Editing any
 *                     one auto-balances the other two so both legs return the
 *                     same payout (a balanced two-leg arb).
 *
 * Display: numerics in a system-mono stack so they're easy to read; labels in
 * the system sans stack. No custom Google fonts inside this component.
 */
export default function OddsLadderCard({ card, anchorId }: OddsLadderCardProps) {
  // Axis selection (which ladder row drives the pivot + stake calc).
  const [selectedAxisKey, setSelectedAxisKey] = useState<string | null>(null);
  // Per-axis bookmaker overrides for back/lay legs (null = auto-best).
  // Keyed by axisKey so switching axes resets to auto.
  const [legOverride, setLegOverride] = useState<{ axisKey: string; backBmId: string | null; layBmId: string | null } | null>(null);
  // Stake-input state. The user can edit any one of these; the other two
  // auto-recompute to maintain balanced arb when odds are known.
  // The active "source" determines who is the source of truth on render.
  const [stake, setStake] = useState<{ source: 'total' | 'back' | 'lay'; value: number }>({
    source: 'total',
    value: 100,
  });

  const strongestIdx = useMemo(() => pickStrongestAxisIndex(card.axes), [card.axes]);
  const explicitIdx =
    selectedAxisKey !== null ? card.axes.findIndex((a) => a.axisKey === selectedAxisKey) : -1;
  const effectiveIdx = explicitIdx >= 0 ? explicitIdx : strongestIdx;
  const selectedAxis = card.axes[effectiveIdx] ?? card.axes[0];

  // Resolve override state against the currently selected axis.
  const activeOverride =
    legOverride && selectedAxis && legOverride.axisKey === selectedAxis.axisKey ? legOverride : null;

  // Effective legs (per-row click overrides default "best").
  const effectiveBack: AxisLeg | null = useMemo(() => {
    if (!selectedAxis) return null;
    if (activeOverride?.backBmId) {
      const row = selectedAxis.bookmakerRows.find((r) => r.bookmakerId === activeOverride.backBmId);
      if (row?.backOdds != null) {
        return {
          bookmakerId: row.bookmakerId,
          bookmakerName: row.bookmakerName,
          sourceUrl: row.sourceUrl,
          odds: row.backOdds,
        };
      }
    }
    return selectedAxis.bestBack;
  }, [selectedAxis, activeOverride]);

  const effectiveLay: AxisLeg | null = useMemo(() => {
    if (!selectedAxis) return null;
    if (activeOverride?.layBmId) {
      const row = selectedAxis.bookmakerRows.find((r) => r.bookmakerId === activeOverride.layBmId);
      if (row?.layOdds != null) {
        return {
          bookmakerId: row.bookmakerId,
          bookmakerName: row.bookmakerName,
          sourceUrl: row.sourceUrl,
          odds: row.layOdds,
        };
      }
    }
    return selectedAxis.bestLay;
  }, [selectedAxis, activeOverride]);

  // Implied % and ROI for the *effective* (possibly user-overridden) pair.
  const effectiveImpliedPct =
    effectiveBack && effectiveLay
      ? (1 / effectiveBack.odds + 1 / effectiveLay.odds) * 100
      : null;
  const effectiveRoi = roiFromImpliedPct(effectiveImpliedPct);
  const effectiveSameBook =
    !!effectiveBack && !!effectiveLay && effectiveBack.bookmakerId === effectiveLay.bookmakerId;

  // Stake split — derive based on the active source input.
  const split = computeStakeSplit(
    effectiveBack?.odds ?? null,
    effectiveLay?.odds ?? null,
    stake,
  );

  const backStakeStr = split ? split.backStake.toFixed(2) : '';
  const layStakeStr = split ? split.layStake.toFixed(2) : '';
  const totalStr = split ? split.total.toFixed(2) : String(stake.value);

  function handleStakeInput(source: 'total' | 'back' | 'lay', text: string) {
    const value = Number(text);
    if (!Number.isFinite(value) || value < 0) return;
    setStake({ source, value });
  }

  const sectionId = anchorId ?? card.cardKey;

  // Status badge for the card header.
  const headerArbCount = card.arbCount;

  return (
    <article
      id={sectionId}
      className="border-b border-border px-4 pb-6 pt-4 last:border-b-0"
      style={{
        // Use system fonts inside this component (override the Outfit /
        // JetBrains Mono globals — the user wants something more "boring"
        // and easier to read).
        ['--font-sans' as string]:
          'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        ['--font-mono' as string]:
          'ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {/* Card head */}
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2 px-1 pb-3">
        <h3 className="m-0 text-[18px] font-semibold tracking-[-0.005em] text-text">{card.title}</h3>
        {card.pairEyebrow && (
          <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-text-muted">
            {card.pairEyebrow}
          </span>
        )}
        <span className="ml-auto flex items-baseline gap-4 font-mono text-[11px] uppercase tracking-[0.06em] text-text-muted">
          {headerArbCount > 0 && (
            <span className="font-bold text-accent">
              {headerArbCount} ARB{headerArbCount > 1 ? 'S' : ''}
            </span>
          )}
          <span>{card.bookmakerCount} BOOK{card.bookmakerCount === 1 ? '' : 'S'}</span>
          {card.axes.length > 1 && (
            <span>
              {card.axes.length} {card.category === 'match' ? 'AXES' : 'LINES'}
            </span>
          )}
        </span>
      </header>

      {/* 3-column body separated by vertical rules */}
      <div className="grid grid-cols-1 border-b border-t border-border md:grid-cols-[minmax(280px,1.2fr)_minmax(360px,1.6fr)] xl:grid-cols-[minmax(300px,1.2fr)_minmax(400px,2fr)_minmax(280px,1fr)]">
        {/* ─── Ladder ───────────────────────────────────────────────── */}
        <div className="min-w-0 px-4 py-3 xl:border-r xl:border-border">
          {card.axes.length > 1 && (
            <div className="mb-2 px-1 font-mono text-[10px] uppercase tracking-[0.12em] text-text-muted">
              {card.category === 'match' ? 'Outcome' : 'Line'}
            </div>
          )}
          <div className="flex flex-col">
            {card.axes.map((axis) => {
              const isSelected = axis.axisKey === selectedAxis?.axisKey;
              const roi = roiFromImpliedPct(axis.bestPairImpliedPct);
              const roiTier = impliedPctTier(axis.bestPairImpliedPct);
              return (
                <button
                  key={axis.axisKey}
                  type="button"
                  onClick={() => {
                    setSelectedAxisKey(axis.axisKey);
                    // Clear any per-row overrides when switching axes.
                    setLegOverride(null);
                  }}
                  aria-pressed={isSelected}
                  className={`relative grid w-full grid-cols-[minmax(40px,auto)_minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-x-3 border-t border-dotted border-border bg-transparent py-2 pl-3 pr-1 text-left text-[13px] transition-colors first:border-t-0 ${
                    isSelected ? 'text-text' : 'text-text-secondary hover:text-text'
                  }`}
                >
                  <span
                    aria-hidden
                    className="pointer-events-none absolute left-0 top-2 bottom-2 w-0.5 transition-colors"
                    style={{ background: isSelected ? 'var(--color-accent)' : 'transparent' }}
                  />
                  <span className="font-mono text-[13px] font-semibold text-text">{axis.lineTag}</span>
                  <BookmakerLegSummary leg={axis.bestBack} muted={!isSelected} />
                  <BookmakerLegSummary leg={axis.bestLay} muted={!isSelected} />
                  <span
                    className={`text-right font-mono text-[12px] font-semibold ${roiColor(roi)}`}
                    title={
                      axis.bestPairImpliedPct != null
                        ? `Implied ${axis.bestPairImpliedPct.toFixed(1)}%`
                        : undefined
                    }
                  >
                    {formatRoi(roi)}
                    {roiTier === 'arb' && axis.bestPairSameBook ? ' *' : ''}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* ─── Pivot ────────────────────────────────────────────────── */}
        <div className="min-w-0 border-t border-border bg-surface px-4 py-3 md:border-l md:border-t-0 xl:border-r">
          <div className="mb-2 px-1 font-mono text-[10px] uppercase tracking-[0.12em] text-text-muted">
            {selectedAxis ? (
              <>
                {card.bookmakerCount} BOOK{card.bookmakerCount === 1 ? '' : 'S'} · LINE{' '}
                <span className="font-semibold text-text normal-case tracking-normal">
                  {selectedAxis.lineTag}
                </span>
                {' '}· click an odds to use it in the calculator
              </>
            ) : (
              'No data'
            )}
          </div>
          {selectedAxis && selectedAxis.bookmakerRows.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-[13px]" style={{ fontFamily: 'var(--font-mono)' }}>
                <thead>
                  <tr className="border-b border-border text-[10px] font-medium uppercase tracking-[0.08em] text-text-muted">
                    <th className="pb-2 pr-3 text-left font-medium">Book</th>
                    <th className="pb-2 pr-3 text-right font-medium">{selectedAxis.backColumnLabel}</th>
                    <th className="pb-2 pr-3 text-right font-medium">{selectedAxis.layColumnLabel}</th>
                    <th className="pb-2 text-right font-medium">ROI</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedAxis.bookmakerRows.map((row) => (
                    <PivotRow
                      key={row.bookmakerId}
                      row={row}
                      isBackSelected={
                        activeOverride?.backBmId
                          ? row.bookmakerId === activeOverride.backBmId
                          : !!selectedAxis.bestBack && row.bookmakerId === selectedAxis.bestBack.bookmakerId &&
                            row.backOdds === selectedAxis.bestBack.odds
                      }
                      isLaySelected={
                        activeOverride?.layBmId
                          ? row.bookmakerId === activeOverride.layBmId
                          : !!selectedAxis.bestLay && row.bookmakerId === selectedAxis.bestLay.bookmakerId &&
                            row.layOdds === selectedAxis.bestLay.odds
                      }
                      onPickBack={() =>
                        setLegOverride((prev) => ({
                          axisKey: selectedAxis.axisKey,
                          backBmId: row.bookmakerId,
                          layBmId: prev?.axisKey === selectedAxis.axisKey ? prev.layBmId : null,
                        }))
                      }
                      onPickLay={() =>
                        setLegOverride((prev) => ({
                          axisKey: selectedAxis.axisKey,
                          backBmId: prev?.axisKey === selectedAxis.axisKey ? prev.backBmId : null,
                          layBmId: row.bookmakerId,
                        }))
                      }
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-1 py-3 text-center text-[12px] text-text-muted">
              No bookmaker data for this axis
            </div>
          )}
        </div>

        {/* ─── Stake calc ───────────────────────────────────────────── */}
        <div className="min-w-0 border-t border-border bg-bg px-4 py-3 xl:border-l xl:border-t-0">
          {effectiveBack && effectiveLay ? (
            <>
              <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-text-muted">
                Stake calculator
              </div>
              <div className="space-y-1 border-b border-border pb-3 text-[13px] leading-snug">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-text-muted">►</span>
                  <BookmakerBadge name={effectiveBack.bookmakerName} compact />
                  <span className="text-text-secondary">·</span>
                  <span className="font-medium text-text">{selectedAxis.backLabel}</span>
                  <span className="ml-auto font-mono font-semibold text-text">
                    {formatOdds(effectiveBack.odds)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-text-muted">+</span>
                  <BookmakerBadge name={effectiveLay.bookmakerName} compact />
                  <span className="text-text-secondary">·</span>
                  <span className="font-medium text-text">{selectedAxis.layLabel}</span>
                  <span className="ml-auto font-mono font-semibold text-text">
                    {formatOdds(effectiveLay.odds)}
                  </span>
                </div>
                <div className={`pt-2 font-mono text-[12px] font-semibold ${roiColor(effectiveRoi)}`}>
                  {effectiveRoi != null
                    ? `ROI ${formatRoi(effectiveRoi)}${
                        effectiveSameBook
                          ? '  ·  same book (display only)'
                          : effectiveRoi > 0
                            ? '  ·  arb'
                            : '  ·  margin'
                      }`
                    : 'ROI —'}
                </div>
              </div>

              {/* Three inputs — Back / Lay / Total. Editing one auto-balances the others. */}
              <div className="space-y-2 pt-3 text-[13px]">
                <StakeInput
                  label={`Stake ${selectedAxis.backLabel}`}
                  hint={effectiveBack.bookmakerName}
                  value={backStakeStr}
                  onChange={(v) => handleStakeInput('back', v)}
                />
                <StakeInput
                  label={`Stake ${selectedAxis.layLabel}`}
                  hint={effectiveLay.bookmakerName}
                  value={layStakeStr}
                  onChange={(v) => handleStakeInput('lay', v)}
                />
                <StakeInput label="Total stake" hint="bankroll" value={totalStr} onChange={(v) => handleStakeInput('total', v)} />
                {split && (
                  <div className="mt-2 grid grid-cols-2 gap-2 border-t border-border pt-2 text-[12px]">
                    <div className="text-text-secondary">Worst-case profit</div>
                    <div
                      className={`text-right font-mono font-semibold ${
                        split.profit >= 0 ? 'text-accent' : 'text-warning'
                      }`}
                    >
                      {split.profit >= 0 ? '+' : '−'}
                      {Math.abs(split.profit).toFixed(2)}u
                    </div>
                    <div className="text-text-secondary">ROI</div>
                    <div
                      className={`text-right font-mono font-semibold ${
                        split.roi >= 0 ? 'text-accent' : 'text-warning'
                      }`}
                    >
                      {formatRoi(split.roi)}
                    </div>
                  </div>
                )}
              </div>

              <div className="mt-4 flex flex-col gap-1.5">
                {effectiveSameBook ? (
                  <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted">
                    Same bookmaker — not a tradable arb
                  </span>
                ) : (
                  <>
                    {effectiveBack.sourceUrl ? (
                      <a
                        href={effectiveBack.sourceUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-[11px] uppercase tracking-[0.12em] text-accent hover:text-text"
                      >
                        ▸ Open {selectedAxis.backLabel} @ {effectiveBack.bookmakerName}
                      </a>
                    ) : null}
                    {effectiveLay.sourceUrl ? (
                      <a
                        href={effectiveLay.sourceUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-[11px] uppercase tracking-[0.12em] text-accent hover:text-text"
                      >
                        ▸ Open {selectedAxis.layLabel} @ {effectiveLay.bookmakerName}
                      </a>
                    ) : null}
                    {!effectiveBack.sourceUrl && !effectiveLay.sourceUrl && (
                      <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted">
                        Source URLs unavailable
                      </span>
                    )}
                  </>
                )}
              </div>
            </>
          ) : (
            <div className="px-1 py-2 text-[13px] text-text-muted">
              Insufficient odds to compute pair
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

/* ────────────────────────── helpers ────────────────────────── */

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

interface StakeSplit {
  backStake: number;
  layStake: number;
  total: number;
  /** Worst-case profit assuming the BACK leg wins (= guaranteed return − total). */
  profit: number;
  /** ROI = profit / total × 100. */
  roi: number;
}

function computeStakeSplit(
  backOdds: number | null,
  layOdds: number | null,
  stake: { source: 'total' | 'back' | 'lay'; value: number },
): StakeSplit | null {
  if (backOdds == null || layOdds == null || backOdds <= 0 || layOdds <= 0) return null;
  if (!Number.isFinite(stake.value) || stake.value < 0) return null;

  let backStake: number;
  let layStake: number;
  if (stake.source === 'total') {
    // Balanced split.
    backStake = (stake.value * layOdds) / (backOdds + layOdds);
    layStake = stake.value - backStake;
  } else if (stake.source === 'back') {
    backStake = stake.value;
    layStake = (backStake * backOdds) / layOdds;
  } else {
    layStake = stake.value;
    backStake = (layStake * layOdds) / backOdds;
  }

  const total = backStake + layStake;
  const returnIfBack = backStake * backOdds;
  const returnIfLay = layStake * layOdds;
  const minReturn = Math.min(returnIfBack, returnIfLay);
  const profit = minReturn - total;
  const roi = total > 0 ? (profit / total) * 100 : 0;
  return {
    backStake: round2(backStake),
    layStake: round2(layStake),
    total: round2(total),
    profit: round2(profit),
    roi: round2(roi),
  };
}

function round2(value: number): number {
  return Number.isFinite(value) ? Number(value.toFixed(2)) : 0;
}

/* ────────────────────────── small subcomponents ────────────────────────── */

function BookmakerLegSummary({ leg, muted }: { leg: AxisLeg | null; muted: boolean }) {
  if (!leg) {
    return <span className={muted ? 'text-text-muted' : 'text-text-secondary'}>—</span>;
  }
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <BookmakerBadge name={leg.bookmakerName} compact />
      <span className="min-w-0 truncate text-[12px] text-text-secondary" title={leg.bookmakerName}>
        {leg.bookmakerName}
      </span>
      <span className="ml-auto shrink-0 font-mono text-[13px] font-semibold text-text">
        {formatOdds(leg.odds)}
      </span>
    </span>
  );
}

function PivotRow({
  row,
  isBackSelected,
  isLaySelected,
  onPickBack,
  onPickLay,
}: {
  row: AxisBookmakerRow;
  isBackSelected: boolean;
  isLaySelected: boolean;
  onPickBack: () => void;
  onPickLay: () => void;
}) {
  const roi = roiFromImpliedPct(row.impliedPct);
  return (
    <tr className="border-b border-dotted border-border last:border-0">
      <td className="py-2 pr-3 text-left">
        <BookmakerBadge name={row.bookmakerName} compact href={row.sourceUrl ?? undefined} />
      </td>
      <td className="py-1 pr-2 text-right">
        {row.backOdds != null ? (
          <button
            type="button"
            onClick={onPickBack}
            aria-pressed={isBackSelected}
            className={`-mr-1 inline-flex min-w-[64px] items-center justify-end gap-1 rounded-sm px-2 py-1 font-mono text-[13px] font-semibold transition ${
              isBackSelected
                ? 'bg-accent/[0.18] text-accent ring-1 ring-accent/40'
                : row.isBestBack
                  ? 'text-accent hover:bg-accent/[0.06]'
                  : 'text-text hover:bg-text/[0.04]'
            }`}
          >
            {/* Reserve a fixed-width slot for the ◆ marker so the cell width
                doesn't shift when the same-row selection state toggles. */}
            <span aria-hidden className="w-3 text-left text-[10px]">
              {row.isBestBack ? '◆' : ''}
            </span>
            <span>{formatOdds(row.backOdds)}</span>
          </button>
        ) : (
          <span className="px-2 font-mono text-[13px] text-text-muted">—</span>
        )}
      </td>
      <td className="py-1 pr-2 text-right">
        {row.layOdds != null ? (
          <button
            type="button"
            onClick={onPickLay}
            aria-pressed={isLaySelected}
            className={`-mr-1 inline-flex min-w-[64px] items-center justify-end gap-1 rounded-sm px-2 py-1 font-mono text-[13px] font-semibold transition ${
              isLaySelected
                ? 'bg-accent/[0.18] text-accent ring-1 ring-accent/40'
                : row.isBestLay
                  ? 'text-accent hover:bg-accent/[0.06]'
                  : 'text-text hover:bg-text/[0.04]'
            }`}
          >
            <span aria-hidden className="w-3 text-left text-[10px]">
              {row.isBestLay ? '◆' : ''}
            </span>
            <span>{formatOdds(row.layOdds)}</span>
          </button>
        ) : (
          <span className="px-2 font-mono text-[13px] text-text-muted">—</span>
        )}
      </td>
      <td className={`py-2 text-right font-mono text-[12px] font-semibold ${roiColor(roi)}`}>
        {formatRoi(roi)}
      </td>
    </tr>
  );
}

function StakeInput({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 border-b border-border pb-1">
      <span className="flex-1 truncate text-text-secondary">
        {label}
        {hint && <span className="ml-1.5 text-[11px] text-text-muted">· {hint}</span>}
      </span>
      <input
        type="number"
        inputMode="decimal"
        min={0}
        step="0.01"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-20 bg-transparent text-right font-mono text-[14px] font-semibold text-text outline-none focus:text-accent"
        style={{ fontFamily: 'var(--font-mono)' }}
        aria-label={label}
      />
      <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-text-muted">u</span>
    </label>
  );
}
