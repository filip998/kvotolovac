import { useDeferredValue, useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { EventReviewCase, EventReviewStatus, EventReviewVariant } from '../api/types';
import { formatDateTime, formatRelativeTime } from '../utils/format';
import { buildSearchIndex, filterSearchIndex, normalizeSearchText } from '../utils/search';
import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import OfferSearchStrip from './OfferSearchStrip';

const STATUS_OPTIONS: { value: EventReviewStatus; label: string }[] = [
  { value: 'pending', label: 'Pending' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'declined', label: 'Declined' },
];

const REASON_LABELS: Record<string, string> = {
  candidate_event_equivalence: 'Possible same game',
  manual_event_equivalence: 'Manual event link',
  weak_event_equivalence: 'Weak event evidence',
};

function reasonLabel(reasonCode: string) {
  return REASON_LABELS[reasonCode] ?? reasonCode.replace(/_/g, ' ');
}

function eventLabel(row: EventReviewCase) {
  const home = row.primary_home_team ?? row.variants[0]?.source_home_team ?? row.variants[0]?.home_team;
  const away = row.primary_away_team ?? row.variants[0]?.source_away_team ?? row.variants[0]?.away_team;
  if (!home && !away) {
    return 'Unknown event';
  }
  return `${home ?? 'Unknown'} vs ${away ?? 'Unknown'}`;
}

function variantLabel(variant: EventReviewVariant) {
  const home = variant.source_home_team ?? variant.home_team;
  const away = variant.source_away_team ?? variant.away_team;
  return `${home} vs ${away}`;
}

function confidenceLabel(confidence: number | null) {
  if (confidence == null) {
    return 'unknown';
  }
  return `${Math.round(confidence * 100)}%`;
}

function statusBadgeClass(status: EventReviewStatus) {
  switch (status) {
    case 'accepted':
      return 'border-accent/35 bg-accent/[0.12] text-accent';
    case 'declined':
      return 'border-danger/30 bg-danger/10 text-danger';
    default:
      return 'border-warning/30 bg-warning/10 text-warning';
  }
}

function confidenceBadgeClass(confidence: number | null) {
  if (confidence == null) {
    return 'border-border bg-bg text-text-muted';
  }
  if (confidence >= 0.85) {
    return 'border-accent/30 bg-accent/10 text-accent';
  }
  if (confidence >= 0.65) {
    return 'border-warning/30 bg-warning/10 text-warning';
  }
  return 'border-danger/30 bg-danger/10 text-danger';
}

function statusTimestamp(row: EventReviewCase) {
  if (row.status === 'accepted') {
    return row.accepted_at ?? row.updated_at;
  }
  if (row.status === 'declined') {
    return row.declined_at ?? row.updated_at;
  }
  return row.updated_at ?? row.created_at;
}

export default function EventReviewPanel({
  rows,
  isLoading,
  errorMessage,
  searchQuery,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  onAccept,
  onDecline,
  acceptingCaseId,
  decliningCaseId,
  actionMessage,
}: {
  rows: EventReviewCase[];
  isLoading: boolean;
  errorMessage: string | null;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  statusFilter: EventReviewStatus;
  onStatusFilterChange: (status: EventReviewStatus) => void;
  onAccept: (caseId: number) => void;
  onDecline: (caseId: number) => void;
  acceptingCaseId: number | null;
  decliningCaseId: number | null;
  actionMessage: string | null;
}) {
  const appliedSearchQuery = useDeferredValue(searchQuery);
  const rowsForStatus = useMemo(
    () => rows.filter((row) => row.status === statusFilter),
    [rows, statusFilter]
  );
  const searchableRows = useMemo(
    () =>
      buildSearchIndex(rowsForStatus, (row) => [
        eventLabel(row),
        row.primary_league_name,
        row.reason_code,
        row.method,
        row.fingerprint,
        ...row.source_bookmaker_ids,
        ...row.source_league_labels,
        ...row.evidence,
        ...row.variants.flatMap((variant) => [
          variant.bookmaker_name,
          variant.bookmaker_id,
          variant.league_name,
          variant.source_league_name,
          variant.home_team,
          variant.away_team,
          variant.source_home_team,
          variant.source_away_team,
        ]),
      ]),
    [rowsForStatus]
  );
  const filteredRows = useMemo(
    () => filterSearchIndex(searchableRows, appliedSearchQuery),
    [appliedSearchQuery, searchableRows]
  );
  const sortedRows = useMemo(
    () =>
      [...filteredRows].sort(
        (left, right) =>
          (right.confidence ?? Number.NEGATIVE_INFINITY) -
            (left.confidence ?? Number.NEGATIVE_INFINITY) ||
          left.start_time.localeCompare(right.start_time) ||
          left.id - right.id
      ),
    [filteredRows]
  );
  const statusCounts = useMemo(
    () =>
      STATUS_OPTIONS.reduce<Record<EventReviewStatus, number>>(
        (acc, option) => {
          acc[option.value] = rows.filter((row) => row.status === option.value).length;
          return acc;
        },
        { pending: 0, accepted: 0, declined: 0 }
      ),
    [rows]
  );
  const hasSearchQuery = normalizeSearchText(appliedSearchQuery).length > 0;
  const activeSearchLabel = appliedSearchQuery.trim();
  const totalVariants = rows.reduce((sum, row) => sum + row.variants.length, 0);
  const uniqueBookmakers = new Set(
    rows.flatMap((row) => [
      ...row.source_bookmaker_ids,
      ...row.variants.map((variant) => variant.bookmaker_id).filter(Boolean),
    ])
  ).size;

  const searchStrip = (
    <OfferSearchStrip
      value={searchQuery}
      onChange={onSearchChange}
      scopeLabel="Event review"
      placeholder="Search games, bookmaker variants, leagues, or fingerprint evidence"
      resultCount={filteredRows.length}
      totalCount={rowsForStatus.length}
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
          <p className="text-sm text-danger">Failed to load event review: {errorMessage}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {searchStrip}

      <section className="overflow-hidden rounded-2xl border border-border bg-surface">
        <div className="relative p-5">
          <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-warning/70 via-accent/70 to-transparent" />
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <h3 className="text-base font-semibold text-text">Event equivalence desk</h3>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">
                Decide whether bookmaker source events are the same game. Accepting links event
                variants for comparison without merging canonical teams.
              </p>
            </div>
            <div className="rounded-lg border border-border bg-bg/60 px-3 py-2 text-xs text-text-muted">
              Event-only decisions · team labels stay separate
            </div>
          </div>

          {actionMessage && (
            <div className="mt-4 rounded-lg border border-accent/30 bg-accent/[0.08] px-4 py-3 text-sm text-text-secondary">
              {actionMessage}
            </div>
          )}

          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl border border-border bg-bg/60 px-4 py-3">
              <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Pending</div>
              <div className="mt-2 font-mono text-2xl font-semibold text-warning">
                {statusCounts.pending}
              </div>
            </div>
            <div className="rounded-xl border border-border bg-bg/60 px-4 py-3">
              <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Accepted</div>
              <div className="mt-2 font-mono text-2xl font-semibold text-accent">
                {statusCounts.accepted}
              </div>
            </div>
            <div className="rounded-xl border border-border bg-bg/60 px-4 py-3">
              <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Declined</div>
              <div className="mt-2 font-mono text-2xl font-semibold text-danger">
                {statusCounts.declined}
              </div>
            </div>
            <div className="rounded-xl border border-border bg-bg/60 px-4 py-3">
              <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Variants</div>
              <div className="mt-2 font-mono text-2xl font-semibold text-text-secondary">
                {totalVariants}
              </div>
              <div className="mt-1 text-[11px] text-text-muted">{uniqueBookmakers} books</div>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            {STATUS_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => onStatusFilterChange(option.value)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                  statusFilter === option.value
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                {option.label}
                <span className="ml-1.5 font-mono text-[11px] text-text-muted">
                  {statusCounts[option.value]}
                </span>
              </button>
            ))}
          </div>
        </div>
      </section>

      {hasSearchQuery && sortedRows.length === 0 && rowsForStatus.length > 0 ? (
        <EmptyState
          title={`No event candidates match "${activeSearchLabel}"`}
          message="Search checks primary event labels, source variants, leagues, bookmakers, and evidence."
        />
      ) : rowsForStatus.length === 0 ? (
        <EmptyState
          title={`No ${statusFilter} event candidates`}
          message="Event review decisions will appear here as resolver evidence creates candidates."
        />
      ) : (
        <section className="space-y-3">
          {sortedRows.map((row) => {
            const disabled = acceptingCaseId === row.id || decliningCaseId === row.id;
            const timestamp = statusTimestamp(row);

            return (
              <article
                key={row.id}
                className="overflow-hidden rounded-2xl border border-border bg-surface"
              >
                <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_22rem]">
                  <div className="min-w-0 p-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusBadgeClass(row.status)}`}>
                        {row.status}
                      </span>
                      <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${confidenceBadgeClass(row.confidence)}`}>
                        {confidenceLabel(row.confidence)} confidence
                      </span>
                      <span className="rounded-full border border-border bg-bg px-2.5 py-1 text-[11px] font-medium text-text-secondary">
                        {reasonLabel(row.reason_code)}
                      </span>
                      <span className="rounded-full border border-border bg-bg px-2.5 py-1 text-[11px] font-medium text-text-muted">
                        {row.method.replace(/_/g, ' ')}
                      </span>
                    </div>

                    <div className="mt-4">
                      <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">
                        Representative event
                      </div>
                      <h3 className="mt-1 text-xl font-semibold tracking-tight text-text">
                        {eventLabel(row)}
                      </h3>
                      <div className="mt-2 flex flex-wrap gap-2 text-xs text-text-muted">
                        <span>{row.primary_league_name ?? row.source_league_labels[0] ?? 'Unknown league'}</span>
                        <span>·</span>
                        <span>{formatDateTime(row.start_time)}</span>
                        <span>·</span>
                        <span>{row.variants.length} source variant{row.variants.length === 1 ? '' : 's'}</span>
                      </div>
                    </div>

                    <div className="mt-5 grid gap-2">
                      {row.variants.map((variant) => (
                        <div
                          key={`${row.id}-${variant.match_id}-${variant.bookmaker_id ?? 'unknown'}`}
                          className="rounded-xl border border-border bg-bg/45 px-3 py-3"
                        >
                          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <BookmakerBadge
                                  name={variant.bookmaker_name ?? variant.bookmaker_id ?? 'Unknown book'}
                                />
                                <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                                  {variant.orientation.replace(/_/g, ' ')}
                                </span>
                                {variant.confidence != null && (
                                  <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                                    {confidenceLabel(variant.confidence)}
                                  </span>
                                )}
                              </div>
                              <div className="mt-2 text-sm font-medium text-text">
                                {variantLabel(variant)}
                              </div>
                              <div className="mt-1 text-xs text-text-muted">
                                {variant.source_league_name ?? variant.league_name ?? 'Unknown league'} ·{' '}
                                {formatDateTime(variant.source_start_time ?? variant.start_time)}
                              </div>
                            </div>

                            <div className="flex shrink-0 items-center gap-3 text-xs">
                              {variant.source_url && (
                                <a
                                  href={variant.source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="font-medium text-text-muted transition hover:text-accent"
                                >
                                  Source ↗
                                </a>
                              )}
                              <Link
                                to={`/matches/${variant.match_id}`}
                                className="font-medium text-text-muted transition hover:text-accent"
                              >
                                Match →
                              </Link>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>

                    {row.evidence.length > 0 && (
                      <div className="mt-5">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">
                          Evidence
                        </div>
                        <ul className="mt-2 grid gap-1.5 md:grid-cols-2">
                          {row.evidence.map((item) => (
                            <li key={`${row.id}-${item}`} className="flex gap-2 text-sm leading-6 text-text-secondary">
                              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-warning" />
                              <span>{item}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>

                  <aside className="border-t border-border bg-bg/45 p-5 xl:border-l xl:border-t-0">
                    <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">
                      Decision controls
                    </div>
                    <div className="mt-3 rounded-xl border border-border bg-surface px-3 py-3">
                      <div className="text-xs text-text-muted">Fingerprint</div>
                      <div className="mt-1 break-all font-mono text-[11px] text-text-secondary">
                        {row.fingerprint}
                      </div>
                    </div>
                    <div className="mt-3 rounded-xl border border-border bg-surface px-3 py-3">
                      <div className="text-xs text-text-muted">
                        {row.status === 'pending' ? 'Last updated' : 'Decision time'}
                      </div>
                      <div className="mt-1 text-sm font-medium text-text">
                        {formatRelativeTime(timestamp)}
                      </div>
                      {row.resolved_event_id && (
                        <div className="mt-2 break-all font-mono text-[11px] text-text-muted">
                          {row.resolved_event_id}
                        </div>
                      )}
                    </div>

                    <div className="mt-4 space-y-2">
                      <button
                        type="button"
                        onClick={() => onAccept(row.id)}
                        disabled={disabled || row.status === 'accepted'}
                        className="w-full rounded-md border border-accent/50 bg-accent px-3 py-2 text-sm font-semibold text-bg transition hover:border-accent disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {acceptingCaseId === row.id
                          ? 'Linking event…'
                          : row.status === 'declined'
                            ? 'Accept anyway'
                            : row.status === 'accepted'
                              ? 'Already accepted'
                              : 'Accept as same game'}
                      </button>
                      <button
                        type="button"
                        onClick={() => onDecline(row.id)}
                        disabled={disabled || row.status === 'declined'}
                        className="w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm font-medium text-text-muted transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {decliningCaseId === row.id
                          ? 'Declining…'
                          : row.status === 'declined'
                            ? 'Already declined'
                            : 'Decline candidate'}
                      </button>
                    </div>
                  </aside>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
