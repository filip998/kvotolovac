import { useSystemStatus, useTriggerScrape } from '../api/hooks';
import { formatRelativeTime } from '../utils/format';
import BookmakerBadge from './BookmakerBadge';

export default function StatusBar() {
  const { data: status, isLoading } = useSystemStatus();
  const triggerScrape = useTriggerScrape();

  const scanProgress = status?.scan;
  const scanPercent =
    scanProgress && scanProgress.total_tasks > 0
      ? Math.round((scanProgress.completed_tasks / scanProgress.total_tasks) * 100)
      : 0;

  if (isLoading || !status) {
    return (
      <div className="border-b border-border bg-surface px-5 py-3">
        <div className="mx-auto flex max-w-7xl gap-6 sm:px-1">
          <div className="h-12 w-32 animate-pulse rounded-md bg-surface-raised" />
          <div className="h-12 w-32 animate-pulse rounded-md bg-surface-raised" />
          <div className="h-12 w-32 animate-pulse rounded-md bg-surface-raised" />
        </div>
      </div>
    );
  }

  const metrics = [
    {
      label: 'Discrepancies',
      value: String(status.active_discrepancies ?? status.total_discrepancies ?? 0),
    },
    {
      label: 'Last scan',
      value:
        status.last_scrape_at || status.last_scrape
          ? formatRelativeTime(status.last_scrape_at ?? status.last_scrape)
          : 'Warming up',
    },
    {
      label: 'Matches',
      value: String(status.total_matches ?? 0),
    },
  ];

  return (
    <div className="border-b border-border bg-surface px-5 py-3">
      <div className="mx-auto max-w-7xl space-y-3 sm:px-1">
        {/* Top row: metrics + scan button */}
        <div className="flex flex-wrap items-center gap-6">
          {metrics.map((metric) => (
            <div key={metric.label} className="flex items-baseline gap-2">
              <span className="font-mono text-lg font-semibold text-text">{metric.value}</span>
              <span className="text-xs text-text-muted">{metric.label}</span>
            </div>
          ))}

          <div className="ml-auto flex items-center gap-3">
            <span
              className={`flex items-center gap-1.5 text-xs ${
                scanProgress?.in_progress ? 'text-accent' : 'text-text-muted'
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  scanProgress?.in_progress ? 'bg-accent animate-pulse' : 'bg-text-muted'
                }`}
              />
              {scanProgress?.in_progress
                ? `${scanProgress.phase} · ${scanProgress.completed_tasks}/${scanProgress.total_tasks}`
                : 'Idle'}
            </span>
            <button
              onClick={() => triggerScrape.mutate()}
              disabled={triggerScrape.isPending || scanProgress?.in_progress}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-bg transition hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-50"
            >
              {scanProgress?.in_progress
                ? `${scanPercent}%`
                : triggerScrape.isPending
                  ? 'Starting…'
                  : 'Scan now'}
            </button>
          </div>
        </div>

        {/* Bookmakers row */}
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[11px] font-medium uppercase tracking-wider text-text-muted">Books</span>
          {(status.bookmaker_status ?? []).map((bm) => (
            <div
              key={bm.id}
              title={`${bm.name}: ${bm.is_active ? 'Active' : 'Inactive'} · ${formatRelativeTime(bm.last_scrape)}`}
              className="relative"
            >
              <BookmakerBadge name={bm.name} compact />
              <span
                className={`absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full ${
                  bm.is_active ? 'bg-accent' : 'bg-danger'
                }`}
              />
            </div>
          ))}
          {(!status.bookmaker_status || status.bookmaker_status.length === 0) && (
            <span className="text-xs text-text-muted">
              {status.active_bookmakers} registered
            </span>
          )}
        </div>

        {/* Scan progress bar */}
        {scanProgress?.in_progress && (
          <div>
            <div className="h-0.5 overflow-hidden rounded-full bg-surface-raised">
              <div
                className="h-full rounded-full bg-accent transition-all"
                style={{ width: `${Math.max(scanPercent, 3)}%` }}
              />
            </div>
            <p className="mt-1 text-[11px] text-text-muted">
              Started {formatRelativeTime(scanProgress.started_at)}
              {scanProgress.failed_tasks > 0 ? ` · ${scanProgress.failed_tasks} failed` : ''}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
