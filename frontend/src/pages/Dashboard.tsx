import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  useCanonicalTeams,
  useMergeCanonicalTeam,
  useUnmergeCanonicalTeam,
  useApproveTeamReviewCase,
  useDeclineTeamReviewCase,
  useMatches,
  useOpportunities,
  useSystemStatus,
  useTeamReviewCases,
  useUnresolvedOdds,
} from '../api/hooks';
import type { OpportunityBoardFilters } from '../api/types';
import FilterBar from '../components/FilterBar';
import BookmakerFilterDeck from '../components/BookmakerFilterDeck';
import EdgeGroupRow from '../components/EdgeRow';
import EmptyState from '../components/EmptyState';
import LoadingSpinner from '../components/LoadingSpinner';
import PageShell from '../components/PageShell';
import CanonicalTeamsPanel from '../components/CanonicalTeamsPanel';
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
import { groupUnresolvedOdds } from '../utils/unresolvedWarnings';
import { buildOpportunityEdges } from '../utils/edgeAdapter';
import { groupEdgesByMarket } from '../utils/edgeGrouping';
import { normalizeOpportunityMarketType } from '../utils/constants';

type DashboardTab = 'opportunities' | 'tracked' | 'teams' | 'canonical' | 'warnings';
type SportFilter = 'both' | 'basketball' | 'football' | 'tennis';

const SPORT_FILTER_OPTIONS: { value: SportFilter; label: string }[] = [
  { value: 'both', label: 'All sports' },
  { value: 'basketball', label: 'Basketball' },
  { value: 'football', label: 'Football' },
  { value: 'tennis', label: 'Tennis' },
];

function sportFilterToParam(value: SportFilter): string | undefined {
  return value === 'both' ? undefined : value;
}

