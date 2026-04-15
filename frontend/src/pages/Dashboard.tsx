import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useDiscrepancies, useMatches, useSystemStatus, useUnresolvedOdds } from '../api/hooks';
import type { DiscrepancyFilters, Discrepancy } from '../api/types';
import { formatOdds, formatThreshold, formatPercentage, formatGap, formatRelativeTime, profitColor } from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import FilterBar from '../components/FilterBar';
import BookmakerFilterDeck from '../components/BookmakerFilterDeck';
import SortControls from '../components/SortControls';
import MatchAccordion from '../components/MatchAccordion';
import BookmakerBadge from '../components/BookmakerBadge';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import PageShell from '../components/PageShell';
import TrackedMatchesPanel from '../components/TrackedMatchesPanel';
import UnresolvedOddsPanel from '../components/UnresolvedOddsPanel';
import OfferSearchStrip from '../components/OfferSearchStrip';
import { useBookmakerFilter } from '../hooks/useBookmakerFilter';
import { filterItemsBySearch, normalizeSearchText } from '../utils/search';

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

type DashboardTab = 'discrepancies' | 'tracked' | 'warnings';
type ViewMode = 'by-match' | 'flat';

export default function Dashboard() {
  const queryClient = useQueryClient();
  const {
    selectedBookmakerIds,
    updateSelectedBookmakerIds,
    search: sharedSearch,
  } = useBookmakerFilter();
  const [filters, setFilters] = useState<DiscrepancyFilters>({
    sort_by: 'profit_margin',
    sort_order: 'desc',
  });
  const [activeTab, setActiveTab] = useState<DashboardTab>('discrepancies');
  const [viewMode, setViewMode] = useState<ViewMode>('flat');
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsedLeagues, setCollapsedLeagues] = useState<Set<string>>(new Set());
  const previousScanInProgressRef = useRef(false);

  const switchTab = (nextTab: DashboardTab) => {
    if (nextTab !== activeTab) {
      setSearchQuery('');
    }
    setActiveTab(nextTab);
  };

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

  const discrepancyFilters = useMemo(
    () => ({
      ...filters,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    }),
    [filters, selectedBookmakerIds]
  );

  const {
    data: discrepancies,
    isLoading,
    isError,
    error,
    refetch: refetchDiscrepancies,
  } = useDiscrepancies(discrepancyFilters, { enabled: activeTab === 'discrepancies' });
  const {
    data: matches,
    isLoading: matchesLoading,
    isError: matchesError,
    error: matchesLoadError,
  } = useMatches(
    {
      limit: 200,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'tracked' }
  );
  const {
    data: unresolvedOdds,
    isLoading: unresolvedLoading,
    isError: unresolvedError,
    error: unresolvedLoadError,
    refetch: refetchUnresolvedOdds,
  } = useUnresolvedOdds(
    {
      limit: 200,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'warnings' }
  );
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
    if (scanJustFinished && activeTab === 'warnings') {
      void queryClient.invalidateQueries({ queryKey: ['unresolvedOdds'] });
      void refetchUnresolvedOdds();
    }

    previousScanInProgressRef.current = scanInProgress;
  }, [activeTab, queryClient, refetchDiscrepancies, refetchUnresolvedOdds, status?.scan.in_progress]);

  const filteredDiscrepancies = useMemo(
    () =>
      filterItemsBySearch(discrepancies ?? [], searchQuery, (discrepancy) => [
        discrepancy.home_team,
        discrepancy.away_team,
        `${discrepancy.home_team} ${discrepancy.away_team}`,
        discrepancy.player_name,
      ]),
    [discrepancies, searchQuery]
  );
  const hasSearchQuery = normalizeSearchText(searchQuery).length > 0;
  const activeSearchLabel = searchQuery.trim();

  // Group discrepancies by league, then by match
  const grouped = useMemo<LeagueGroup[]>(() => {
    if (!filteredDiscrepancies) return [];

    const leagueMap = new Map<string, Map<string, MatchGroup>>();

    for (const d of filteredDiscrepancies) {
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
  }, [filteredDiscrepancies]);

  const discrepancyCount = discrepancies?.length ?? 0;
  const filteredDiscrepancyCount = filteredDiscrepancies.length;
  const unresolvedCount = unresolvedOdds?.length ?? 0;

  return (
    <PageShell
      eyebrow="Live board"
      title={
        activeTab === 'discrepancies'
          ? 'Find exploitable line gaps before the market closes.'
          : activeTab === 'tracked'
            ? 'Inspect the stored board even when no gap is flashing.'
            : 'Review dropped player props before they disappear from the board.'
      }
      description={
        activeTab === 'discrepancies'
          ? 'Snapshot grouped by league and matchup. Work downward from the highest-margin thresholds.'
          : activeTab === 'tracked'
            ? 'Open tracked matches to review player markets, bookmaker prices, and discrepancy-linked lines.'
            : 'Internal warnings for shared-platform props that failed matchup resolution in the current scrape snapshot.'
      }
    >
      <div className="space-y-6">
        {/* Tabs + Filters */}
        <section className="space-y-4">
          <BookmakerFilterDeck
            selectedBookmakerIds={selectedBookmakerIds}
            onChange={updateSelectedBookmakerIds}
          />
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex gap-1">
              <button
                onClick={() => switchTab('discrepancies')}
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
                onClick={() => switchTab('tracked')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'tracked'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Tracked odds
              </button>
              <button
                onClick={() => switchTab('warnings')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'warnings'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Warnings
                {activeTab === 'warnings' && unresolvedCount > 0 && (
                  <span className="ml-1.5 font-mono text-xs text-warning">{unresolvedCount}</span>
                )}
              </button>
            </div>
            {activeTab === 'discrepancies' && (
              <div className="ml-auto flex items-center gap-3">
                <div className="flex items-center gap-1 rounded-md bg-surface-raised p-0.5">
                  <button
                    onClick={() => setViewMode('flat')}
                    aria-label="Flat list view"
                    aria-pressed={viewMode === 'flat'}
                    className={`rounded px-2 py-1 text-xs font-medium transition ${
                      viewMode === 'flat'
                        ? 'bg-bg text-text'
                        : 'text-text-muted hover:text-text-secondary'
                    }`}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <line x1="3" y1="6" x2="21" y2="6" />
                      <line x1="3" y1="12" x2="21" y2="12" />
                      <line x1="3" y1="18" x2="21" y2="18" />
                    </svg>
                  </button>
                  <button
                    onClick={() => setViewMode('by-match')}
                    aria-label="Group by match view"
                    aria-pressed={viewMode === 'by-match'}
                    className={`rounded px-2 py-1 text-xs font-medium transition ${
                      viewMode === 'by-match'
                        ? 'bg-bg text-text'
                        : 'text-text-muted hover:text-text-secondary'
                    }`}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <rect x="3" y="3" width="7" height="7" rx="1" />
                      <rect x="14" y="3" width="7" height="7" rx="1" />
                      <rect x="3" y="14" width="7" height="7" rx="1" />
                      <rect x="14" y="14" width="7" height="7" rx="1" />
                    </svg>
                  </button>
                </div>
                <SortControls filters={filters} onChange={setFilters} />
              </div>
            )}
          </div>
          {activeTab === 'discrepancies' && (
            <>
              <div className="rounded-lg border border-border bg-surface p-4">
                <FilterBar filters={filters} onChange={setFilters} />
              </div>
              <OfferSearchStrip
                value={searchQuery}
                onChange={setSearchQuery}
                scopeLabel="Discrepancies"
                placeholder="Search team or player names, e.g. PAOK or Nunn"
                resultCount={filteredDiscrepancyCount}
                totalCount={discrepancyCount}
              />
            </>
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
          ) : hasSearchQuery && filteredDiscrepancyCount === 0 ? (
            <EmptyState
              title={`No discrepancy rows match "${activeSearchLabel}"`}
              message="Search checks matchup and player names after your current bookmaker, market, and gap filters."
            />
          ) : viewMode === 'flat' ? (
            /* ── Flat ranked list ── */
            <div className="overflow-hidden rounded-lg border border-border bg-surface">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
                      <th className="px-4 py-2.5 text-left">Player / Market</th>
                      <th className="px-4 py-2.5 text-left">Match</th>
                      <th className="px-4 py-2.5 text-right">Edge</th>
                      <th className="hidden px-4 py-2.5 text-right md:table-cell">Middle</th>
                      <th className="hidden px-4 py-2.5 text-left sm:table-cell">Over</th>
                      <th className="hidden px-4 py-2.5 text-left sm:table-cell">Under</th>
                      <th className="px-4 py-2.5 text-right">Gap</th>
                      <th className="hidden px-4 py-2.5 text-right lg:table-cell">Time</th>
                      <th className="px-4 py-2.5"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDiscrepancies.map((d) => {
                      const marketLabel = MARKET_TYPE_LABELS[d.market_type] || d.market_type;
                      return (
                        <tr
                          key={d.id}
                          className="border-t border-border transition hover:bg-surface-raised"
                        >
                          <td className="px-4 py-2.5">
                            <div className="font-medium text-text">
                              {d.player_name || marketLabel}
                            </div>
                            {d.player_name && (
                              <div className="text-[11px] text-text-muted">{marketLabel}</div>
                            )}
                          </td>
                          <td className="px-4 py-2.5">
                            <div className="text-text-secondary">
                              {d.home_team} vs {d.away_team}
                            </div>
                            <div className="text-[11px] text-text-muted">{d.league_name}</div>
                          </td>
                          <td className={`px-4 py-2.5 text-right font-mono font-bold ${profitColor(d.profit_margin)}`}>
                            {formatPercentage(d.profit_margin)}
                          </td>
                          <td className="hidden px-4 py-2.5 text-right md:table-cell">
                            {d.middle_profit_margin != null && d.gap > 0 ? (
                              <span className={`font-mono font-bold ${profitColor(d.middle_profit_margin)}`}>
                                {formatPercentage(d.middle_profit_margin)}
                              </span>
                            ) : (
                              <span className="text-text-muted">—</span>
                            )}
                          </td>
                          <td className="hidden px-4 py-2.5 sm:table-cell">
                            <div className="flex items-center gap-1.5">
                              <BookmakerBadge name={d.bookmaker_a_name} compact />
                              <span className="font-mono text-text-secondary">
                                {formatThreshold(d.threshold_a)} @ {formatOdds(d.odds_a)}
                              </span>
                            </div>
                          </td>
                          <td className="hidden px-4 py-2.5 sm:table-cell">
                            <div className="flex items-center gap-1.5">
                              <BookmakerBadge name={d.bookmaker_b_name} compact />
                              <span className="font-mono text-text-secondary">
                                {formatThreshold(d.threshold_b)} @ {formatOdds(d.odds_b)}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                            {formatGap(d.gap)}
                          </td>
                          <td className="hidden px-4 py-2.5 text-right text-text-muted lg:table-cell">
                            {formatRelativeTime(d.detected_at)}
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <Link
                              to={`/matches/${d.match_id}${sharedSearch}`}
                              aria-label={`View ${d.player_name || marketLabel} for ${d.home_team} vs ${d.away_team}`}
                              className="text-xs font-medium text-text-muted transition hover:text-accent"
                            >
                              →
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
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
        ) : activeTab === 'tracked' ? (
          <TrackedMatchesPanel
            matches={matches || []}
            selectedBookmakerIds={selectedBookmakerIds}
            isLoading={matchesLoading}
            errorMessage={matchesError ? (matchesLoadError as Error)?.message || 'Unknown error' : null}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
          />
        ) : (
          <UnresolvedOddsPanel
            rows={unresolvedOdds || []}
            isLoading={unresolvedLoading}
            errorMessage={unresolvedError ? (unresolvedLoadError as Error)?.message || 'Unknown error' : null}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
          />
        )}
      </div>
    </PageShell>
  );
}
