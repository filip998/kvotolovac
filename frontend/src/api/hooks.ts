import { useQuery, useMutation } from '@tanstack/react-query';
import client from './client';
import type { League, Match, OddsOffer, Discrepancy, SystemStatus, DiscrepancyFilters } from './types';
import {
  mockLeagues,
  mockMatches,
  mockOddsOffers,
  mockDiscrepancies,
  mockSystemStatus,
} from './mockData';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

function delay(ms = 300): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --- Discrepancies ---

export function useDiscrepancies(filters: DiscrepancyFilters = {}) {
  return useQuery<Discrepancy[]>({
    queryKey: ['discrepancies', filters],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        let results = [...mockDiscrepancies];

        if (filters.league) {
          results = results.filter((d) => d.league_name === filters.league);
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
          const aVal = a[sortBy as keyof Discrepancy] as number;
          const bVal = b[sortBy as keyof Discrepancy] as number;
          return sortOrder === 'desc' ? bVal - aVal : aVal - bVal;
        });

        return results;
      }
      const { data } = await client.get<Discrepancy[]>('/discrepancies', { params: filters });
      return data;
    },
    refetchInterval: 30000,
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

// --- Matches ---

export function useMatches(
  params: {
    league?: string;
    status?: string;
    limit?: number;
    offset?: number;
    loadAll?: boolean;
  } = {}
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
        return results;
      }

      if (!params.loadAll) {
        const { data } = await client.get<Match[]>('/matches', { params });
        return data;
      }

      const pageSize = params.limit ?? 200;
      const initialOffset = params.offset ?? 0;
      const allMatches: Match[] = [];

      for (let offset = initialOffset; ; offset += pageSize) {
        const { data } = await client.get<Match[]>('/matches', {
          params: { ...params, limit: pageSize, offset },
        });
        allMatches.push(...data);
        if (data.length < pageSize) {
          break;
        }
      }

      return allMatches;
    },
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
    refetchInterval: 15000,
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
