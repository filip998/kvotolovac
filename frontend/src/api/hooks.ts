import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { dataSource } from './dataSource';
import type {
  Bookmaker,
  CanonicalTeam,
  CanonicalTeamFilters,
  CanonicalTeamMerge,
  CanonicalTeamPageFilters,
  CanonicalTeamsPage,
  CanonicalTeamUnmerge,
  EventDetail,
  EventMergeInput,
  EventMergeResult,
  EventOddsOffer,
  EventReviewAction,
  EventReviewCase,
  EventReviewFilters,
  League,
  Match,
  MatchMergeInput,
  MatchMergeResult,
  OddsOffer,
  Opportunity,
  OpportunityFilters,
  OutcomeOffer,
  OutcomeOfferFilters,
  ScrapeRuntimeSettingsUpdate,
  ScrapeSettingsResponse,
  SystemStatus,
  TeamReviewAction,
  TeamReviewApproval,
  TeamReviewCase,
  TeamReviewFilters,
  TelegramNotificationProfile,
  TelegramNotificationProfileInput,
  TelegramSettingsResponse,
  TelegramTestMessageResponse,
  UnresolvedOdds,
  UnresolvedOddsFilters,
} from './types';
import type {
  CanonicalTeamMergeVariables,
  CanonicalTeamUnmergeVariables,
  DeleteTelegramProfileResult,
  EventReviewAcceptVariables,
  EventReviewCaseVariables,
  MatchFilters,
  TeamReviewApprovalVariables,
  TeamReviewCaseVariables,
  TelegramProfileVariables,
  TriggerScrapeResult,
  UpdateTelegramProfileVariables,
} from './dataSource/types';

// --- Generic opportunities / outcome offers ---

export function useOpportunities(
  filters: OpportunityFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<Opportunity[]>({
    queryKey: ['opportunities', filters],
    queryFn: () => dataSource.getOpportunities(filters),
    enabled: options.enabled ?? true,
    placeholderData: (previousData) => previousData,
    staleTime: 30000,
    refetchInterval: options.enabled === false ? false : filters.loadAll ? false : 30000,
  });
}

export function useOutcomeOffers(
  filters: OutcomeOfferFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<OutcomeOffer[]>({
    queryKey: ['outcomeOffers', filters],
    queryFn: () => dataSource.getOutcomeOffers(filters),
    enabled: options.enabled ?? true,
    placeholderData: (previousData) => previousData,
    staleTime: 30000,
    refetchInterval: options.enabled === false ? false : filters.loadAll ? false : 30000,
  });
}

// --- Unresolved odds ---

export function useUnresolvedOdds(
  filters: UnresolvedOddsFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<UnresolvedOdds[]>({
    queryKey: ['unresolvedOdds', filters],
    queryFn: () => dataSource.getUnresolvedOdds(filters),
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
    queryFn: () => dataSource.getTeamReviewCases(filters),
    enabled: options.enabled ?? true,
    refetchInterval: options.enabled === false ? false : 30000,
  });
}

export function useApproveTeamReviewCase() {
  return useMutation<TeamReviewApproval, Error, TeamReviewApprovalVariables>({
    mutationFn: (variables) => dataSource.approveTeamReviewCase(variables),
  });
}

export function useDeclineTeamReviewCase() {
  return useMutation<TeamReviewAction, Error, TeamReviewCaseVariables>({
    mutationFn: (variables) => dataSource.declineTeamReviewCase(variables),
  });
}

export function useEventReviewCases(
  filters: EventReviewFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<EventReviewCase[]>({
    queryKey: ['eventReviewCases', filters],
    queryFn: () => dataSource.getEventReviewCases(filters),
    enabled: options.enabled ?? true,
    refetchInterval: options.enabled === false ? false : 30000,
  });
}

export function useAcceptEventReviewCase() {
  return useMutation<EventReviewAction, Error, EventReviewAcceptVariables>({
    mutationFn: (variables) => dataSource.acceptEventReviewCase(variables),
  });
}

export function useDeclineEventReviewCase() {
  return useMutation<EventReviewAction, Error, EventReviewCaseVariables>({
    mutationFn: (variables) => dataSource.declineEventReviewCase(variables),
  });
}

export function useCanonicalTeams(
  filters: CanonicalTeamFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<CanonicalTeam[]>({
    queryKey: ['canonicalTeams', filters],
    queryFn: () => dataSource.getCanonicalTeams(filters),
    enabled: options.enabled ?? true,
    staleTime: 30000,
  });
}

export function useCanonicalTeamsPage(
  filters: CanonicalTeamPageFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<CanonicalTeamsPage>({
    queryKey: ['canonicalTeams', 'page', filters],
    queryFn: () => dataSource.getCanonicalTeamsPage(filters),
    enabled: options.enabled ?? true,
    placeholderData: (previousData) => previousData,
    staleTime: 30000,
  });
}

export function useMergeCanonicalTeam() {
  return useMutation<CanonicalTeamMerge, Error, CanonicalTeamMergeVariables>({
    mutationFn: (variables) => dataSource.mergeCanonicalTeam(variables),
  });
}

export function useUnmergeCanonicalTeam() {
  return useMutation<CanonicalTeamUnmerge, Error, CanonicalTeamUnmergeVariables>({
    mutationFn: (variables) => dataSource.unmergeCanonicalTeam(variables),
  });
}

