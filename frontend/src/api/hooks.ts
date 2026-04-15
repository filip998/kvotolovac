import { useQuery, useMutation } from '@tanstack/react-query';
import client from './client';
import type {
  Bookmaker,
  League,
  Match,
  OddsOffer,
  Discrepancy,
  MatchingReviewApproval,
  MatchingReviewCase,
  MatchingReviewFilters,
  MatchingReviewSummary,
  SystemStatus,
  DiscrepancyFilters,
  TeamReviewAction,
  TeamReviewApproval,
  TeamReviewCase,
  TeamReviewFilters,
  UnresolvedOdds,
  UnresolvedOddsFilters,
} from './types';
import {
  mockBookmakers,
  mockLeagues,
  mockMatches,
  mockOddsOffers,
  mockDiscrepancies,
  mockMatchingReviewCases,
  mockMatchingReviewSummary,
  mockUnresolvedOdds,
  mockSystemStatus,
  mockTeamReviewCases,
} from './mockData';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

function delay(ms = 300): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function serializeArrayParam(values?: string[]): string | undefined {
  return values && values.length > 0 ? values.join(',') : undefined;
}

function updateMockMatchingReviewSummaryForApproval(caseItem: MatchingReviewCase) {
  const league = mockMatchingReviewSummary.leagues.find(
    (leagueRow) => leagueRow.league_id === caseItem.suggested_league_id
  );
  if (league && caseItem.status !== 'approved') {
    league.pending_reviews = Math.max(0, league.pending_reviews - 1);
    league.approved_reviews += 1;
  }
  if (caseItem.status !== 'approved') {
    mockMatchingReviewSummary.pending_reviews = Math.max(
      0,
      mockMatchingReviewSummary.pending_reviews - 1
    );
    mockMatchingReviewSummary.approved_reviews += 1;
  }
}

function updateMockTeamReviewCaseStatus(caseId: number, status: TeamReviewCase['status']): TeamReviewCase {
  const caseItem = mockTeamReviewCases.find((item) => item.id === caseId);
  if (!caseItem) {
    throw new Error('Team review case not found');
  }
  caseItem.status = status;
  return caseItem;
}

// --- Discrepancies ---

export function useDiscrepancies(
  filters: DiscrepancyFilters = {},
  options: { enabled?: boolean } = {}
) {
  const shouldLoadAll = !!filters.loadAll;

  return useQuery<Discrepancy[]>({
    queryKey: ['discrepancies', filters],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        let results = [...mockDiscrepancies];

        if (filters.league) {
          results = results.filter((d) => d.league_name === filters.league);
        }
        if (filters.bookmaker_ids?.length) {
          const selected = new Set(filters.bookmaker_ids);
          results = results.filter(
            (d) => selected.has(d.bookmaker_a_id) || selected.has(d.bookmaker_b_id)
          );
        }
        if (filters.market_type) {
          results = results.filter((d) => d.market_type === filters.market_type);
        }
        if (filters.min_gap !== undefined && filters.min_gap > 0) {
          results = results.filter((d) => d.gap >= filters.min_gap!);
        }

        const sortBy = filters.sort_by || 'profit_margin';
        const sortOrder = filters.sort_order || 'desc';
        results.sort((a, b) => {
          const aVal = (a[sortBy as keyof Discrepancy] as number | null | undefined) ?? Number.NEGATIVE_INFINITY;
          const bVal = (b[sortBy as keyof Discrepancy] as number | null | undefined) ?? Number.NEGATIVE_INFINITY;
          return sortOrder === 'desc' ? bVal - aVal : aVal - bVal;
        });

        return results;
      }

      const { loadAll, ...requestFilters } = filters;
      const serializedFilters = {
        ...requestFilters,
        bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
      };
      if (!loadAll) {
        const { data } = await client.get<Discrepancy[]>('/discrepancies', {
          params: serializedFilters,
        });
        return data;
      }

      const pageSize = requestFilters.limit ?? 200;
      const initialOffset = requestFilters.offset ?? 0;
      const allDiscrepancies: Discrepancy[] = [];

      for (let offset = initialOffset; ; offset += pageSize) {
        const { data } = await client.get<Discrepancy[]>('/discrepancies', {
          params: { ...serializedFilters, limit: pageSize, offset },
        });
        allDiscrepancies.push(...data);
        if (data.length < pageSize) {
          break;
        }
      }

      return allDiscrepancies;
    },
    enabled: options.enabled ?? true,
    placeholderData: (previousData) => previousData,
    staleTime: 30000,
    refetchInterval: options.enabled === false ? false : shouldLoadAll ? false : 30000,
  });
}

