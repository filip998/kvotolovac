import { useSystemStatus, useTriggerScrape } from '../api/hooks';
import { formatRelativeTime } from '../utils/format';

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
      <div className="border-b border-gray-800 bg-gray-900/50 px-4 py-2.5">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <div className="h-4 w-48 animate-pulse rounded bg-gray-800" />
          <div className="h-4 w-24 animate-pulse rounded bg-gray-800" />
        </div>
      </div>
    );
  }

  return (
    <div className="border-b border-gray-800 bg-gray-900/50 px-4 py-2.5">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full bg-green-400 animate-pulse" />
            <span className="font-semibold text-white">{status.active_discrepancies ?? status.total_discrepancies}</span>
            <span className="text-gray-400">active discrepancies</span>
          </span>
          <span className="hidden text-gray-600 sm:inline">|</span>
          <span className="text-gray-400">
            Last scan:{' '}
            <span className="text-gray-300">
              {status.last_scrape_at || status.last_scrape
                ? formatRelativeTime(status.last_scrape_at ?? status.last_scrape)
                : 'still warming up'}
            </span>
          </span>
          <span className="hidden text-gray-600 sm:inline">|</span>
          <span className="text-gray-400">
            <span className="text-gray-300">{status.total_matches}</span> matches tracked
          </span>
          {scanProgress?.in_progress && (
            <>
              <span className="hidden text-gray-600 sm:inline">|</span>
              <span className="text-brand-300">
                Scan {scanProgress.completed_tasks}/{scanProgress.total_tasks}
                {scanProgress.failed_tasks > 0 ? ` · ${scanProgress.failed_tasks} failed` : ''}
                {' · '}
                {scanProgress.phase}
              </span>
            </>
          )}
        </div>

        <div className="flex items-center gap-3">
          {status.bookmaker_status && status.bookmaker_status.length > 0 && (
            <div className="hidden items-center gap-1.5 sm:flex">
              {status.bookmaker_status.map((bm) => (
                <span
                  key={bm.id}
                  title={`${bm.name}: ${bm.is_active ? 'Active' : 'Inactive'} — ${formatRelativeTime(bm.last_scrape)}`}
                  className={`inline-block h-2 w-2 rounded-full ${bm.is_active ? 'bg-green-400' : 'bg-red-400'}`}
                />
              ))}
            </div>
          )}
          <button
            onClick={() => triggerScrape.mutate()}
            disabled={triggerScrape.isPending || scanProgress?.in_progress}
            className="rounded-md bg-brand-600/20 px-3 py-1 text-xs font-medium text-brand-400 transition hover:bg-brand-600/30 disabled:opacity-50"
          >
            {scanProgress?.in_progress
              ? `Scanning ${scanPercent}%`
              : triggerScrape.isPending
                ? 'Scanning...'
                : '↻ Scan Now'}
          </button>
        </div>
      </div>
      {scanProgress?.in_progress && (
        <div className="mx-auto mt-2 max-w-6xl">
          <div className="h-1.5 overflow-hidden rounded-full bg-gray-800">
            <div
              className="h-full rounded-full bg-brand-400 transition-all"
              style={{ width: `${Math.max(scanPercent, 5)}%` }}
            />
          </div>
          <p className="mt-1 text-xs text-gray-400">
            Scan started {formatRelativeTime(scanProgress.started_at)}. Progress updates as each
            bookmaker finishes.
          </p>
        </div>
      )}
    </div>
  );
}
