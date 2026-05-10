/**
 * Trading-terminal odds-card data model.
 *
 * Converts a flat list of `OddsOffer` and/or `OutcomeOffer` rows for one
 * match/event into a list of `MarketCard`s. Each card has a small set of
 * `AxisRow`s — one per back↔lay pairing (a single 1X2 outcome paired with
 * its double-chance complement, or one Over/Under threshold, etc.).
 *
 * Conventions:
 *   - "back" leg = the side displayed on the left of a pivot
 *   - "lay" leg  = the complement that, together with the back, covers the
 *     full outcome space (so `1/odds_back + 1/odds_lay < 1` is an arb).
 *
 * Pure module — no React, no I/O. Safe to unit-test.
 */

import type {
  EventOddsOffer,
  MarketType,
  OddsOffer,
  OutcomeMarketType,
  OutcomeOffer,
} from '../api/types';
import { formatHandicapLine } from './format';

export type CardCategory = 'match' | 'totals' | 'handicap' | 'player';

export type CardSport = 'football' | 'basketball' | 'tennis';

export interface AxisLeg {
  bookmakerId: string;
  bookmakerName: string;
  sourceUrl: string | null;
  odds: number;
}

/** Per-bookmaker row in a single axis pivot. */
export interface AxisBookmakerRow {
  bookmakerId: string;
  bookmakerName: string;
  sourceUrl: string | null;
  backOdds: number | null;
  layOdds: number | null;
  /** 1/back + 1/lay × 100; null if either side is missing. */
  impliedPct: number | null;
  isBestBack: boolean;
  isBestLay: boolean;
}

/** One ladder row inside a card. */
export interface AxisRow {
  axisKey: string;
  /** Short tag for the ladder column ("1", "X", "2", "2.5", "−6.5"). */
  lineTag: string;
  /** Human-readable label for the back side ("1", "Over", "Home cover"). */
  backLabel: string;
  /** Human-readable label for the lay side ("X2", "Under", "Away cover"). */
  layLabel: string;
  /** Pivot-header label for the back column (e.g. "X (back)"). */
  backColumnLabel: string;
  /** Pivot-header label for the lay column (e.g. "12 (lay)"). */
  layColumnLabel: string;
  /** Numeric line for sorting (totals/handicap/threshold); 0 for outcome markets. */
  numericLine: number;
  /** Best back leg across all bookmakers offering this side. */
  bestBack: AxisLeg | null;
  /** Best lay leg across all bookmakers offering this side. */
  bestLay: AxisLeg | null;
  /** Implied % using the best back + best lay (cross-book). */
  bestPairImpliedPct: number | null;
  /**
   * True when the best back and best lay come from the same bookmaker.
   * In that case the "implied %" is just that bookmaker's overround — not a
   * tradable cross-book arb. UIs should warn the user.
   */
  bestPairSameBook: boolean;
  /** Per-bookmaker rows for the pivot table, sorted. */
  bookmakerRows: AxisBookmakerRow[];
}

export interface MarketCard {
  cardKey: string;
  category: CardCategory;
  sport: CardSport;
  /** Card title shown in the header (e.g. "Match Result", "LeBron James"). */
  title: string;
  /** Optional small grey eyebrow under the title — e.g. "1↔X2 · X↔12 · 2↔1X". */
  pairEyebrow: string;
  /** Total bookmaker count seen across this card's axes. */
  bookmakerCount: number;
  /** Number of axes that have implied % < 100. */
  arbCount: number;
  axes: AxisRow[];
  /** Player metadata (only set for `category === 'player'`). */
  player?: {
    key: string;
    displayName: string;
    statKey: string;
    statLabel: string;
  };
}

// ─── Sport classification ──────────────────────────────────────────────────

const FOOTBALL_MARKETS = new Set<string>([
  'football_result',
  'football_double_chance',
  'football_result_double_chance',
  'football_total_goals',
]);

const TENNIS_MARKETS = new Set<string>([
  'tennis_match_winner',
]);

export function sportFromMarket(marketType: string): CardSport {
  if (FOOTBALL_MARKETS.has(marketType)) return 'football';
  if (TENNIS_MARKETS.has(marketType)) return 'tennis';
  return 'basketball';
}

// ─── Stat label mapping for player markets ────────────────────────────────

