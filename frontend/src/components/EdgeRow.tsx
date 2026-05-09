import { Fragment, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Edge } from '../api/types';
import {
  formatGap,
  formatOdds,
  formatPercentage,
  formatRelativeTime,
  profitColor,
} from '../utils/format';
import { edgeMarketHeadline, formatLegLineLabel, formatOutcomeLabel } from '../utils/edgeFormatting';
import type { EdgeGroup } from '../utils/edgeGrouping';
import { eventOrMatchPath } from '../utils/routes';
import BookmakerBadge from './BookmakerBadge';
import StakeCalculatorPanel from './StakeCalculatorPanel';

interface EdgeGroupRowProps {
  group: EdgeGroup;
  totalUnits: number;
  isCalculatorExpanded: boolean;
  onToggleCalculator: (groupKey: string) => void;
  sharedSearch: string;
}

function LegCell({ edge, side }: { edge: Edge; side: 'a' | 'b' }) {
  const leg = side === 'a' ? edge.leg_a : edge.leg_b;
  const lineLabel = formatLegLineLabel(leg, edge.market_type);
  const outcomeLabel = formatOutcomeLabel(leg.outcome_code, edge.market_type, leg.line);
  return (
    <div className="flex items-center gap-1.5">
      <BookmakerBadge name={leg.bookmaker_name} compact />
      <span className="font-mono text-text-secondary">
        {outcomeLabel}
        {lineLabel ? ` ${lineLabel}` : ''} @ {formatOdds(leg.odds)}
      </span>
    </div>
  );
}

function SportPill({ sport }: { sport: Edge['sport'] }) {
  const label = sport === 'football' ? '⚽' : sport === 'tennis' ? '🎾' : '🏀';
  return (
    <span
      className="inline-flex items-center rounded-full border border-border/70 bg-bg/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary"
      title={sport}
      aria-label={sport}
    >
      {label}
    </span>
  );
}

function edgePrimaryValue(edge: Edge): number | null {
  if (edge.opportunity_type === 'middle' && edge.middle_ev != null) {
    return edge.middle_ev;
  }
  return edge.profit_margin;
}

function edgePrimaryLabel(edge: Edge): string {
  return edge.opportunity_type === 'middle' && edge.middle_ev != null ? 'EV' : 'ROI';
}

function confidenceLabel(edge: Edge): string | null {
  if (edge.opportunity_type !== 'middle' || !edge.middle_model_confidence) {
    return null;
  }
  return edge.middle_model_confidence === 'fallback'
    ? 'fallback'
    : `${edge.middle_model_confidence} confidence`;
}

function confidenceTitle(edge: Edge): string | undefined {
  if (edge.opportunity_type !== 'middle') return undefined;
  const diagnostics = edge.middle_model_diagnostics;
  const model = typeof diagnostics.model_family === 'string' ? diagnostics.model_family : null;
  const reason = typeof diagnostics.reason === 'string' ? diagnostics.reason : null;
  if (model && reason) return `${model}: ${reason}`;
  if (model) return model;
  return reason ?? undefined;
}

