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
    <div className="grid gap-4 lg:grid-cols-[1fr_1fr_1.15fr_auto]">
      <div>
        <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-text-muted">
          League
        </label>
        <select
          value={filters.league || ''}
          onChange={(e) => update({ league: e.target.value || undefined })}
          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none transition focus:border-border-hover"
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
        <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-text-muted">
          Market
        </label>
        <select
          value={filters.market_type || ''}
          onChange={(e) =>
            update({
              market_type: e.target.value ? (e.target.value as MarketType) : undefined,
              })
          }
          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none transition focus:border-border-hover"
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
        <label className="mb-1.5 flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-text-muted">
          <span>Min gap</span>
          <span className="font-mono normal-case tracking-normal text-text">
            {filters.min_gap?.toFixed(1) || '0.0'}
          </span>
        </label>
        <input
          type="range"
          min="0"
          max="5"
          step="0.5"
          value={filters.min_gap || 0}
          onChange={(e) => update({ min_gap: parseFloat(e.target.value) || undefined })}
          className="mt-2 h-1 w-full cursor-pointer appearance-none rounded-full bg-surface-raised"
        />
      </div>

      <div className="flex items-end">
        <button
          onClick={() => onChange({ sort_by: 'profit_margin', sort_order: 'desc' })}
          className="rounded-md border border-border bg-bg px-3 py-2 text-sm font-medium text-text-secondary transition hover:border-border-hover hover:text-text"
        >
          Reset
        </button>
      </div>
    </div>
  );
}