// --- Matches ---

export function useMatches(
  params: MatchFilters = {},
  options: { enabled?: boolean } = {}
) {
  return useQuery<Match[]>({
    queryKey: ['matches', params],
    queryFn: () => dataSource.getMatches(params),
    enabled: options.enabled ?? true,
  });
}

export function useMatch(id: string) {
  return useQuery<Match>({
    queryKey: ['match', id],
    queryFn: () => dataSource.getMatch(id),
    enabled: !!id,
  });
}

export function useEvent(id: string) {
  return useQuery<EventDetail>({
    queryKey: ['event', id],
    queryFn: () => dataSource.getEvent(id),
    enabled: !!id,
  });
}

export function useMergeMatches() {
  return useMutation<MatchMergeResult, Error, MatchMergeInput>({
    mutationFn: (payload) => dataSource.mergeMatches(payload),
  });
}

export function useMergeEvents() {
  return useMutation<EventMergeResult, Error, EventMergeInput>({
    mutationFn: (payload) => dataSource.mergeEvents(payload),
  });
}

export function useMatchOdds(matchId: string) {
  return useQuery<OddsOffer[]>({
    queryKey: ['matchOdds', matchId],
    queryFn: () => dataSource.getMatchOdds(matchId),
    enabled: !!matchId,
  });
}

export function useEventOdds(eventId: string) {
  return useQuery<EventOddsOffer[]>({
    queryKey: ['eventOdds', eventId],
    queryFn: () => dataSource.getEventOdds(eventId),
    enabled: !!eventId,
  });
}

export function useMatchOutcomeOffers(matchId: string) {
  return useQuery<OutcomeOffer[]>({
    queryKey: ['matchOutcomeOffers', matchId],
    queryFn: () => dataSource.getMatchOutcomeOffers(matchId),
    enabled: !!matchId,
  });
}

export function useEventOutcomeOffers(eventId: string) {
  return useQuery<OutcomeOffer[]>({
    queryKey: ['eventOutcomeOffers', eventId],
    queryFn: () => dataSource.getEventOutcomeOffers(eventId),
    enabled: !!eventId,
  });
}

export function useMatchHistory(matchId: string) {
  return useQuery<OddsOffer[]>({
    queryKey: ['matchHistory', matchId],
    queryFn: () => dataSource.getMatchHistory(matchId),
    enabled: !!matchId,
  });
}

// --- Leagues ---

export function useLeagues() {
  return useQuery<League[]>({
    queryKey: ['leagues'],
    queryFn: () => dataSource.getLeagues(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useBookmakers() {
  return useQuery<Bookmaker[]>({
    queryKey: ['bookmakers'],
    queryFn: () => dataSource.getBookmakers(),
    staleTime: 5 * 60 * 1000,
  });
}

// --- System Status ---

export function useSystemStatus() {
  return useQuery<SystemStatus>({
    queryKey: ['status'],
    queryFn: () => dataSource.getSystemStatus(),
    refetchInterval: (query) => (query.state.data?.scan?.in_progress ? 2000 : 15000),
  });
}

// --- Runtime Scrape Settings ---

export function useScrapeSettings() {
  return useQuery<ScrapeSettingsResponse>({
    queryKey: ['settings', 'scrape'],
    queryFn: () => dataSource.getScrapeSettings(),
    staleTime: 30 * 1000,
  });
}

export function useUpdateScrapeSettings() {
  const queryClient = useQueryClient();
  return useMutation<ScrapeSettingsResponse, Error, ScrapeRuntimeSettingsUpdate>({
    mutationFn: (payload) => dataSource.updateScrapeSettings(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['settings', 'scrape'] });
      void queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });
}

// --- Telegram Settings ---

export function useTelegramSettings() {
  return useQuery<TelegramSettingsResponse>({
    queryKey: ['settings', 'telegram'],
    queryFn: () => dataSource.getTelegramSettings(),
    staleTime: 30 * 1000,
  });
}

export function useCreateTelegramProfile() {
  const queryClient = useQueryClient();
  return useMutation<TelegramNotificationProfile, Error, TelegramNotificationProfileInput>({
    mutationFn: (payload) => dataSource.createTelegramProfile(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['settings', 'telegram'] });
    },
  });
}

export function useUpdateTelegramProfile() {
  const queryClient = useQueryClient();
  return useMutation<TelegramNotificationProfile, Error, UpdateTelegramProfileVariables>({
    mutationFn: (variables) => dataSource.updateTelegramProfile(variables),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['settings', 'telegram'] });
    },
  });
}

export function useDeleteTelegramProfile() {
  const queryClient = useQueryClient();
  return useMutation<DeleteTelegramProfileResult, Error, TelegramProfileVariables>({
    mutationFn: (variables) => dataSource.deleteTelegramProfile(variables),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['settings', 'telegram'] });
    },
  });
}

export function useTestTelegramProfile() {
  return useMutation<TelegramTestMessageResponse, Error, TelegramProfileVariables>({
    mutationFn: (variables) => dataSource.testTelegramProfile(variables),
  });
}

// --- Scrape Trigger ---

export function useTriggerScrape() {
  return useMutation<TriggerScrapeResult, Error>({
    mutationFn: () => dataSource.triggerScrape(),
  });
}