export function useDiscrepancy(id: number) {
  return useQuery<Discrepancy>({
    queryKey: ['discrepancy', id],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        const d = mockDiscrepancies.find((d) => d.id === id);
        if (!d) throw new Error('Not found');
        return d;
      }
      const { data } = await client.get<Discrepancy>(`/discrepancies/${id}`);
      return data;
    },
  });
}

// --- Unresolved odds ---

export function useUnresolvedOdds(
  filters: UnresolvedOddsFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<UnresolvedOdds[]>({
    queryKey: ['unresolvedOdds', filters],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        let results = [...mockUnresolvedOdds];

        if (filters.bookmaker_id) {
          results = results.filter((row) => row.bookmaker_id === filters.bookmaker_id);
        }
        if (filters.bookmaker_ids?.length) {
          const selected = new Set(filters.bookmaker_ids);
          results = results.filter((row) => selected.has(row.bookmaker_id));
        }
        if (filters.reason_code) {
          results = results.filter((row) => row.reason_code === filters.reason_code);
        }
        if (filters.market_type) {
          results = results.filter((row) => row.market_type === filters.market_type);
        }
        if (filters.league_id) {
          results = results.filter((row) => row.league_id === filters.league_id);
        }

        return results;
      }

      const { loadAll, ...requestFilters } = filters;
      const serializedFilters = {
        ...requestFilters,
        bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
      };
      if (!loadAll) {
        const { data } = await client.get<UnresolvedOdds[]>('/unresolved-odds', {
          params: serializedFilters,
        });
        return data;
      }

      const pageSize = requestFilters.limit ?? 200;
      const initialOffset = requestFilters.offset ?? 0;
      const allRows: UnresolvedOdds[] = [];

      for (let offset = initialOffset; ; offset += pageSize) {
        const { data } = await client.get<UnresolvedOdds[]>('/unresolved-odds', {
          params: { ...serializedFilters, limit: pageSize, offset },
        });
        allRows.push(...data);
        if (data.length < pageSize) {
          break;
        }
      }

      return allRows;
    },
    enabled: options.enabled ?? true,
    refetchInterval: options.enabled === false ? false : 30000,
  });
}

// --- Matching review ---

export function useMatchingReviewSummary(
  filters: Pick<MatchingReviewFilters, 'bookmaker_ids'> = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<MatchingReviewSummary>({
    queryKey: ['matchingReviewSummary', filters],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        return mockMatchingReviewSummary;
      }
      const { data } = await client.get<MatchingReviewSummary>('/matching-review/summary', {
        params: {
          bookmaker_ids: serializeArrayParam(filters.bookmaker_ids),
        },
      });
      return data;
    },
    enabled: options.enabled ?? true,
    refetchInterval: options.enabled === false ? false : 30000,
  });
}

