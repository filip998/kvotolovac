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
  const sortedMatches = [...matches].sort((a, b) => a.start_time.localeCompare(b.start_time));

  return (
    <section className="space-y-4 rounded-xl border border-gray-800 bg-gray-900/30 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-bold text-white">Tracked matches</h3>
          <p className="text-sm text-gray-400">
            No discrepancy is not the same as no scrape. Open a match to inspect fetched player
            markets and bookmaker odds.
          </p>
        </div>
        <span className="rounded-full bg-gray-800 px-2.5 py-1 text-xs font-medium text-gray-300">
          {sortedMatches.length} {sortedMatches.length === 1 ? 'match' : 'matches'} tracked
        </span>
      </div>

      {isLoading ? (
        <div className="rounded-lg border border-dashed border-gray-800 px-4 py-6 text-center text-sm text-gray-500">
          Loading tracked matches...
        </div>
      ) : errorMessage ? (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-6 text-center text-sm text-red-300">
          Failed to load tracked matches: {errorMessage}
        </div>
      ) : sortedMatches.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-800 px-4 py-6 text-center text-sm text-gray-500">
          No fetched matches are stored yet.
        </div>
      ) : (
        <div className="grid gap-3">
          {sortedMatches.map((match) => (
            <Link
              key={match.id}
              to={`/matches/${match.id}`}
              className="rounded-lg border border-gray-800 bg-gray-950/40 px-4 py-3 transition hover:border-brand-500/40 hover:bg-gray-900/60"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-brand-400">
                    {match.league_name}
                  </div>
                  <div className="font-semibold text-white">
                    {match.home_team} vs {match.away_team}
                  </div>
                  <div className="text-sm text-gray-500">{formatDateTime(match.start_time)}</div>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      match.status === 'live'
                        ? 'bg-red-500/20 text-red-400'
                        : match.status === 'upcoming'
                          ? 'bg-blue-500/20 text-blue-400'
                          : 'bg-gray-500/20 text-gray-400'
                    }`}
                  >
                    {match.status.toUpperCase()}
                  </span>
                  <span className="text-sm font-medium text-brand-400">View fetched odds →</span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
