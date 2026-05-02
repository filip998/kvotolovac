import { Fragment } from 'react';
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
import BookmakerBadge from './BookmakerBadge';
import StakeCalculatorPanel from './StakeCalculatorPanel';

interface EdgeRowProps {
  edge: Edge;
  totalUnits: number;
  isCalculatorExpanded: boolean;
  onToggleCalculator: (edgeId: string) => void;
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
  const label = sport === 'football' ? '⚽' : '🏀';
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

export default function EdgeRow({
  edge,
  totalUnits,
  isCalculatorExpanded,
  onToggleCalculator,
  sharedSearch,
}: EdgeRowProps) {
  const headline = edgeMarketHeadline(edge);
  const calculatorPanelId = `flat-calculator-${edge.id}`;
  const ariaLabel = `${isCalculatorExpanded ? 'Hide' : 'View'} stake calculator for ${
    edge.player_name || headline
  } in ${edge.home_team ?? '?'} vs ${edge.away_team ?? '?'}`;

  return (
    <Fragment>
      <tr className="border-t border-border transition hover:bg-surface-raised">
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-1.5">
            <SportPill sport={edge.sport} />
            <span className="font-medium text-text">{edge.player_name || headline}</span>
          </div>
          {edge.player_name && (
            <div className="text-[11px] text-text-muted">{headline}</div>
          )}
        </td>
        <td className="px-4 py-2.5">
          <div className="text-text-secondary">
            {edge.home_team ?? '—'} vs {edge.away_team ?? '—'}
          </div>
          <div className="text-[11px] text-text-muted">{edge.league_name}</div>
        </td>
        <td
          className={`px-4 py-2.5 text-right font-mono font-bold ${
            edge.profit_margin != null ? profitColor(edge.profit_margin) : 'text-text-muted'
          }`}
        >
          {edge.profit_margin != null ? formatPercentage(edge.profit_margin) : '—'}
        </td>
        <td className="hidden px-4 py-2.5 text-right md:table-cell">
          {edge.middle_profit_margin != null && (edge.gap ?? 0) > 0 ? (
            <span className={`font-mono font-bold ${profitColor(edge.middle_profit_margin)}`}>
              {formatPercentage(edge.middle_profit_margin)}
            </span>
          ) : (
            <span className="text-text-muted">—</span>
          )}
        </td>
        <td className="hidden px-4 py-2.5 sm:table-cell">
          <LegCell edge={edge} side="a" />
        </td>
        <td className="hidden px-4 py-2.5 sm:table-cell">
          <LegCell edge={edge} side="b" />
        </td>
        <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
          {edge.gap != null ? formatGap(edge.gap) : '—'}
        </td>
        <td className="hidden px-4 py-2.5 text-right text-text-muted lg:table-cell">
          {formatRelativeTime(edge.detected_at)}
        </td>
        <td className="px-4 py-2.5 text-right">
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              aria-expanded={isCalculatorExpanded}
              aria-controls={calculatorPanelId}
              aria-label={ariaLabel}
              onClick={() => onToggleCalculator(edge.id)}
              className="text-[11px] font-medium text-text-muted transition hover:text-text"
            >
              {isCalculatorExpanded ? 'Hide' : 'View'}
            </button>
            <Link
              to={`/matches/${edge.match_id}${sharedSearch}`}
              aria-label={`View ${edge.player_name || headline} for ${edge.home_team ?? ''} vs ${edge.away_team ?? ''}`}
              className="text-xs font-medium text-text-muted transition hover:text-accent"
            >
              →
            </Link>
          </div>
        </td>
      </tr>
      {isCalculatorExpanded && (
        <tr className="border-t border-border bg-bg/20">
          <td colSpan={9} className="px-4 py-3">
            <div id={calculatorPanelId}>
              <StakeCalculatorPanel edge={edge} totalUnits={totalUnits} />
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}
