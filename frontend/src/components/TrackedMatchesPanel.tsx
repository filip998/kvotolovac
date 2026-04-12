import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Match } from '../api/types';
import { formatDateTime } from '../utils/format';

interface TrackedMatchesPanelProps {
  matches: Match[];
  isLoading?: boolean;
  errorMessage?: string | null;
}

export default function TrackedMatchesPanel({
  matches,
  isLoading = false,
  errorMessage = null,
}: TrackedMatchesPanelProps) {
  const [referenceTimeMs, setReferenceTimeMs] = useState(() => Date.now());

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
    <section className="rounded-xl border border-line-700/70 bg-ink-900 p-5">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-xl font-semibold text-white">Tracked matches</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
            Upcoming fetched matches only. Open a matchup to inspect bookmaker odds and player
            markets even when no discrepancy is currently active.
          </p>
        </div>
        <span className="rounded-full border border-line-700/70 bg-ink-850 px-3 py-1 text-xs font-medium text-slate-200">
          {sortedMatches.length} tracked
        </span>
      </div>

      {isLoading ? (
        <div className="rounded-xl border border-dashed border-line-700/70 px-4 py-8 text-center text-sm text-slate-500">
          Loading tracked matches...
        </div>
      ) : errorMessage ? (
        <div className="rounded-xl border border-rose-300/20 bg-rose-300/10 px-4 py-8 text-center text-sm text-rose-100">
          Failed to load tracked matches: {errorMessage}
        </div>
      ) : sortedMatches.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line-700/70 px-4 py-8 text-center text-sm text-slate-500">
          No upcoming fetched matches are stored right now.
        </div>
      ) : (
        <div className="grid gap-3">
          {sortedMatches.map((match) => (
            <Link
              key={match.id}
              to={`/matches/${match.id}`}
              className="group rounded-xl border border-line-700/70 bg-ink-950 px-4 py-4 transition hover:border-line-500"
            >
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <div className="mb-2 inline-flex rounded-full border border-line-700/70 bg-ink-850 px-2.5 py-1 text-xs font-medium text-slate-300">
                    {match.league_name}
                  </div>
                  <div className="text-lg font-semibold text-white">
                    {match.home_team} vs {match.away_team}
                  </div>
                  <div className="mt-2 text-sm text-slate-400">{formatDateTime(match.start_time)}</div>
                </div>
                <div className="flex flex-col items-end gap-3">
                  <span
                    className={`rounded-full border px-3 py-1 text-xs font-medium ${
                      match.status === 'live'
                        ? 'border-rose-300/20 bg-rose-300/10 text-rose-100'
                        : match.status === 'upcoming'
                          ? 'border-line-600 bg-white/[0.04] text-slate-200'
                          : 'border-line-600 bg-white/[0.04] text-slate-300'
                    }`}
                  >
                    {match.status}
                  </span>
                  <span className="text-sm font-medium text-slate-200 transition group-hover:text-white">
                    View fetched odds →
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
