import { useMemo } from 'react';
import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import type { UnresolvedOdds } from '../api/types';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import { filterItemsBySearch, normalizeSearchText } from '../utils/search';
import OfferSearchStrip from './OfferSearchStrip';
import {
  formatDateTime,
  formatOdds,
  formatRelativeTime,
  formatThreshold,
} from '../utils/format';

const REASON_LABELS: Record<string, string> = {
  no_canonical_matchup_for_team_at_slot: 'No canonical matchup at this league/time slot',
  ambiguous_multiple_matchups_for_team_at_slot:
    'Multiple canonical matchups matched the same team at this slot',
};

function reasonLabel(reasonCode: string) {
  return REASON_LABELS[reasonCode] ?? reasonCode.replace(/_/g, ' ');
}

export default function UnresolvedOddsPanel({
  rows,
  isLoading,
  errorMessage,
  searchQuery,
  onSearchChange,
}: {
  rows: UnresolvedOdds[];
  isLoading: boolean;
  errorMessage: string | null;
  searchQuery: string;
  onSearchChange: (value: string) => void;
}) {
  const filteredRows = useMemo(
    () =>
      filterItemsBySearch(rows, searchQuery, (row) => [
        row.player_name,
        row.raw_team_name,
        row.normalized_team_name,
      ]),
    [rows, searchQuery]
  );
  const hasSearchQuery = normalizeSearchText(searchQuery).length > 0;
  const activeSearchLabel = searchQuery.trim();
  const searchStrip = (
    <OfferSearchStrip
      value={searchQuery}
      onChange={onSearchChange}
      scopeLabel="Warnings"
      placeholder="Search dropped team or player names, e.g. PAOK or Sloukas"
      resultCount={filteredRows.length}
      totalCount={rows.length}
      tone="warning"
    />
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <LoadingSpinner />
      </div>
    );
  }

  if (errorMessage) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <div className="rounded-lg border border-danger/30 bg-danger/10 p-6 text-center">
          <p className="text-sm text-danger">Failed to load warnings: {errorMessage}</p>
        </div>
      </div>
    );
  }

  if (hasSearchQuery && filteredRows.length === 0 && rows.length > 0) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <EmptyState
          title={`No warnings match "${activeSearchLabel}"`}
          message="Warnings search checks team and player names in the dropped-prop snapshot. Try a broader name or clear the query."
        />
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <EmptyState
          title="No unresolved odds in the current snapshot"
          message="All shared-platform player props were assigned to tracked matches in this scrape."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {searchStrip}

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface p-4 text-sm text-text-secondary">
        <span className="font-medium text-text">
          {hasSearchQuery ? `${filteredRows.length} of ${rows.length}` : filteredRows.length} dropped rows
        </span>
        <span>
          {new Set(filteredRows.map((row) => row.bookmaker_id)).size} bookmakers
        </span>
        <span>Current snapshot only</span>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
                <th className="px-4 py-2.5 text-left">Player / Market</th>
                <th className="px-4 py-2.5 text-left">Bookmaker</th>
                <th className="px-4 py-2.5 text-left">Team / League</th>
                <th className="px-4 py-2.5 text-left">Reason</th>
                <th className="hidden px-4 py-2.5 text-left lg:table-cell">Same-slot matchups</th>
                <th className="hidden px-4 py-2.5 text-right xl:table-cell">Seen</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => {
                const marketLabel =
                  MARKET_TYPE_LABELS[row.market_type as keyof typeof MARKET_TYPE_LABELS] ??
                  row.market_type;
                const bookmakerName = row.bookmaker_name ?? row.bookmaker_id;
                return (
                  <tr key={row.id} className="border-t border-border align-top transition hover:bg-surface-raised">
                    <td className="px-4 py-3">
                      <div className="font-medium text-text">{row.player_name || marketLabel}</div>
                      <div className="text-[11px] text-text-muted">
                        {row.player_name ? marketLabel : 'No player name'}
                      </div>
                      <div className="mt-1 font-mono text-[11px] text-text-secondary">
                        {formatThreshold(row.threshold)} @ {formatOdds(row.over_odds)} / {formatOdds(row.under_odds)}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <BookmakerBadge name={bookmakerName} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-text-secondary">{row.raw_team_name}</div>
                      {row.normalized_team_name !== row.raw_team_name && (
                        <div className="text-[11px] text-text-muted">
                          Normalized: {row.normalized_team_name}
                        </div>
                      )}
                      <div className="mt-1 text-[11px] text-text-muted">
                        {row.league_name ?? row.league_id}
                        {row.start_time ? ` · ${formatDateTime(row.start_time)}` : ''}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-warning">{reasonLabel(row.reason_code)}</div>
                      <div className="mt-1 text-[11px] text-text-muted">
                        Candidate matches: {row.candidate_count}
                      </div>
                    </td>
                    <td className="hidden px-4 py-3 lg:table-cell">
                      {row.available_matchups_same_slot.length > 0 ? (
                        <div className="space-y-1">
                          {row.available_matchups_same_slot.map((matchup) => (
                            <div key={`${row.id}-${matchup}`} className="text-[11px] text-text-secondary">
                              {matchup}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <span className="text-[11px] text-text-muted">No canonical matchups at this slot</span>
                      )}
                    </td>
                    <td className="hidden px-4 py-3 text-right text-text-muted xl:table-cell">
                      {formatRelativeTime(row.scraped_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
