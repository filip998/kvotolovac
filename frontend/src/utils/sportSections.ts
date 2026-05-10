/**
 * Sport-aware section grouping for the event-detail page.
 *
 * Takes a list of `MarketCard`s and arranges them into the per-sport
 * section structure (Match · Totals · Handicap · Player markets).
 *
 * Pure module, no React.
 */

import type { CardCategory, MarketCard } from './oddsAxes';

export interface PageSection {
  /** 1-based index for the editorial header label ("01 · MATCH MARKETS"). */
  index: number;
  /** Section title shown in the heading. */
  title: string;
  /** Anchor id used by the jump-bar. */
  anchorId: string;
  /** What kind of cards live in this section. */
  category: CardCategory;
  /** Cards in display order. */
  cards: MarketCard[];
  /** Player-section only: stat-tab map keyed by `statKey`. */
  statTabs?: PlayerStatTab[];
}

export interface PlayerStatTab {
  statKey: string;
  statLabel: string;
  cards: MarketCard[];
  /** Distinct player keys seen for this stat, in card order. */
  playerKeys: string[];
}

const CATEGORY_TITLES: Record<CardCategory, string> = {
  match: 'Match markets',
  totals: 'Totals',
  handicap: 'Match · Handicap',
  player: 'Player markets',
};

const ANCHOR_BY_CATEGORY: Record<CardCategory, string> = {
  match: 'sec-match',
  totals: 'sec-totals',
  handicap: 'sec-handicap',
  player: 'sec-players',
};

const CATEGORY_ORDER: CardCategory[] = ['match', 'handicap', 'totals', 'player'];

/**
 * Build the page-level sections for an event.
 *
 * - Cards are grouped by `category`.
 * - Sections appear in `CATEGORY_ORDER`, with empty sections omitted.
 * - For the player section, cards are further grouped into `statTabs`.
 *
 * The optional `sport` argument is currently used only for future hooks
 * (e.g. per-sport section ordering). We do **not** filter by it: each card
 * already carries its sport classification from `extractCards`, and an
 * upstream `event.sport` mismatch (unknown / missing tag) would otherwise
 * silently hide cards. Each event/match in practice has one sport, so the
 * cards naturally agree.
 */
export function groupCardsBySection(cards: MarketCard[]): PageSection[] {
  const byCategory = new Map<CardCategory, MarketCard[]>();
  for (const card of cards) {
    if (!byCategory.has(card.category)) byCategory.set(card.category, []);
    byCategory.get(card.category)!.push(card);
  }

  const sections: PageSection[] = [];
  let index = 0;
  for (const category of CATEGORY_ORDER) {
    const categoryCards = byCategory.get(category);
    if (!categoryCards || categoryCards.length === 0) continue;
    index += 1;
    const section: PageSection = {
      index,
      title: CATEGORY_TITLES[category],
      anchorId: ANCHOR_BY_CATEGORY[category],
      category,
      cards: categoryCards,
    };
    if (category === 'player') {
      section.statTabs = buildStatTabs(categoryCards);
    }
    sections.push(section);
  }
  return sections;
}

function buildStatTabs(cards: MarketCard[]): PlayerStatTab[] {
  const byStat = new Map<string, PlayerStatTab>();
  for (const card of cards) {
    if (!card.player) continue;
    const { statKey, statLabel } = card.player;
    let tab = byStat.get(statKey);
    if (!tab) {
      tab = { statKey, statLabel, cards: [], playerKeys: [] };
      byStat.set(statKey, tab);
    }
    tab.cards.push(card);
    if (!tab.playerKeys.includes(card.player.key)) {
      tab.playerKeys.push(card.player.key);
    }
  }
  // Sort tabs by label for stability.
  return Array.from(byStat.values()).sort((a, b) => a.statLabel.localeCompare(b.statLabel));
}

/** All distinct players across the player section (for the chip filter). */
export function collectPlayersAcrossTabs(statTabs: PlayerStatTab[]): { key: string; displayName: string }[] {
  const seen = new Map<string, { key: string; displayName: string }>();
  for (const tab of statTabs) {
    for (const card of tab.cards) {
      if (!card.player) continue;
      if (!seen.has(card.player.key)) {
        seen.set(card.player.key, { key: card.player.key, displayName: card.player.displayName });
      }
    }
  }
  return Array.from(seen.values()).sort((a, b) => a.displayName.localeCompare(b.displayName));
}
