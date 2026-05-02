import { useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { MarketGroup } from '../utils/discrepancyGrouping';
import {
  formatGap,
  formatHandicapLine,
  formatOdds,
  formatPercentage,
  formatRelativeTime,
  formatThreshold,
  isHandicapMarket,
  profitBgColor,
  profitColor,
} from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import BookmakerBadge from './BookmakerBadge';
import StakeCalculatorPanel from './StakeCalculatorPanel';

interface MarketSummaryCardProps {
  group: MarketGroup;
  totalUnits: number;
}

/**
 * One card per (match, market_type, player_name).
 *
 * Collapsed by default: shows the best (highest profit_margin) line and
 * its stake calculator. Click "Show N lines" to expand into a compact
 * ladder table where each row is a discrepancy in the group; click a row
 * to make it the "selected" line, and the header + stake calculator
 * rebind to that line.
 *
 * This is a presentation refactor of the per-discrepancy
 * ``DiscrepancyCard`` — same data, just collapsed by group so a single
 * match's threshold ladder doesn't dominate the dashboard.
 */
export default function MarketSummaryCard({ group, totalUnits }: MarketSummaryCardProps) {
  const location = useLocation();
  const [isExpanded, setIsExpanded] = useState(false);
  // ``null`` means "no explicit selection — track current group.best".
  // We only set a real id when the user clicks a ladder row. This keeps
  // the collapsed card in sync with the latest best line across React
  // Query refetches: when a new line becomes ``group.best`` (margins
  // shift every scrape cycle), we display the new best instead of
  // pinning whatever happened to be best at mount time.
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // Resolve the selected discrepancy. If the user hasn't selected
  // anything (or the previously-selected line vanished from the group),
  // fall back to the current group.best.
  const selected = useMemo(() => {
    if (selectedId === null) return group.best;
    return group.lines.find((line) => line.id === selectedId) ?? group.best;
  }, [group.best, group.lines, selectedId]);

  const handicap = isHandicapMarket(selected.market_type);
  const sideALabel = handicap ? 'Home' : 'Over';
  const sideBLabel = handicap ? 'Away' : 'Under';
  const lineALabel = handicap
    ? formatHandicapLine(selected.threshold_a, 'home')
    : formatThreshold(selected.threshold_a);
  const lineBLabel = handicap
    ? formatHandicapLine(selected.threshold_b, 'away')
    : formatThreshold(selected.threshold_b);

  const marketLabel = MARKET_TYPE_LABELS[group.marketType] || group.marketType;
  const headerLabel = group.playerName
    ? `${group.playerName} — ${marketLabel}`
    : marketLabel;
  const lineCount = group.lines.length;
  const showExpansionToggle = lineCount > 1;

  return (
    <div
      className={`rounded-[24px] border p-4 shadow-[0_20px_60px_-42px_rgba(0,0,0,0.9)] transition hover:-translate-y-0.5 hover:border-border-hover ${profitBgColor(selected.profit_margin)}`}
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.24em] text-text-muted">
              {marketLabel}
            </span>
            {showExpansionToggle && (
              <span className="rounded-full border border-border/70 bg-bg/60 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary">
                {lineCount} lines
              </span>
            )}
          </div>
          <h4 className="mt-2 text-sm font-semibold text-text">{headerLabel}</h4>
        </div>
        <div className="text-right">
          <div className={`font-mono text-lg font-bold ${profitColor(selected.profit_margin)}`}>
            {formatPercentage(selected.profit_margin)}
          </div>
          <div className="mt-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-muted">
            {selected.gap > 0 ? `${formatGap(selected.gap)} pt gap` : 'same threshold'}
          </div>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3">
        <div className="rounded-2xl border border-border/70 bg-bg/55 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
          <div className="mb-2">
            <BookmakerBadge
              name={selected.bookmaker_a_name}
              href={selected.bookmaker_a_source_url}
              ariaLabel={`Open ${selected.bookmaker_a_name} match page`}
            />
          </div>
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
            {sideALabel}
          </span>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="font-mono text-lg font-semibold text-text">{lineALabel}</span>
            <span className="font-mono text-base font-semibold text-text">
              {formatOdds(selected.odds_a)}
            </span>
          </div>
        </div>
        <div className="rounded-2xl border border-border/70 bg-bg/55 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
          <div className="mb-2">
            <BookmakerBadge
              name={selected.bookmaker_b_name}
              href={selected.bookmaker_b_source_url}
              ariaLabel={`Open ${selected.bookmaker_b_name} match page`}
            />
          </div>
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
            {sideBLabel}
          </span>
          <div className="mt-1 flex items-end justify-between gap-2">
            <span className="font-mono text-lg font-semibold text-text">{lineBLabel}</span>
            <span className="font-mono text-base font-semibold text-text">
              {formatOdds(selected.odds_b)}
            </span>
          </div>
        </div>
      </div>

      <StakeCalculatorPanel discrepancy={selected} totalUnits={totalUnits} />

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 text-xs">
        <div className="flex items-center gap-3">
          <span className="font-mono font-medium text-text-secondary">
            {selected.gap > 0 ? `Gap ${formatGap(selected.gap)} pts` : 'Same threshold'}
          </span>
          {selected.middle_profit_margin !== undefined &&
            selected.middle_profit_margin !== null &&
            selected.gap > 0 && (
              <span className={`font-mono font-medium ${profitColor(selected.middle_profit_margin)}`}>
                Middle {formatPercentage(selected.middle_profit_margin)}
              </span>
            )}
          <span className="text-text-muted">
            Detected {formatRelativeTime(selected.detected_at)}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {showExpansionToggle && (
            <button
              type="button"
              onClick={() => setIsExpanded((prev) => !prev)}
              aria-expanded={isExpanded}
              className="font-medium text-text-secondary transition hover:text-accent"
            >
              {isExpanded ? 'Hide ladder' : `Show ${lineCount} lines`}
            </button>
          )}
          <Link
            to={{ pathname: `/matches/${selected.match_id}`, search: location.search }}
            className="font-medium text-text-secondary transition hover:text-accent"
          >
            View match →
          </Link>
        </div>
      </div>

      {isExpanded && showExpansionToggle && (
        <div className="mt-3 overflow-hidden rounded-2xl border border-border/70 bg-bg/30">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border/70 text-[10px] font-medium uppercase tracking-[0.18em] text-text-muted">
                  <th className="px-3 py-2 text-left">{sideALabel}</th>
                  <th className="px-3 py-2 text-left">{sideBLabel}</th>
                  <th className="px-3 py-2 text-right">Gap</th>
                  <th className="px-3 py-2 text-right">Edge</th>
                  <th className="hidden px-3 py-2 text-right md:table-cell">Middle</th>
                </tr>
              </thead>
              <tbody>
                {group.lines.map((line) => {
                  const lineHandicap = isHandicapMarket(line.market_type);
                  const lineA = lineHandicap
                    ? formatHandicapLine(line.threshold_a, 'home')
                    : formatThreshold(line.threshold_a);
                  const lineB = lineHandicap
                    ? formatHandicapLine(line.threshold_b, 'away')
                    : formatThreshold(line.threshold_b);
                  const isSelected = line.id === selected.id;
                  return (
                    <tr
                      key={line.id}
                      onClick={() => setSelectedId(line.id)}
                      className={`cursor-pointer border-t border-border/40 transition hover:bg-surface-raised ${
                        isSelected ? 'bg-accent/[0.08]' : ''
                      }`}
                      aria-selected={isSelected}
                    >
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          <BookmakerBadge name={line.bookmaker_a_name} compact />
                          <span className="font-mono text-text-secondary">
                            {lineA} @ {formatOdds(line.odds_a)}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          <BookmakerBadge name={line.bookmaker_b_name} compact />
                          <span className="font-mono text-text-secondary">
                            {lineB} @ {formatOdds(line.odds_b)}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-text-secondary">
                        {formatGap(line.gap)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-mono font-bold ${profitColor(line.profit_margin)}`}
                      >
                        {formatPercentage(line.profit_margin)}
                      </td>
                      <td className="hidden px-3 py-2 text-right md:table-cell">
                        {line.middle_profit_margin != null && line.gap > 0 ? (
                          <span
                            className={`font-mono font-medium ${profitColor(line.middle_profit_margin)}`}
                          >
                            {formatPercentage(line.middle_profit_margin)}
                          </span>
                        ) : (
                          <span className="text-text-muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
