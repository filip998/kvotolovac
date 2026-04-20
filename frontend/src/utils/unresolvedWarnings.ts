import type { UnresolvedOdds } from '../api/types';

export interface UnresolvedWarningGroup {
  id: string;
  reasonCode: string;
  sport: string;
  normalizedTeamName: string;
  rawTeamNames: string[];
  leagueLabels: string[];
  startTime: string | null;
  candidateCount: number;
  matchupContext: string[];
  affectedOddsCount: number;
  bookmakerNames: string[];
  marketTypes: string[];
  playerNames: string[];
  latestScrapedAt: string | null;
}

function pushUnique(values: string[], value: string | null | undefined) {
  if (!value || values.includes(value)) {
    return;
  }

  values.push(value);
}

function buildGroupKey(row: UnresolvedOdds) {
  const matchupContext =
    row.candidate_matchups.length > 0 ? row.candidate_matchups : row.available_matchups_same_slot;

  return JSON.stringify([
    row.sport,
    row.league_id,
    row.reason_code,
    row.start_time ?? '',
    row.normalized_team_name,
    [...matchupContext].sort(),
  ]);
}

function latestTimestamp(current: string | null, candidate: string | null) {
  if (!candidate) {
    return current;
  }
  if (!current) {
    return candidate;
  }

  const currentTime = Date.parse(current);
  const candidateTime = Date.parse(candidate);
  if (Number.isNaN(currentTime) || Number.isNaN(candidateTime)) {
    return current > candidate ? current : candidate;
  }

  return candidateTime > currentTime ? candidate : current;
}

export function groupUnresolvedOdds(rows: readonly UnresolvedOdds[]): UnresolvedWarningGroup[] {
  const groups = new Map<string, UnresolvedWarningGroup>();

  for (const row of rows) {
    const key = buildGroupKey(row);
    const leagueLabel = row.league_name ?? row.league_id;
    const bookmakerName = row.bookmaker_name ?? row.bookmaker_id;
    const matchupContext =
      row.candidate_matchups.length > 0 ? row.candidate_matchups : row.available_matchups_same_slot;
    const group = groups.get(key);

    if (!group) {
      groups.set(key, {
        id: key,
        reasonCode: row.reason_code,
        sport: row.sport,
        normalizedTeamName: row.normalized_team_name,
        rawTeamNames: [row.raw_team_name],
        leagueLabels: leagueLabel ? [leagueLabel] : [],
        startTime: row.start_time,
        candidateCount: row.candidate_count,
        matchupContext: [...matchupContext],
        affectedOddsCount: 1,
        bookmakerNames: [bookmakerName],
        marketTypes: [row.market_type],
        playerNames: row.player_name ? [row.player_name] : [],
        latestScrapedAt: row.scraped_at,
      });
      continue;
    }

    group.affectedOddsCount += 1;
    group.candidateCount = Math.max(group.candidateCount, row.candidate_count);
    group.latestScrapedAt = latestTimestamp(group.latestScrapedAt, row.scraped_at);

    pushUnique(group.rawTeamNames, row.raw_team_name);
    pushUnique(group.leagueLabels, leagueLabel);
    pushUnique(group.bookmakerNames, bookmakerName);
    pushUnique(group.marketTypes, row.market_type);
    pushUnique(group.playerNames, row.player_name);
  }

  return Array.from(groups.values());
}
