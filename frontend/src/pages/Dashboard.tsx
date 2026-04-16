import { Fragment, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  useCanonicalTeams,
  useMergeCanonicalTeam,
  useApproveTeamReviewCase,
  useDeclineTeamReviewCase,
  useDiscrepancies,
  useMatches,
  useSystemStatus,
  useTeamReviewCases,
  useUnresolvedOdds,
} from '../api/hooks';
import type { Discrepancy, DiscrepancyFilters } from '../api/types';
import {
  formatGap,
  formatOdds,
  formatPercentage,
  formatRelativeTime,
  formatThreshold,
  profitColor,
} from '../utils/format';
import { MARKET_TYPE_LABELS } from '../utils/constants';
import FilterBar from '../components/FilterBar';
import BookmakerFilterDeck from '../components/BookmakerFilterDeck';
import BookmakerBadge from '../components/BookmakerBadge';
import EmptyState from '../components/EmptyState';
import LoadingSpinner from '../components/LoadingSpinner';
import MatchAccordion from '../components/MatchAccordion';
import PageShell from '../components/PageShell';
import SortControls from '../components/SortControls';
import CanonicalTeamsPanel from '../components/CanonicalTeamsPanel';
import StakeCalculatorPanel from '../components/StakeCalculatorPanel';
import TeamReviewPanel from '../components/TeamReviewPanel';
import TrackedMatchesPanel from '../components/TrackedMatchesPanel';
import UnresolvedOddsPanel from '../components/UnresolvedOddsPanel';
import OfferSearchStrip from '../components/OfferSearchStrip';
import {
  formatDashboardStakeUnitsInput,
  useDashboardStakeUnits,
} from '../hooks/useDashboardStakeUnits';
import { useBookmakerFilter } from '../hooks/useBookmakerFilter';
import { buildSearchIndex, filterSearchIndex, normalizeSearchText } from '../utils/search';

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

type DashboardTab = 'discrepancies' | 'tracked' | 'teams' | 'canonical' | 'warnings';
type ViewMode = 'by-match' | 'flat';