const PLAYER_STAT_LABELS: Record<string, string> = {
  player_points: 'Points',
  player_points_milestones: 'Points Milestones',
  player_rebounds: 'Rebounds',
  player_assists: 'Assists',
  player_3points: '3-Pointers',
  player_steals: 'Steals',
  player_blocks: 'Blocks',
  player_turnovers: 'Turnovers',
  player_points_rebounds: 'Pts + Reb',
  player_points_assists: 'Pts + Ast',
  player_rebounds_assists: 'Reb + Ast',
  player_points_rebounds_assists: 'Pts + Reb + Ast',
};

export function playerStatLabel(marketType: MarketType): string {
  return PLAYER_STAT_LABELS[marketType] ?? marketType;
}

export function isPlayerMarket(marketType: MarketType): boolean {
  return marketType.startsWith('player_');
}

// ─── Helpers ───────────────────────────────────────────────────────────────

function impliedPctOf(back: number | null | undefined, lay: number | null | undefined): number | null {
  if (back == null || lay == null || back <= 0 || lay <= 0) return null;
  return (1 / back + 1 / lay) * 100;
}

function pickHigher(a: AxisLeg | null, b: AxisLeg | null): AxisLeg | null {
  if (!a) return b;
  if (!b) return a;
  return b.odds > a.odds ? b : a;
}

function safeAxisKey(parts: (string | number | null | undefined)[]): string {
  return parts.map((p) => (p === null || p === undefined ? '' : String(p))).join('|');
}

// ─── OutcomeOffer extraction ──────────────────────────────────────────────

/** Outcome-pair definitions per OutcomeMarketType. */
type OutcomePairDef = {
  axisKey: string;
  /** Outcome code that is treated as the "back" side. */
  backCode: string;
  /** Outcome code that is treated as the "lay" side. */
  layCode: string;
  /** Display labels. */
  lineTag: string;
  backLabel: string;
  layLabel: string;
};

const FOOTBALL_RESULT_PAIRS: OutcomePairDef[] = [
  { axisKey: 'home', backCode: 'home', layCode: 'draw_or_away', lineTag: '1', backLabel: '1', layLabel: 'X2' },
  { axisKey: 'draw', backCode: 'draw', layCode: 'home_or_away', lineTag: 'X', backLabel: 'X', layLabel: '12' },
  { axisKey: 'away', backCode: 'away', layCode: 'home_or_draw', lineTag: '2', backLabel: '2', layLabel: '1X' },
];

const TWO_WAY_PAIR: OutcomePairDef = {
  axisKey: 'home_away',
  backCode: 'home',
  layCode: 'away',
  lineTag: 'H',
  backLabel: 'Home',
  layLabel: 'Away',
};

const TOTALS_PAIR_LABELS = {
  backLabel: 'Over',
  layLabel: 'Under',
};

interface OfferIndex {
  byCode: Map<string, OutcomeOffer[]>;
  bookmakerCount: number;
}

function indexOutcomeOffers(offers: OutcomeOffer[]): OfferIndex {
  const byCode = new Map<string, OutcomeOffer[]>();
  const bookmakerIds = new Set<string>();
  for (const o of offers) {
    bookmakerIds.add(o.bookmaker_id);
    if (!byCode.has(o.outcome_code)) byCode.set(o.outcome_code, []);
    byCode.get(o.outcome_code)!.push(o);
  }
  return { byCode, bookmakerCount: bookmakerIds.size };
}

