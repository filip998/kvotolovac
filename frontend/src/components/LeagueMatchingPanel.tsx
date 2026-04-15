import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import type { MatchingReviewCase, MatchingReviewSummary } from '../api/types';
import { formatDateTime, formatRelativeTime } from '../utils/format';

const REASON_LABELS: Record<string, string> = {
  league_inferred_from_event_context: 'Matched from teams + exact time',
  league_conflict_resolved_by_event_context: 'Bookmakers disagree on the league',
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

export default function LeagueMatchingPanel({
  summary,
  cases,
  isLoading,
  errorMessage,
  onApprove,
  approvingCaseId,
  approvalMessage,
}: {
  summary: MatchingReviewSummary | undefined;
  cases: MatchingReviewCase[];
  isLoading: boolean;
  errorMessage: string | null;
  onApprove: (caseId: number, leagueId: string) => void;
  approvingCaseId: number | null;
  approvalMessage: string | null;
}) {
  if (isLoading) {
    return <LoadingSpinner />;
  }

  if (errorMessage) {
    return (
      <div className="rounded-lg border border-danger/30 bg-danger/10 p-6 text-center">
        <p className="text-sm text-danger">Failed to load league matching review: {errorMessage}</p>
      </div>
    );
  }

  const leagueHealth = summary?.leagues ?? [];
  const pendingCases = cases.filter((row) => row.status !== 'approved');
  const approvedCases = cases.filter((row) => row.status === 'approved');
  const approvedCount = approvedCases.length;
  const overviewItems = [
    {
      label: 'Tracked events',
      value: summary?.total_matches ?? 0,
      tone: 'text-text',
    },
    {
      label: 'Needs review',
      value: summary?.pending_reviews ?? 0,
      tone: 'text-warning',
    },
    {
      label: 'Saved aliases',
      value: summary?.approved_reviews ?? 0,
      tone: 'text-accent',
    },
    {
      label: 'Context matched',
      value: summary?.inferred_events ?? 0,
      tone: 'text-text-secondary',
    },
  ];

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-border bg-surface p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h3 className="text-base font-semibold text-text">League review queue</h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-text-secondary">
              Approve the suggested league when the teams and exact tip-off clearly describe the
              same event.
            </p>
          </div>
          <div className="text-xs text-text-muted">Saved aliases apply on the next scrape.</div>
        </div>

        {approvalMessage && (
          <div className="mt-4 rounded-lg border border-accent/30 bg-accent/[0.08] px-4 py-3 text-sm text-text-secondary">
            {approvalMessage}
          </div>
        )}

        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {overviewItems.map((item) => (
            <div key={item.label} className="rounded-lg border border-border bg-bg/60 px-4 py-3">
              <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                {item.label}
              </div>
              <div className={`mt-2 font-mono text-2xl font-semibold ${item.tone}`}>
                {item.value}
              </div>
            </div>
          ))}
        </div>

        {approvedCount > 0 && (
          <p className="mt-4 text-xs text-text-muted">
            {approvedCount} suggestion{approvedCount === 1 ? '' : 's'} already approved in this
            snapshot.
          </p>
        )}
      </section>

      {cases.length === 0 ? (
        <EmptyState
          title="No league review actions in this snapshot"
          message="The current snapshot did not produce any reviewable league suggestions."
        />
      ) : pendingCases.length > 0 ? (
        <section className="space-y-3">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-text">Needs review</h3>
              <p className="mt-1 text-sm text-text-secondary">
                Each suggestion is based on the team pairing and exact start time in the current
                snapshot.
              </p>
            </div>
            <div className="text-xs text-text-muted">
              {pendingCases.length} open suggestion{pendingCases.length === 1 ? '' : 's'}
            </div>
          </div>

          <div className="space-y-3">
            {pendingCases.map((row) => (
              <article key={row.id} className="rounded-xl border border-border bg-surface p-4">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                  <div className="min-w-0 flex-1 space-y-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <BookmakerBadge name={row.bookmaker_name ?? row.bookmaker_id} />
                      <span className="rounded-full border border-border bg-bg px-2 py-1 text-[11px] font-medium text-text-secondary">
                        {reasonLabel(row.reason_code)}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-1 text-[11px] font-medium ${confidenceBadgeClass(
                          row.confidence
                        )}`}
                      >
                        {row.confidence} confidence
                      </span>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2">
                      <div className="rounded-lg border border-border bg-bg/40 px-3 py-3">
                        <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                          Raw league
                        </div>
                        <div className="mt-2 font-medium text-text">{row.raw_league_id}</div>
                        <div className="mt-1 text-xs text-text-muted">
                          {row.normalized_raw_league_id}
                        </div>
                      </div>

                      <div className="rounded-lg border border-border bg-bg/40 px-3 py-3">
                        <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                          Suggested league
                        </div>
                        <div className="mt-2 font-medium text-text">
                          {row.suggested_league_name ?? row.suggested_league_id}
                        </div>
                        <div className="mt-1 text-xs text-text-muted">
                          {row.suggested_league_id}
                        </div>
                      </div>
                    </div>

                    <div className="rounded-lg border border-border bg-bg/60 px-3 py-3">
                      <div className="font-medium text-text">
                        {row.home_team} vs {row.away_team}
                      </div>
                      <div className="mt-1 text-xs text-text-muted">
                        {row.start_time ? formatDateTime(row.start_time) : 'Unknown start time'}
                      </div>
                      <div className="mt-1 text-xs text-text-muted">
                        Seen {formatRelativeTime(row.scraped_at)}
                      </div>
                    </div>

                    {row.evidence.length > 0 && (
                      <div>
                        <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                          Why it was matched
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

                  <div className="flex shrink-0 flex-col gap-2 xl:w-40">
                    <button
                      type="button"
                      onClick={() => onApprove(row.id, row.suggested_league_id)}
                      disabled={approvingCaseId === row.id}
                      className="rounded-md border border-border bg-bg px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {approvingCaseId === row.id ? 'Saving...' : 'Approve alias'}
                    </button>
                    <p className="text-[11px] leading-5 text-text-muted">
                      This saves the bookmaker label for future scrapes.
                    </p>
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
            Everything in this snapshot is already approved and saved for the next scrape.
          </p>
        </section>
      )}

      {approvedCases.length > 0 && (
        <section className="overflow-hidden rounded-xl border border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-text">Approved in this snapshot</h3>
            <p className="mt-1 text-xs text-text-muted">
              These aliases are already saved and will apply on the next scrape.
            </p>
          </div>

          <div className="divide-y divide-border">
            {approvedCases.map((row) => (
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
                    {row.raw_league_id}{' '}
                    <span className="text-text-muted">{'->'}</span>{' '}
                    {row.suggested_league_name ?? row.suggested_league_id}
                  </div>
                  <div className="mt-1 text-xs text-text-muted">
                    {row.home_team} vs {row.away_team}
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

      {leagueHealth.length > 0 ? (
        <section className="overflow-hidden rounded-xl border border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-text">Snapshot by league</h3>
            <p className="mt-1 text-xs text-text-muted">
              Quick coverage check for the current snapshot, including pending and approved alias
              work.
            </p>
          </div>

          <div className="divide-y divide-border">
            {leagueHealth.map((league) => (
              <div
                key={league.league_id}
                className="flex flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between"
              >
                <div>
                  <div className="font-medium text-text">{league.league_name}</div>
                  <div className="text-[11px] text-text-muted">{league.league_id}</div>
                </div>

                <div className="grid grid-cols-3 gap-3 text-right md:min-w-[18rem]">
                  <div>
                    <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                      Matched
                    </div>
                    <div className="mt-1 font-mono text-sm text-text-secondary">
                      {league.matched_events}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                      Pending
                    </div>
                    <div className="mt-1 font-mono text-sm text-warning">
                      {league.pending_reviews}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
                      Approved
                    </div>
                    <div className="mt-1 font-mono text-sm text-accent">
                      {league.approved_reviews}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <EmptyState
          title="No league health data yet"
          message="Trigger a scrape to populate league matching coverage and review counts."
        />
      )}
    </div>
  );
}
