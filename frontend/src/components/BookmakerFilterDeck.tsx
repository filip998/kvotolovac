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
    <section className="relative overflow-hidden rounded-[28px] border border-border/80 bg-[radial-gradient(circle_at_top_left,_rgba(250,208,122,0.18),_transparent_42%),linear-gradient(135deg,rgba(255,255,255,0.02),rgba(255,255,255,0.01))] p-4 shadow-[0_24px_80px_-42px_rgba(0,0,0,0.8)] sm:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(120deg,transparent,rgba(255,255,255,0.03),transparent)] opacity-80" />
      <div className="relative space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-border/70 bg-bg/70 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.28em] text-text-muted">
              Bookmaker lens
              <span className="h-1 w-1 rounded-full bg-accent" />
              <span className="tracking-[0.16em] text-text-secondary">{summaryLabel}</span>
            </div>
            <h3 className="mt-3 font-display text-lg font-semibold text-text sm:text-xl">
              Shape the board around the books you actually care about.
            </h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-text-secondary">
              Shared across discrepancies, tracked matches, league matching review, and match
              detail. The selection lives in the URL, so your lens survives every click-through.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <div className="rounded-2xl border border-border/70 bg-bg/75 px-3 py-2 text-right">
              <div className="font-mono text-sm font-semibold text-text">
                {selectedBookmakerIds.length || bookmakerCards.length}
              </div>
              <div className="text-[10px] uppercase tracking-[0.24em] text-text-muted">
                {selectedBookmakerIds.length ? 'selected' : 'visible'}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onChange([])}
              disabled={selectedBookmakerIds.length === 0}
              className="rounded-2xl border border-border/70 bg-bg/75 px-3 py-2 text-xs font-semibold text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-45"
            >
              Clear lens
            </button>
          </div>
        </div>

        <div className="flex flex-wrap gap-2.5">
          <button
            type="button"
            aria-pressed={selectedBookmakerIds.length === 0}
            onClick={() => onChange([])}
            className={`group inline-flex items-center gap-3 rounded-2xl border px-3.5 py-2.5 text-left transition ${
              selectedBookmakerIds.length === 0
                ? 'border-accent/60 bg-accent/[0.12] text-text shadow-[0_0_0_1px_rgba(250,208,122,0.22)]'
                : 'border-border/70 bg-bg/70 text-text-secondary hover:border-border-hover hover:text-text'
            }`}
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-border/70 bg-surface-raised font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-text">
              All
            </span>
            <span>
              <span className="block text-sm font-semibold text-inherit">All bookmakers</span>
              <span className="block text-[11px] uppercase tracking-[0.18em] text-text-muted">
                Reset scope
              </span>
            </span>
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
                className={`group inline-flex min-w-[180px] items-center gap-3 rounded-2xl border px-3.5 py-2.5 text-left transition ${
                  isSelected
                    ? 'border-accent/60 bg-accent/[0.12] text-text shadow-[0_0_0_1px_rgba(250,208,122,0.22)]'
                    : 'border-border/70 bg-bg/70 text-text-secondary hover:border-border-hover hover:text-text'
                }`}
                title={
                  bookmaker.lastScrape
                    ? `${bookmaker.name} • ${bookmaker.isActive ? 'Active' : 'Idle'}`
                    : bookmaker.name
                }
              >
                <span
                  className={`rounded-xl border p-1 transition ${
                    isSelected
                      ? 'border-accent/60 bg-bg/80'
                      : 'border-border/70 bg-surface-raised/80'
                  }`}
                >
                  <BookmakerBadge name={bookmaker.name} compact />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-inherit">
                    {bookmaker.name}
                  </span>
                  <span
                    className={`block text-[11px] uppercase tracking-[0.18em] ${
                      bookmaker.isActive ? 'text-accent' : 'text-text-muted'
                    }`}
                  >
                    {bookmaker.isActive ? 'Live snapshot' : 'Idle'}
                  </span>
                </span>
                <span
                  className={`rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] ${
                    isSelected
                      ? 'bg-accent/15 text-accent'
                      : 'bg-surface-raised text-text-muted'
                  }`}
                >
                  {isSelected ? 'On' : 'Add'}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