function buildAxisRow(
  pair: OutcomePairDef,
  byCode: Map<string, OutcomeOffer[]>,
  options?: { numericLine?: number; lineTagOverride?: string; backColumnLabel?: string; layColumnLabel?: string },
): AxisRow | null {
  const backOffers = byCode.get(pair.backCode) ?? [];
  const layOffers = byCode.get(pair.layCode) ?? [];
  if (backOffers.length === 0 && layOffers.length === 0) return null;

  // Per-bookmaker pivot: union of bookmakers across both sides.
  const byBookmaker = new Map<string, { back?: OutcomeOffer; lay?: OutcomeOffer; bookmakerName: string }>();
  for (const o of backOffers) {
    const slot = byBookmaker.get(o.bookmaker_id) ?? { bookmakerName: o.bookmaker_name ?? o.bookmaker_id };
    slot.back = o;
    slot.bookmakerName = o.bookmaker_name ?? slot.bookmakerName;
    byBookmaker.set(o.bookmaker_id, slot);
  }
  for (const o of layOffers) {
    const slot = byBookmaker.get(o.bookmaker_id) ?? { bookmakerName: o.bookmaker_name ?? o.bookmaker_id };
    slot.lay = o;
    slot.bookmakerName = o.bookmaker_name ?? slot.bookmakerName;
    byBookmaker.set(o.bookmaker_id, slot);
  }

  let bestBack: AxisLeg | null = null;
  let bestLay: AxisLeg | null = null;
  for (const o of backOffers) {
    const candidate: AxisLeg = {
      bookmakerId: o.bookmaker_id,
      bookmakerName: o.bookmaker_name ?? o.bookmaker_id,
      sourceUrl: o.source_url ?? null,
      odds: o.odds,
    };
    bestBack = pickHigher(bestBack, candidate);
  }
  for (const o of layOffers) {
    const candidate: AxisLeg = {
      bookmakerId: o.bookmaker_id,
      bookmakerName: o.bookmaker_name ?? o.bookmaker_id,
      sourceUrl: o.source_url ?? null,
      odds: o.odds,
    };
    bestLay = pickHigher(bestLay, candidate);
  }

  const bookmakerRows: AxisBookmakerRow[] = Array.from(byBookmaker.entries())
    .map(([bookmakerId, slot]) => {
      const backOdds = slot.back?.odds ?? null;
      const layOdds = slot.lay?.odds ?? null;
      return {
        bookmakerId,
        bookmakerName: slot.bookmakerName,
        sourceUrl: slot.back?.source_url ?? slot.lay?.source_url ?? null,
        backOdds,
        layOdds,
        impliedPct: impliedPctOf(backOdds, layOdds),
        isBestBack: bestBack ? bestBack.bookmakerId === bookmakerId && bestBack.odds === backOdds : false,
        isBestLay: bestLay ? bestLay.bookmakerId === bookmakerId && bestLay.odds === layOdds : false,
      };
    })
    .sort((a, b) => {
      // Best back first, then by bookmaker name.
      const aBack = a.backOdds ?? -Infinity;
      const bBack = b.backOdds ?? -Infinity;
      if (bBack !== aBack) return bBack - aBack;
      return a.bookmakerName.localeCompare(b.bookmakerName);
    });

  const lineTag = options?.lineTagOverride ?? pair.lineTag;
  const backColumnLabel = options?.backColumnLabel ?? `${pair.backLabel} (back)`;
  const layColumnLabel = options?.layColumnLabel ?? `${pair.layLabel} (lay)`;

  return {
    axisKey: pair.axisKey,
    lineTag,
    backLabel: pair.backLabel,
    layLabel: pair.layLabel,
    backColumnLabel,
    layColumnLabel,
    numericLine: options?.numericLine ?? 0,
    bestBack,
    bestLay,
    bestPairImpliedPct: impliedPctOf(bestBack?.odds, bestLay?.odds),
    bestPairSameBook: !!bestBack && !!bestLay && bestBack.bookmakerId === bestLay.bookmakerId,
    bookmakerRows,
  };
}

function countAllBookmakers(rows: AxisRow[]): number {
  const ids = new Set<string>();
  for (const row of rows) {
    for (const br of row.bookmakerRows) ids.add(br.bookmakerId);
  }
  return ids.size;
}

function countArbs(rows: AxisRow[]): number {
  // A same-book "best pair" is just that bookmaker's overround — not a tradable arb.
  return rows.filter(
    (r) => r.bestPairImpliedPct !== null && r.bestPairImpliedPct < 100 && !r.bestPairSameBook,
  ).length;
}

// ─── Footbal 1X2 + DC card ────────────────────────────────────────────────

function buildFootballMatchResultCard(offers: OutcomeOffer[], cardKeyPrefix = 'fb-match-result'): MarketCard | null {
  if (offers.length === 0) return null;
  const { byCode } = indexOutcomeOffers(offers);
  const axes: AxisRow[] = [];
  for (const pair of FOOTBALL_RESULT_PAIRS) {
    const row = buildAxisRow(pair, byCode);
    if (row) axes.push(row);
  }
  if (axes.length === 0) return null;
  return {
    cardKey: cardKeyPrefix,
    category: 'match',
    sport: 'football',
    title: 'Match Result',
    pairEyebrow: '1↔X2 · X↔12 · 2↔1X',
    bookmakerCount: countAllBookmakers(axes),
    arbCount: countArbs(axes),
    axes,
  };
}

// ─── Outcome-based totals (football_total_goals) ──────────────────────────

