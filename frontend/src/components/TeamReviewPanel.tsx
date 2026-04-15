import { useDeferredValue, useMemo } from 'react';
import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import OfferSearchStrip from './OfferSearchStrip';
import type { TeamReviewCase } from '../api/types';
import { formatDateTime, formatRelativeTime } from '../utils/format';
import { buildSearchIndex, filterSearchIndex, normalizeSearchText } from '../utils/search';

const REASON_LABELS: Record<string, string> = {
  candidate_team_match_same_start_time: 'Same teams, same exact tip-off',
};

function reasonLabel(reasonCode: string) {
  return REASON_LABELS[reasonCode] ?? reasonCode.replace(/_/g, ' ');
}

function confidenceBadgeClass(confidence: string) {
  switch (confidence) {
    case 'high':
      return 'border-accent/30 bg-accent/10 text-accent';
    case 'low':
      return 'border-warning/30 bg-warning/10 text-warning';
    default:
      return 'border-border bg-bg text-text-secondary';
  }
}

interface TeamReviewGroup {
  key: string;
  suggestedTeamName: string;
  scopeLeagueName: string | null;
  startTime: string | null;
  rows: TeamReviewCase[];
}

export default function TeamReviewPanel({
  rows,
  isLoading,
  errorMessage,
  searchQuery,
  onSearchChange,
  onApprove,
  onDecline,
  approvingCaseId,
  decliningCaseId,
  actionMessage,
}: {
  rows: TeamReviewCase[];
  isLoading: boolean;
  errorMessage: string | null;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  onApprove: (caseId: number) => void;
  onDecline: (caseId: number) => void;
  approvingCaseId: number | null;
  decliningCaseId: number | null;
  actionMessage: string | null;
}) {
  const appliedSearchQuery = useDeferredValue(searchQuery);
  const searchableRows = useMemo(
    () =>
      buildSearchIndex(rows, (row) => [
        row.suggested_team_name,
        row.raw_team_name,
        row.normalized_raw_team_name,
        row.scope_league_name,
        row.bookmaker_name,
      ]),
    [rows]
  );
  const filteredRows = useMemo(
    () => filterSearchIndex(searchableRows, appliedSearchQuery),
    [appliedSearchQuery, searchableRows]
  );
  const hasSearchQuery = normalizeSearchText(appliedSearchQuery).length > 0;
  const activeSearchLabel = appliedSearchQuery.trim();
  const pendingRows = filteredRows.filter((row) => row.status === 'pending');
  const approvedRows = filteredRows.filter((row) => row.status === 'approved');
  const pendingGroups = useMemo<TeamReviewGroup[]>(() => {
    const groups = new Map<string, TeamReviewGroup>();

    for (const row of pendingRows) {
      const key = `${row.suggested_team_name}::${row.start_time ?? 'unknown'}::${row.scope_league_id ?? 'none'}`;
      const existing = groups.get(key);
      if (existing) {
        existing.rows.push(row);
        continue;
      }
      groups.set(key, {
        key,
        suggestedTeamName: row.suggested_team_name,
        scopeLeagueName: row.scope_league_name,
        startTime: row.start_time,
        rows: [row],
      });
    }

    return Array.from(groups.values()).sort((left, right) => {
      const leftTime = left.startTime ?? '';
      const rightTime = right.startTime ?? '';
      if (leftTime !== rightTime) {
        return leftTime.localeCompare(rightTime);
      }
      return left.suggestedTeamName.localeCompare(right.suggestedTeamName);
    });
  }, [pendingRows]);

  const searchStrip = (
    <OfferSearchStrip
      value={searchQuery}
      onChange={onSearchChange}
      scopeLabel="Teams"
      placeholder="Search canonical or raw team names, e.g. Buducnost or Rilski"
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
          <p className="text-sm text-danger">Failed to load team review: {errorMessage}</p>
        </div>
      </div>
    );
  }

  if (hasSearchQuery && filteredRows.length === 0 && rows.length > 0) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <EmptyState
          title={`No team review rows match "${activeSearchLabel}"`}
          message="Search checks both the canonical club and the unresolved raw team labels from the current snapshot."
        />
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <EmptyState
          title="No team review actions in this snapshot"
          message="Every team label already resolved cleanly at the current exact start times."
        />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {searchStrip}

      <section className="rounded-xl border border-border bg-surface p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h3 className="text-base font-semibold text-text">Team review queue</h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-text-secondary">
              Approve a raw team name on the right when it clearly belongs to the canonical club on
              the left. Saved aliases apply on the next scrape.
            </p>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-3 py-2 text-xs text-text-muted">
            Exact time + both teams drive the game match
          </div>
        </div>

        {actionMessage && (
          <div className="mt-4 rounded-lg border border-accent/30 bg-accent/[0.08] px-4 py-3 text-sm text-text-secondary">
            {actionMessage}
          </div>
        )}

        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Pending</div>
            <div className="mt-2 font-mono text-2xl font-semibold text-warning">
              {rows.filter((row) => row.status === 'pending').length}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Approved</div>
            <div className="mt-2 font-mono text-2xl font-semibold text-accent">
              {rows.filter((row) => row.status === 'approved').length}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Bookmakers</div>
            <div className="mt-2 font-mono text-2xl font-semibold text-text">
              {new Set(rows.map((row) => row.bookmaker_id)).size}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Canonical clubs</div>
            <div className="mt-2 font-mono text-2xl font-semibold text-text-secondary">
              {new Set(rows.map((row) => row.suggested_team_name)).size}
            </div>
          </div>
        </div>
      </section>

      {pendingGroups.length > 0 ? (
        <section className="space-y-3">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-text">Needs review</h3>
              <p className="mt-1 text-sm text-text-secondary">
                Left is the canonical club. Right is each unresolved label we can save into it.
              </p>
            </div>
            <div className="text-xs text-text-muted">
              {pendingRows.length} open alias{pendingRows.length === 1 ? '' : 'es'}
            </div>
          </div>

          <div className="space-y-3">
            {pendingGroups.map((group) => (
              <article key={group.key} className="rounded-xl border border-border bg-surface p-4">
                <div className="grid gap-4 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
                  <div className="rounded-xl border border-border bg-bg/60 p-4">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">
                      Canonical club
                    </div>
                    <div className="mt-3 text-xl font-semibold tracking-tight text-text">
                      {group.suggestedTeamName}
                    </div>
                    <div className="mt-2 space-y-1 text-sm text-text-secondary">
                      <div>{group.scopeLeagueName ?? 'League scope not resolved yet'}</div>
                      <div>
                        {group.startTime ? formatDateTime(group.startTime) : 'Unknown exact start time'}
                      </div>
                    </div>
                    <div className="mt-4 rounded-lg border border-border bg-surface px-3 py-3 text-xs leading-6 text-text-muted">
                      Approving any row below saves that raw label as a future alias for this club.
                    </div>
                  </div>

                  <div className="space-y-3">
                    {group.rows.map((row) => (
                      <div
                        key={row.id}
                        className="rounded-xl border border-border bg-bg/45 px-4 py-4"
                      >
                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <BookmakerBadge name={row.bookmaker_name ?? row.bookmaker_id} />
                              <span className="rounded-full border border-border bg-surface px-2 py-1 text-[11px] font-medium text-text-secondary">
                                {reasonLabel(row.reason_code)}
                              </span>
                              <span
                                className={`rounded-full border px-2 py-1 text-[11px] font-medium ${confidenceBadgeClass(
                                  row.confidence
                                )}`}
                              >
                                {row.confidence} confidence
                              </span>
                              {row.similarity_score != null && (
                                <span className="rounded-full border border-border bg-surface px-2 py-1 text-[11px] font-medium text-text-muted">
                                  score {row.similarity_score}
                                </span>
                              )}
                            </div>

                            <div className="mt-3">
                              <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                                Raw label
                              </div>
                              <div className="mt-2 text-base font-medium text-text">
                                {row.raw_team_name}
                              </div>
                              {row.normalized_raw_team_name !== row.raw_team_name && (
                                <div className="mt-1 text-xs text-text-muted">
                                  normalized: {row.normalized_raw_team_name}
                                </div>
                              )}
                              <div className="mt-2 text-xs text-text-muted">
                                {row.scope_league_name ?? row.raw_league_id}
                              </div>
                              <div className="mt-1 text-xs text-text-muted">
                                Seen {formatRelativeTime(row.scraped_at)}
                              </div>
                            </div>

                            {row.evidence.length > 0 && (
                              <ul className="mt-3 space-y-1.5">
                                {row.evidence.map((item) => (
                                  <li
                                    key={`${row.id}-${item}`}
                                    className="flex gap-2 text-sm leading-6 text-text-secondary"
                                  >
                                    <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-text-muted" />
                                    <span>{item}</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>

                          <div className="flex shrink-0 flex-col gap-2 lg:w-36">
                            <button
                              type="button"
                              onClick={() => onApprove(row.id)}
                              disabled={
                                !row.scope_league_id ||
                                approvingCaseId === row.id ||
                                decliningCaseId === row.id
                              }
                              className="rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {!row.scope_league_id
                                ? 'Needs scope'
                                : approvingCaseId === row.id
                                  ? 'Saving...'
                                  : 'Approve'}
                            </button>
                            <button
                              type="button"
                              onClick={() => onDecline(row.id)}
                              disabled={approvingCaseId === row.id || decliningCaseId === row.id}
                              className="rounded-md border border-border bg-transparent px-3 py-2 text-xs font-medium text-text-muted transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {decliningCaseId === row.id ? 'Skipping...' : 'Decline'}
                            </button>
                            {!row.scope_league_id && (
                              <p className="text-[11px] leading-5 text-text-muted">
                                Resolve the competition first before saving a permanent alias.
                              </p>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : (
        <section className="rounded-xl border border-border bg-surface p-5">
          <h3 className="text-sm font-semibold text-text">Review queue cleared</h3>
          <p className="mt-1 text-sm text-text-secondary">
            There are no pending team aliases left in this snapshot.
          </p>
        </section>
      )}

      {approvedRows.length > 0 && (
        <section className="overflow-hidden rounded-xl border border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-text">Approved in this snapshot</h3>
            <p className="mt-1 text-xs text-text-muted">
              These mappings are already saved for the next scrape.
            </p>
          </div>

          <div className="divide-y divide-border">
            {approvedRows.map((row) => (
              <div
                key={row.id}
                className="flex flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <BookmakerBadge name={row.bookmaker_name ?? row.bookmaker_id} />
                    <span className="rounded-full border border-accent/30 bg-accent/10 px-2 py-1 text-[11px] font-medium text-accent">
                      Approved
                    </span>
                  </div>
                  <div className="mt-2 text-sm text-text">
                    {row.raw_team_name} <span className="text-text-muted">{'->'}</span>{' '}
                    {row.suggested_team_name}
                  </div>
                  <div className="mt-1 text-xs text-text-muted">
                    {row.scope_league_name ?? row.raw_league_id}
                  </div>
                </div>

                <div className="text-xs text-text-muted md:text-right">
                  <div>{row.start_time ? formatDateTime(row.start_time) : 'Unknown start time'}</div>
                  <div className="mt-1">Seen {formatRelativeTime(row.scraped_at)}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