export function useMatchingReviewCases(
  filters: MatchingReviewFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<MatchingReviewCase[]>({
    queryKey: ['matchingReviewCases', filters],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        let results = [...mockMatchingReviewCases];
        if (filters.bookmaker_id) {
          results = results.filter((row) => row.bookmaker_id === filters.bookmaker_id);
        }
        if (filters.bookmaker_ids?.length) {
          const selected = new Set(filters.bookmaker_ids);
          results = results.filter((row) => selected.has(row.bookmaker_id));
        }
        if (filters.league_id) {
          results = results.filter((row) => row.suggested_league_id === filters.league_id);
        }
        if (filters.status) {
          results = results.filter((row) => row.status === filters.status);
        }
        return results;
      }

      const { loadAll, ...requestFilters } = filters;
      const serializedFilters = {
        ...requestFilters,
        bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
      };
      if (!loadAll) {
        const { data } = await client.get<MatchingReviewCase[]>('/matching-review/cases', {
          params: serializedFilters,
        });
        return data;
      }

      const pageSize = requestFilters.limit ?? 200;
      const initialOffset = requestFilters.offset ?? 0;
      const allRows: MatchingReviewCase[] = [];

      for (let offset = initialOffset; ; offset += pageSize) {
        const { data } = await client.get<MatchingReviewCase[]>('/matching-review/cases', {
          params: { ...serializedFilters, limit: pageSize, offset },
        });
        allRows.push(...data);
        if (data.length < pageSize) {
          break;
        }
      }

      return allRows;
    },
    enabled: options.enabled ?? true,
    refetchInterval: options.enabled === false ? false : 30000,
  });
}

export function useApproveMatchingReviewCase() {
  return useMutation<
    MatchingReviewApproval,
    Error,
    { caseId: number; leagueId?: string }
  >({
    mutationFn: async ({ caseId, leagueId }) => {
      if (USE_MOCK) {
        await delay();
        const caseItem = mockMatchingReviewCases.find((item) => item.id === caseId);
        if (!caseItem) {
          throw new Error('Matching review case not found');
        }
        updateMockMatchingReviewSummaryForApproval(caseItem);
        caseItem.status = 'approved';
        return {
          case_id: caseId,
          status: 'approved',
          saved_alias: caseItem.raw_league_id,
          saved_league_id: leagueId ?? caseItem.suggested_league_id,
          saved_league_name: caseItem.suggested_league_name,
        };
      }

      const { data } = await client.post<MatchingReviewApproval>(
        `/matching-review/cases/${caseId}/approve`,
        leagueId ? { league_id: leagueId } : {}
      );
      return data;
    },
  });
}

export function useTeamReviewCases(
  filters: TeamReviewFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<TeamReviewCase[]>({
    queryKey: ['teamReviewCases', filters],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        let results = [...mockTeamReviewCases];
        if (filters.bookmaker_id) {
          results = results.filter((row) => row.bookmaker_id === filters.bookmaker_id);
        }
        if (filters.bookmaker_ids?.length) {
          const selected = new Set(filters.bookmaker_ids);
          results = results.filter((row) => selected.has(row.bookmaker_id));
        }
        if (filters.status) {
          results = results.filter((row) => row.status === filters.status);
        }
        return results;
      }

      const { loadAll, ...requestFilters } = filters;
      const serializedFilters = {
        ...requestFilters,
        bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
      };
      if (!loadAll) {
        const { data } = await client.get<TeamReviewCase[]>('/team-review/cases', {
          params: serializedFilters,
        });
        return data;
      }

      const pageSize = requestFilters.limit ?? 200;
      const initialOffset = requestFilters.offset ?? 0;
      const allRows: TeamReviewCase[] = [];

      for (let offset = initialOffset; ; offset += pageSize) {
        const { data } = await client.get<TeamReviewCase[]>('/team-review/cases', {
          params: { ...serializedFilters, limit: pageSize, offset },
        });
        allRows.push(...data);
        if (data.length < pageSize) {
          break;
        }
      }

      return allRows;
    },
    enabled: options.enabled ?? true,
    refetchInterval: options.enabled === false ? false : 30000,
  });
}

export function useApproveTeamReviewCase() {
  return useMutation<TeamReviewApproval, Error, { caseId: number }>({
    mutationFn: async ({ caseId }) => {
      if (USE_MOCK) {
        await delay();
        const caseItem = updateMockTeamReviewCaseStatus(caseId, 'approved');
        return {
          case_id: caseId,
          status: 'approved',
          saved_alias: caseItem.raw_team_name,
          saved_team_name: caseItem.suggested_team_name,
          resolved_team_name: null,
        };
      }

      const { data } = await client.post<TeamReviewApproval>(
        `/team-review/cases/${caseId}/approve`
      );
      return data;
    },
  });
}