function buildOutcomeTotalsCards(
  offers: OutcomeOffer[],
  _marketType: OutcomeMarketType,
  cardKey: string,
  title: string,
  sport: CardSport,
): MarketCard | null {
  // Group by line.
  const byLine = new Map<number, OutcomeOffer[]>();
  for (const o of offers) {
    if (o.line === null) continue;
    if (!byLine.has(o.line)) byLine.set(o.line, []);
    byLine.get(o.line)!.push(o);
  }
  if (byLine.size === 0) return null;

  const axes: AxisRow[] = [];
  for (const [line, lineOffers] of byLine.entries()) {
    const { byCode } = indexOutcomeOffers(lineOffers);
    const pair: OutcomePairDef = {
      axisKey: `line:${line}`,
      backCode: 'over',
      layCode: 'under',
      lineTag: line.toFixed(1),
      backLabel: TOTALS_PAIR_LABELS.backLabel,
      layLabel: TOTALS_PAIR_LABELS.layLabel,
    };
    const row = buildAxisRow(pair, byCode, {
      numericLine: line,
      backColumnLabel: 'Over',
      layColumnLabel: 'Under',
    });
    if (row) axes.push(row);
  }
  if (axes.length === 0) return null;
  axes.sort((a, b) => a.numericLine - b.numericLine);

  return {
    cardKey,
    category: 'totals',
    sport,
    title,
    pairEyebrow: 'Over ↔ Under · per line',
    bookmakerCount: countAllBookmakers(axes),
    arbCount: countArbs(axes),
    axes,
  };
}

// ─── Two-way markets (Match Winner, Tennis) ───────────────────────────────

function buildTwoWayMatchWinnerCard(
  offers: OutcomeOffer[],
  _marketType: OutcomeMarketType,
  cardKey: string,
  sport: CardSport,
): MarketCard | null {
  if (offers.length === 0) return null;
  const { byCode } = indexOutcomeOffers(offers);
  const row = buildAxisRow(TWO_WAY_PAIR, byCode, {
    backColumnLabel: 'Home',
    layColumnLabel: 'Away',
  });
  if (!row) return null;
  return {
    cardKey,
    category: 'match',
    sport,
    title: 'Match Winner',
    pairEyebrow: 'Home ↔ Away',
    bookmakerCount: countAllBookmakers([row]),
    arbCount: countArbs([row]),
    axes: [row],
  };
}

// ─── OddsOffer extraction (handicap, totals, player props) ────────────────

interface OddsAxisInputs {
  cardKey: string;
  category: CardCategory;
  sport: CardSport;
  title: string;
  pairEyebrow: string;
  /** "Over" / "Under" or "Home cover" / "Away cover". */
  backLabel: string;
  layLabel: string;
  /** How to render the line tag for each row. */
  formatLineTag: (threshold: number) => string;
  /** Numeric line used for sorting (defaults to the threshold). */
  formatNumericLine?: (threshold: number) => number;
  player?: MarketCard['player'];
}

