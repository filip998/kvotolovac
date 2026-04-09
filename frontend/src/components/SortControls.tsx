import type { DiscrepancyFilters } from '../api/types';

interface SortControlsProps {
  filters: DiscrepancyFilters;
  onChange: (filters: DiscrepancyFilters) => void;
}

const SORT_OPTIONS = [
  { value: 'profit_margin', label: 'Profit Margin' },
  { value: 'gap', label: 'Gap Size' },
  { value: 'detected_at', label: 'Detection Time' },
];

export default function SortControls({ filters, onChange }: SortControlsProps) {
  const currentSort = filters.sort_by || 'profit_margin';
  const currentOrder = filters.sort_order || 'desc';

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-gray-500">Sort:</span>
      <div className="flex flex-wrap gap-1">
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
                ? 'bg-brand-600/20 text-brand-400'
                : 'text-gray-500 hover:bg-gray-800 hover:text-gray-300'
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
