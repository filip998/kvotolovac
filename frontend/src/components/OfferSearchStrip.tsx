import { normalizeSearchText } from '../utils/search';

interface OfferSearchStripProps {
  value: string;
  onChange: (value: string) => void;
  scopeLabel: string;
  placeholder: string;
  resultCount: number;
  totalCount: number;
  tone?: 'accent' | 'warning';
}

export default function OfferSearchStrip({
  value,
  onChange,
  scopeLabel,
  placeholder,
  resultCount,
  totalCount,
  tone = 'accent',
}: OfferSearchStripProps) {
  const normalizedQuery = normalizeSearchText(value);
  const hasActiveQuery = normalizedQuery.length > 0;
  const hasRawValue = value.length > 0;
  const inputId = `offer-search-${scopeLabel.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

  const toneStyles =
    tone === 'warning'
      ? {
          shell: 'border-warning/18 bg-surface/92',
          badge: 'border-warning/25 bg-warning/10 text-warning',
          icon: 'text-warning',
          input:
            'focus-within:border-warning/40 focus-within:bg-bg/95 focus-within:shadow-[0_0_0_1px_rgba(245,158,11,0.14)]',
          clear:
            'border-warning/20 bg-warning/8 text-warning hover:border-warning/35 hover:bg-warning/12',
        }
      : {
          shell: 'border-border/70 bg-surface/92',
          badge: 'border-accent/25 bg-accent/10 text-accent',
          icon: 'text-accent',
          input:
            'focus-within:border-accent/40 focus-within:bg-bg/95 focus-within:shadow-[0_0_0_1px_rgba(212,255,0,0.14)]',
          clear:
            'border-border/70 bg-bg/78 text-text-secondary hover:border-border-hover hover:text-text',
        };

  return (
    <section
      className={`rounded-[18px] border px-3 py-3 shadow-[0_14px_34px_-30px_rgba(0,0,0,0.95)] sm:px-4 ${toneStyles.shell}`}
    >
      <div className="flex flex-wrap items-center gap-2.5">
        <div
          className={`flex min-w-0 flex-1 items-center gap-2.5 rounded-[14px] border border-border/70 bg-bg/82 px-3 py-2 transition ${toneStyles.input}`}
        >
          <span className={`shrink-0 ${toneStyles.icon}`} aria-hidden="true">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
          </span>

          <label htmlFor={inputId} className="sr-only">
            Search {scopeLabel}
          </label>
          <input
            id={inputId}
            type="search"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 bg-transparent text-sm font-medium text-text outline-none placeholder:font-normal placeholder:text-text-muted"
          />
        </div>

        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-[10px] font-semibold uppercase tracking-[0.22em] text-text-muted sm:inline">
            {scopeLabel}
          </span>
          <span className={`rounded-full border px-2.5 py-1 font-mono text-[11px] ${toneStyles.badge}`}>
            {hasActiveQuery ? `${resultCount}/${totalCount}` : totalCount}
          </span>
          {hasRawValue && (
            <button
              type="button"
              onClick={() => onChange('')}
              className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold transition ${toneStyles.clear}`}
            >
              Clear
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
