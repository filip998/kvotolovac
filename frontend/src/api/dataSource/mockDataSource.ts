import {
  mockBookmakers,
  mockCanonicalTeams,
  mockEventOddsOffers,
  mockEventReviewCases,
  mockEvents,
  mockLeagues,
  mockMatches,
  mockOddsOffers,
  mockOpportunities,
  mockOutcomeOffers,
  mockScrapeSettings,
  mockSystemStatus,
  mockTeamReviewCases,
  mockTelegramSettings,
  mockUnresolvedOdds,
} from '../mockData';
import type {
  CanonicalTeam,
  CanonicalTeamFilters,
  CanonicalTeamMerge,
  CanonicalTeamPageFilters,
  EventDetail,
  EventReviewCase,
  EventReviewFilters,
  Match,
  MatchMergeInput,
  OpportunityFilters,
  OutcomeOfferFilters,
  ScrapeRuntimeSettingsUpdate,
  ScrapeSettingsResponse,
  TeamReviewApprovalInput,
  TeamReviewCase,
  TeamReviewFilters,
  TelegramNotificationProfile,
  TelegramNotificationProfileInput,
  TelegramNotificationProfileUpdate,
  TelegramSettingsResponse,
  UnresolvedOddsFilters,
} from '../types';
import { normalizeSearchText } from '../../utils/search';
import type {
  CanonicalTeamMergeVariables,
  CanonicalTeamUnmergeVariables,
  DataSource,
  EventReviewAcceptVariables,
  EventReviewCaseVariables,
  MatchFilters,
  TeamReviewApprovalVariables,
  TeamReviewCaseVariables,
  TelegramProfileVariables,
  UpdateTelegramProfileVariables,
} from './types';

const mockCanonicalTeamMergeHistory: {
  sourceTeam: CanonicalTeam;
  targetTeamId: number;
  targetAliasesBefore: string[];
}[] = [];

function delay(ms = 300): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function filterMockCanonicalTeams(filters: CanonicalTeamFilters = {}) {
  const search = normalizeSearchText(filters.search);
  let results = [...mockCanonicalTeams];
  if (filters.include_merged) {
    const activeTeamIds = new Set(results.map((team) => team.id));
    results = [
      ...results,
      ...mockCanonicalTeamMergeHistory
        .filter((history) => !activeTeamIds.has(history.sourceTeam.id))
        .map((history) => ({
          ...history.sourceTeam,
          aliases: [...history.sourceTeam.aliases],
          merged_into_team_id: history.targetTeamId,
        })),
    ];
  }
  if (filters.sport) {
    results = results.filter((team) => team.sport === filters.sport);
  }
  if (search) {
    results = results.filter((team) =>
      normalizeSearchText([team.display_name, ...team.aliases].join(' ')).includes(search)
    );
  }
  return results;
}

function cloneMockScrapeSettings(): ScrapeSettingsResponse {
  return JSON.parse(JSON.stringify(mockScrapeSettings)) as ScrapeSettingsResponse;
}

function cloneMockTelegramSettings(): TelegramSettingsResponse {
  return JSON.parse(JSON.stringify(mockTelegramSettings)) as TelegramSettingsResponse;
}

function updateMockScrapeSettings(payload: ScrapeRuntimeSettingsUpdate): ScrapeSettingsResponse {
  const base = mockScrapeSettings.pending ?? mockScrapeSettings.applied;
  const next = {
    ...base,
    ...payload,
  };
  const now = new Date().toISOString();
  if (mockSystemStatus.scan.in_progress) {
    mockScrapeSettings.pending = next;
    mockScrapeSettings.pending_at = now;
    mockScrapeSettings.has_pending_changes = true;
    mockScrapeSettings.applied_immediately = false;
  } else {
    mockScrapeSettings.applied = next;
    mockScrapeSettings.pending = null;
    mockScrapeSettings.pending_at = null;
    mockScrapeSettings.applied_at = now;
    mockScrapeSettings.has_pending_changes = false;
    mockScrapeSettings.applied_immediately = true;
  }

  const selected = new Set((mockScrapeSettings.pending ?? mockScrapeSettings.applied).enabled_bookmakers);
  mockScrapeSettings.options.bookmakers = mockScrapeSettings.options.bookmakers.map((bookmaker) => ({
    ...bookmaker,
    enabled: selected.has(bookmaker.id),
  }));
  return cloneMockScrapeSettings();
}

