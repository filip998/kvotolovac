import { useDeferredValue, useMemo } from 'react';
import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import type { UnresolvedOdds } from '../api/types';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import { buildSearchIndex, filterSearchIndex, normalizeSearchText } from '../utils/search';
import OfferSearchStrip from './OfferSearchStrip';
import { groupUnresolvedOdds } from '../utils/unresolvedWarnings';
import {
  formatDateTime,
  formatRelativeTime,
} from '../utils/format';

const REASON_LABELS: Record<string, string> = {
  no_canonical_matchup_for_team_at_slot: 'No canonical matchup at this league/time slot',
  ambiguous_multiple_matchups_for_team_at_slot:
    'Multiple canonical matchups matched the same team at this slot',
};

function reasonLabel(reasonCode: string) {
  return REASON_LABELS[reasonCode] ?? reasonCode.replace(/_/g, ' ');
}

function countLabel(count: number, singular: string, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function leagueSummary(leagueLabels: string[]) {
  if (leagueLabels.length === 0) {
    return 'Unknown league';
  }
  if (leagueLabels.length === 1) {
    return leagueLabels[0];
  }

  return `${leagueLabels[0]} +${leagueLabels.length - 1} variants`;
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
  const appliedSearchQuery = useDeferredValue(searchQuery);
  const groupedRows = useMemo(() => groupUnresolvedOdds(rows), [rows]);
  const searchableRows = useMemo(
    () =>
      buildSearchIndex(groupedRows, (group) => [
        group.normalizedTeamName,
        ...group.rawTeamNames,
        ...group.leagueLabels,
        ...group.matchupContext,
        ...group.playerNames,
        ...group.bookmakerNames,
        ...group.marketTypes.map(
          (marketType) =>
            MARKET_TYPE_LABELS[marketType as keyof typeof MARKET_TYPE_LABELS] ?? marketType
        ),
      ]),
    [groupedRows]
  );

  const filteredRows = useMemo(
    () => filterSearchIndex(searchableRows, appliedSearchQuery),
    [appliedSearchQuery, searchableRows]
  );
  const totalAffectedOdds = useMemo(
    () => groupedRows.reduce((sum, group) => sum + group.affectedOddsCount, 0),
    [groupedRows]
  );
  const filteredAffectedOdds = useMemo(
    () => filteredRows.reduce((sum, group) => sum + group.affectedOddsCount, 0),
    [filteredRows]
  );
  const hasSearchQuery = normalizeSearchText(appliedSearchQuery).length > 0;
  const activeSearchLabel = appliedSearchQuery.trim();
  const searchStrip = (
    <OfferSearchStrip
      value={searchQuery}
      onChange={onSearchChange}
      scopeLabel="Warnings"
      placeholder="Search ambiguous teams, matchup context or players, e.g. Barcelona or Brizuela"
      resultCount={filteredRows.length}
      totalCount={groupedRows.length}
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
          message="Warnings search checks ambiguous teams, matchup context, players, leagues and bookmakers in the current snapshot."
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
          {hasSearchQuery
            ? `${filteredRows.length} of ${groupedRows.length}`
            : filteredRows.length}{' '}
          ambiguous events
        </span>
        <span>
          {hasSearchQuery ? `${filteredAffectedOdds} of ${totalAffectedOdds}` : filteredAffectedOdds}{' '}
          affected odds
        </span>
        <span>
          {new Set(filteredRows.flatMap((group) => group.bookmakerNames)).size} bookmakers
        </span>
        <span>Current snapshot only</span>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
                <th className="px-4 py-2.5 text-left">Ambiguous team / event</th>
                <th className="px-4 py-2.5 text-left">Bookmakers</th>
                <th className="px-4 py-2.5 text-left">Impact</th>
                <th className="px-4 py-2.5 text-left">Reason</th>
                <th className="hidden px-4 py-2.5 text-left lg:table-cell">Relevant matchups</th>
                <th className="hidden px-4 py-2.5 text-right xl:table-cell">Seen</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((group) => {
                const hasRawTeamVariants = group.rawTeamNames.some(
                  (rawTeamName) => rawTeamName !== group.normalizedTeamName
                );
                return (
                  <tr key={group.id} className="border-t border-border align-top transition hover:bg-surface-raised">
                    <td className="px-4 py-3">
                      <div className="font-medium text-text">{group.normalizedTeamName}</div>
                      {hasRawTeamVariants && (
                        <div className="text-[11px] text-text-muted">
                          Seen as: {group.rawTeamNames.join(', ')}
                        </div>
                      )}
                      <div className="mt-1 text-[11px] text-text-muted">
                        {leagueSummary(group.leagueLabels)}
                        {group.startTime ? ` · ${formatDateTime(group.startTime)}` : ''}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        {group.bookmakerNames.map((bookmakerName) => (
                          <BookmakerBadge key={`${group.id}-${bookmakerName}`} name={bookmakerName} compact />
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-text">
                        {countLabel(group.affectedOddsCount, 'affected odd', 'affected odds')}
                      </div>
                      <div className="mt-1 text-[11px] text-text-muted">
                        {countLabel(group.playerNames.length, 'player')} ·{' '}
                        {countLabel(group.marketTypes.length, 'market')}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-warning">{reasonLabel(group.reasonCode)}</div>
                      <div className="mt-1 text-[11px] text-text-muted">
                        {group.candidateCount > 0
                          ? `Candidate matches: ${group.candidateCount}`
                          : group.matchupContext.length > 0
                            ? countLabel(group.matchupContext.length, 'same-slot matchup')
                            : 'No same-slot matchups'}
                      </div>
                    </td>
                    <td className="hidden px-4 py-3 lg:table-cell">
                      {group.matchupContext.length > 0 ? (
                        <div className="space-y-1">
                          {group.matchupContext.map((matchup) => (
                            <div key={`${group.id}-${matchup}`} className="text-[11px] text-text-secondary">
                              {matchup}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <span className="text-[11px] text-text-muted">No canonical matchups at this slot</span>
                      )}
                    </td>
                    <td className="hidden px-4 py-3 text-right text-text-muted xl:table-cell">
                      {formatRelativeTime(group.latestScrapedAt)}
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
