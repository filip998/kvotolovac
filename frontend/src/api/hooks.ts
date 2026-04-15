import { useQuery, useMutation } from '@tanstack/react-query';
import client from './client';
import type {
  Bookmaker,
  League,
  Match,
  OddsOffer,
  Discrepancy,
  SystemStatus,
  DiscrepancyFilters,
  UnresolvedOdds,
  UnresolvedOddsFilters,
} from './types';
import {
  mockBookmakers,
  mockLeagues,
  mockMatches,
  mockOddsOffers,
  mockDiscrepancies,
  mockUnresolvedOdds,
  mockSystemStatus,
} from './mockData';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

function delay(ms = 300): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function serializeArrayParam(values?: string[]): string | undefined {
  return values && values.length > 0 ? values.join(',') : undefined;
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
