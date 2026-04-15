import { useMemo } from 'react';
import { useBookmakers, useSystemStatus } from '../api/hooks';
import BookmakerBadge from './BookmakerBadge';

interface BookmakerFilterDeckProps {
  selectedBookmakerIds: string[];
  onChange: (nextBookmakerIds: string[]) => void;
}

export default function BookmakerFilterDeck({
  selectedBookmakerIds,
  onChange,
}: BookmakerFilterDeckProps) {
  const { data: bookmakers } = useBookmakers();
  const { data: status } = useSystemStatus();

  const selectedSet = useMemo(() => new Set(selectedBookmakerIds), [selectedBookmakerIds]);

  const bookmakerCards = useMemo(() => {
    const statusById = new Map(
      (status?.bookmaker_status ?? []).map((bookmaker) => [
        bookmaker.id,
        {
          isActive: bookmaker.is_active,
          lastScrape: bookmaker.last_scrape,
        },
      ])
    );

    const knownIds = new Set<string>([
      ...(bookmakers ?? []).map((bookmaker) => bookmaker.id),
      ...(status?.bookmaker_status ?? []).map((bookmaker) => bookmaker.id),
      ...selectedBookmakerIds,
    ]);

    return Array.from(knownIds)
      .map((id) => {
        const bookmaker =
          bookmakers?.find((candidate) => candidate.id === id) ??
          status?.bookmaker_status?.find((candidate) => candidate.id === id);
        const bookmakerStatus = statusById.get(id);

        return {
          id,
          name: bookmaker?.name ?? id,
          isActive: bookmakerStatus?.isActive ?? bookmaker?.is_active ?? false,
          lastScrape: bookmakerStatus?.lastScrape ?? null,
        };
      })
      .sort((a, b) => {
        if (a.isActive !== b.isActive) {
          return a.isActive ? -1 : 1;
        }
        return a.name.localeCompare(b.name);
      });
  }, [bookmakers, selectedBookmakerIds, status?.bookmaker_status]);

  const selectedLabels = bookmakerCards
    .filter((bookmaker) => selectedSet.has(bookmaker.id))
    .map((bookmaker) => bookmaker.name);

  const summaryLabel =
    selectedLabels.length === 0
      ? 'All bookmakers'
      : selectedLabels.length <= 3
        ? selectedLabels.join(', ')
        : `${selectedLabels.slice(0, 2).join(', ')} +${selectedLabels.length - 2}`;

  return (
    <section className="rounded-[18px] border border-border/70 bg-surface/92 px-3 py-3 shadow-[0_14px_34px_-30px_rgba(0,0,0,0.95)] sm:px-4">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-text-muted">
                Books
              </span>
              <span className="rounded-full border border-accent/25 bg-accent/10 px-2.5 py-1 font-mono text-[11px] text-accent">
                {selectedBookmakerIds.length || bookmakerCards.length}
              </span>
            </div>
            <p className="mt-1 truncate text-xs text-text-secondary">{summaryLabel}</p>
          </div>

          <button
            type="button"
            onClick={() => onChange([])}
            disabled={selectedBookmakerIds.length === 0}
            className="rounded-full border border-border/70 bg-bg/78 px-2.5 py-1 text-[11px] font-semibold text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-45"
          >
            Clear
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            aria-pressed={selectedBookmakerIds.length === 0}
            onClick={() => onChange([])}
            className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1.5 text-left transition ${
              selectedBookmakerIds.length === 0
                ? 'border-accent/40 bg-accent/10 text-text'
                : 'border-border/70 bg-bg/80 text-text-secondary hover:border-border-hover hover:text-text'
            }`}
          >
            <span className="rounded-full bg-surface-raised px-2 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-text">
              All
            </span>
            <span className="text-xs font-medium text-inherit">All bookmakers</span>
          </button>

          {bookmakerCards.map((bookmaker) => {
            const isSelected = selectedSet.has(bookmaker.id);

            return (
              <button
                key={bookmaker.id}
                type="button"
                aria-pressed={isSelected}
                onClick={() => {
                  if (isSelected) {
                    onChange(
                      selectedBookmakerIds.filter(
                        (selectedBookmakerId) => selectedBookmakerId !== bookmaker.id
                      )
                    );
                    return;
                  }

                  onChange([...selectedBookmakerIds, bookmaker.id]);
                }}
                className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1.5 text-left transition ${
                  isSelected
                    ? 'border-accent/40 bg-accent/10 text-text'
                    : 'border-border/70 bg-bg/80 text-text-secondary hover:border-border-hover hover:text-text'
                }`}
                title={
                  bookmaker.lastScrape
                    ? `${bookmaker.name} • ${bookmaker.isActive ? 'Active' : 'Idle'}`
                    : bookmaker.name
                }
              >
                <BookmakerBadge name={bookmaker.name} compact />
                <span className="text-xs font-medium text-inherit">{bookmaker.name}</span>
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    bookmaker.isActive ? 'bg-accent' : 'bg-text-muted/55'
                  }`}
                  aria-hidden="true"
                />
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