export default function Dashboard() {
  const queryClient = useQueryClient();
  const {
    selectedBookmakerIds,
    updateSelectedBookmakerIds,
    search: sharedSearch,
  } = useBookmakerFilter();
  const { units: stakeUnits, updateUnits: updateStakeUnits, minUnits } = useDashboardStakeUnits();
  const [filters, setFilters] = useState<DiscrepancyFilters>({
    sort_by: 'profit_margin',
    sort_order: 'desc',
  });
  const [activeTab, setActiveTab] = useState<DashboardTab>('discrepancies');
  const [viewMode, setViewMode] = useState<ViewMode>('flat');
  const [searchQuery, setSearchQuery] = useState('');
  const appliedSearchQuery = useDeferredValue(searchQuery);
  const [collapsedLeagues, setCollapsedLeagues] = useState<Set<string>>(new Set());
  const [expandedFlatCalculatorIds, setExpandedFlatCalculatorIds] = useState<Set<number>>(new Set());
  const [teamReviewMessage, setTeamReviewMessage] = useState<string | null>(null);
  const [canonicalTeamMessage, setCanonicalTeamMessage] = useState<string | null>(null);
  const [selectedCanonicalMergeSourceId, setSelectedCanonicalMergeSourceId] = useState<number | null>(
    null
  );
  const [stakeUnitsInput, setStakeUnitsInput] = useState(() =>
    formatDashboardStakeUnitsInput(stakeUnits)
  );
  const previousScanInProgressRef = useRef(false);
  const normalizedAppliedSearchQuery = useMemo(
    () => normalizeSearchText(appliedSearchQuery),
    [appliedSearchQuery]
  );
  const hasSearchQuery = normalizedAppliedSearchQuery.length > 0;

  const switchTab = useCallback((nextTab: DashboardTab) => {
    if (nextTab !== activeTab) {
      setSearchQuery('');
    }
    setActiveTab(nextTab);
  }, [activeTab]);

  useEffect(() => {
    setStakeUnitsInput(formatDashboardStakeUnitsInput(stakeUnits));
  }, [stakeUnits]);

  const toggleLeague = useCallback((league: string) => {
    setCollapsedLeagues((prev) => {
      const next = new Set(prev);
      if (next.has(league)) {
        next.delete(league);
      } else {
        next.add(league);
      }
      return next;
    });
  }, []);

  const shouldLoadAllDiscrepancies =
    activeTab === 'discrepancies' && hasSearchQuery;

  const toggleFlatCalculator = useCallback((discrepancyId: number) => {
    setExpandedFlatCalculatorIds((prev) => {
      const next = new Set(prev);
      if (next.has(discrepancyId)) {
        next.delete(discrepancyId);
      } else {
        next.add(discrepancyId);
      }
      return next;
    });
  }, []);

  const commitStakeUnits = () => {
    const parsed = Number(stakeUnitsInput.replace(',', '.'));

    if (!Number.isFinite(parsed) || parsed < minUnits) {
      setStakeUnitsInput(formatDashboardStakeUnitsInput(stakeUnits));
      return;
    }

    const normalized = updateStakeUnits(parsed);
    setStakeUnitsInput(formatDashboardStakeUnitsInput(normalized));
  };

  const discrepancyFilters = useMemo(
    () => ({
      ...filters,
      loadAll: shouldLoadAllDiscrepancies,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    }),
    [filters, selectedBookmakerIds, shouldLoadAllDiscrepancies]
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
  const {
    data: teamReviewCases,
    isLoading: teamReviewLoading,
    isError: teamReviewError,
    error: teamReviewLoadError,
    refetch: refetchTeamReviewCases,
  } = useTeamReviewCases(
    {
      limit: 200,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'teams' }
  );
  const {
    data: canonicalTeams,
    isLoading: canonicalTeamsLoading,
    isError: canonicalTeamsError,
    error: canonicalTeamsLoadError,
    refetch: refetchCanonicalTeams,
  } = useCanonicalTeams(
    {
      sport: 'basketball',
      limit: 300,
    },
    { enabled: activeTab === 'canonical' }
  );
  const { data: status } = useSystemStatus();
  const approveTeamReviewCase = useApproveTeamReviewCase();
  const declineTeamReviewCase = useDeclineTeamReviewCase();
  const mergeCanonicalTeam = useMergeCanonicalTeam();

  const isInitialScanInProgress =
    activeTab === 'discrepancies' && !!status?.scan.in_progress && !status.last_scrape_at;
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
    if (scanJustFinished && activeTab === 'teams') {
      void queryClient.invalidateQueries({ queryKey: ['teamReviewCases'] });
      void refetchTeamReviewCases();
    }
    if (scanJustFinished && activeTab === 'canonical') {
      void queryClient.invalidateQueries({ queryKey: ['canonicalTeams'] });
      void refetchCanonicalTeams();
    }

    previousScanInProgressRef.current = scanInProgress;
  }, [
    activeTab,
    queryClient,
    refetchDiscrepancies,
    refetchCanonicalTeams,
    refetchTeamReviewCases,
    refetchUnresolvedOdds,
    status?.scan.in_progress,
  ]);

  const discrepancySearchIndex = useMemo(
    () =>
      buildSearchIndex(discrepancies ?? [], (discrepancy) => [
        discrepancy.home_team,
        discrepancy.away_team,
        `${discrepancy.home_team} ${discrepancy.away_team}`,
        discrepancy.player_name,
      ]),
    [discrepancies]
  );

  const filteredDiscrepancies = useMemo(
    () => filterSearchIndex(discrepancySearchIndex, appliedSearchQuery),
    [appliedSearchQuery, discrepancySearchIndex]
  );
  const activeSearchLabel = appliedSearchQuery.trim();

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
  const teamReviewCount = teamReviewCases?.filter((row) => row.status === 'pending').length ?? 0;
  const canonicalTeamCount = canonicalTeams?.length ?? 0;
  const teamApproveCaseId =
    approveTeamReviewCase.isPending ? approveTeamReviewCase.variables?.caseId ?? null : null;
  const teamDeclineCaseId =
    declineTeamReviewCase.isPending ? declineTeamReviewCase.variables?.caseId ?? null : null;
  const canonicalMergeSourceId =
    mergeCanonicalTeam.isPending ? mergeCanonicalTeam.variables?.sourceTeamId ?? null : null;
  const canonicalMergeTargetId =
    mergeCanonicalTeam.isPending ? mergeCanonicalTeam.variables?.targetTeamId ?? null : null;

  const handleApproveTeamCase = (
    caseId: number,
    payload?: { teamId?: number; createTeamName?: string }
  ) => {
    setTeamReviewMessage(null);
    approveTeamReviewCase.mutate(
      {
        caseId,
        team_id: payload?.teamId,
        create_team_name: payload?.createTeamName,
      },
      {
        onSuccess: (result) => {
          setTeamReviewMessage(
            result.resolved_team_name
              ? `Saved "${result.saved_alias}" -> ${result.saved_team_name}. Current canonical: ${result.resolved_team_name}. Run the next scrape to apply it.`
              : `Saved "${result.saved_alias}" -> ${result.saved_team_name}. Run the next scrape to apply it.`
          );
          void queryClient.invalidateQueries({ queryKey: ['teamReviewCases'] });
          void queryClient.invalidateQueries({ queryKey: ['canonicalTeams'] });
          void refetchTeamReviewCases();
          void refetchCanonicalTeams();
        },
        onError: (mutationError) => {
          setTeamReviewMessage(`Failed to save team alias: ${mutationError.message}`);
        },
      }
    );
  };

  const handleDeclineTeamCase = (caseId: number) => {
    setTeamReviewMessage(null);
    declineTeamReviewCase.mutate(
      { caseId },
      {
        onSuccess: () => {
          setTeamReviewMessage('Declined for this snapshot. No alias was saved.');
          void queryClient.invalidateQueries({ queryKey: ['teamReviewCases'] });
          void refetchTeamReviewCases();
        },
        onError: (mutationError) => {
          setTeamReviewMessage(`Failed to decline team alias: ${mutationError.message}`);
        },
      }
    );
  };

  const handleMergeCanonicalTeam = (sourceTeamId: number, targetTeamId: number) => {
    setCanonicalTeamMessage(null);
    mergeCanonicalTeam.mutate(
      { sourceTeamId, targetTeamId },
      {
        onSuccess: (result) => {
          setCanonicalTeamMessage(
            `Merged team ${result.source_team_id} into ${result.merged_team_name}. Run the next scrape to apply the merged aliases everywhere.`
          );
          setSelectedCanonicalMergeSourceId(null);
          void queryClient.invalidateQueries({ queryKey: ['canonicalTeams'] });
          void queryClient.invalidateQueries({ queryKey: ['teamReviewCases'] });
          void refetchCanonicalTeams();
        },
        onError: (mutationError) => {
          setCanonicalTeamMessage(`Failed to merge canonical teams: ${mutationError.message}`);
        },
      }
    );
  };

  const discrepancyContent = useMemo(() => {
    if (isLoading) {
      return <LoadingSpinner />;
    }

    if (isInitialScanInProgress && (isTimeoutError || !discrepancies || discrepancies.length === 0)) {
      return (
        <div className="rounded-lg border border-border bg-surface p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-base font-semibold text-text">Initial scan in progress</h3>
              <p className="mt-1 text-sm text-text-secondary">
                The backend is scraping bookmakers for the first snapshot. Board will populate when the cycle completes.
              </p>
            </div>
            <span className="font-mono text-xs text-text-muted">
              {status?.scan.completed_tasks}/{status?.scan.total_tasks}
              {(status?.scan.failed_tasks ?? 0) > 0 ? ` · ${status?.scan.failed_tasks} failed` : ''}
            </span>
          </div>
          <div className="mt-4 h-0.5 overflow-hidden rounded-full bg-surface-raised">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{
                width: `${status?.scan.total_tasks ? Math.max(3, Math.round((status.scan.completed_tasks / status.scan.total_tasks) * 100)) : 10}%`,
              }}
            />
          </div>
        </div>
      );
    }

    if (isError) {
      return (
        <div className="rounded-lg border border-danger/30 bg-danger/10 p-6 text-center">
          <p className="text-sm text-danger">
            Failed to load discrepancies: {(error as Error)?.message || 'Unknown error'}
          </p>
        </div>
      );
    }

    if (!discrepancies || discrepancies.length === 0) {
      return (
        <EmptyState
          title="No discrepancies right now"
          message="Scraping may still be working normally. Switch to tracked odds to inspect upcoming matches and player markets."
        />
      );
    }

    if (hasSearchQuery && filteredDiscrepancyCount === 0) {
      return (
        <EmptyState
          title={`No discrepancy rows match "${activeSearchLabel}"`}
          message="Search checks matchup and player names after your current bookmaker, market, and gap filters."
        />
      );
    }

    if (viewMode === 'flat') {
      return (
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
                  const calculatorPanelId = `flat-calculator-${d.id}`;
                  const isCalculatorExpanded = expandedFlatCalculatorIds.has(d.id);

                  return (
                    <Fragment key={d.id}>
                      <tr className="border-t border-border transition hover:bg-surface-raised">
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
                        <td
                          className={`px-4 py-2.5 text-right font-mono font-bold ${profitColor(d.profit_margin)}`}
                        >
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
                          <div className="flex items-center justify-end gap-3">
                            <button
                              type="button"
                              aria-expanded={isCalculatorExpanded}
                              aria-controls={calculatorPanelId}
                              aria-label={`${isCalculatorExpanded ? 'Hide' : 'View'} stake calculator for ${d.player_name || marketLabel} in ${d.home_team} vs ${d.away_team}`}
                              onClick={() => toggleFlatCalculator(d.id)}
                              className="text-[11px] font-medium text-text-muted transition hover:text-text"
                            >
                              {isCalculatorExpanded ? 'Hide' : 'View'}
                            </button>
                            <Link
                              to={`/matches/${d.match_id}${sharedSearch}`}
                              aria-label={`View ${d.player_name || marketLabel} for ${d.home_team} vs ${d.away_team}`}
                              className="text-xs font-medium text-text-muted transition hover:text-accent"
                            >
                              →
                            </Link>
                          </div>
                        </td>
                      </tr>
                      {isCalculatorExpanded && (
                        <tr className="border-t border-border bg-bg/20">
                          <td colSpan={9} className="px-4 py-3">
                            <div id={calculatorPanelId}>
                              <StakeCalculatorPanel discrepancy={d} totalUnits={stakeUnits} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      );
    }

    return (
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
                    totalUnits={stakeUnits}
                  />
                ))}
              </div>
            )}
          </section>
        ))}
      </div>
    );
  }, [
    activeSearchLabel,
    collapsedLeagues,
    discrepancies,
    error,
    filteredDiscrepancies,
    filteredDiscrepancyCount,
    grouped,
    hasSearchQuery,
    isError,
    isInitialScanInProgress,
    isLoading,
    isTimeoutError,
    expandedFlatCalculatorIds,
    sharedSearch,
    stakeUnits,
    status,
    toggleFlatCalculator,
    toggleLeague,
    viewMode,
  ]);

  return (
    <PageShell
      eyebrow="Live board"
        title={
          activeTab === 'discrepancies'
            ? 'Find exploitable line gaps before the market closes.'
            : activeTab === 'tracked'
              ? 'Inspect the stored board even when no gap is flashing.'
              : activeTab === 'teams'
                ? 'Resolve team labels into the right canonical club.'
                : activeTab === 'canonical'
                  ? 'Merge duplicate canonical teams before they fragment the board.'
                  : 'Inspect odds that still need manual review.'
        }
        description={
          activeTab === 'discrepancies'
            ? 'Snapshot grouped by league and matchup. Work downward from the highest-margin thresholds.'
            : activeTab === 'tracked'
              ? 'Open tracked matches to review player markets, bookmaker prices, and discrepancy-linked lines.'
              : activeTab === 'teams'
                ? 'Approve the best candidate, choose another canonical team, or create a new one inline.'
                : activeTab === 'canonical'
                  ? 'Select the duplicate source team, merge it into the correct target, and keep aliases under one canonical identity.'
                  : 'Review unresolved odds rows that could not be placed confidently on the board.'
        }
    >
      <div className="space-y-6">
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
                onClick={() => switchTab('teams')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'teams'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Team review
                {activeTab === 'teams' && teamReviewCount > 0 && (
                  <span className="ml-1.5 font-mono text-xs text-warning">{teamReviewCount}</span>
                )}
              </button>
              <button
                onClick={() => switchTab('canonical')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'canonical'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Canonical teams
                {activeTab === 'canonical' && canonicalTeamCount > 0 && (
                  <span className="ml-1.5 font-mono text-xs text-accent">
                    {canonicalTeamCount}
                  </span>
                )}
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
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="text-[11px] font-medium uppercase tracking-[0.2em] text-text-muted">
                      Total stake
                    </div>
                    <p
                      id="dashboard-stake-units-note"
                      className="mt-1 text-sm text-text-secondary"
                    >
                      Shared across inline calculators and saved in this browser.
                    </p>
                  </div>

                  <div className="flex items-center gap-3 self-start sm:self-auto">
                    <label
                      htmlFor="dashboard-stake-units"
                      className="text-[11px] font-medium uppercase tracking-[0.2em] text-text-muted"
                    >
                      Units
                    </label>
                    <div className="flex items-center gap-2 rounded-md border border-border bg-bg px-3 py-2">
                      <input
                        id="dashboard-stake-units"
                        name="dashboardStakeUnits"
                        type="number"
                        inputMode="decimal"
                        min={minUnits}
                        step="0.1"
                        aria-describedby="dashboard-stake-units-note"
                        value={stakeUnitsInput}
                        onChange={(e) => setStakeUnitsInput(e.target.value)}
                        onBlur={commitStakeUnits}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.currentTarget.blur();
                          }
                        }}
                        className="w-20 bg-transparent text-right font-mono text-base font-semibold text-text outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                      />
                      <span className="text-xs font-medium text-text-muted">u</span>
                    </div>
                  </div>
                </div>
              </div>
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
          discrepancyContent
        ) : activeTab === 'tracked' ? (
          <TrackedMatchesPanel
            matches={matches || []}
            selectedBookmakerIds={selectedBookmakerIds}
            isLoading={matchesLoading}
            errorMessage={matchesError ? (matchesLoadError as Error)?.message || 'Unknown error' : null}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
          />
        ) : activeTab === 'teams' ? (
          <TeamReviewPanel
            rows={teamReviewCases || []}
            isLoading={teamReviewLoading}
            errorMessage={teamReviewError ? (teamReviewLoadError as Error)?.message || 'Unknown error' : null}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            onApprove={handleApproveTeamCase}
            onDecline={handleDeclineTeamCase}
            approvingCaseId={teamApproveCaseId}
            decliningCaseId={teamDeclineCaseId}
            actionMessage={teamReviewMessage}
          />
        ) : activeTab === 'canonical' ? (
          <CanonicalTeamsPanel
            teams={canonicalTeams || []}
            isLoading={canonicalTeamsLoading}
            errorMessage={
              canonicalTeamsError ? (canonicalTeamsLoadError as Error)?.message || 'Unknown error' : null
            }
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            selectedSourceTeamId={selectedCanonicalMergeSourceId}
            onSelectSource={setSelectedCanonicalMergeSourceId}
            onMerge={handleMergeCanonicalTeam}
            mergingSourceTeamId={canonicalMergeSourceId}
            mergingTargetTeamId={canonicalMergeTargetId}
            actionMessage={canonicalTeamMessage}
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