function nextMockTelegramProfileId(): number {
  return Math.max(0, ...mockTelegramSettings.profiles.map((profile) => profile.id)) + 1;
}

function createMockTelegramProfile(
  payload: TelegramNotificationProfileInput
): TelegramNotificationProfile {
  const now = new Date().toISOString();
  const profile: TelegramNotificationProfile = {
    ...payload,
    id: nextMockTelegramProfileId(),
    rate_limited_until: null,
    last_delivery_error: null,
    created_at: now,
    updated_at: now,
  };
  mockTelegramSettings.profiles.push(profile);
  return JSON.parse(JSON.stringify(profile)) as TelegramNotificationProfile;
}

function updateMockTelegramProfile(
  profileId: number,
  payload: TelegramNotificationProfileUpdate
): TelegramNotificationProfile {
  const profile = mockTelegramSettings.profiles.find((item) => item.id === profileId);
  if (!profile) {
    throw new Error('Telegram profile not found');
  }
  Object.assign(profile, payload, { updated_at: new Date().toISOString() });
  return JSON.parse(JSON.stringify(profile)) as TelegramNotificationProfile;
}

function updateMockTeamReviewCaseStatus(caseId: number, status: TeamReviewCase['status']): TeamReviewCase {
  const caseItem = mockTeamReviewCases.find((item) => item.id === caseId);
  if (!caseItem) {
    throw new Error('Team review case not found');
  }
  caseItem.status = status;
  return caseItem;
}

