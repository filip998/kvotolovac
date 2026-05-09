import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Match, MatchBookmaker } from '../api/types';
import { formatDateTime } from '../utils/format';
import { eventOrMatchPath } from '../utils/routes';
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

interface TrackedGroup {
  key: string;
  primary: Match;
  members: Match[];
  bookmakers: MatchBookmaker[];
  searchTerms: string[];
}

function groupMatchesByResolvedEvent(matches: Match[]): TrackedGroup[] {
  const groupMap = new Map<string, Match[]>();
  for (const match of matches) {
    const key = match.resolved_event_id ?? match.id;
    const existing = groupMap.get(key);
    if (existing) {
      existing.push(match);
    } else {
      groupMap.set(key, [match]);
    }
  }

  const groups: TrackedGroup[] = [];
  for (const [key, members] of groupMap) {
    const sortedMembers = [...members].sort((left, right) => {
      const leftCount = left.available_bookmakers.length;
      const rightCount = right.available_bookmakers.length;
      if (leftCount !== rightCount) {
        return rightCount - leftCount;
      }
      return left.id.localeCompare(right.id);
    });
    const primary = sortedMembers[0];
    const seenBookmakers = new Set<string>();
    const bookmakers: MatchBookmaker[] = [];
    for (const member of sortedMembers) {
      for (const bookmaker of member.available_bookmakers) {
        if (seenBookmakers.has(bookmaker.id)) continue;
        seenBookmakers.add(bookmaker.id);
        bookmakers.push(bookmaker);
      }
    }
    const searchTerms: string[] = [];
    for (const member of sortedMembers) {
      searchTerms.push(member.home_team);
      searchTerms.push(member.away_team);
      searchTerms.push(`${member.home_team} ${member.away_team}`);
    }
    groups.push({ key, primary, members: sortedMembers, bookmakers, searchTerms });
  }
  return groups;
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

  const upcomingGroups = useMemo(
    () => groupMatchesByResolvedEvent(upcomingMatches),
    [upcomingMatches]
  );

  const searchableGroups = useMemo(
    () => buildSearchIndex(upcomingGroups, (group) => group.searchTerms),
    [upcomingGroups]
  );

  const filteredGroups = useMemo(
    () => filterSearchIndex(searchableGroups, appliedSearchQuery),
    [searchableGroups, appliedSearchQuery]
  );

  const sortedGroups = useMemo(
    () =>
      [...filteredGroups].sort((a, b) => {
        const leftStart = a.primary.start_time ?? '';
        const rightStart = b.primary.start_time ?? '';
        if (leftStart && rightStart) {
          return leftStart.localeCompare(rightStart);
        }
        if (leftStart) return -1;
        if (rightStart) return 1;
        return (
          a.primary.home_team.localeCompare(b.primary.home_team) ||
          a.primary.away_team.localeCompare(b.primary.away_team)
        );
      }),
    [filteredGroups]
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

    if (hasSearchQuery && sortedGroups.length === 0 && upcomingGroups.length > 0) {
      return (
        <EmptyState
          title={`No tracked matchups match "${activeSearchLabel}"`}
          message="Tracked odds search checks team and matchup names only. Try a broader club name or clear the query."
        />
      );
    }

    if (sortedGroups.length === 0) {
      return (
        <EmptyState
          title="No upcoming fetched matches stored right now"
          message="Tracked odds lists upcoming matches across all selected sports. Try widening the sport filter or clearing the bookmaker filter."
        />
      );
    }

    return (
      <div className="grid gap-2">
        {sortedGroups.map((group) => {
          const { primary, members, bookmakers } = group;
          const checked = selectedForMerge.has(group.key);
          const isMultiMember = members.length > 1;
          const rowClasses = `group flex flex-wrap items-center justify-between gap-4 rounded-lg border bg-surface px-4 py-3 transition ${
            mergeMode && checked
              ? 'border-accent bg-accent/[0.06]'
              : 'border-border hover:border-border-hover'
          }`;

          const variantSummary = isMultiMember
            ? Array.from(
                new Set(
                  members.map((member) => `${member.home_team} vs ${member.away_team}`)
                )
              )
            : [];

          const inner = (
            <>
              <div className="flex flex-1 items-start gap-3">
                {mergeMode && (
                  <input
                    type="checkbox"
                    aria-label={`Select ${primary.home_team} vs ${primary.away_team} for event merge`}
                    className="mt-1 h-4 w-4 cursor-pointer accent-accent"
                    checked={checked}
                    onChange={(e) => {
                      e.stopPropagation();
                      toggleSelectedForMerge(group.key);
                    }}
                    onClick={(e) => e.stopPropagation()}
                  />
                )}
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className="inline-flex items-center rounded-full border border-border/70 bg-bg/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-text-secondary"
                      title={primary.sport}
                      aria-label={primary.sport}
                    >
                      {primary.sport === 'football' ? '⚽' : '🏀'}
                    </span>
                    <span className="text-[11px] font-medium uppercase tracking-wider text-accent">
                      {primary.league_name}
                    </span>
                    <span
                      className={`text-[11px] font-medium ${
                        primary.status === 'live' ? 'text-danger' : 'text-text-muted'
                      }`}
                    >
                      {primary.status}
                    </span>
                    {isMultiMember && (
                      <span
                        className="rounded-full border border-accent/40 bg-accent/[0.08] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-accent"
                        title={`Resolved to one event with ${members.length} bookmaker variants`}
                      >
                        Linked × {members.length}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-sm font-semibold text-text">
                    {primary.home_team} vs {primary.away_team}
                  </div>
                  <div className="mt-0.5 text-xs text-text-muted">
                    {formatDateTime(primary.start_time)}
                  </div>
                  {variantSummary.length > 1 && (
                    <div className="mt-1 text-[11px] text-text-muted" title="Other bookmaker labels for this event">
                      Also: {variantSummary.slice(1).join(' · ')}
                    </div>
                  )}
                  {bookmakers.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {bookmakers.map((bookmaker) => {
                        const highlighted =
                          selectedSet.size > 0 && selectedSet.has(bookmaker.id);

                        return (
                          <span
                            key={`${group.key}-${bookmaker.id}`}
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
                key={group.key}
                type="button"
                onClick={() => toggleSelectedForMerge(group.key)}
                className={`${rowClasses} text-left`}
              >
                {inner}
              </button>
            );
          }
          return (
            <Link
              key={group.key}
              to={eventOrMatchPath(primary.id, primary.resolved_event_id, location.search)}
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
    sortedGroups,
    upcomingGroups.length,
  ]);

  const selectedMatches = useMemo(() => {
    const flat: Match[] = [];
    for (const group of sortedGroups) {
      if (!selectedForMerge.has(group.key)) continue;
      for (const member of group.members) {
        flat.push(member);
      }
    }
    return flat;
  }, [sortedGroups, selectedForMerge]);

  const selectedGroupCount = useMemo(
    () => sortedGroups.filter((group) => selectedForMerge.has(group.key)).length,
    [sortedGroups, selectedForMerge]
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
            {mergeMode ? 'Done selecting' : 'Select events'}
          </button>
          <span className="font-mono text-xs text-text-muted">
            {hasSearchQuery ? `${sortedGroups.length} of ${upcomingGroups.length}` : sortedGroups.length}{' '}
            tracked
          </span>
        </div>
      </div>

      <OfferSearchStrip
        value={searchQuery}
        onChange={onSearchChange}
        scopeLabel="Tracked"
        placeholder="Search matchup or team names, e.g. PAOK or Panathinaikos"
        resultCount={sortedGroups.length}
        totalCount={upcomingGroups.length}
      />

      {resultsContent}

      {mergeMode && selectedGroupCount >= 2 && (
        <div className="sticky bottom-4 z-30 flex items-center justify-between gap-3 rounded-lg border border-accent/60 bg-surface/95 px-4 py-3 shadow-lg backdrop-blur">
          <div className="text-sm text-text">
            <span className="font-semibold">{selectedGroupCount}</span> events selected
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
              Merge events…
            </button>
          </div>
        </div>
      )}

      {mergeModalOpen && selectedGroupCount >= 2 && selectedMatches.length >= 2 && (
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
