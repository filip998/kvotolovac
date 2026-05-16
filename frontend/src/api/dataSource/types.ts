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
  TeamReviewApprovalInput,
  TeamReviewCase,
  TeamReviewFilters,
  TelegramNotificationProfile,
  TelegramNotificationProfileInput,
  TelegramNotificationProfileUpdate,
  TelegramSettingsResponse,
  TelegramTestMessageResponse,
  UnresolvedOdds,
  UnresolvedOddsFilters,
} from '../types';

export interface MatchFilters {
  league?: string;
  sport?: string;
  status?: string;
  bookmaker_ids?: string[];
  limit?: number;
  offset?: number;
  loadAll?: boolean;
}

export type TeamReviewApprovalVariables = { caseId: number } & TeamReviewApprovalInput;

export interface TeamReviewCaseVariables {
  caseId: number;
}

export interface EventReviewAcceptVariables {
  caseId: number;
  primaryMatchId?: string;
}

export interface EventReviewCaseVariables {
  caseId: number;
}

export interface CanonicalTeamMergeVariables {
  sourceTeamId: number;
  targetTeamId: number;
}

export interface CanonicalTeamUnmergeVariables {
  sourceTeamId: number;
}

export interface UpdateTelegramProfileVariables {
  profileId: number;
  payload: TelegramNotificationProfileUpdate;
}

export interface TelegramProfileVariables {
  profileId: number;
}

export interface DeleteTelegramProfileResult {
  profile_id: number;
  deleted: boolean;
}

export interface TriggerScrapeResult {
  message: string;
}

export interface DataSource {
  getOpportunities(filters?: OpportunityFilters): Promise<Opportunity[]>;
  getOutcomeOffers(filters?: OutcomeOfferFilters): Promise<OutcomeOffer[]>;
  getUnresolvedOdds(filters?: UnresolvedOddsFilters): Promise<UnresolvedOdds[]>;
  getTeamReviewCases(filters?: TeamReviewFilters): Promise<TeamReviewCase[]>;
  approveTeamReviewCase(
    variables: TeamReviewApprovalVariables
  ): Promise<TeamReviewApproval>;
  declineTeamReviewCase(variables: TeamReviewCaseVariables): Promise<TeamReviewAction>;
  getEventReviewCases(filters?: EventReviewFilters): Promise<EventReviewCase[]>;
  acceptEventReviewCase(
    variables: EventReviewAcceptVariables
  ): Promise<EventReviewAction>;
  declineEventReviewCase(variables: EventReviewCaseVariables): Promise<EventReviewAction>;
  getCanonicalTeams(filters?: CanonicalTeamFilters): Promise<CanonicalTeam[]>;
  getCanonicalTeamsPage(filters?: CanonicalTeamPageFilters): Promise<CanonicalTeamsPage>;
  mergeCanonicalTeam(variables: CanonicalTeamMergeVariables): Promise<CanonicalTeamMerge>;
  unmergeCanonicalTeam(
    variables: CanonicalTeamUnmergeVariables
  ): Promise<CanonicalTeamUnmerge>;
  getMatches(params?: MatchFilters): Promise<Match[]>;
  getMatch(id: string): Promise<Match>;
  getEvent(id: string): Promise<EventDetail>;
  mergeMatches(payload: MatchMergeInput): Promise<MatchMergeResult>;
  mergeEvents(payload: EventMergeInput): Promise<EventMergeResult>;
  getMatchOdds(matchId: string): Promise<OddsOffer[]>;
  getEventOdds(eventId: string): Promise<EventOddsOffer[]>;
  getMatchOutcomeOffers(matchId: string): Promise<OutcomeOffer[]>;
  getEventOutcomeOffers(eventId: string): Promise<OutcomeOffer[]>;
  getMatchHistory(matchId: string): Promise<OddsOffer[]>;
  getLeagues(): Promise<League[]>;
  getBookmakers(): Promise<Bookmaker[]>;
  getSystemStatus(): Promise<SystemStatus>;
  getScrapeSettings(): Promise<ScrapeSettingsResponse>;
  updateScrapeSettings(
    payload: ScrapeRuntimeSettingsUpdate
  ): Promise<ScrapeSettingsResponse>;
  getTelegramSettings(): Promise<TelegramSettingsResponse>;
  createTelegramProfile(
    payload: TelegramNotificationProfileInput
  ): Promise<TelegramNotificationProfile>;
  updateTelegramProfile(
    variables: UpdateTelegramProfileVariables
  ): Promise<TelegramNotificationProfile>;
  deleteTelegramProfile(
    variables: TelegramProfileVariables
  ): Promise<DeleteTelegramProfileResult>;
  testTelegramProfile(
    variables: TelegramProfileVariables
  ): Promise<TelegramTestMessageResponse>;
  triggerScrape(): Promise<TriggerScrapeResult>;
}
