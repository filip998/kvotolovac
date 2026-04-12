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
      eyebrow="Live discrepancy board"
      title={
        activeTab === 'discrepancies'
          ? 'Find exploitable line gaps before the market closes.'
          : 'Inspect the stored board even when no gap is flashing.'
      }
      description={
        activeTab === 'discrepancies'
          ? 'Group the current snapshot by league and matchup, then work downward from the highest-margin thresholds. Filters stay lightweight so the board remains fast to scan.'
          : 'Open tracked matches to review player markets, current bookmaker prices, and any discrepancy-linked lines already captured by the backend.'
      }
      aside={
        <div className="space-y-4">
          <div>
            <p className="text-sm text-slate-400">Board summary</p>
            <p className="mt-2 text-3xl font-semibold text-white">
              {activeTab === 'discrepancies' ? discrepancyCount : status?.total_matches ?? 0}
            </p>
            <p className="mt-2 text-sm text-slate-400">
              {activeTab === 'discrepancies' ? 'live discrepancies on board' : 'matches in the active snapshot'}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-line-700/70 bg-ink-950 px-3 py-4">
              <div className="text-2xl font-semibold text-white">
                {grouped.length}
              </div>
              <div className="mt-1 text-xs text-slate-500">leagues</div>
            </div>
            <div className="rounded-lg border border-line-700/70 bg-ink-950 px-3 py-4">
              <div className="text-2xl font-semibold text-white">
                {status?.scan.in_progress ? status.scan.phase : 'idle'}
              </div>
              <div className="mt-1 text-xs text-slate-500">scan phase</div>
            </div>
          </div>
          <div className="rounded-lg border border-line-700/70 bg-ink-950 px-4 py-3 text-sm leading-6 text-slate-400">
            {status?.scan.in_progress
              ? `The backend is currently ${status.scan.phase}. Progress is reflected live in the status rail above.`
              : 'Use the discrepancy board for opportunity-first scanning, then jump to tracked matches when you need the wider market context.'}
          </div>
        </div>
      }
    >
      <div className="space-y-6">
        <section className="rounded-xl border border-line-700/70 bg-ink-900 p-3">
          <div className="grid gap-3 lg:grid-cols-[auto_1fr]">
            <div className="inline-flex rounded-lg border border-line-700/70 bg-ink-950 p-1">
              <button
                onClick={() => setActiveTab('discrepancies')}
                className={`rounded-md px-4 py-2.5 text-sm font-medium transition ${
                  activeTab === 'discrepancies'
                    ? 'bg-ink-750 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                Discrepancies
              </button>
              <button
                onClick={() => setActiveTab('tracked')}
                className={`rounded-md px-4 py-2.5 text-sm font-medium transition ${
                  activeTab === 'tracked'
                    ? 'bg-ink-750 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                Tracked odds
              </button>
            </div>
            {activeTab === 'discrepancies' && (
              <div className="rounded-lg border border-line-700/70 bg-ink-950 p-4">
                <FilterBar filters={filters} onChange={setFilters} />
                <div className="mt-4 border-t border-line-700/60 pt-4">
                  <SortControls filters={filters} onChange={setFilters} />
                </div>
              </div>
            )}
          </div>
        </section>

        {activeTab === 'discrepancies' ? (
          isLoading ? (
            <LoadingSpinner />
          ) : isInitialScanInProgress && (isTimeoutError || !discrepancies || discrepancies.length === 0) ? (
            <div className="rounded-xl border border-line-700/70 bg-ink-900 p-6">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="text-xl font-semibold text-white">Initial scan in progress</h3>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
                    The backend is warming up and scraping bookmakers for the first complete
                    snapshot. This board will fill itself as soon as the cycle completes.
                  </p>
                </div>
                <div className="rounded-full border border-line-700/70 bg-ink-950/55 px-4 py-2 text-sm font-medium text-slate-200">
                  {status.scan.completed_tasks}/{status.scan.total_tasks} finished
                  {status.scan.failed_tasks > 0 ? ` · ${status.scan.failed_tasks} failed` : ''}
                </div>
              </div>
              <div className="mt-5 overflow-hidden rounded-full border border-line-700/70 bg-ink-950/80">
                <div
                  className="h-2 rounded-full bg-gradient-to-r from-brand-100 via-brand-300 to-brand-500 transition-all"
                  style={{
                    width: `${status.scan.total_tasks > 0 ? Math.max(5, Math.round((status.scan.completed_tasks / status.scan.total_tasks) * 100)) : 10}%`,
                  }}
                />
              </div>
              <p className="mt-3 text-xs text-slate-500">Phase: {status.scan.phase}</p>
            </div>
          ) : isError ? (
            <div className="rounded-xl border border-rose-300/20 bg-rose-300/10 p-6 text-center">
              <p className="text-sm text-rose-100">
                Failed to load discrepancies: {(error as Error)?.message || 'Unknown error'}
              </p>
            </div>
          ) : !discrepancies || discrepancies.length === 0 ? (
            <EmptyState
              title="No discrepancies right now"
              message="Scraping may still be working normally. Switch to the tracked board to inspect upcoming fetched matches and player markets."
            />
          ) : (
            <div className="space-y-8">
              {grouped.map((lg) => (
                <section key={lg.league}>
                  <button
                    onClick={() => toggleLeague(lg.league)}
                    className="mb-4 flex w-full items-center gap-3 text-left"
                  >
                    <span
                      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line-700/70 bg-ink-900 text-sm text-slate-300 transition-transform ${
                        collapsedLeagues.has(lg.league) ? '' : 'rotate-90'
                      }`}
                    >
                      ›
                    </span>
                    <div className="flex flex-wrap items-center gap-3">
                      <h3 className="text-xl font-semibold text-white">{lg.league}</h3>
                      <span className="rounded-full border border-line-700/70 bg-ink-950 px-3 py-1 text-xs font-medium text-slate-400">
                        {lg.matches.reduce((sum, m) => sum + m.discrepancies.length, 0)} discrepancies
                      </span>
                    </div>
                  </button>
                  {!collapsedLeagues.has(lg.league) && (
                    <div className="space-y-4">
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