export function useDeclineTeamReviewCase() {
  return useMutation<TeamReviewAction, Error, { caseId: number }>({
    mutationFn: async ({ caseId }) => {
      if (USE_MOCK) {
        await delay();
        updateMockTeamReviewCaseStatus(caseId, 'declined');
        return {
          case_id: caseId,
          status: 'declined',
        };
      }

      const { data } = await client.post<TeamReviewAction>(
        `/team-review/cases/${caseId}/decline`
      );
      return data;
    },
  });
}

// --- Matches ---

export function useMatches(
  params: {
    league?: string;
    status?: string;
    bookmaker_ids?: string[];
    limit?: number;
    offset?: number;
    loadAll?: boolean;
  } = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<Match[]>({
    queryKey: ['matches', params],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        let results = [...mockMatches];
        if (params.league) {
          results = results.filter((m) => m.league_id === params.league);
        }
        if (params.status) {
          results = results.filter((m) => m.status === params.status);
        }
        if (params.bookmaker_ids?.length) {
          const selected = new Set(params.bookmaker_ids);
          results = results.filter((m) =>
            m.available_bookmakers.some((bookmaker) => selected.has(bookmaker.id))
          );
        }
        return results;
      }

      const requestParams = {
        ...params,
        league_id: params.league,
        league: undefined,
        bookmaker_ids: serializeArrayParam(params.bookmaker_ids),
      };
      if (!params.loadAll) {
        const { data } = await client.get<Match[]>('/matches', { params: requestParams });
        return data;
      }

      const pageSize = params.limit ?? 200;
      const initialOffset = params.offset ?? 0;
      const allMatches: Match[] = [];

      for (let offset = initialOffset; ; offset += pageSize) {
        const { data } = await client.get<Match[]>('/matches', {
          params: { ...requestParams, limit: pageSize, offset },
        });
        allMatches.push(...data);
        if (data.length < pageSize) {
          break;
        }
      }

      return allMatches;
    },
    enabled: options.enabled ?? true,
  });
}

export function useMatch(id: string) {
  return useQuery<Match>({
    queryKey: ['match', id],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        const m = mockMatches.find((m) => m.id === id);
        if (!m) throw new Error('Match not found');
        return m;
      }
      const { data } = await client.get<Match>(`/matches/${id}`);
      return data;
    },
    enabled: !!id,
  });
}

export function useMatchOdds(matchId: string) {
  return useQuery<OddsOffer[]>({
    queryKey: ['matchOdds', matchId],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        return mockOddsOffers.filter((o) => o.match_id === matchId);
      }
      const { data } = await client.get<OddsOffer[]>(`/matches/${matchId}/odds`);
      return data;
    },
    enabled: !!matchId,
  });
}

export function useMatchHistory(matchId: string) {
  return useQuery<OddsOffer[]>({
    queryKey: ['matchHistory', matchId],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        return mockOddsOffers.filter((o) => o.match_id === matchId);
      }
      const { data } = await client.get<OddsOffer[]>(`/matches/${matchId}/history`);
      return data;
    },
    enabled: !!matchId,
  });
}

// --- Leagues ---

export function useLeagues() {
  return useQuery<League[]>({
    queryKey: ['leagues'],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        return mockLeagues;
      }
      const { data } = await client.get<League[]>('/leagues');
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useBookmakers() {
  return useQuery<Bookmaker[]>({
    queryKey: ['bookmakers'],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        return mockBookmakers;
      }
      const { data } = await client.get<Bookmaker[]>('/bookmakers');
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

// --- System Status ---

export function useSystemStatus() {
  return useQuery<SystemStatus>({
    queryKey: ['status'],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        return mockSystemStatus;
      }
      const { data } = await client.get<SystemStatus>('/status');
      return data;
    },
    refetchInterval: (query) => (query.state.data?.scan?.in_progress ? 2000 : 15000),
  });
}

// --- Scrape Trigger ---

export function useTriggerScrape() {
  return useMutation({
    mutationFn: async () => {
      if (USE_MOCK) {
        await delay(1000);
        return { message: 'Scrape triggered' };
      }
      const { data } = await client.post('/scrape/trigger');
      return data;
    },
  });
}
