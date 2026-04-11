import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useDiscrepancies, useMatches, useSystemStatus } from '../api/hooks';
import type { DiscrepancyFilters, Discrepancy } from '../api/types';
import FilterBar from '../components/FilterBar';
import SortControls from '../components/SortControls';
import MatchAccordion from '../components/MatchAccordion';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import TrackedMatchesPanel from '../components/TrackedMatchesPanel';

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

type DashboardTab = 'discrepancies' | 'tracked';

export default function Dashboard() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<DiscrepancyFilters>({
    sort_by: 'profit_margin',
    sort_order: 'desc',
  });
  const [activeTab, setActiveTab] = useState<DashboardTab>('discrepancies');
  const [collapsedLeagues, setCollapsedLeagues] = useState<Set<string>>(new Set());
  const previousScanInProgressRef = useRef(false);

  const toggleLeague = (league: string) => {
    setCollapsedLeagues((prev) => {
      const next = new Set(prev);
      if (next.has(league)) {
        next.delete(league);
      } else {
        next.add(league);
      }
      return next;
    });
  };

  const {
    data: discrepancies,
    isLoading,
    isError,
    error,
    refetch: refetchDiscrepancies,
  } = useDiscrepancies(filters, { enabled: activeTab === 'discrepancies' });
  const {
    data: matches,
    isLoading: matchesLoading,
    isError: matchesError,
    error: matchesLoadError,
  } = useMatches({ limit: 200, loadAll: true }, { enabled: activeTab === 'tracked' });
  const { data: status } = useSystemStatus();

  const isInitialScanInProgress =
    activeTab === 'discrepancies' &&
    !!status?.scan.in_progress &&
    !status.last_scrape_at;
  const isTimeoutError =
    typeof (error as Error | undefined)?.message === 'string' &&
    (error as Error).message.toLowerCase().includes('timeout');

  useEffect(() => {
    const scanInProgress = !!status?.scan.in_progress;
    const scanJustFinished = previousScanInProgressRef.current && !scanInProgress;

    if (scanJustFinished && activeTab === 'discrepancies') {
      void queryClient.invalidateQueries({ queryKey: ['discrepancies'] });
      void refetchDiscrepancies();
    }

    previousScanInProgressRef.current = scanInProgress;
  }, [activeTab, queryClient, refetchDiscrepancies, status?.scan.in_progress]);

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
        <h2 className="text-xl font-bold text-white sm:text-2xl">
          {activeTab === 'discrepancies' ? 'Discrepancy Dashboard' : 'Tracked Odds Dashboard'}
        </h2>
        <p className="text-sm text-gray-500">
          {activeTab === 'discrepancies'
            ? 'Find profitable odds gaps across Serbian bookmakers'
            : 'Inspect upcoming fetched matches and open per-match player odds even when no discrepancies exist'}
        </p>
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-2">
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => setActiveTab('discrepancies')}
            className={`rounded-lg px-4 py-3 text-sm font-semibold transition ${
              activeTab === 'discrepancies'
                ? 'bg-brand-600/20 text-brand-300'
                : 'text-gray-400 hover:bg-gray-800/50 hover:text-gray-200'
            }`}
          >
            Discrepancies
          </button>
          <button
            onClick={() => setActiveTab('tracked')}
            className={`rounded-lg px-4 py-3 text-sm font-semibold transition ${
              activeTab === 'tracked'
                ? 'bg-brand-600/20 text-brand-300'
                : 'text-gray-400 hover:bg-gray-800/50 hover:text-gray-200'
            }`}
          >
            Tracked Odds
          </button>
        </div>
      </div>

      {activeTab === 'discrepancies' && (
        <div className="space-y-3 rounded-xl border border-gray-800 bg-gray-900/30 p-4">
          <FilterBar filters={filters} onChange={setFilters} />
          <div className="border-t border-gray-800 pt-3">
            <SortControls filters={filters} onChange={setFilters} />
          </div>
        </div>
      )}

      {activeTab === 'discrepancies' ? (
        isLoading ? (
          <LoadingSpinner />
        ) : isInitialScanInProgress && (isTimeoutError || !discrepancies || discrepancies.length === 0) ? (
          <div className="rounded-xl border border-brand-500/20 bg-brand-500/10 p-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-base font-semibold text-white">Initial scan in progress</h3>
                <p className="text-sm text-gray-300">
                  The backend is warming up and scraping bookmakers for the first snapshot. This
                  page will populate automatically when the scan completes.
                </p>
              </div>
              <div className="text-sm text-brand-300">
                {status.scan.completed_tasks}/{status.scan.total_tasks} finished
                {status.scan.failed_tasks > 0 ? ` · ${status.scan.failed_tasks} failed` : ''}
              </div>
            </div>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-gray-800">
              <div
                className="h-full rounded-full bg-brand-400 transition-all"
                style={{
                  width: `${status.scan.total_tasks > 0 ? Math.max(5, Math.round((status.scan.completed_tasks / status.scan.total_tasks) * 100)) : 10}%`,
                }}
              />
            </div>
            <p className="mt-2 text-xs uppercase tracking-wide text-gray-400">
              Phase: {status.scan.phase}
            </p>
          </div>
        ) : isError ? (
          <div className="rounded-xl border border-red-500/20 bg-red-500/10 p-6 text-center">
            <p className="text-sm text-red-400">
              Failed to load discrepancies: {(error as Error)?.message || 'Unknown error'}
            </p>
          </div>
        ) : !discrepancies || discrepancies.length === 0 ? (
          <div className="space-y-6">
            <EmptyState
              title="No discrepancies right now"
              message="Scraping may still be working normally. Switch to the Tracked Odds tab to inspect upcoming fetched matches and player markets."
            />
          </div>
        ) : (
          <div className="space-y-6">
            {grouped.map((lg) => (
              <section key={lg.league}>
                <button
                  onClick={() => toggleLeague(lg.league)}
                  className="mb-3 flex w-full items-center gap-2 text-left transition-opacity hover:opacity-80"
                >
                  <span className={`text-xs text-gray-500 transition-transform ${collapsedLeagues.has(lg.league) ? '' : 'rotate-90'}`}>▶</span>
                  <span className="text-lg">🏀</span>
                  <h3 className="text-base font-bold text-white">{lg.league}</h3>
                  <span className="rounded-full bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
                    {lg.matches.reduce((sum, m) => sum + m.discrepancies.length, 0)} discrepancies
                  </span>
                </button>
                {!collapsedLeagues.has(lg.league) && (
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
                )}
              </section>
            ))}
          </div>
        )
      ) : (
        <TrackedMatchesPanel
          matches={matches || []}
          isLoading={matchesLoading}
          errorMessage={matchesError ? (matchesLoadError as Error)?.message || 'Unknown error' : null}
        />
      )}
    </div>
  );
}
