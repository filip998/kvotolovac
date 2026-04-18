import { useQuery, useMutation } from '@tanstack/react-query';
import client from './client';
import type {
  Bookmaker,
  CanonicalTeam,
  CanonicalTeamFilters,
  CanonicalTeamMerge,
  League,
  Match,
  MatchMergeInput,
  MatchMergeResult,
  OddsOffer,
  Discrepancy,
  SystemStatus,
  DiscrepancyFilters,
  TeamReviewAction,
  TeamReviewApproval,
  TeamReviewApprovalInput,
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
  mockUnresolvedOdds,
  mockSystemStatus,
  mockCanonicalTeams,
  mockTeamReviewCases,
} from './mockData';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';

function delay(ms = 300): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function serializeArrayParam(values?: string[]): string | undefined {
  return values && values.length > 0 ? values.join(',') : undefined;
}

function updateMockTeamReviewCaseStatus(caseId: number, status: TeamReviewCase['status']): TeamReviewCase {
  const caseItem = mockTeamReviewCases.find((item) => item.id === caseId);
  if (!caseItem) {
    throw new Error('Team review case not found');
  }
  caseItem.status = status;
  return caseItem;
}

function appendMockCanonicalAlias(team: CanonicalTeam, alias: string) {
  const normalizedAlias = alias.trim();
  if (!normalizedAlias) {
    return;
  }
  if (!team.aliases.includes(normalizedAlias)) {
    team.aliases = [normalizedAlias, ...team.aliases];
    team.alias_count = team.aliases.length;
  }
}

function nextMockCanonicalTeamId() {
  return Math.max(0, ...mockCanonicalTeams.map((team) => team.id)) + 1;
}