function LineRow({
  edge,
  selected,
  onSelect,
}: {
  edge: Edge;
  selected: boolean;
  onSelect: (edge: Edge) => void;
}) {
  const lineALabel = formatLegLineLabel(edge.leg_a, edge.market_type);
  const lineBLabel = formatLegLineLabel(edge.leg_b, edge.market_type);
  const outcomeALabel = formatOutcomeLabel(edge.leg_a.outcome_code, edge.market_type, edge.leg_a.line);
  const outcomeBLabel = formatOutcomeLabel(edge.leg_b.outcome_code, edge.market_type, edge.leg_b.line);
  const primaryValue = edgePrimaryValue(edge);
  return (
    <button
      type="button"
      onClick={() => onSelect(edge)}
      aria-selected={selected}
      className={`flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left text-xs transition ${
        selected
          ? 'border-accent/50 bg-accent/[0.10]'
          : 'border-border/60 bg-bg/60 hover:border-border-hover'
      }`}
    >
      <div className="flex flex-1 flex-wrap items-center gap-3">
        <span className={`font-mono font-semibold ${primaryValue != null ? profitColor(primaryValue) : 'text-text-muted'}`}>
          {primaryValue != null ? `${edgePrimaryLabel(edge)} ${formatPercentage(primaryValue)}` : '—'}
        </span>
        {edge.opportunity_type === 'middle' && edge.middle_hit_probability != null && (
          <span className="font-mono text-text-muted">
            hit {formatPercentage(edge.middle_hit_probability)}
          </span>
        )}
        <div className="flex items-center gap-1.5">
          <BookmakerBadge name={edge.leg_a.bookmaker_name} compact />
          <span className="font-mono text-text-secondary">
            {outcomeALabel}
            {lineALabel ? ` ${lineALabel}` : ''} @ {formatOdds(edge.leg_a.odds)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <BookmakerBadge name={edge.leg_b.bookmaker_name} compact />
          <span className="font-mono text-text-secondary">
            {outcomeBLabel}
            {lineBLabel ? ` ${lineBLabel}` : ''} @ {formatOdds(edge.leg_b.odds)}
          </span>
        </div>
      </div>
      <span className="font-mono text-text-muted">
        {edge.gap != null ? `${formatGap(edge.gap)} pt` : '—'}
      </span>
    </button>
  );
}

export default function EdgeGroupRow({
  group,
  totalUnits,
  isCalculatorExpanded,
  onToggleCalculator,
  sharedSearch,
}: EdgeGroupRowProps) {
  // null sentinel = "track current group.best across refetches".
  // Only set to a real id when the user explicitly picks a ladder line.
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showLadder, setShowLadder] = useState(false);

  const selected = useMemo(() => {
    if (selectedId === null) return group.best;
    return group.lines.find((line) => line.source_id === selectedId) ?? group.best;
  }, [group.best, group.lines, selectedId]);

  const headline = edgeMarketHeadline(selected);
  const primaryValue = edgePrimaryValue(selected);
  const confidence = confidenceLabel(selected);
  const calculatorPanelId = `flat-calculator-${group.key}`;
  const lineCount = group.lines.length;
  const hasLadder = lineCount > 1;
  const ariaLabel = `${isCalculatorExpanded ? 'Hide' : 'View'} stake calculator for ${
    selected.player_name || headline
  } in ${selected.home_team ?? '?'} vs ${selected.away_team ?? '?'}`;

  return (
    <Fragment>
      <tr className="border-t border-border transition hover:bg-surface-raised">
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1.5">
            <SportPill sport={selected.sport} />
            <span className="font-medium text-text">{selected.player_name || headline}</span>
            {hasLadder && (
              <span
                className="rounded-full border border-border/70 bg-bg/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary"
                title={`${lineCount} lines in this market`}
              >
                {lineCount} lines
              </span>
            )}
            {confidence && (
              <span
                title={confidenceTitle(selected)}
                className="rounded-full border border-border/70 bg-bg/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary"
              >
                {confidence}
              </span>
            )}
          </div>
          {selected.player_name && (
            <div className="text-[11px] text-text-muted">{headline}</div>
          )}
        </td>
        <td className="px-4 py-2.5">
          <div className="text-text-secondary">
            {selected.home_team ?? '—'} vs {selected.away_team ?? '—'}
          </div>
          <div className="text-[11px] text-text-muted">{selected.league_name}</div>
        </td>
        <td
          className={`px-4 py-2.5 text-right font-mono font-bold ${
            primaryValue != null ? profitColor(primaryValue) : 'text-text-muted'
          }`}
        >
          <div>{primaryValue != null ? formatPercentage(primaryValue) : '—'}</div>
          {selected.opportunity_type === 'middle' && selected.middle_ev != null && (
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-text-muted">
              EV
            </div>
          )}
        </td>
        <td className="hidden px-4 py-2.5 text-right md:table-cell">
          {selected.middle_profit_margin != null && (selected.gap ?? 0) > 0 ? (
            <div>
              <div className={`font-mono font-bold ${profitColor(selected.middle_profit_margin)}`}>
                {formatPercentage(selected.middle_profit_margin)}
              </div>
              {selected.middle_hit_probability != null && (
                <div className="font-mono text-[11px] text-text-muted">
                  hit {formatPercentage(selected.middle_hit_probability)}
                </div>
              )}
            </div>
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </td>
        <td className="hidden px-4 py-2.5 sm:table-cell">
          <LegCell edge={selected} side="a" />
        </td>
        <td className="hidden px-4 py-2.5 sm:table-cell">
          <LegCell edge={selected} side="b" />
        </td>
        <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
          {selected.gap != null ? formatGap(selected.gap) : '—'}
        </td>
        <td className="hidden px-4 py-2.5 text-right text-text-muted lg:table-cell">
          {formatRelativeTime(selected.detected_at)}
        </td>
        <td className="px-4 py-2.5 text-right">
          <div className="flex items-center justify-end gap-3">
            {hasLadder && (
              <button
                type="button"
                aria-expanded={showLadder}
                onClick={() => setShowLadder((prev) => !prev)}
                className="text-[11px] font-medium text-text-muted transition hover:text-text"
              >
                {showLadder ? 'Hide lines' : `Show ${lineCount} lines`}
              </button>
            )}
            <button
              type="button"
              aria-expanded={isCalculatorExpanded}
              aria-controls={calculatorPanelId}
              aria-label={ariaLabel}
              onClick={() => onToggleCalculator(group.key)}
              className="text-[11px] font-medium text-text-muted transition hover:text-text"
            >
              {isCalculatorExpanded ? 'Hide calc' : 'Calc'}
            </button>
            <Link
              to={eventOrMatchPath(selected.match_id, selected.resolved_event_id, sharedSearch)}
              aria-label={`View ${selected.player_name || headline} for ${selected.home_team ?? ''} vs ${selected.away_team ?? ''}`}
              className="text-xs font-medium text-text-muted transition hover:text-accent"
            >
              →
            </Link>
          </div>
        </td>
      </tr>
      {hasLadder && showLadder && (
        <tr className="border-t border-border bg-bg/15">
          <td colSpan={9} className="px-4 py-3">
            <div className="space-y-1.5">
              {group.lines.map((line) => (
                <LineRow
                  key={`${group.key}-${line.id}`}
                  edge={line}
                  selected={line.id === selected.id}
                  onSelect={(picked) => setSelectedId(picked.source_id)}
                />
              ))}
            </div>
          </td>
        </tr>
      )}
      {isCalculatorExpanded && (
        <tr className="border-t border-border bg-bg/20">
          <td colSpan={9} className="px-4 py-3">
            <div id={calculatorPanelId}>
              <StakeCalculatorPanel edge={selected} totalUnits={totalUnits} />
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}
