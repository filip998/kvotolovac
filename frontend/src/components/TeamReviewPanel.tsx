import { useDeferredValue, useMemo, useState } from 'react';
import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import OfferSearchStrip from './OfferSearchStrip';
import type { TeamReviewCase } from '../api/types';
import { formatDateTime, formatRelativeTime } from '../utils/format';
import { buildSearchIndex, filterSearchIndex, normalizeSearchText } from '../utils/search';

const REASON_LABELS: Record<string, string> = {
  candidate_team_match_same_start_time: 'Same kickoff with usable event context',
  candidate_team_search: 'Top fuzzy matches in this sport',
};

const REVIEW_KIND_LABELS: Record<string, string> = {
  alias_suggestion: 'One-click suggestion',
  candidate_search: 'Manual pick required',
};

function reasonLabel(reasonCode: string) {
  return REASON_LABELS[reasonCode] ?? reasonCode.replace(/_/g, ' ');
}

function reviewKindLabel(reviewKind: string) {
  return REVIEW_KIND_LABELS[reviewKind] ?? reviewKind.replace(/_/g, ' ');
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
  onApprove: (
    caseId: number,
    payload?: { teamId?: number; createTeamName?: string }
  ) => void;
  onDecline: (caseId: number) => void;
  approvingCaseId: number | null;
  decliningCaseId: number | null;
  actionMessage: string | null;
}) {
  const [createTeamNames, setCreateTeamNames] = useState<Record<number, string>>({});
  const appliedSearchQuery = useDeferredValue(searchQuery);
  const searchableRows = useMemo(
    () =>
      buildSearchIndex(rows, (row) => [
        row.suggested_team_name,
        row.raw_team_name,
        row.normalized_raw_team_name,
        row.scope_league_name,
        row.bookmaker_name,
        row.matched_counterpart_team,
        row.canonical_home_team,
        row.canonical_away_team,
        row.sport,
        ...row.candidate_teams.map((candidate) => candidate.team_name),
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
  const uniqueSuggestedTeams = new Set(
    rows
      .map((row) => row.suggested_team_name)
      .filter((value): value is string => Boolean(value))
  ).size;

  const searchStrip = (
    <OfferSearchStrip
      value={searchQuery}
      onChange={onSearchChange}
      scopeLabel="Teams"
      placeholder="Search raw labels, canonical teams, or candidate teams"
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
          message="Search checks the raw label, suggested canonical team, and every candidate team."
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
              Each row can be resolved directly: save the suggested team, choose another candidate,
              or create a new canonical team inline.
            </p>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-3 py-2 text-xs text-text-muted">
            Sport + exact kickoff + teams drive matching
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
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
              Suggested teams
            </div>
            <div className="mt-2 font-mono text-2xl font-semibold text-text-secondary">
              {uniqueSuggestedTeams}
            </div>
          </div>
        </div>
      </section>

      {pendingRows.length > 0 ? (
        <section className="space-y-3">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-text">Needs review</h3>
              <p className="mt-1 text-sm text-text-secondary">
                Use the strongest candidate when it fits, or create a new canonical team when none
                of the suggestions are correct.
              </p>
            </div>
            <div className="text-xs text-text-muted">
              {pendingRows.length} open case{pendingRows.length === 1 ? '' : 's'}
            </div>
          </div>

          <div className="space-y-3">
            {pendingRows.map((row) => {
              const createTeamName = createTeamNames[row.id] ?? '';
              const alternateCandidates = row.candidate_teams.filter(
                (candidate) => candidate.team_id !== row.suggested_team_id
              );
              const disableActions =
                approvingCaseId === row.id || decliningCaseId === row.id;

              return (
                <article key={row.id} className="rounded-xl border border-border bg-surface p-4">
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
                    <div className="min-w-0 space-y-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <BookmakerBadge name={row.bookmaker_name ?? row.bookmaker_id} />
                        <span className="rounded-full border border-border bg-bg px-2 py-1 text-[11px] font-medium text-text-secondary">
                          {reasonLabel(row.reason_code)}
                        </span>
                        <span className="rounded-full border border-border bg-bg px-2 py-1 text-[11px] font-medium text-text-muted">
                          {reviewKindLabel(row.review_kind)}
                        </span>
                        <span
                          className={`rounded-full border px-2 py-1 text-[11px] font-medium ${confidenceBadgeClass(
                            row.confidence
                          )}`}
                        >
                          {row.confidence} confidence
                        </span>
                        {row.similarity_score != null && (
                          <span className="rounded-full border border-border bg-bg px-2 py-1 text-[11px] font-medium text-text-muted">
                            score {Math.round(row.similarity_score)}
                          </span>
                        )}
                      </div>

                      <div className="grid gap-3 md:grid-cols-2">
                        <div className="rounded-lg border border-border bg-bg/45 px-3 py-3">
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
                          <div className="mt-1 text-xs text-text-muted">{row.sport}</div>
                        </div>

                        <div className="rounded-lg border border-border bg-bg/45 px-3 py-3">
                          <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                            Event context
                          </div>
                          <div className="mt-2 text-sm font-medium text-text">
                            {row.start_time ? formatDateTime(row.start_time) : 'Unknown exact start time'}
                          </div>
                          <div className="mt-1 text-xs text-text-muted">
                            Seen {formatRelativeTime(row.scraped_at)}
                          </div>
                          {row.matched_counterpart_team && (
                            <div className="mt-2 text-xs text-text-secondary">
                              Matched other team: {row.matched_counterpart_team}
                            </div>
                          )}
                          {(row.canonical_home_team || row.canonical_away_team) && (
                            <div className="mt-1 text-xs text-text-secondary">
                              Canonical event:{' '}
                              {row.canonical_home_team ?? 'Unknown'} vs {row.canonical_away_team ?? 'Unknown'}
                            </div>
                          )}
                        </div>
                      </div>

                      {row.evidence.length > 0 && (
                        <div>
                          <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                            Evidence
                          </div>
                          <ul className="mt-2 space-y-1.5">
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
                        </div>
                      )}
                    </div>

                    <div className="space-y-3 rounded-xl border border-border bg-bg/35 p-4">
                      <div>
                        <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                          Suggested action
                        </div>
                        {row.suggested_team_name ? (
                          <>
                            <div className="mt-2 text-base font-semibold text-text">
                              {row.suggested_team_name}
                            </div>
                            <button
                              type="button"
                              onClick={() =>
                                onApprove(
                                  row.id,
                                  row.suggested_team_id != null
                                    ? { teamId: row.suggested_team_id }
                                    : undefined
                                )
                              }
                              disabled={disableActions}
                              className="mt-3 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {approvingCaseId === row.id
                                ? 'Saving...'
                                : `Save as ${row.suggested_team_name}`}
                            </button>
                          </>
                        ) : (
                          <p className="mt-2 text-sm text-text-secondary">
                            No default suggestion was strong enough. Pick one of the candidates below
                            or create a new canonical team.
                          </p>
                        )}
                      </div>

                      {alternateCandidates.length > 0 && (
                        <div>
                          <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                            Other candidates
                          </div>
                          <div className="mt-2 space-y-2">
                            {alternateCandidates.map((candidate) => (
                              <button
                                key={`${row.id}-${candidate.team_id}`}
                                type="button"
                                onClick={() => onApprove(row.id, { teamId: candidate.team_id })}
                                disabled={disableActions}
                                className="flex w-full items-center justify-between rounded-md border border-border bg-surface px-3 py-2 text-left text-sm text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                <span className="min-w-0 truncate">{candidate.team_name}</span>
                                <span className="ml-3 shrink-0 text-[11px] text-text-muted">
                                  {candidate.score != null ? Math.round(candidate.score) : '—'}
                                </span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      <div>
                        <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                          Create new canonical team
                        </div>
                        <input
                          type="text"
                          value={createTeamName}
                          onChange={(event) =>
                            setCreateTeamNames((current) => ({
                              ...current,
                              [row.id]: event.target.value,
                            }))
                          }
                          placeholder="Enter canonical team name"
                          disabled={disableActions}
                          className="mt-2 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition placeholder:text-text-muted focus:border-border-hover disabled:cursor-not-allowed disabled:opacity-60"
                        />
                        <button
                          type="button"
                          onClick={() =>
                            onApprove(row.id, {
                              createTeamName: createTeamName.trim(),
                            })
                          }
                          disabled={disableActions || createTeamName.trim().length === 0}
                          className="mt-2 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {approvingCaseId === row.id ? 'Saving...' : 'Create and save alias'}
                        </button>
                      </div>

                      <button
                        type="button"
                        onClick={() => onDecline(row.id)}
                        disabled={disableActions}
                        className="w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm font-medium text-text-muted transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {decliningCaseId === row.id ? 'Skipping...' : 'Decline for now'}
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
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
                    {row.suggested_team_name ?? 'Saved canonical team'}
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
