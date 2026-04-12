import type { DiscrepancyFilters } from '../api/types';

interface SortControlsProps {
  filters: DiscrepancyFilters;
  onChange: (filters: DiscrepancyFilters) => void;
}

const SORT_OPTIONS = [
  { value: 'profit_margin', label: 'Edge' },
  { value: 'middle_profit_margin', label: 'Middle' },
  { value: 'gap', label: 'Gap' },
  { value: 'detected_at', label: 'Time' },
];

export default function SortControls({ filters, onChange }: SortControlsProps) {
  const currentSort = filters.sort_by || 'profit_margin';
  const currentOrder = filters.sort_order || 'desc';

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-wider text-text-muted">Sort</span>
      <div className="flex gap-1">
        {SORT_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => {
              if (currentSort === opt.value) {
                onChange({ ...filters, sort_order: currentOrder === 'desc' ? 'asc' : 'desc' });
              } else {
                onChange({ ...filters, sort_by: opt.value, sort_order: 'desc' });
              }
            }}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
              currentSort === opt.value
                ? 'bg-accent/15 text-accent'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {opt.label}
            {currentSort === opt.value && (
              <span className="ml-1">{currentOrder === 'desc' ? '↓' : '↑'}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