function updateMockEventReviewCaseStatus(
  caseId: number,
  status: EventReviewCase['status']
): EventReviewCase {
  const caseItem = mockEventReviewCases.find((item) => item.id === caseId);
  if (!caseItem) {
    throw new Error('Event review case not found');
  }
  caseItem.status = status;
  caseItem.updated_at = new Date().toISOString();
  if (status === 'accepted') {
    caseItem.accepted_at = caseItem.accepted_at ?? caseItem.updated_at;
    caseItem.declined_at = null;
    caseItem.resolved_event_id = caseItem.resolved_event_id ?? `evt_mock_${caseItem.id}`;
  }
  if (status === 'declined') {
    caseItem.declined_at = caseItem.declined_at ?? caseItem.updated_at;
    caseItem.accepted_at = null;
    caseItem.resolved_event_id = null;
  }
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

function mockEventMemberKeys(eventId: string): Set<string> {
  const event = mockEvents.find((item) => item.id === eventId);
  return new Set(event?.members.map((member) => `${member.match_id}:${member.bookmaker_id}`) ?? []);
}

export const mockDataSource: DataSource = {
  async getOpportunities(filters: OpportunityFilters = {}) {
    await delay();
    let results = [...mockOpportunities];
    if (filters.sport) {
      results = results.filter((row) => row.sport === filters.sport);
    }
    if (filters.market_type) {
      results = results.filter((row) => row.market_type === filters.market_type);
    }
    if (filters.bookmaker_ids?.length) {
      const selected = new Set(filters.bookmaker_ids);
      results = results.filter((row) =>
        row.legs.some((leg) => selected.has(leg.bookmaker_id))
      );
    }
    results.sort(
      (a, b) =>
        (b.profit_margin ?? Number.NEGATIVE_INFINITY) -
        (a.profit_margin ?? Number.NEGATIVE_INFINITY)
    );
    return results;
  },

  async getOutcomeOffers(filters: OutcomeOfferFilters = {}) {
    await delay();
    let results = [...mockOutcomeOffers];
    if (filters.sport) {
      results = results.filter((row) => row.match_id.startsWith(`${filters.sport}-`));
    }
    if (filters.match_id) {
      results = results.filter((row) => row.match_id === filters.match_id);
    }
    if (filters.market_type) {
      results = results.filter((row) => row.market_type === filters.market_type);
    }
    if (filters.bookmaker_ids?.length) {
      const selected = new Set(filters.bookmaker_ids);
      results = results.filter((row) => selected.has(row.bookmaker_id));
    }
    return results;
  },

  async getUnresolvedOdds(filters: UnresolvedOddsFilters = {}) {
    await delay();
    let results = [...mockUnresolvedOdds];

    if (filters.bookmaker_id) {
      results = results.filter((row) => row.bookmaker_id === filters.bookmaker_id);
    }
    if (filters.bookmaker_ids?.length) {
      const selected = new Set(filters.bookmaker_ids);
      results = results.filter((row) => selected.has(row.bookmaker_id));
    }
    if (filters.sport) {
      results = results.filter((row) => row.sport === filters.sport);
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
  },

  async getTeamReviewCases(filters: TeamReviewFilters = {}) {
    await delay();
    let results = [...mockTeamReviewCases];
    if (filters.bookmaker_id) {
      results = results.filter((row) => row.bookmaker_id === filters.bookmaker_id);
    }
    if (filters.bookmaker_ids?.length) {
      const selected = new Set(filters.bookmaker_ids);
      results = results.filter((row) => selected.has(row.bookmaker_id));
    }
    if (filters.sport) {
      results = results.filter((row) => row.sport === filters.sport);
    }
    if (filters.status) {
      results = results.filter((row) => row.status === filters.status);
    }
    return results;
  },

  async approveTeamReviewCase({
    caseId,
    team_id,
    create_team_name,
  }: TeamReviewApprovalVariables) {
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
  },

  async declineTeamReviewCase({ caseId }: TeamReviewCaseVariables) {
    await delay();
    updateMockTeamReviewCaseStatus(caseId, 'declined');
    return {
      case_id: caseId,
      status: 'declined',
    };
  },

  async getEventReviewCases(filters: EventReviewFilters = {}) {
    await delay();
    let results = [...mockEventReviewCases];
    if (filters.bookmaker_id) {
      results = results.filter(
        (row) =>
          row.source_bookmaker_ids.includes(filters.bookmaker_id!) ||
          row.variants.some((variant) => variant.bookmaker_id === filters.bookmaker_id)
      );
    }
    if (filters.bookmaker_ids?.length) {
      const selected = new Set(filters.bookmaker_ids);
      results = results.filter(
        (row) =>
          row.source_bookmaker_ids.some((bookmakerId) => selected.has(bookmakerId)) ||
          row.variants.some((variant) => variant.bookmaker_id != null && selected.has(variant.bookmaker_id))
      );
    }
    if (filters.sport) {
      results = results.filter((row) => row.sport === filters.sport);
    }
    if (filters.status) {
      results = results.filter((row) => row.status === filters.status);
    }
    const offset = filters.offset ?? 0;
    const limit = filters.limit ?? results.length;
    return results.slice(offset, offset + limit);
  },

  async acceptEventReviewCase({ caseId, primaryMatchId }: EventReviewAcceptVariables) {
    await delay();
    const updatedCase = updateMockEventReviewCaseStatus(caseId, 'accepted');
    if (primaryMatchId) {
      updatedCase.primary_match_id = primaryMatchId;
    }
    return {
      case_id: caseId,
      status: 'accepted',
      resolved_event_id: updatedCase.resolved_event_id,
    };
  },

  async declineEventReviewCase({ caseId }: EventReviewCaseVariables) {
    await delay();
    updateMockEventReviewCaseStatus(caseId, 'declined');
    return {
      case_id: caseId,
      status: 'declined',
      resolved_event_id: null,
    };
  },

  async getCanonicalTeams(filters: CanonicalTeamFilters = {}) {
    await delay();
    const results = filterMockCanonicalTeams(filters);
    const offset = filters.offset ?? 0;
    const limit = filters.limit ?? results.length;
    return results.slice(offset, offset + limit);
  },

  async getCanonicalTeamsPage(filters: CanonicalTeamPageFilters = {}) {
    await delay();
    const results = filterMockCanonicalTeams(filters);
    const offset = filters.offset ?? 0;
    const limit = filters.limit ?? 25;
    return {
      items: results.slice(offset, offset + limit),
      total: results.length,
      limit,
      offset,
    };
  },

  async mergeCanonicalTeam({
    sourceTeamId,
    targetTeamId,
  }: CanonicalTeamMergeVariables): Promise<CanonicalTeamMerge> {
    await delay();
    if (sourceTeamId === targetTeamId) {
      throw new Error('Cannot merge a canonical team into itself');
    }

    const sourceIndex = mockCanonicalTeams.findIndex((team) => team.id === sourceTeamId);
    const targetTeam = mockCanonicalTeams.find((team) => team.id === targetTeamId);
    if (sourceIndex === -1 || !targetTeam) {
      throw new Error('Canonical team not found');
    }

    const targetAliasesBefore = [...targetTeam.aliases];
    const [sourceTeam] = mockCanonicalTeams.splice(sourceIndex, 1);
    mockCanonicalTeamMergeHistory.push({
      sourceTeam: {
        ...sourceTeam,
        aliases: [...sourceTeam.aliases],
        merged_into_team_id: targetTeamId,
      },
      targetTeamId,
      targetAliasesBefore,
    });
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
      opportunities_found: 0,
    };
  },

  async unmergeCanonicalTeam({ sourceTeamId }: CanonicalTeamUnmergeVariables) {
    await delay();
    const historyIndex = mockCanonicalTeamMergeHistory
      .map((history) => history.sourceTeam.id)
      .lastIndexOf(sourceTeamId);
    if (historyIndex === -1) {
      throw new Error('No active merge history exists for this canonical team');
    }

    const [history] = mockCanonicalTeamMergeHistory.splice(historyIndex, 1);
    if (mockCanonicalTeams.some((team) => team.id === sourceTeamId)) {
      throw new Error('Canonical team is already active');
    }

    const targetTeam = mockCanonicalTeams.find((team) => team.id === history.targetTeamId);
    if (targetTeam) {
      targetTeam.aliases = [...history.targetAliasesBefore];
      targetTeam.alias_count = targetTeam.aliases.length;
    }

    mockCanonicalTeams.push({
      ...history.sourceTeam,
      aliases: [...history.sourceTeam.aliases],
      merged_into_team_id: null,
    });
    mockCanonicalTeams.sort((left, right) => left.display_name.localeCompare(right.display_name));

    return {
      source_team_id: sourceTeamId,
      target_team_id: history.targetTeamId,
      restored_team_name: history.sourceTeam.display_name,
    };
  },

  async getMatches(params: MatchFilters = {}) {
    await delay();
    let results = [...mockMatches];
    if (params.league) {
      results = results.filter((m) => m.league_id === params.league);
    }
    if (params.sport) {
      results = results.filter((m) => m.sport === params.sport);
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
  },

  async getMatch(id: string): Promise<Match> {
    await delay();
    const m = mockMatches.find((m) => m.id === id);
    if (!m) throw new Error('Match not found');
    return m;
  },

  async getEvent(id: string): Promise<EventDetail> {
    await delay();
    const event = mockEvents.find((event) => event.id === id);
    if (!event) throw new Error('Event not found');
    return JSON.parse(JSON.stringify(event)) as EventDetail;
  },

  async mergeMatches(payload: MatchMergeInput) {
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
      reassigned_outcome_offers: 0,
      reassigned_opportunities: 0,
      deleted_source_matches: payload.source_match_ids.length,
    };
  },

  async mergeEvents(payload) {
    await delay();
    if (!payload.source_match_ids.length) {
      throw new Error('source_match_ids must not be empty');
    }
    if (payload.source_match_ids.includes(payload.primary_match_id)) {
      throw new Error('primary_match_id must not appear in source_match_ids');
    }
    const primary = mockMatches.find((m) => m.id === payload.primary_match_id);
    if (!primary) throw new Error(`Primary match ${payload.primary_match_id} not found`);
    for (const sid of payload.source_match_ids) {
      const source = mockMatches.find((m) => m.id === sid);
      if (!source) throw new Error(`Source match ${sid} not found`);
      if (source.sport !== primary.sport) {
        throw new Error(`Source match ${sid} sport differs from primary match`);
      }
      if ((source.start_time ?? '') !== (primary.start_time ?? '')) {
        throw new Error(`Source match ${sid} start_time differs from primary`);
      }
    }
    const linkedMatchIds = [payload.primary_match_id, ...payload.source_match_ids];
    return {
      resolved_event_id: `evt_manual_${linkedMatchIds.slice().sort().join('_')}`,
      primary_match_id: payload.primary_match_id,
      linked_match_ids: linkedMatchIds,
      linked_member_count: linkedMatchIds.reduce((count, matchId) => {
        const match = mockMatches.find((m) => m.id === matchId);
        return count + (match?.available_bookmakers.length ?? 0);
      }, 0),
      opportunities_rebuilt: 0,
    };
  },

  async getMatchOdds(matchId: string) {
    await delay();
    return mockOddsOffers.filter((o) => o.match_id === matchId);
  },

  async getEventOdds(eventId: string) {
    await delay();
    const memberKeys = mockEventMemberKeys(eventId);
    return mockEventOddsOffers.filter((offer) =>
      memberKeys.has(`${offer.match_id}:${offer.bookmaker_id}`)
    );
  },

  async getMatchOutcomeOffers(matchId: string) {
    await delay();
    return mockOutcomeOffers.filter((offer) => offer.match_id === matchId);
  },

  async getEventOutcomeOffers(eventId: string) {
    await delay();
    const event = mockEvents.find((item) => item.id === eventId);
    const matchIds = new Set(event?.members.map((member) => member.match_id) ?? []);
    return mockOutcomeOffers.filter((offer) => matchIds.has(offer.match_id));
  },

  async getMatchHistory(matchId: string) {
    await delay();
    return mockOddsOffers.filter((o) => o.match_id === matchId);
  },

  async getLeagues() {
    await delay();
    return mockLeagues;
  },

  async getBookmakers() {
    await delay();
    return mockBookmakers;
  },

  async getSystemStatus() {
    await delay();
    return mockSystemStatus;
  },

  async getScrapeSettings() {
    await delay();
    return cloneMockScrapeSettings();
  },

  async updateScrapeSettings(payload: ScrapeRuntimeSettingsUpdate) {
    await delay();
    return updateMockScrapeSettings(payload);
  },

  async getTelegramSettings() {
    await delay();
    return cloneMockTelegramSettings();
  },

  async createTelegramProfile(payload: TelegramNotificationProfileInput) {
    await delay();
    return createMockTelegramProfile(payload);
  },

  async updateTelegramProfile({ profileId, payload }: UpdateTelegramProfileVariables) {
    await delay();
    return updateMockTelegramProfile(profileId, payload);
  },

  async deleteTelegramProfile({ profileId }: TelegramProfileVariables) {
    await delay();
    const index = mockTelegramSettings.profiles.findIndex((item) => item.id === profileId);
    if (index === -1) {
      throw new Error('Telegram profile not found');
    }
    mockTelegramSettings.profiles.splice(index, 1);
    return { profile_id: profileId, deleted: true };
  },

  async testTelegramProfile({ profileId }: TelegramProfileVariables) {
    await delay();
    const profile = mockTelegramSettings.profiles.find((item) => item.id === profileId);
    if (!profile) {
      throw new Error('Telegram profile not found');
    }
    return {
      profile_id: profileId,
      ok: true,
      message_id: Math.floor(Math.random() * 100000),
    };
  },

  async triggerScrape() {
    await delay(1000);
    return { message: 'Scrape triggered' };
  },
};