function resolveMockTeamReviewApproval(
  caseItem: TeamReviewCase,
  payload: TeamReviewApprovalInput
): { savedTeamId: number; savedTeamName: string } {
  const createTeamName = payload.create_team_name?.trim();
  if (createTeamName) {
    const existingTeam = mockCanonicalTeams.find((team) => team.display_name === createTeamName);
    if (existingTeam) {
      appendMockCanonicalAlias(existingTeam, caseItem.raw_team_name);
      return {
        savedTeamId: existingTeam.id,
        savedTeamName: existingTeam.display_name,
      };
    }

    const newTeam: CanonicalTeam = {
      id: nextMockCanonicalTeamId(),
      sport: caseItem.sport,
      display_name: createTeamName,
      aliases: [caseItem.raw_team_name, createTeamName],
      alias_count: 2,
      merged_into_team_id: null,
    };
    mockCanonicalTeams.unshift(newTeam);
    return {
      savedTeamId: newTeam.id,
      savedTeamName: newTeam.display_name,
    };
  }

  if (payload.team_id != null) {
    const targetTeam = mockCanonicalTeams.find((team) => team.id === payload.team_id);
    if (!targetTeam) {
      throw new Error('Canonical team not found');
    }
    appendMockCanonicalAlias(targetTeam, caseItem.raw_team_name);
    return {
      savedTeamId: targetTeam.id,
      savedTeamName: targetTeam.display_name,
    };
  }

  const suggestedTeamId = caseItem.suggested_team_id ?? caseItem.candidate_teams[0]?.team_id ?? null;
  const suggestedTeamName =
    caseItem.suggested_team_name ?? caseItem.candidate_teams[0]?.team_name ?? null;

  if (!suggestedTeamName) {
    throw new Error('No suggested team available for this review case');
  }

  const existingTeam =
    (suggestedTeamId != null
      ? mockCanonicalTeams.find((team) => team.id === suggestedTeamId)
      : undefined) ??
    mockCanonicalTeams.find((team) => team.display_name === suggestedTeamName);

  if (existingTeam) {
    appendMockCanonicalAlias(existingTeam, caseItem.raw_team_name);
    return {
      savedTeamId: existingTeam.id,
      savedTeamName: existingTeam.display_name,
    };
  }

  const createdTeam: CanonicalTeam = {
    id: suggestedTeamId ?? nextMockCanonicalTeamId(),
    sport: caseItem.sport,
    display_name: suggestedTeamName,
    aliases: [caseItem.raw_team_name, suggestedTeamName],
    alias_count: 2,
    merged_into_team_id: null,
  };
  mockCanonicalTeams.unshift(createdTeam);
  return {
    savedTeamId: createdTeam.id,
    savedTeamName: createdTeam.display_name,
  };
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
  return useMutation<TeamReviewApproval, Error, { caseId: number } & TeamReviewApprovalInput>({
    mutationFn: async ({ caseId, team_id, create_team_name }) => {
      if (USE_MOCK) {
        await delay();
        const caseItem = mockTeamReviewCases.find((item) => item.id === caseId);
        if (!caseItem) {
          throw new Error('Team review case not found');
        }
        const target = resolveMockTeamReviewApproval(caseItem, {
          team_id,
          create_team_name,
        });
        const updatedCaseItem = updateMockTeamReviewCaseStatus(caseId, 'approved');
        return {
          case_id: caseId,
          status: 'approved',
          saved_alias: updatedCaseItem.raw_team_name,
          saved_team_id: target.savedTeamId,
          saved_team_name: target.savedTeamName,
          resolved_team_name: null,
        };
      }

      const payload =
        team_id != null || (create_team_name?.trim()?.length ?? 0) > 0
          ? {
              team_id,
              create_team_name,
            }
          : {};
      const { data } = await client.post<TeamReviewApproval>(`/team-review/cases/${caseId}/approve`, payload);
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

export function useCanonicalTeams(
  filters: CanonicalTeamFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<CanonicalTeam[]>({
    queryKey: ['canonicalTeams', filters],
    queryFn: async () => {
      if (USE_MOCK) {
        await delay();
        const search = filters.search?.trim().toLowerCase();
        let results = [...mockCanonicalTeams];
        if (filters.sport) {
          results = results.filter((team) => team.sport === filters.sport);
        }
        if (search) {
          results = results.filter((team) =>
            [team.display_name, ...team.aliases].some((value) => value.toLowerCase().includes(search))
          );
        }
        const offset = filters.offset ?? 0;
        const limit = filters.limit ?? results.length;
        return results.slice(offset, offset + limit);
      }

      const { data } = await client.get<CanonicalTeam[]>('/canonical-teams', {
        params: filters,
      });
      return data;
    },
    enabled: options.enabled ?? true,
    staleTime: 30000,
  });
}

export function useMergeCanonicalTeam() {
  return useMutation<
    CanonicalTeamMerge,
    Error,
    { sourceTeamId: number; targetTeamId: number }
  >({
    mutationFn: async ({ sourceTeamId, targetTeamId }) => {
      if (USE_MOCK) {
        await delay();
        if (sourceTeamId === targetTeamId) {
          throw new Error('Cannot merge a canonical team into itself');
        }

        const sourceIndex = mockCanonicalTeams.findIndex((team) => team.id === sourceTeamId);
        const targetTeam = mockCanonicalTeams.find((team) => team.id === targetTeamId);
        if (sourceIndex === -1 || !targetTeam) {
          throw new Error('Canonical team not found');
        }

        const [sourceTeam] = mockCanonicalTeams.splice(sourceIndex, 1);
        targetTeam.aliases = Array.from(
          new Set([sourceTeam.display_name, ...sourceTeam.aliases, ...targetTeam.aliases])
        ).sort((left, right) => left.localeCompare(right));
        targetTeam.alias_count = targetTeam.aliases.length;

        return {
          source_team_id: sourceTeamId,
          target_team_id: targetTeamId,
          merged_team_name: targetTeam.display_name,
          matches_scraped: 0,
          odds_scraped: 0,
          discrepancies_found: 0,
        };
      }

      const { data } = await client.post<CanonicalTeamMerge>(
        `/canonical-teams/${sourceTeamId}/merge`,
        { target_team_id: targetTeamId }
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

export function useMergeMatches() {
  return useMutation<MatchMergeResult, Error, MatchMergeInput>({
    mutationFn: async (payload) => {
      if (USE_MOCK) {
        await delay();
        if (!payload.source_match_ids.length) {
          throw new Error('source_match_ids must not be empty');
        }
        if (payload.source_match_ids.includes(payload.target_match_id)) {
          throw new Error('target_match_id must not appear in source_match_ids');
        }
        const target = mockMatches.find((m) => m.id === payload.target_match_id);
        if (!target) throw new Error(`Target match ${payload.target_match_id} not found`);
        for (const sid of payload.source_match_ids) {
          const src = mockMatches.find((m) => m.id === sid);
          if (!src) throw new Error(`Source match ${sid} not found`);
          if ((src.start_time ?? '') !== (target.start_time ?? '')) {
            throw new Error(`Source match ${sid} start_time differs from target`);
          }
        }
        // Mutate mocks: drop sources, "transfer" their bookmakers into target
        const dropped = new Set(payload.source_match_ids);
        for (let i = mockMatches.length - 1; i >= 0; i--) {
          const m = mockMatches[i];
          if (dropped.has(m.id)) {
            for (const bm of m.available_bookmakers) {
              if (!target.available_bookmakers.some((b) => b.id === bm.id)) {
                target.available_bookmakers.push(bm);
              }
            }
            mockMatches.splice(i, 1);
          }
        }
        return {
          target_match_id: payload.target_match_id,
          merged_source_match_ids: [...payload.source_match_ids],
          merged_team_ids: [...payload.team_pairings],
          reassigned_odds: 0,
          reassigned_odds_history: 0,
          reassigned_discrepancies: 0,
          deleted_source_matches: payload.source_match_ids.length,
        };
      }
      const { data } = await client.post<MatchMergeResult>('/matches/merge', payload);
      return data;
    },
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
