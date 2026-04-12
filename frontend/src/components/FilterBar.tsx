import { MARKET_TYPES, MARKET_TYPE_LABELS } from '../utils/constants';
import { useLeagues } from '../api/hooks';
import type { DiscrepancyFilters, MarketType } from '../api/types';

interface FilterBarProps {
  filters: DiscrepancyFilters;
  onChange: (filters: DiscrepancyFilters) => void;
}

export default function FilterBar({ filters, onChange }: FilterBarProps) {
  const { data: leagues } = useLeagues();

  const update = (patch: Partial<DiscrepancyFilters>) => {
    onChange({ ...filters, ...patch });
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.15fr)_auto]">
      <div>
        <label className="mb-2 block text-sm font-medium text-slate-300">
          League
        </label>
        <select
          value={filters.league || ''}
          onChange={(e) => update({ league: e.target.value || undefined })}
          className="w-full rounded-lg border border-line-700/70 bg-ink-950 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-line-500"
        >
          <option value="">All Leagues</option>
          {leagues?.map((l) => (
            <option key={l.id} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="mb-2 block text-sm font-medium text-slate-300">
          Market
        </label>
        <select
          value={filters.market_type || ''}
          onChange={(e) =>
            update({
              market_type: e.target.value ? (e.target.value as MarketType) : undefined,
              })
          }
          className="w-full rounded-lg border border-line-700/70 bg-ink-950 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-line-500"
        >
          <option value="">All Markets</option>
          {MARKET_TYPES.map((marketType) => (
            <option key={marketType} value={marketType}>
              {MARKET_TYPE_LABELS[marketType]}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="mb-2 flex items-center justify-between text-sm font-medium text-slate-300">
          <span>Minimum gap</span>
          <span className="font-mono text-sm tracking-normal text-white">
            {filters.min_gap?.toFixed(1) || '0.0'} pts
          </span>
        </label>
        <input
          type="range"
          min="0"
          max="5"
          step="0.5"
          value={filters.min_gap || 0}
          onChange={(e) => update({ min_gap: parseFloat(e.target.value) || undefined })}
          className="mt-2 h-2 w-full cursor-pointer appearance-none rounded-full bg-ink-800"
        />
      </div>

      <div className="flex items-end">
        <button
          onClick={() => onChange({ sort_by: 'profit_margin', sort_order: 'desc' })}
          className="w-full rounded-lg border border-line-700/70 bg-ink-950 px-4 py-2.5 text-sm font-medium text-slate-300 transition hover:border-line-500 hover:text-white"
        >
          Reset filters
        </button>
      </div>
    </div>
  );
}