export default function Dashboard() {
  const queryClient = useQueryClient();
  const {
    selectedBookmakerIds,
    updateSelectedBookmakerIds,
    search: sharedSearch,
  } = useBookmakerFilter();
  const { units: stakeUnits, updateUnits: updateStakeUnits, minUnits } = useDashboardStakeUnits();
  const [filters, setFilters] = useState<OpportunityBoardFilters>({
    sort_by: 'profit_margin',
    sort_order: 'desc',
  });
  const [activeTab, setActiveTab] = useState<DashboardTab>('opportunities');
  const [searchQuery, setSearchQuery] = useState('');
  const [diagnosticsSport, setDiagnosticsSport] = useState<'basketball' | 'football'>('basketball');
  const [opportunitiesSport, setOpportunitiesSport] = useState<SportFilter>('both');
  const [trackedSport, setTrackedSport] = useState<SportFilter>('both');
  const appliedSearchQuery = useDeferredValue(searchQuery);
  const [expandedFlatCalculatorIds, setExpandedFlatCalculatorIds] = useState<Set<string>>(new Set());
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
  const activeSearchLabel = appliedSearchQuery.trim();

  const switchTab = useCallback((nextTab: DashboardTab) => {
    if (nextTab !== activeTab) {
      setSearchQuery('');
    }
    setActiveTab(nextTab);
  }, [activeTab]);

  useEffect(() => {
    setStakeUnitsInput(formatDashboardStakeUnitsInput(stakeUnits));
  }, [stakeUnits]);

  const toggleFlatCalculator = useCallback((edgeId: string) => {
    setExpandedFlatCalculatorIds((prev) => {
      const next = new Set(prev);
      if (next.has(edgeId)) {
        next.delete(edgeId);
      } else {
        next.add(edgeId);
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
  const opportunityMarketTypeFilter = normalizeOpportunityMarketType(filters.market_type);

  const {
    data: opportunities,
    isLoading: opportunitiesLoading,
    isError: opportunitiesError,
    error: opportunitiesLoadError,
    refetch: refetchOpportunities,
  } = useOpportunities(
    {
      sport: sportFilterToParam(opportunitiesSport),
      market_type: opportunityMarketTypeFilter,
      limit: 200,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'opportunities' }
  );
  const {
    data: matches,
    isLoading: matchesLoading,
    isError: matchesError,
    error: matchesLoadError,
  } = useMatches(
    {
      sport: sportFilterToParam(trackedSport),
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
      sport: diagnosticsSport,
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
      sport: diagnosticsSport,
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
      sport: diagnosticsSport,
      limit: 300,
      include_merged: true,
    },
    { enabled: activeTab === 'canonical' }
  );
  const { data: status } = useSystemStatus();
  const approveTeamReviewCase = useApproveTeamReviewCase();
  const declineTeamReviewCase = useDeclineTeamReviewCase();
  const mergeCanonicalTeam = useMergeCanonicalTeam();
  const unmergeCanonicalTeam = useUnmergeCanonicalTeam();

  const opportunitiesPanelLoading = opportunitiesLoading;
  const opportunitiesPanelError = opportunitiesError
    ? (opportunitiesLoadError as Error)?.message
    : null;

  const isInitialScanInProgress =
    activeTab === 'opportunities' && !!status?.scan.in_progress && !status.last_scrape_at;
  const isTimeoutError =
    typeof (opportunitiesLoadError as Error | undefined)?.message === 'string' &&
    (opportunitiesLoadError as Error).message.toLowerCase().includes('timeout');

  useEffect(() => {
    const scanInProgress = !!status?.scan.in_progress;
    const scanJustFinished = previousScanInProgressRef.current && !scanInProgress;

    if (scanJustFinished && activeTab === 'opportunities') {
      void queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      void refetchOpportunities();
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
    refetchOpportunities,
    refetchCanonicalTeams,
    refetchTeamReviewCases,
    refetchUnresolvedOdds,
    status?.scan.in_progress,
  ]);

  const minGapFilter = filters.min_gap ?? 0;
  const marketTypeFilter = opportunityMarketTypeFilter;
  const leagueFilter = filters.league;

  const edges = useMemo(() => {
    let all = buildOpportunityEdges(opportunities, opportunitiesSport);
    if (marketTypeFilter) {
      all = all.filter((edge) => edge.market_type === marketTypeFilter);
    }
    if (leagueFilter) {
      all = all.filter((edge) => edge.league_name === leagueFilter);
    }
    if (minGapFilter > 0) {
      all = all.filter((edge) => (edge.gap ?? 0) >= minGapFilter);
    }
    return all;
  }, [opportunities, opportunitiesSport, marketTypeFilter, leagueFilter, minGapFilter]);

  const edgeGroups = useMemo(() => groupEdgesByMarket(edges), [edges]);

  const sortedGroups = useMemo(() => {
    const sortBy = filters.sort_by ?? 'profit_margin';
    const sortOrder = filters.sort_order ?? 'desc';
    const direction = sortOrder === 'desc' ? -1 : 1;
    const ranked = [...edgeGroups];
    ranked.sort((a, b) => {
      const aVal = (a.best[sortBy as keyof typeof a.best] as number | null | undefined) ?? Number.NEGATIVE_INFINITY;
      const bVal = (b.best[sortBy as keyof typeof b.best] as number | null | undefined) ?? Number.NEGATIVE_INFINITY;
      if (aVal === bVal) return 0;
      return aVal < bVal ? -direction : direction;
    });
    return ranked;
  }, [edgeGroups, filters.sort_by, filters.sort_order]);

  const groupSearchIndex = useMemo(
    () =>
      buildSearchIndex(sortedGroups, (group) => [
        group.homeTeam,
        group.awayTeam,
        group.homeTeam && group.awayTeam ? `${group.homeTeam} ${group.awayTeam}` : null,
        group.playerName,
      ]),
    [sortedGroups]
  );

  const filteredGroups = useMemo(
    () => filterSearchIndex(groupSearchIndex, appliedSearchQuery),
    [appliedSearchQuery, groupSearchIndex]
  );

  const unresolvedWarningGroups = useMemo(
    () => groupUnresolvedOdds(unresolvedOdds ?? []),
    [unresolvedOdds]
  );

  const opportunityCount = edgeGroups.length;
  const filteredOpportunityCount = filteredGroups.length;
  const unresolvedCount = unresolvedWarningGroups.length;
  const teamReviewCount = teamReviewCases?.filter((row) => row.status === 'pending').length ?? 0;
  const canonicalTeamCount =
    canonicalTeams?.filter((team) => team.merged_into_team_id == null).length ?? 0;
  const teamApproveCaseId =
    approveTeamReviewCase.isPending ? approveTeamReviewCase.variables?.caseId ?? null : null;
  const teamDeclineCaseId =
    declineTeamReviewCase.isPending ? declineTeamReviewCase.variables?.caseId ?? null : null;
  const canonicalMergeSourceId =
    mergeCanonicalTeam.isPending ? mergeCanonicalTeam.variables?.sourceTeamId ?? null : null;
  const canonicalMergeTargetId =
    mergeCanonicalTeam.isPending ? mergeCanonicalTeam.variables?.targetTeamId ?? null : null;
  const canonicalUnmergeTeamId =
    unmergeCanonicalTeam.isPending ? unmergeCanonicalTeam.variables?.sourceTeamId ?? null : null;

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
          const resolvedSuffix = result.resolved_team_name
            ? ` Current canonical: ${result.resolved_team_name}.`
            : '';
          const mergedPrefix = result.merged_source_team_name
            ? `Merged canonical team ${result.merged_source_team_name} into ${result.saved_team_name}. `
            : '';
          setTeamReviewMessage(
            `${mergedPrefix}Saved "${result.saved_alias}" -> ${result.saved_team_name}.${resolvedSuffix} Run the next scrape to apply it.`
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

  const handleUnmergeCanonicalTeam = (sourceTeamId: number) => {
    setCanonicalTeamMessage(null);
    unmergeCanonicalTeam.mutate(
      { sourceTeamId },
      {
        onSuccess: (result) => {
          setCanonicalTeamMessage(
            `Restored ${result.restored_team_name} as a standalone canonical team. Run the next scrape to apply the split everywhere.`
          );
          setSelectedCanonicalMergeSourceId(null);
          void queryClient.invalidateQueries({ queryKey: ['canonicalTeams'] });
          void queryClient.invalidateQueries({ queryKey: ['teamReviewCases'] });
          void refetchCanonicalTeams();
        },
        onError: (mutationError) => {
          setCanonicalTeamMessage(`Failed to unmerge canonical team: ${mutationError.message}`);
        },
      }
    );
  };

  const edgeContent = useMemo(() => {
    if (opportunitiesPanelLoading) {
      return <LoadingSpinner />;
    }

    if (
      isInitialScanInProgress &&
      (isTimeoutError || filteredGroups.length === 0)
    ) {
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

    if (opportunitiesPanelError) {
      return (
        <div className="rounded-lg border border-danger/30 bg-danger/10 p-6 text-center">
          <p className="text-sm text-danger">Failed to load opportunities: {opportunitiesPanelError}</p>
        </div>
      );
    }

    if (hasSearchQuery && filteredOpportunityCount === 0 && opportunityCount > 0) {
      return (
        <EmptyState
          title={`No opportunities match "${activeSearchLabel}"`}
          message="Search checks matchup, league, and player names after your current bookmaker, market, and gap filters."
        />
      );
    }

    if (filteredGroups.length === 0) {
      return (
        <EmptyState
          title="No opportunities right now"
          message="Scraping may still be working normally. Switch to tracked odds to inspect upcoming matches and player markets."
        />
      );
    }

    return (
      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-[11px] font-medium uppercase tracking-wider text-text-muted">
                <th className="px-4 py-2.5 text-left">Player / Market</th>
                <th className="px-4 py-2.5 text-left">Match</th>
                <th className="px-4 py-2.5 text-right">Best edge</th>
                <th className="hidden px-4 py-2.5 text-right md:table-cell">Middle</th>
                <th className="hidden px-4 py-2.5 text-left sm:table-cell">Best side A</th>
                <th className="hidden px-4 py-2.5 text-left sm:table-cell">Best side B</th>
                <th className="px-4 py-2.5 text-right">Gap</th>
                <th className="hidden px-4 py-2.5 text-right lg:table-cell">Time</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {filteredGroups.map((group) => (
                <EdgeGroupRow
                  key={group.key}
                  group={group}
                  totalUnits={stakeUnits}
                  isCalculatorExpanded={expandedFlatCalculatorIds.has(group.key)}
                  onToggleCalculator={toggleFlatCalculator}
                  sharedSearch={sharedSearch}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }, [
    activeSearchLabel,
    expandedFlatCalculatorIds,
    filteredGroups,
    filteredOpportunityCount,
    hasSearchQuery,
    isInitialScanInProgress,
    isTimeoutError,
    opportunitiesPanelError,
    opportunitiesPanelLoading,
    opportunityCount,
    sharedSearch,
    stakeUnits,
    status,
    toggleFlatCalculator,
  ]);


  return (
    <PageShell
      eyebrow="Live board"
        title={
          activeTab === 'opportunities'
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
          activeTab === 'opportunities'
            ? 'Every detected edge across basketball, football, and tennis, ranked by margin. Filter by sport to narrow the board.'
            : activeTab === 'tracked'
              ? 'Open tracked matches to review player markets, bookmaker prices, and fetched lines.'
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
                onClick={() => switchTab('opportunities')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'opportunities'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Opportunities
                {activeTab === 'opportunities' && opportunityCount > 0 && (
                  <span className="ml-1.5 font-mono text-xs text-accent">{opportunityCount}</span>
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
          </div>
          {(activeTab === 'opportunities' || activeTab === 'tracked') && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
                Sport
              </span>
              {SPORT_FILTER_OPTIONS.map((option) => {
                const value = option.value;
                const selected =
                  activeTab === 'opportunities' ? opportunitiesSport : trackedSport;
                const setter =
                  activeTab === 'opportunities' ? setOpportunitiesSport : setTrackedSport;
                return (
                  <button
                    key={value}
                    onClick={() => setter(value)}
                    className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                      selected === value
                        ? 'bg-surface-raised text-text'
                        : 'text-text-muted hover:text-text'
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          )}
          {activeTab === 'opportunities' && (
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
                scopeLabel="Opportunities"
                placeholder="Search team or player names, e.g. PAOK or Nunn"
                resultCount={filteredOpportunityCount}
                totalCount={opportunityCount}
              />
            </>
          )}
          {(activeTab === 'teams' || activeTab === 'canonical' || activeTab === 'warnings') && (
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
                Sport
              </span>
              {(['basketball', 'football'] as const).map((sport) => (
                <button
                  key={sport}
                  onClick={() => setDiagnosticsSport(sport)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                    diagnosticsSport === sport
                      ? 'bg-surface-raised text-text'
                      : 'text-text-muted hover:text-text'
                  }`}
                >
                  {sport === 'basketball' ? 'Basketball' : 'Football'}
                </button>
              ))}
            </div>
          )}
        </section>

        {activeTab === 'opportunities' ? (
          edgeContent
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
            onUnmerge={handleUnmergeCanonicalTeam}
            mergingSourceTeamId={canonicalMergeSourceId}
            mergingTargetTeamId={canonicalMergeTargetId}
            unmergingTeamId={canonicalUnmergeTeamId}
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