function buildCardFromOddsOffers(offers: OddsOffer[], inputs: OddsAxisInputs): MarketCard | null {
  if (offers.length === 0) return null;

  // Group rows by threshold.
  const byThreshold = new Map<number, OddsOffer[]>();
  for (const o of offers) {
    if (!byThreshold.has(o.threshold)) byThreshold.set(o.threshold, []);
    byThreshold.get(o.threshold)!.push(o);
  }

  const axes: AxisRow[] = [];
  for (const [threshold, rows] of byThreshold.entries()) {
    let bestBack: AxisLeg | null = null;
    let bestLay: AxisLeg | null = null;
    const bookmakerRows: AxisBookmakerRow[] = [];
    for (const r of rows) {
      const backOdds = r.over_odds;
      const layOdds = r.under_odds;
      const candidateBack: AxisLeg | null = backOdds != null
        ? {
            bookmakerId: r.bookmaker_id,
            bookmakerName: r.bookmaker_name,
            sourceUrl: r.source_url ?? null,
            odds: backOdds,
          }
        : null;
      const candidateLay: AxisLeg | null = layOdds != null
        ? {
            bookmakerId: r.bookmaker_id,
            bookmakerName: r.bookmaker_name,
            sourceUrl: r.source_url ?? null,
            odds: layOdds,
          }
        : null;
      bestBack = pickHigher(bestBack, candidateBack);
      bestLay = pickHigher(bestLay, candidateLay);
      bookmakerRows.push({
        bookmakerId: r.bookmaker_id,
        bookmakerName: r.bookmaker_name,
        sourceUrl: r.source_url ?? null,
        backOdds,
        layOdds,
        impliedPct: impliedPctOf(backOdds, layOdds),
        isBestBack: false, // patched below once we know best
        isBestLay: false,
      });
    }
    // Mark best
    for (const br of bookmakerRows) {
      br.isBestBack = !!bestBack && br.bookmakerId === bestBack.bookmakerId && br.backOdds === bestBack.odds;
      br.isBestLay = !!bestLay && br.bookmakerId === bestLay.bookmakerId && br.layOdds === bestLay.odds;
    }
    bookmakerRows.sort((a, b) => {
      const aBack = a.backOdds ?? -Infinity;
      const bBack = b.backOdds ?? -Infinity;
      if (bBack !== aBack) return bBack - aBack;
      return a.bookmakerName.localeCompare(b.bookmakerName);
    });
    axes.push({
      axisKey: safeAxisKey(['threshold', threshold]),
      lineTag: inputs.formatLineTag(threshold),
      backLabel: inputs.backLabel,
      layLabel: inputs.layLabel,
      backColumnLabel: inputs.backLabel,
      layColumnLabel: inputs.layLabel,
      numericLine: inputs.formatNumericLine ? inputs.formatNumericLine(threshold) : threshold,
      bestBack,
      bestLay,
      bestPairImpliedPct: impliedPctOf(bestBack?.odds, bestLay?.odds),
      bestPairSameBook: !!bestBack && !!bestLay && bestBack.bookmakerId === bestLay.bookmakerId,
      bookmakerRows,
    });
  }
  if (axes.length === 0) return null;
  axes.sort((a, b) => a.numericLine - b.numericLine);

  return {
    cardKey: inputs.cardKey,
    category: inputs.category,
    sport: inputs.sport,
    title: inputs.title,
    pairEyebrow: inputs.pairEyebrow,
    bookmakerCount: countAllBookmakers(axes),
    arbCount: countArbs(axes),
    axes,
    player: inputs.player,
  };
}

// ─── Top-level extraction ─────────────────────────────────────────────────

export interface ExtractedCards {
  cards: MarketCard[];
}

function buildPlayerKey(playerName: string | null, eventScopedKey?: string | null): string {
  return eventScopedKey ?? (playerName ? `raw:${playerName}` : 'unknown');
}

function buildPlayerDisplayName(offer: EventOddsOffer | OddsOffer): string {
  if ('event_player_display_name' in offer && offer.event_player_display_name) {
    return offer.event_player_display_name;
  }
  return offer.player_name ?? 'Unknown player';
}

/**
 * Extract `MarketCard[]` from an event/match's full set of offers.
 *
 * `outcomeOffers` covers football match-result/double-chance/total-goals,
 * basketball/tennis match-winner, and any other markets stored as discrete
 * outcome rows. `oddsOffers` covers home_handicap_ot, game_total*, and
 * player_*.
 */
