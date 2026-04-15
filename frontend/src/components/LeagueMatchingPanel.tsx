import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import type { MatchingReviewCase, MatchingReviewSummary } from '../api/types';
import { formatDateTime, formatRelativeTime } from '../utils/format';

const REASON_LABELS: Record<string, string> = {
  league_inferred_from_event_context: 'Inferred from teams + exact time',
  league_conflict_resolved_by_event_context: 'Conflicting league labels on the same event',
};

function reasonLabel(reasonCode: string) {
  return REASON_LABELS[reasonCode] ?? reasonCode.replace(/_/g, ' ');
}

function confidenceTone(confidence: string) {
  switch (confidence) {
    case 'high':
      return 'text-accent';
    case 'low':
      return 'text-warning';
    default:
      return 'text-text-secondary';
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

  return (
    <div className="space-y-6">
      {approvalMessage && (
        <div className="rounded-lg border border-accent/30 bg-accent/[0.08] px-4 py-3 text-sm text-text-secondary">
          {approvalMessage}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-4">
        {[
          {
            label: 'Tracked matches',
            value: summary?.total_matches ?? 0,
            tone: 'text-text',
          },
          {
            label: 'Pending reviews',
            value: summary?.pending_reviews ?? 0,
            tone: 'text-warning',
          },
          {
            label: 'Approved aliases',
            value: summary?.approved_reviews ?? 0,
            tone: 'text-accent',
          },
          {
            label: 'Inferred events',
            value: summary?.inferred_events ?? 0,
            tone: 'text-text-secondary',
          },
        ].map((item) => (
          <div
            key={item.label}
            className="rounded-lg border border-border bg-surface px-4 py-3"
          >
            <div className="text-[11px] uppercase tracking-wider text-text-muted">{item.label}</div>
            <div className={`mt-2 font-mono text-2xl font-semibold ${item.tone}`}>{item.value}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-border bg-surface p-4 text-sm text-text-secondary">
        Approvals are saved into the backend league registry. Run the next scrape to apply the
        new alias to live matching.
      </div>

      {summary && summary.leagues.length > 0 ? (
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-text">League matching health</h3>
            <p className="mt-1 text-xs text-text-muted">
              Current snapshot coverage plus pending or approved review actions by league.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
                  <th className="px-4 py-2.5 text-left">League</th>
                  <th className="px-4 py-2.5 text-right">Matched</th>
                  <th className="px-4 py-2.5 text-right">Pending</th>
                  <th className="px-4 py-2.5 text-right">Approved</th>
                </tr>
              </thead>
              <tbody>
                {summary.leagues.map((league) => (
                  <tr key={league.league_id} className="border-t border-border">
                    <td className="px-4 py-3">
                      <div className="font-medium text-text">{league.league_name}</div>
                      <div className="text-[11px] text-text-muted">{league.league_id}</div>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">
                      {league.matched_events}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-warning">
                      {league.pending_reviews}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-accent">
                      {league.approved_reviews}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <EmptyState
          title="No league health data yet"
          message="Trigger a scrape to populate league matching coverage and review counts."
        />
      )}

      {cases.length === 0 ? (
        <EmptyState
          title="No league review actions in this snapshot"
          message="The current snapshot did not produce any reviewable league suggestions."
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <div className="border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-text">Review suggestions</h3>
            <p className="mt-1 text-xs text-text-muted">
              Approve a suggestion to save the raw bookmaker league label as a future alias.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
                  <th className="px-4 py-2.5 text-left">Bookmaker / Raw league</th>
                  <th className="px-4 py-2.5 text-left">Suggested league</th>
                  <th className="px-4 py-2.5 text-left">Event</th>
                  <th className="hidden px-4 py-2.5 text-left xl:table-cell">Evidence</th>
                  <th className="px-4 py-2.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((row) => (
                  <tr
                    key={row.id}
                    className="border-t border-border align-top transition hover:bg-surface-raised"
                  >
                    <td className="px-4 py-3">
                      <BookmakerBadge name={row.bookmaker_name ?? row.bookmaker_id} />
                      <div className="mt-2 font-medium text-text">{row.raw_league_id}</div>
                      <div className="text-[11px] text-text-muted">{row.normalized_raw_league_id}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-text">
                        {row.suggested_league_name ?? row.suggested_league_id}
                      </div>
                      <div className="text-[11px] text-text-muted">{row.suggested_league_id}</div>
                      <div className={`mt-1 text-[11px] font-medium ${confidenceTone(row.confidence)}`}>
                        {row.confidence} confidence
                      </div>
                      <div className="mt-1 text-[11px] text-text-muted">
                        {reasonLabel(row.reason_code)}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-text">
                        {row.home_team} vs {row.away_team}
                      </div>
                      <div className="text-[11px] text-text-muted">
                        {row.start_time ? formatDateTime(row.start_time) : 'Unknown start time'}
                      </div>
                      <div className="mt-1 text-[11px] text-text-muted">
                        Seen {formatRelativeTime(row.scraped_at)}
                      </div>
                    </td>
                    <td className="hidden px-4 py-3 xl:table-cell">
                      <div className="space-y-1">
                        {row.evidence.map((item) => (
                          <div
                            key={`${row.id}-${item}`}
                            className="text-[11px] leading-5 text-text-secondary"
                          >
                            {item}
                          </div>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {row.status === 'approved' ? (
                        <span className="inline-flex rounded-full bg-accent/15 px-2.5 py-1 text-[11px] font-medium text-accent">
                          Approved
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onApprove(row.id, row.suggested_league_id)}
                          disabled={approvingCaseId === row.id}
                          className="rounded-md border border-border bg-bg px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {approvingCaseId === row.id ? 'Saving...' : 'Approve alias'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
