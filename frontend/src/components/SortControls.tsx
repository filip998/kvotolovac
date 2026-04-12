import type { DiscrepancyFilters } from '../api/types';

interface SortControlsProps {
  filters: DiscrepancyFilters;
  onChange: (filters: DiscrepancyFilters) => void;
}

const SORT_OPTIONS = [
  { value: 'profit_margin', label: 'Edge ROI' },
  { value: 'middle_profit_margin', label: 'Middle ROI' },
  { value: 'gap', label: 'Gap Size' },
  { value: 'detected_at', label: 'Detection Time' },
];

export default function SortControls({ filters, onChange }: SortControlsProps) {
  const currentSort = filters.sort_by || 'profit_margin';
  const currentOrder = filters.sort_order || 'desc';

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm font-medium text-slate-300">Sort by</span>
      <div className="flex flex-wrap gap-2">
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
            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
              currentSort === opt.value
                ? 'border-line-500 bg-white/[0.05] text-white'
                : 'border-line-700/70 bg-ink-950 text-slate-400 hover:border-line-600 hover:text-slate-200'
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
