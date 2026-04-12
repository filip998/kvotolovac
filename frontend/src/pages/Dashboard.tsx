import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useDiscrepancies, useMatches, useSystemStatus } from '../api/hooks';
import type { DiscrepancyFilters, Discrepancy } from '../api/types';
import FilterBar from '../components/FilterBar';
import SortControls from '../components/SortControls';
import MatchAccordion from '../components/MatchAccordion';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import PageShell from '../components/PageShell';
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

  const discrepancyCount = discrepancies?.length ?? 0;

  return (
    <PageShell
      eyebrow="Live board"
      title={
        activeTab === 'discrepancies'
          ? 'Find exploitable line gaps before the market closes.'
          : 'Inspect the stored board even when no gap is flashing.'
      }
      description={
        activeTab === 'discrepancies'
          ? 'Snapshot grouped by league and matchup. Work downward from the highest-margin thresholds.'
          : 'Open tracked matches to review player markets, bookmaker prices, and discrepancy-linked lines.'
      }
    >
      <div className="space-y-6">
        {/* Tabs + Filters */}
        <section className="space-y-4">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex gap-1">
              <button
                onClick={() => setActiveTab('discrepancies')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'discrepancies'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Discrepancies
                {activeTab === 'discrepancies' && discrepancyCount > 0 && (
                  <span className="ml-1.5 font-mono text-xs text-accent">{discrepancyCount}</span>
                )}
              </button>
              <button
                onClick={() => setActiveTab('tracked')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'tracked'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Tracked odds
              </button>
            </div>
            {activeTab === 'discrepancies' && (
              <div className="ml-auto">
                <SortControls filters={filters} onChange={setFilters} />
              </div>
            )}
          </div>
          {activeTab === 'discrepancies' && (
            <div className="rounded-lg border border-border bg-surface p-4">
              <FilterBar filters={filters} onChange={setFilters} />
            </div>
          )}
        </section>

        {activeTab === 'discrepancies' ? (
          isLoading ? (
            <LoadingSpinner />
          ) : isInitialScanInProgress && (isTimeoutError || !discrepancies || discrepancies.length === 0) ? (
            <div className="rounded-lg border border-border bg-surface p-6">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="text-base font-semibold text-text">Initial scan in progress</h3>
                  <p className="mt-1 text-sm text-text-secondary">
                    The backend is scraping bookmakers for the first snapshot. Board will populate when the cycle completes.
                  </p>
                </div>
                <span className="font-mono text-xs text-text-muted">
                  {status.scan.completed_tasks}/{status.scan.total_tasks}
                  {status.scan.failed_tasks > 0 ? ` · ${status.scan.failed_tasks} failed` : ''}
                </span>
              </div>
              <div className="mt-4 h-0.5 overflow-hidden rounded-full bg-surface-raised">
                <div
                  className="h-full rounded-full bg-accent transition-all"
                  style={{
                    width: `${status.scan.total_tasks > 0 ? Math.max(3, Math.round((status.scan.completed_tasks / status.scan.total_tasks) * 100)) : 10}%`,
                  }}
                />
              </div>
            </div>
          ) : isError ? (
            <div className="rounded-lg border border-danger/30 bg-danger/10 p-6 text-center">
              <p className="text-sm text-danger">
                Failed to load discrepancies: {(error as Error)?.message || 'Unknown error'}
              </p>
            </div>
          ) : !discrepancies || discrepancies.length === 0 ? (
            <EmptyState
              title="No discrepancies right now"
              message="Scraping may still be working normally. Switch to tracked odds to inspect upcoming matches and player markets."
            />
          ) : (
            <div className="space-y-8">
              {grouped.map((lg) => (
                <section key={lg.league}>
                  <button
                    onClick={() => toggleLeague(lg.league)}
                    className="mb-3 flex w-full items-center gap-2 text-left"
                  >
                    <span
                      className={`text-xs text-text-muted transition-transform ${
                        collapsedLeagues.has(lg.league) ? '' : 'rotate-90'
                      }`}
                    >
                      ▶
                    </span>
                    <h3 className="text-sm font-semibold uppercase tracking-wide text-accent">{lg.league}</h3>
                    <span className="font-mono text-xs text-text-muted">
                      {lg.matches.reduce((sum, m) => sum + m.discrepancies.length, 0)}
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
    </PageShell>
  );
}
