import client from '../client';
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
} from '../types';
import type {
  CanonicalTeamMergeVariables,
  CanonicalTeamUnmergeVariables,
  DataSource,
  DeleteTelegramProfileResult,
  EventReviewAcceptVariables,
  EventReviewCaseVariables,
  MatchFilters,
  TeamReviewApprovalVariables,
  TeamReviewCaseVariables,
  TelegramProfileVariables,
  TriggerScrapeResult,
  UpdateTelegramProfileVariables,
} from './types';

function serializeArrayParam(values?: string[]): string | undefined {
  return values && values.length > 0 ? values.join(',') : undefined;
}

async function loadAllPages<T>(
  getPage: (offset: number, limit: number) => Promise<T[]>,
  pageSize: number,
  initialOffset: number
): Promise<T[]> {
  const allRows: T[] = [];
  for (let offset = initialOffset; ; offset += pageSize) {
    const data = await getPage(offset, pageSize);
    allRows.push(...data);
    if (data.length < pageSize) {
      break;
    }
  }
  return allRows;
}

export const httpDataSource: DataSource = {
  async getOpportunities(filters: OpportunityFilters = {}) {
    const { loadAll, ...requestFilters } = filters;
    const params = {
      ...requestFilters,
      bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
    };
    if (!loadAll) {
      const { data } = await client.get<Opportunity[]>('/opportunities', { params });
      return data;
    }

    const pageSize = requestFilters.limit ?? 200;
    const initialOffset = requestFilters.offset ?? 0;
    return loadAllPages(
      async (offset, limit) => {
        const { data } = await client.get<Opportunity[]>('/opportunities', {
          params: { ...params, limit, offset },
        });
        return data;
      },
      pageSize,
      initialOffset
    );
  },

  async getOutcomeOffers(filters: OutcomeOfferFilters = {}) {
    const { loadAll, ...requestFilters } = filters;
    const params = {
      ...requestFilters,
      bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
    };
    if (!loadAll) {
      const { data } = await client.get<OutcomeOffer[]>('/market-offers', { params });
      return data;
    }

    const pageSize = requestFilters.limit ?? 500;
    const initialOffset = requestFilters.offset ?? 0;
    return loadAllPages(
      async (offset, limit) => {
        const { data } = await client.get<OutcomeOffer[]>('/market-offers', {
          params: { ...params, limit, offset },
        });
        return data;
      },
      pageSize,
      initialOffset
    );
  },

  async getUnresolvedOdds(filters: UnresolvedOddsFilters = {}) {
    const { loadAll, ...requestFilters } = filters;
    const params = {
      ...requestFilters,
      bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
    };
    if (!loadAll) {
      const { data } = await client.get<UnresolvedOdds[]>('/unresolved-odds', {
        params,
      });
      return data;
    }

    const pageSize = requestFilters.limit ?? 200;
    const initialOffset = requestFilters.offset ?? 0;
    return loadAllPages(
      async (offset, limit) => {
        const { data } = await client.get<UnresolvedOdds[]>('/unresolved-odds', {
          params: { ...params, limit, offset },
        });
        return data;
      },
      pageSize,
      initialOffset
    );
  },

  async getTeamReviewCases(filters: TeamReviewFilters = {}) {
    const { loadAll, ...requestFilters } = filters;
    const params = {
      ...requestFilters,
      bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
    };
    if (!loadAll) {
      const { data } = await client.get<TeamReviewCase[]>('/team-review/cases', {
        params,
      });
      return data;
    }

    const pageSize = requestFilters.limit ?? 200;
    const initialOffset = requestFilters.offset ?? 0;
    return loadAllPages(
      async (offset, limit) => {
        const { data } = await client.get<TeamReviewCase[]>('/team-review/cases', {
          params: { ...params, limit, offset },
        });
        return data;
      },
      pageSize,
      initialOffset
    );
  },

  async approveTeamReviewCase({
    caseId,
    team_id,
    create_team_name,
  }: TeamReviewApprovalVariables) {
    const payload =
      team_id != null || (create_team_name?.trim()?.length ?? 0) > 0
        ? {
            team_id,
            create_team_name,
          }
        : {};
    const { data } = await client.post<TeamReviewApproval>(
      `/team-review/cases/${caseId}/approve`,
      payload
    );
    return data;
  },

  async declineTeamReviewCase({ caseId }: TeamReviewCaseVariables) {
    const { data } = await client.post<TeamReviewAction>(
      `/team-review/cases/${caseId}/decline`
    );
    return data;
  },

  async getEventReviewCases(filters: EventReviewFilters = {}) {
    const { loadAll, ...requestFilters } = filters;
    const params = {
      ...requestFilters,
      bookmaker_ids: serializeArrayParam(requestFilters.bookmaker_ids),
    };
    if (!loadAll) {
      const { data } = await client.get<EventReviewCase[]>('/event-review/cases', {
        params,
      });
      return data;
    }

    const pageSize = requestFilters.limit ?? 200;
    const initialOffset = requestFilters.offset ?? 0;
    return loadAllPages(
      async (offset, limit) => {
        const { data } = await client.get<EventReviewCase[]>('/event-review/cases', {
          params: { ...params, limit, offset },
        });
        return data;
      },
      pageSize,
      initialOffset
    );
  },

  async acceptEventReviewCase({ caseId, primaryMatchId }: EventReviewAcceptVariables) {
    const payload = primaryMatchId ? { primary_match_id: primaryMatchId } : {};
    const { data } = await client.post<EventReviewAction>(
      `/event-review/cases/${caseId}/accept`,
      payload
    );
    return data;
  },

  async declineEventReviewCase({ caseId }: EventReviewCaseVariables) {
    const { data } = await client.post<EventReviewAction>(
      `/event-review/cases/${caseId}/decline`
    );
    return data;
  },

  async getCanonicalTeams(filters: CanonicalTeamFilters = {}) {
    const { data } = await client.get<CanonicalTeam[]>('/canonical-teams', {
      params: filters,
    });
    return data;
  },

  async getCanonicalTeamsPage(filters: CanonicalTeamPageFilters = {}) {
    const { data } = await client.get<CanonicalTeamsPage>('/canonical-teams/page', {
      params: filters,
    });
    return data;
  },

  async mergeCanonicalTeam({
    sourceTeamId,
    targetTeamId,
  }: CanonicalTeamMergeVariables) {
    const { data } = await client.post<CanonicalTeamMerge>(
      `/canonical-teams/${sourceTeamId}/merge`,
      { target_team_id: targetTeamId }
    );
    return data;
  },

  async unmergeCanonicalTeam({ sourceTeamId }: CanonicalTeamUnmergeVariables) {
    const { data } = await client.post<CanonicalTeamUnmerge>(
      `/canonical-teams/${sourceTeamId}/unmerge`
    );
    return data;
  },

  async getMatches(params: MatchFilters = {}) {
    const { loadAll, ...requestFilters } = params;
    const requestParams = {
      ...requestFilters,
      league_id: params.league,
      league: undefined,
      bookmaker_ids: serializeArrayParam(params.bookmaker_ids),
    };
    if (!loadAll) {
      const { data } = await client.get<Match[]>('/matches', { params: requestParams });
      return data;
    }

    const pageSize = requestFilters.limit ?? 200;
    const initialOffset = requestFilters.offset ?? 0;
    return loadAllPages(
      async (offset, limit) => {
        const { data } = await client.get<Match[]>('/matches', {
          params: { ...requestParams, limit, offset },
        });
        return data;
      },
      pageSize,
      initialOffset
    );
  },

  async getMatch(id: string) {
    const { data } = await client.get<Match>(`/matches/${id}`);
    return data;
  },

  async getEvent(id: string) {
    const { data } = await client.get<EventDetail>(`/events/${id}`);
    return data;
  },

  async mergeMatches(payload: MatchMergeInput) {
    const { data } = await client.post<MatchMergeResult>('/matches/merge', payload);
    return data;
  },

  async mergeEvents(payload: EventMergeInput) {
    const { data } = await client.post<EventMergeResult>('/event-review/merge', payload);
    return data;
  },

  async getMatchOdds(matchId: string) {
    const { data } = await client.get<OddsOffer[]>(`/matches/${matchId}/odds`);
    return data;
  },

  async getEventOdds(eventId: string) {
    const { data } = await client.get<EventOddsOffer[]>(`/events/${eventId}/odds`);
    return data;
  },

  async getMatchOutcomeOffers(matchId: string) {
    const { data } = await client.get<OutcomeOffer[]>(
      `/matches/${matchId}/market-offers`
    );
    return data;
  },

  async getEventOutcomeOffers(eventId: string) {
    const { data } = await client.get<OutcomeOffer[]>(
      `/events/${eventId}/market-offers`
    );
    return data;
  },

  async getMatchHistory(matchId: string) {
    const { data } = await client.get<OddsOffer[]>(`/matches/${matchId}/history`);
    return data;
  },

  async getLeagues() {
    const { data } = await client.get<League[]>('/leagues');
    return data;
  },

  async getBookmakers() {
    const { data } = await client.get<Bookmaker[]>('/bookmakers');
    return data;
  },

  async getSystemStatus() {
    const { data } = await client.get<SystemStatus>('/status');
    return data;
  },

  async getScrapeSettings() {
    const { data } = await client.get<ScrapeSettingsResponse>('/settings/scrape');
    return data;
  },

  async updateScrapeSettings(payload: ScrapeRuntimeSettingsUpdate) {
    const { data } = await client.patch<ScrapeSettingsResponse>(
      '/settings/scrape',
      payload
    );
    return data;
  },

  async getTelegramSettings() {
    const { data } = await client.get<TelegramSettingsResponse>('/settings/telegram');
    return data;
  },

  async createTelegramProfile(payload: TelegramNotificationProfileInput) {
    const { data } = await client.post<TelegramNotificationProfile>(
      '/settings/telegram/profiles',
      payload
    );
    return data;
  },

  async updateTelegramProfile({ profileId, payload }: UpdateTelegramProfileVariables) {
    const { data } = await client.patch<TelegramNotificationProfile>(
      `/settings/telegram/profiles/${profileId}`,
      payload
    );
    return data;
  },

  async deleteTelegramProfile({ profileId }: TelegramProfileVariables) {
    const { data } = await client.delete<DeleteTelegramProfileResult>(
      `/settings/telegram/profiles/${profileId}`
    );
    return data;
  },

  async testTelegramProfile({ profileId }: TelegramProfileVariables) {
    const { data } = await client.post<TelegramTestMessageResponse>(
      `/settings/telegram/profiles/${profileId}/test`
    );
    return data;
  },

  async triggerScrape() {
    const { data } = await client.post<TriggerScrapeResult>('/scrape/trigger');
    return data;
  },
};
