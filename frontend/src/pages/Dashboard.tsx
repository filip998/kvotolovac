import { useState, useMemo } from 'react';
import { useDiscrepancies } from '../api/hooks';
import type { DiscrepancyFilters, Discrepancy } from '../api/types';
import FilterBar from '../components/FilterBar';
import SortControls from '../components/SortControls';
import MatchAccordion from '../components/MatchAccordion';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';

interface MatchGroup {
  matchId: string;
  homeTeam: string;
  awayTeam: string;
  startTime: string;
  discrepancies: Discrepancy[];
}

interface LeagueGroup {
  league: string;
  matches: MatchGroup[];
}

export default function Dashboard() {
  const [filters, setFilters] = useState<DiscrepancyFilters>({
    sort_by: 'profit_margin',
    sort_order: 'desc',
  });

  const { data: discrepancies, isLoading, isError, error } = useDiscrepancies(filters);

  // Group discrepancies by league, then by match
  const grouped = useMemo<LeagueGroup[]>(() => {
    if (!discrepancies) return [];

    const leagueMap = new Map<string, Map<string, MatchGroup>>();

    for (const d of discrepancies) {
      if (!leagueMap.has(d.league_name)) {
        leagueMap.set(d.league_name, new Map());
      }
      const matchMap = leagueMap.get(d.league_name)!;
      if (!matchMap.has(d.match_id)) {
        matchMap.set(d.match_id, {
          matchId: d.match_id,
          homeTeam: d.home_team,
          awayTeam: d.away_team,
          startTime: d.detected_at,
          discrepancies: [],
        });
      }
      matchMap.get(d.match_id)!.discrepancies.push(d);
    }

    const result: LeagueGroup[] = [];
    for (const [league, matchMap] of leagueMap) {
      result.push({
        league,
        matches: Array.from(matchMap.values()),
      });
    }
    return result;
  }, [discrepancies]);

  return (
    <div className="space-y-6">
      {/* Title */}
      <div>
        <h2 className="text-xl font-bold text-white sm:text-2xl">Discrepancy Dashboard</h2>
        <p className="text-sm text-gray-500">
          Find profitable odds gaps across Serbian bookmakers
        </p>
      </div>

      {/* Filters & Sort */}
      <div className="space-y-3 rounded-xl border border-gray-800 bg-gray-900/30 p-4">
        <FilterBar filters={filters} onChange={setFilters} />
        <div className="border-t border-gray-800 pt-3">
          <SortControls filters={filters} onChange={setFilters} />
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <LoadingSpinner />
      ) : isError ? (
        <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-6 text-center">
          <p className="text-sm text-red-400">
            Failed to load discrepancies: {(error as Error)?.message || 'Unknown error'}
          </p>
        </div>
      ) : !discrepancies || discrepancies.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-6">
          {grouped.map((lg) => (
            <section key={lg.league}>
              <div className="mb-3 flex items-center gap-2">
                <span className="text-lg">🏀</span>
                <h3 className="text-base font-bold text-white">{lg.league}</h3>
                <span className="rounded-full bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
                  {lg.matches.reduce((sum, m) => sum + m.discrepancies.length, 0)} discrepancies
                </span>
              </div>
              <div className="space-y-3">
                {lg.matches.map((mg) => (
                  <MatchAccordion
                    key={mg.matchId}
                    matchId={mg.matchId}
                    homeTeam={mg.homeTeam}
                    awayTeam={mg.awayTeam}
                    startTime={mg.startTime}
                    discrepancies={mg.discrepancies}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
