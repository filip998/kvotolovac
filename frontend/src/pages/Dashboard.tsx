import { Fragment, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  useCanonicalTeams,
  useMergeCanonicalTeam,
  useUnmergeCanonicalTeam,
  useApproveTeamReviewCase,
  useDeclineTeamReviewCase,
  useDiscrepancies,
  useMatches,
  useOpportunities,
  useOutcomeOffers,
  useSystemStatus,
  useTeamReviewCases,
  useUnresolvedOdds,
} from '../api/hooks';
import type { Discrepancy, DiscrepancyFilters, Match, Opportunity, OutcomeOffer } from '../api/types';
import {
  formatDateTime,
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
import { groupUnresolvedOdds } from '../utils/unresolvedWarnings';

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

type DashboardTab = 'discrepancies' | 'football' | 'tracked' | 'teams' | 'canonical' | 'warnings';
type ViewMode = 'by-match' | 'flat';

interface FootballMatchRow {
  match: Match;
  offers: OutcomeOffer[];
  opportunities: Opportunity[];
}

const FOOTBALL_OUTCOME_LABELS: Record<string, string> = {
  under: '0-2',
  over: '3+',
  home: '1',
  draw: 'X',
  away: '2',
  home_or_draw: '1X',
  draw_or_away: 'X2',
  home_or_away: '12',
};
function footballOutcomeLabel(code: string) {
  return FOOTBALL_OUTCOME_LABELS[code] || code;
}

function marketTypeLabel(marketType: string) {
  return MARKET_TYPE_LABELS[marketType as keyof typeof MARKET_TYPE_LABELS] || marketType;
}

function footballOpportunityLabel(opportunity: Opportunity) {
  if (opportunity.opportunity_type === 'same_line_arbitrage') {
    return `Total goals ${opportunity.line ?? 2.5}`;
  }
  if (opportunity.opportunity_type === 'middle') {
    return 'Goals middle';
  }
  return 'Result combo';
}

function bookmakerName(bookmakerId: string, fallback?: string | null) {
  return fallback || bookmakerId;
}

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
  const [diagnosticsSport, setDiagnosticsSport] = useState<'basketball' | 'football'>('basketball');
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
      sport: 'basketball',
      limit: 200,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'tracked' }
  );
  const {
    data: footballMatches,
    isLoading: footballMatchesLoading,
    isError: footballMatchesError,
    error: footballMatchesLoadError,
  } = useMatches(
    {
      sport: 'football',
      limit: 200,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'football' }
  );
  const {
    data: footballOffers,
    isLoading: footballOffersLoading,
    isError: footballOffersError,
    error: footballOffersLoadError,
  } = useOutcomeOffers(
    {
      sport: 'football',
      limit: 500,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'football' }
  );
  const {
    data: footballOpportunities,
    isLoading: footballOpportunitiesLoading,
    isError: footballOpportunitiesError,
    error: footballOpportunitiesLoadError,
    refetch: refetchFootballOpportunities,
  } = useOpportunities(
    {
      sport: 'football',
      limit: 200,
      loadAll: true,
      bookmaker_ids: selectedBookmakerIds.length > 0 ? selectedBookmakerIds : undefined,
    },
    { enabled: activeTab === 'football' }
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
    if (scanJustFinished && activeTab === 'football') {
      void queryClient.invalidateQueries({ queryKey: ['matches'] });
      void queryClient.invalidateQueries({ queryKey: ['outcomeOffers'] });
      void queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      void refetchFootballOpportunities();
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
    refetchFootballOpportunities,
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
  const unresolvedWarningGroups = useMemo(
    () => groupUnresolvedOdds(unresolvedOdds ?? []),
    [unresolvedOdds]
  );
  const footballOpportunitiesByMatch = useMemo(() => {
    const byMatch = new Map<string, Opportunity[]>();
    for (const opportunity of footballOpportunities ?? []) {
      const list = byMatch.get(opportunity.match_id) ?? [];
      list.push(opportunity);
      byMatch.set(opportunity.match_id, list);
    }
    for (const list of byMatch.values()) {
      list.sort(
        (left, right) =>
          (right.profit_margin ?? Number.NEGATIVE_INFINITY) -
          (left.profit_margin ?? Number.NEGATIVE_INFINITY)
      );
    }
    return byMatch;
  }, [footballOpportunities]);
  const footballOffersByMatch = useMemo(() => {
    const byMatch = new Map<string, OutcomeOffer[]>();
    for (const offer of footballOffers ?? []) {
      const list = byMatch.get(offer.match_id) ?? [];
      list.push(offer);
      byMatch.set(offer.match_id, list);
    }
    return byMatch;
  }, [footballOffers]);
  const footballRows = useMemo<FootballMatchRow[]>(() => {
    const rows = (footballMatches ?? []).map((match) => ({
      match,
      offers: footballOffersByMatch.get(match.id) ?? [],
      opportunities: footballOpportunitiesByMatch.get(match.id) ?? [],
    }));
    rows.sort((left, right) => {
      const leftMargin = left.opportunities[0]?.profit_margin ?? Number.NEGATIVE_INFINITY;
      const rightMargin = right.opportunities[0]?.profit_margin ?? Number.NEGATIVE_INFINITY;
      if (leftMargin !== rightMargin) return rightMargin - leftMargin;
      return (left.match.start_time ?? '').localeCompare(right.match.start_time ?? '');
    });
    return rows;
  }, [footballMatches, footballOffersByMatch, footballOpportunitiesByMatch]);
  const footballSearchIndex = useMemo(
    () =>
      buildSearchIndex(footballRows, (row) => [
        row.match.home_team,
        row.match.away_team,
        row.match.league_name,
        ...row.match.available_bookmakers.map((bookmaker) => bookmaker.name),
      ]),
    [footballRows]
  );
  const filteredFootballRows = useMemo(
    () => filterSearchIndex(footballSearchIndex, appliedSearchQuery),
    [appliedSearchQuery, footballSearchIndex]
  );

  const discrepancyCount = discrepancies?.length ?? 0;
  const footballOpportunityCount = footballOpportunities?.length ?? 0;
  const filteredDiscrepancyCount = filteredDiscrepancies.length;
  const filteredFootballCount = filteredFootballRows.length;
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

  const footballContent = useMemo(() => {
    const isFootballLoading =
      footballMatchesLoading || footballOffersLoading || footballOpportunitiesLoading;
    const footballError =
      (footballMatchesError ? (footballMatchesLoadError as Error)?.message : null) ||
      (footballOffersError ? (footballOffersLoadError as Error)?.message : null) ||
      (footballOpportunitiesError ? (footballOpportunitiesLoadError as Error)?.message : null);

    if (isFootballLoading) {
      return <LoadingSpinner />;
    }

    if (footballError) {
      return (
        <div className="rounded-lg border border-danger/30 bg-danger/10 p-6 text-center">
          <p className="text-sm text-danger">Failed to load football board: {footballError}</p>
        </div>
      );
    }

    if (!footballRows.length) {
      return (
        <EmptyState
          title="No football games in the current snapshot"
          message="Enable football in the backend config and run a scrape to populate MaxBet and BalkanBet football markets."
        />
      );
    }

    if (hasSearchQuery && filteredFootballCount === 0) {
      return (
        <EmptyState
          title={`No football games match "${activeSearchLabel}"`}
          message="Search checks team, league, and bookmaker names after your current bookmaker filter."
        />
      );
    }

    return (
      <div className="space-y-3">
        {filteredFootballRows.map((row) => {
          const marketLabels = Array.from(
            new Set(row.offers.map((offer) => marketTypeLabel(offer.market_type)))
          ).sort((left, right) => left.localeCompare(right));
          const topOpportunity = row.opportunities[0];

          return (
            <section
              key={row.match.id}
              className={`rounded-lg border p-4 ${
                topOpportunity?.profit_margin != null
                  ? 'border-accent/25 bg-accent/[0.04]'
                  : 'border-border bg-surface'
              }`}
            >
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
                    <span>{row.match.league_name || row.match.league_id}</span>
                    <span>·</span>
                    <span>{formatDateTime(row.match.start_time)}</span>
                  </div>
                  <h3 className="mt-1 text-lg font-semibold text-text">
                    {row.match.home_team} <span className="text-text-muted">vs</span>{' '}
                    {row.match.away_team}
                  </h3>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {row.match.available_bookmakers.map((bookmaker) => (
                      <BookmakerBadge key={bookmaker.id} name={bookmaker.name} />
                    ))}
                    {marketLabels.map((label) => (
                      <span
                        key={label}
                        className="rounded-full border border-border bg-bg px-2 py-0.5 text-[11px] font-medium text-text-secondary"
                      >
                        {label}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="rounded-md border border-border bg-bg px-3 py-2 text-right">
                  <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">
                    Best edge
                  </div>
                  <div
                    className={`mt-1 font-mono text-lg font-semibold ${
                      topOpportunity?.profit_margin != null
                        ? profitColor(topOpportunity.profit_margin)
                        : 'text-text-muted'
                    }`}
                  >
                    {topOpportunity?.profit_margin != null
                      ? formatPercentage(topOpportunity.profit_margin)
                      : '—'}
                  </div>
                </div>
              </div>

              {row.opportunities.length > 0 ? (
                <div className="mt-4 space-y-2">
                  {row.opportunities.map((opportunity) => (
                    <div
                      key={opportunity.id}
                      className="rounded-md border border-border bg-bg p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <div className="font-medium text-text">
                            {footballOpportunityLabel(opportunity)}
                          </div>
                          <div className="text-xs text-text-muted">
                            {opportunity.opportunity_type === 'same_line_arbitrage'
                              ? 'Both sides cover the same 2.5 goals line.'
                              : 'Exact result paired with the complementary double chance.'}
                          </div>
                        </div>
                        {opportunity.profit_margin != null && (
                          <span className={`font-mono text-sm font-semibold ${profitColor(opportunity.profit_margin)}`}>
                            {formatPercentage(opportunity.profit_margin)}
                          </span>
                        )}
                      </div>
                      <div className="mt-3 grid gap-2 md:grid-cols-2">
                        {opportunity.legs.map((leg) => (
                          <div
                            key={`${opportunity.id}-${leg.bookmaker_id}-${leg.market_type}-${leg.outcome_code}`}
                            className="flex items-center justify-between rounded border border-border bg-surface px-3 py-2"
                          >
                            <div className="min-w-0">
                              <BookmakerBadge name={bookmakerName(leg.bookmaker_id, leg.bookmaker_name)} />
                              <div className="mt-1 text-xs text-text-muted">
                                {marketTypeLabel(leg.market_type)} · {footballOutcomeLabel(leg.outcome_code)}
                              </div>
                            </div>
                            <div className="font-mono text-base font-semibold text-text">
                              {formatOdds(leg.odds)}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-4 rounded-md border border-border bg-bg px-3 py-2 text-sm text-text-secondary">
                  No positive football opportunity on this game yet; offers are still tracked for coverage.
                </p>
              )}
            </section>
          );
        })}
      </div>
    );
  }, [
    activeSearchLabel,
    filteredFootballCount,
    filteredFootballRows,
    footballMatchesError,
    footballMatchesLoadError,
    footballMatchesLoading,
    footballOffersError,
    footballOffersLoadError,
    footballOffersLoading,
    footballOpportunitiesError,
    footballOpportunitiesLoadError,
    footballOpportunitiesLoading,
    footballRows.length,
    hasSearchQuery,
  ]);

  return (
    <PageShell
      eyebrow="Live board"
        title={
          activeTab === 'discrepancies'
            ? 'Find exploitable line gaps before the market closes.'
            : activeTab === 'football'
              ? 'Track football outcome opportunities across the fastest books.'
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
            : activeTab === 'football'
              ? 'All supported football games with MaxBet/BalkanBet coverage, 2.5 goals, match result, and double-chance pairs.'
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
                onClick={() => switchTab('football')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === 'football'
                    ? 'bg-surface-raised text-text'
                    : 'text-text-muted hover:text-text'
                }`}
              >
                Football
                {activeTab === 'football' && footballOpportunityCount > 0 && (
                  <span className="ml-1.5 font-mono text-xs text-accent">
                    {footballOpportunityCount}
                  </span>
                )}
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
          {activeTab === 'football' && (
            <OfferSearchStrip
              value={searchQuery}
              onChange={setSearchQuery}
              scopeLabel="Football"
              placeholder="Search team, league, or bookmaker names"
              resultCount={filteredFootballCount}
              totalCount={footballRows.length}
            />
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

        {activeTab === 'discrepancies' ? (
          discrepancyContent
        ) : activeTab === 'football' ? (
          footballContent
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
