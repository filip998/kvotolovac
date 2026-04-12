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
      <div className="border-b border-line-700/70 bg-ink-900/55 px-4 py-4 backdrop-blur">
        <div className="mx-auto grid max-w-7xl gap-3 sm:grid-cols-3 sm:px-2">
          <div className="h-24 animate-pulse rounded-[24px] border border-line-700/80 bg-ink-850/80" />
          <div className="h-24 animate-pulse rounded-[24px] border border-line-700/80 bg-ink-850/80" />
          <div className="h-24 animate-pulse rounded-[24px] border border-line-700/80 bg-ink-850/80" />
        </div>
      </div>
    );
  }

  const metrics = [
    {
      label: 'Active discrepancies',
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
      label: 'Tracked matches',
      value: String(status.total_matches ?? 0),
    },
  ];

  return (
    <div className="border-b border-line-700/70 bg-ink-900/70 px-4 py-4">
      <div className="mx-auto max-w-7xl space-y-4 sm:px-2">
        <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_260px]">
          <div className="grid gap-3 md:grid-cols-3">
            {metrics.map((metric) => (
              <div
                key={metric.label}
                className="rounded-xl border border-line-700/70 bg-ink-850 px-4 py-4"
              >
                <p className="text-xs text-slate-400">{metric.label}</p>
                <p className="mt-1 text-2xl font-semibold text-white">{metric.value}</p>
              </div>
            ))}
          </div>

          <div className="rounded-xl border border-line-700/70 bg-ink-850 px-4 py-4">
            <div className="flex h-full flex-col justify-between gap-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs text-slate-400">Scheduler</p>
                  <p className="mt-2 text-sm text-slate-300">
                    {scanProgress?.in_progress
                      ? `${scanProgress.phase} · ${scanProgress.completed_tasks}/${scanProgress.total_tasks} tasks`
                      : 'Ready for manual or scheduled scans'}
                  </p>
                </div>
                <span
                  className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium ${
                    scanProgress?.in_progress
                      ? 'border-line-600 bg-white/[0.04] text-white'
                      : 'border-line-700/70 bg-ink-950 text-slate-300'
                  }`}
                >
                  <span
                    className={`h-2 w-2 rounded-full ${
                      scanProgress?.in_progress ? 'bg-white animate-pulse' : 'bg-slate-400'
                    }`}
                  />
                  {scanProgress?.in_progress ? 'Scanning' : 'Online'}
                </span>
              </div>

              <button
                onClick={() => triggerScrape.mutate()}
                disabled={triggerScrape.isPending || scanProgress?.in_progress}
                className="inline-flex items-center justify-center rounded-lg bg-white px-4 py-2.5 text-sm font-medium text-black transition hover:bg-brand-200 disabled:cursor-not-allowed disabled:opacity-55"
              >
                {scanProgress?.in_progress
                  ? `Scanning ${scanPercent}%`
                  : triggerScrape.isPending
                    ? 'Starting scan...'
                    : 'Run fresh scan'}
              </button>
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-line-700/70 bg-ink-850 px-4 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <p className="mr-2 text-xs text-slate-400">Bookmakers</p>
            {(status.bookmaker_status ?? []).map((bm) => (
              <div
                key={bm.id}
                title={`${bm.name}: ${bm.is_active ? 'Active' : 'Inactive'} · ${formatRelativeTime(bm.last_scrape)}`}
                className="relative rounded-lg border border-line-700/60 bg-ink-900 px-2 py-2"
              >
                <span
                  className={`absolute right-2 top-2 h-2.5 w-2.5 rounded-full border border-ink-900 ${
                    bm.is_active ? 'bg-white' : 'bg-rose-300'
                  }`}
                />
                <BookmakerBadge name={bm.name} compact />
              </div>
            ))}
            {(!status.bookmaker_status || status.bookmaker_status.length === 0) && (
              <div className="text-sm text-slate-400">
                {status.active_bookmakers} bookmakers registered
              </div>
            )}
          </div>

          {scanProgress?.in_progress && (
            <div
              className="mt-4 overflow-hidden rounded-full border border-line-700/70 bg-ink-900/80"
            >
              <div
                className="h-2 rounded-full bg-gradient-to-r from-brand-100 via-brand-300 to-brand-500 transition-all"
                style={{ width: `${Math.max(scanPercent, 5)}%` }}
              />
            </div>
          )}
          {scanProgress?.in_progress && (
            <p className="mt-2 text-xs text-slate-500">
              Scan started {formatRelativeTime(scanProgress.started_at)}
              {scanProgress.failed_tasks > 0 ? ` · ${scanProgress.failed_tasks} failed tasks` : ''}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
