import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Match } from '../api/types';
import { formatDateTime } from '../utils/format';
import BookmakerBadge from './BookmakerBadge';

interface TrackedMatchesPanelProps {
  matches: Match[];
  selectedBookmakerIds: string[];
  isLoading?: boolean;
  errorMessage?: string | null;
}

export default function TrackedMatchesPanel({
  matches,
  selectedBookmakerIds,
  isLoading = false,
  errorMessage = null,
}: TrackedMatchesPanelProps) {
  const location = useLocation();
  const [referenceTimeMs, setReferenceTimeMs] = useState(() => Date.now());
  const selectedSet = useMemo(() => new Set(selectedBookmakerIds), [selectedBookmakerIds]);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setReferenceTimeMs(Date.now());
    }, 60_000);

    return () => window.clearInterval(intervalId);
  }, []);

  const upcomingMatches = matches.filter((match) => {
    const startAt = Date.parse(match.start_time);
    if (Number.isNaN(startAt)) {
      return true;
    }
    return startAt >= referenceTimeMs;
  });
  const sortedMatches = [...upcomingMatches].sort((a, b) => a.start_time.localeCompare(b.start_time));

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-text">Tracked matches</h3>
          <p className="mt-1 text-sm text-text-secondary">
            Upcoming fetched matches. Open a matchup to inspect bookmaker odds and player markets.
          </p>
        </div>
        <span className="font-mono text-xs text-text-muted">
          {sortedMatches.length} tracked
        </span>
      </div>

      {isLoading ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-text-muted">
          Loading tracked matches…
        </div>
      ) : errorMessage ? (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-8 text-center text-sm text-danger">
          Failed to load: {errorMessage}
        </div>
      ) : sortedMatches.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-text-muted">
          No upcoming fetched matches stored right now.
        </div>
      ) : (
        <div className="grid gap-2">
          {sortedMatches.map((match) => (
            <Link
              key={match.id}
              to={{ pathname: `/matches/${match.id}`, search: location.search }}
              className="group flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-surface px-4 py-3 transition hover:border-border-hover"
            >
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
              <span className="text-xs font-medium text-text-muted transition group-hover:text-accent">
                View →
              </span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
