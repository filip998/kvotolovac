import { useSystemStatus, useTriggerScrape } from '../api/hooks';
import { formatRelativeTime } from '../utils/format';

export default function StatusBar() {
  const { data: status, isLoading } = useSystemStatus();
  const triggerScrape = useTriggerScrape();

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
            <span className="font-semibold text-white">{status.active_discrepancies}</span>
            <span className="text-gray-400">active discrepancies</span>
          </span>
          <span className="hidden text-gray-600 sm:inline">|</span>
          <span className="text-gray-400">
            Last scan: <span className="text-gray-300">{formatRelativeTime(status.last_scrape)}</span>
          </span>
          <span className="hidden text-gray-600 sm:inline">|</span>
          <span className="text-gray-400">
            <span className="text-gray-300">{status.total_matches}</span> matches tracked
          </span>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-1.5 sm:flex">
            {status.bookmaker_status.map((bm) => (
              <span
                key={bm.id}
                title={`${bm.name}: ${bm.is_active ? 'Active' : 'Inactive'} — ${formatRelativeTime(bm.last_scrape)}`}
                className={`inline-block h-2 w-2 rounded-full ${bm.is_active ? 'bg-green-400' : 'bg-red-400'}`}
              />
            ))}
          </div>
          <button
            onClick={() => triggerScrape.mutate()}
            disabled={triggerScrape.isPending}
            className="rounded-md bg-brand-600/20 px-3 py-1 text-xs font-medium text-brand-400 transition hover:bg-brand-600/30 disabled:opacity-50"
          >
            {triggerScrape.isPending ? 'Scanning...' : '↻ Scan Now'}
          </button>
        </div>
      </div>
    </div>
  );
}
