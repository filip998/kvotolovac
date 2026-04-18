import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Match } from '../api/types';
import { formatDateTime } from '../utils/format';
import { buildSearchIndex, filterSearchIndex, normalizeSearchText } from '../utils/search';
import BookmakerBadge from './BookmakerBadge';
import EmptyState from './EmptyState';
import MergeMatchesModal from './MergeMatchesModal';
import OfferSearchStrip from './OfferSearchStrip';

interface TrackedMatchesPanelProps {
  matches: Match[];
  selectedBookmakerIds: string[];
  isLoading?: boolean;
  errorMessage?: string | null;
  searchQuery: string;
  onSearchChange: (value: string) => void;
}

export default function TrackedMatchesPanel({
  matches,
  selectedBookmakerIds,
  isLoading = false,
  errorMessage = null,
  searchQuery,
  onSearchChange,
}: TrackedMatchesPanelProps) {
  const location = useLocation();
  const [referenceTimeMs, setReferenceTimeMs] = useState(() => Date.now());
  const appliedSearchQuery = useDeferredValue(searchQuery);
  const selectedSet = useMemo(() => new Set(selectedBookmakerIds), [selectedBookmakerIds]);

  const [mergeMode, setMergeMode] = useState(false);
  const [selectedForMerge, setSelectedForMerge] = useState<Set<string>>(() => new Set());
  const [mergeModalOpen, setMergeModalOpen] = useState(false);

  const toggleSelectedForMerge = (id: string) => {
    setSelectedForMerge((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setReferenceTimeMs(Date.now());
    }, 60_000);

    return () => window.clearInterval(intervalId);
  }, []);

  const upcomingMatches = useMemo(
    () =>
      matches.filter((match) => {
        if (!match.start_time) {
          return true;
        }

        const startAt = Date.parse(match.start_time);
        if (Number.isNaN(startAt)) {
          return true;
        }
        return startAt >= referenceTimeMs;
      }),
    [matches, referenceTimeMs]
  );

  const searchableMatches = useMemo(
    () =>
      buildSearchIndex(upcomingMatches, (match) => [
        match.home_team,
        match.away_team,
        `${match.home_team} ${match.away_team}`,
      ]),
    [upcomingMatches]
  );

  const filteredMatches = useMemo(
    () => filterSearchIndex(searchableMatches, appliedSearchQuery),
    [searchableMatches, appliedSearchQuery]
  );

  const sortedMatches = useMemo(
    () =>
      [...filteredMatches].sort((a, b) => {
        if (a.start_time && b.start_time) {
          return a.start_time.localeCompare(b.start_time);
        }
        if (a.start_time) return -1;
        if (b.start_time) return 1;
        return a.home_team.localeCompare(b.home_team) || a.away_team.localeCompare(b.away_team);
      }),
    [filteredMatches]
  );

  const hasSearchQuery = normalizeSearchText(appliedSearchQuery).length > 0;
  const activeSearchLabel = appliedSearchQuery.trim();

  const resultsContent = useMemo(() => {
    if (isLoading) {
      return (
        <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-text-muted">
          Loading tracked matches…
        </div>
      );
    }

    if (errorMessage) {
      return (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-8 text-center text-sm text-danger">
          Failed to load: {errorMessage}
        </div>
      );
    }

    if (hasSearchQuery && sortedMatches.length === 0 && upcomingMatches.length > 0) {
      return (
        <EmptyState
          title={`No tracked matchups match "${activeSearchLabel}"`}
          message="Tracked odds search checks team and matchup names only. Try a broader club name or clear the query."
        />
      );
    }

    if (sortedMatches.length === 0) {
      return (
        <EmptyState
          title="No upcoming fetched matches stored right now"
          message="Tracked odds only lists matches that are still upcoming in the current stored board."
        />
      );
    }

    return (
      <div className="grid gap-2">
        {sortedMatches.map((match) => {
          const checked = selectedForMerge.has(match.id);
          const rowClasses = `group flex flex-wrap items-center justify-between gap-4 rounded-lg border bg-surface px-4 py-3 transition ${
            mergeMode && checked
              ? 'border-accent bg-accent/[0.06]'
              : 'border-border hover:border-border-hover'
          }`;

          const inner = (
            <>
              <div className="flex flex-1 items-start gap-3">
                {mergeMode && (
                  <input
                    type="checkbox"
                    aria-label={`Select ${match.home_team} vs ${match.away_team} for merge`}
                    className="mt-1 h-4 w-4 cursor-pointer accent-accent"
                    checked={checked}
                    onChange={(e) => {
                      e.stopPropagation();
                      toggleSelectedForMerge(match.id);
                    }}
                    onClick={(e) => e.stopPropagation()}
                  />
                )}
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-medium uppercase tracking-wider text-accent">
                      {match.league_name}
                    </span>
                    <span
                      className={`text-[11px] font-medium ${
                        match.status === 'live' ? 'text-danger' : 'text-text-muted'
                      }`}
                    >
                      {match.status}
                    </span>
                  </div>
                  <div className="mt-1 text-sm font-semibold text-text">
                    {match.home_team} vs {match.away_team}
                  </div>
                  <div className="mt-0.5 text-xs text-text-muted">{formatDateTime(match.start_time)}</div>
                  {match.available_bookmakers.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {match.available_bookmakers.map((bookmaker) => {
                        const highlighted =
                          selectedSet.size > 0 && selectedSet.has(bookmaker.id);

                        return (
                          <span
                            key={`${match.id}-${bookmaker.id}`}
                            className={`inline-flex items-center rounded-full border px-1.5 py-1 transition ${
                              highlighted
                                ? 'border-accent/60 bg-accent/[0.12] shadow-[0_0_0_1px_rgba(250,208,122,0.18)]'
                                : 'border-border/70 bg-bg/60'
                            }`}
                            title={bookmaker.name}
                          >
                            <BookmakerBadge name={bookmaker.name} compact />
                          </span>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
              {!mergeMode && (
                <span className="text-xs font-medium text-text-muted transition group-hover:text-accent">
                  View →
                </span>
              )}
            </>
          );

          if (mergeMode) {
            return (
              <button
                key={match.id}
                type="button"
                onClick={() => toggleSelectedForMerge(match.id)}
                className={`${rowClasses} text-left`}
              >
                {inner}
              </button>
            );
          }
          return (
            <Link
              key={match.id}
              to={{ pathname: `/matches/${match.id}`, search: location.search }}
              className={rowClasses}
            >
              {inner}
            </Link>
          );
        })}
      </div>
    );
  }, [
    activeSearchLabel,
    errorMessage,
    hasSearchQuery,
    isLoading,
    location.search,
    mergeMode,
    selectedForMerge,
    selectedSet,
    sortedMatches,
    upcomingMatches.length,
  ]);

  const selectedMatches = useMemo(
    () => sortedMatches.filter((m) => selectedForMerge.has(m.id)),
    [sortedMatches, selectedForMerge]
  );

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-text">Tracked matches</h3>
          <p className="mt-1 text-sm text-text-secondary">
            Upcoming fetched matches. Open a matchup to inspect bookmaker odds and player markets.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => {
              setMergeMode((prev) => {
                if (prev) setSelectedForMerge(new Set());
                return !prev;
              });
            }}
            className={`rounded-md border px-3 py-1.5 text-xs font-medium transition ${
              mergeMode
                ? 'border-accent bg-accent text-bg'
                : 'border-border text-text-muted hover:text-text'
            }`}
          >
            {mergeMode ? 'Done selecting' : 'Select to merge'}
          </button>
          <span className="font-mono text-xs text-text-muted">
            {hasSearchQuery ? `${sortedMatches.length} of ${upcomingMatches.length}` : sortedMatches.length}{' '}
            tracked
          </span>
        </div>
      </div>

      <OfferSearchStrip
        value={searchQuery}
        onChange={onSearchChange}
        scopeLabel="Tracked"
        placeholder="Search matchup or team names, e.g. PAOK or Panathinaikos"
        resultCount={sortedMatches.length}
        totalCount={upcomingMatches.length}
      />

      {resultsContent}

      {mergeMode && selectedMatches.length >= 2 && (
        <div className="sticky bottom-4 z-30 flex items-center justify-between gap-3 rounded-lg border border-accent/60 bg-surface/95 px-4 py-3 shadow-lg backdrop-blur">
          <div className="text-sm text-text">
            <span className="font-semibold">{selectedMatches.length}</span> matches selected
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setSelectedForMerge(new Set())}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-text-muted hover:text-text"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => setMergeModalOpen(true)}
              className="rounded-md border border-accent bg-accent px-3 py-1.5 text-xs font-semibold text-bg"
            >
              Merge matches…
            </button>
          </div>
        </div>
      )}

      {mergeModalOpen && selectedMatches.length >= 2 && (
        <MergeMatchesModal
          matches={selectedMatches}
          onClose={() => setMergeModalOpen(false)}
          onMerged={() => {
            setSelectedForMerge(new Set());
            setMergeMode(false);
          }}
        />
      )}
    </section>
  );
}
