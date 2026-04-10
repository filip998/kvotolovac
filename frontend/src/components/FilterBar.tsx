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
    <div className="flex flex-wrap items-end gap-3">
      {/* League */}
      <div className="min-w-[140px] flex-1 sm:flex-none">
        <label className="mb-1 block text-xs font-medium text-gray-500">League</label>
        <select
          value={filters.league || ''}
          onChange={(e) => update({ league: e.target.value || undefined })}
          className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 outline-none transition focus:border-brand-500"
        >
          <option value="">All Leagues</option>
          {leagues?.map((l) => (
            <option key={l.id} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
      </div>

      {/* Market Type */}
      <div className="min-w-[140px] flex-1 sm:flex-none">
        <label className="mb-1 block text-xs font-medium text-gray-500">Market Type</label>
        <select
          value={filters.market_type || ''}
          onChange={(e) =>
            update({
              market_type: e.target.value ? (e.target.value as MarketType) : undefined,
            })
          }
          className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 outline-none transition focus:border-brand-500"
        >
          <option value="">All Markets</option>
          {MARKET_TYPES.map((marketType) => (
            <option key={marketType} value={marketType}>
              {MARKET_TYPE_LABELS[marketType]}
            </option>
          ))}
        </select>
      </div>

      {/* Min Gap */}
      <div className="min-w-[120px] flex-1 sm:flex-none">
        <label className="mb-1 block text-xs font-medium text-gray-500">
          Min Gap: <span className="font-mono text-gray-300">{filters.min_gap?.toFixed(1) || '0.0'}</span>
        </label>
        <input
          type="range"
          min="0"
          max="5"
          step="0.5"
          value={filters.min_gap || 0}
          onChange={(e) => update({ min_gap: parseFloat(e.target.value) || undefined })}
          className="w-full accent-brand-500"
        />
      </div>

      {/* Reset */}
      <button
        onClick={() => onChange({ sort_by: 'profit_margin', sort_order: 'desc' })}
        className="rounded-lg border border-gray-700 bg-gray-800/50 px-3 py-2 text-sm text-gray-400 transition hover:border-gray-600 hover:text-gray-200"
      >
        Reset
      </button>
    </div>
  );
}