export function extractCards(
  outcomeOffers: OutcomeOffer[],
  oddsOffers: (OddsOffer | EventOddsOffer)[],
): ExtractedCards {
  const cards: MarketCard[] = [];

  // ─ OutcomeOffer: group by market_type
  const outcomeByMarket = new Map<OutcomeMarketType, OutcomeOffer[]>();
  for (const o of outcomeOffers) {
    if (!outcomeByMarket.has(o.market_type)) outcomeByMarket.set(o.market_type, []);
    outcomeByMarket.get(o.market_type)!.push(o);
  }

  const footballResultOffers = outcomeByMarket.get('football_result') ?? [];
  const footballDcOffers = outcomeByMarket.get('football_double_chance') ?? [];
  const footballRdcOffers = outcomeByMarket.get('football_result_double_chance') ?? [];

  // Combined football match result card (from any of the three sources).
  const combinedFootballResult: OutcomeOffer[] = [
    ...footballResultOffers,
    ...footballDcOffers,
    ...footballRdcOffers,
  ];
  const footballResultCard = buildFootballMatchResultCard(combinedFootballResult);
  if (footballResultCard) cards.push(footballResultCard);

  // Football total goals (multi-line).
  const footballTotalsOffers = outcomeByMarket.get('football_total_goals');
  if (footballTotalsOffers && footballTotalsOffers.length > 0) {
    const card = buildOutcomeTotalsCards(
      footballTotalsOffers,
      'football_total_goals',
      'fb-total-goals',
      'Total goals',
      'football',
    );
    if (card) cards.push(card);
  }

  // Match winner (basketball or other 2-way).
  const matchWinnerOffers = outcomeByMarket.get('match_winner');
  if (matchWinnerOffers && matchWinnerOffers.length > 0) {
    const card = buildTwoWayMatchWinnerCard(matchWinnerOffers, 'match_winner', 'match-winner', 'basketball');
    if (card) cards.push(card);
  }

  // Tennis match winner.
  const tennisOffers = outcomeByMarket.get('tennis_match_winner');
  if (tennisOffers && tennisOffers.length > 0) {
    const card = buildTwoWayMatchWinnerCard(tennisOffers, 'tennis_match_winner', 'tennis-match-winner', 'tennis');
    if (card) cards.push(card);
  }

  // ─ OddsOffer: group by (market_type, player_key)
  const oddsByGroup = new Map<string, (OddsOffer | EventOddsOffer)[]>();
  for (const o of oddsOffers) {
    const playerKey = isPlayerMarket(o.market_type)
      ? buildPlayerKey(o.player_name, 'event_scoped_player_key' in o ? o.event_scoped_player_key : null)
      : '';
    const groupKey = `${o.market_type}|${playerKey}`;
    if (!oddsByGroup.has(groupKey)) oddsByGroup.set(groupKey, []);
    oddsByGroup.get(groupKey)!.push(o);
  }

  for (const [groupKey, groupOffers] of oddsByGroup.entries()) {
    const first = groupOffers[0];
    const marketType = first.market_type;

    if (marketType === 'home_handicap_ot') {
      const card = buildCardFromOddsOffers(groupOffers, {
        cardKey: 'bb-handicap-ot',
        category: 'handicap',
        sport: 'basketball',
        title: 'Handicap (+OT)',
        pairEyebrow: 'H1 ↔ H2 · per line',
        backLabel: 'H1',
        layLabel: 'H2',
        formatLineTag: (threshold) => formatHandicapLine(threshold, 'home'),
        formatNumericLine: (threshold) => threshold,
      });
      if (card) cards.push(card);
      continue;
    }

    if (marketType === 'game_total' || marketType === 'game_total_ot') {
      const title = marketType === 'game_total_ot' ? 'Total points (+OT)' : 'Total points';
      const card = buildCardFromOddsOffers(groupOffers, {
        cardKey: `bb-${marketType}`,
        category: 'totals',
        sport: 'basketball',
        title,
        pairEyebrow: 'Over ↔ Under · per line',
        backLabel: 'Over',
        layLabel: 'Under',
        formatLineTag: (threshold) => threshold.toFixed(1),
      });
      if (card) cards.push(card);
      continue;
    }

    if (isPlayerMarket(marketType)) {
      const playerKey = buildPlayerKey(
        first.player_name,
        'event_scoped_player_key' in first ? (first as EventOddsOffer).event_scoped_player_key : null,
      );
      const playerDisplayName = buildPlayerDisplayName(first);
      const statLabel = playerStatLabel(marketType);
      const card = buildCardFromOddsOffers(groupOffers, {
        cardKey: `pl-${groupKey}`,
        category: 'player',
        sport: 'basketball',
        title: `${playerDisplayName} · ${statLabel}`,
        pairEyebrow: 'Over ↔ Under · per threshold',
        backLabel: 'Over',
        layLabel: 'Under',
        formatLineTag: (threshold) => threshold.toFixed(1),
        player: {
          key: playerKey,
          displayName: playerDisplayName,
          statKey: marketType,
          statLabel,
        },
      });
      if (card) cards.push(card);
      continue;
    }

    // Football totals stored as OddsOffer (rare; backend mostly uses OutcomeOffer for these now).
    if (marketType === 'football_total_goals') {
      const card = buildCardFromOddsOffers(groupOffers, {
        cardKey: 'fb-total-goals-odds',
        category: 'totals',
        sport: 'football',
        title: 'Total goals',
        pairEyebrow: 'Over ↔ Under · per line',
        backLabel: 'Over',
        layLabel: 'Under',
        formatLineTag: (threshold) => threshold.toFixed(1),
      });
      if (card && !cards.some((c) => c.cardKey === 'fb-total-goals')) {
        cards.push(card);
      }
      continue;
    }

    // Outcome-pair markets that landed in OddsOffer for some reason — skip to
    // avoid double-rendering (they're already covered by outcomeOffers branch).
  }

  return { cards };
}
