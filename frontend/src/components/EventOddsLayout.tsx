import { useMemo, useState } from 'react';
import type { EventOddsOffer, OddsOffer, OutcomeOffer } from '../api/types';
import { extractCards } from '../utils/oddsAxes';
import {
  collectPlayersAcrossTabs,
  groupCardsBySection,
  type PageSection,
  type PlayerStatTab,
} from '../utils/sportSections';
import OddsLadderCard from './OddsLadderCard';
import StatTabBar from './StatTabBar';
import PlayerChipFilter from './PlayerChipFilter';
import MarketJumpBar from './MarketJumpBar';
import EventWire from './EventWire';

interface EventOddsLayoutProps {
  outcomeOffers: OutcomeOffer[];
  oddsOffers: (OddsOffer | EventOddsOffer)[];
  /** Optional last-updated timestamp for the wire/ticker. */
  lastUpdated?: string | null;
}

/**
 * Composes the full per-event markets area: wire/ticker, jump-bar,
 * and the per-section blocks (Match · Totals · Handicap · Player markets)
 * each rendered as a stack of `OddsLadderCard`s.
 *
 * Page-level state lives here:
 *   - `activeStatKey`  — basketball player markets only; switching tabs
 *     swaps which player cards are visible.
 *   - `activePlayerKey` — basketball player markets only; scopes the
 *     active stat tab to a single player when set.
 *
 * Each card owns its own stake-calculator state internally so users can
 * size legs independently per market.
 */
export default function EventOddsLayout({
  outcomeOffers,
  oddsOffers,
  lastUpdated,
}: EventOddsLayoutProps) {
  const { cards } = useMemo(() => extractCards(outcomeOffers, oddsOffers), [outcomeOffers, oddsOffers]);
  const sections = useMemo(() => groupCardsBySection(cards), [cards]);

  if (sections.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-text-muted">
        No odds data available for this event yet.
      </div>
    );
  }

  return (
    <div
      className="space-y-0"
      style={{
        // System-font scope: the user wanted these screens to feel less
        // "modern" / easier to read than the rest of the app. Override the
        // global Outfit / JetBrains Mono variables for this subtree only.
        ['--font-sans' as string]:
          'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        ['--font-mono' as string]:
          'ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
        fontFamily: 'var(--font-sans)',
        fontFeatureSettings: '"tnum" 1',
      }}
    >
      <EventWire cards={cards} lastUpdated={lastUpdated ?? null} />
      <MarketJumpBar sections={sections} />
      {sections.map((section) => (
        <Section key={section.anchorId} section={section} />
      ))}
    </div>
  );
}

function Section({ section }: { section: PageSection }) {
  if (section.category === 'player') {
    return <PlayerSection section={section} />;
  }
  return (
    <section id={section.anchorId} className="border-t-2 border-text">
      <header className="flex flex-wrap items-baseline gap-3.5 px-4 pb-4 pt-4">
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-accent">
          {String(section.index).padStart(2, '0')}
        </span>
        <span className="text-[11px] font-medium uppercase tracking-[0.22em] text-text-secondary">
          {section.title}
        </span>
        <span aria-hidden className="h-0 flex-1 self-center border-b border-border" />
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-text-muted">
          {section.cards.length} CARD{section.cards.length === 1 ? '' : 'S'}
        </span>
      </header>
      <div>
        {section.cards.map((card) => (
          <OddsLadderCard key={card.cardKey} card={card} />
        ))}
      </div>
    </section>
  );
}

function PlayerSection({ section }: { section: PageSection }) {
  const tabs = useMemo<PlayerStatTab[]>(() => section.statTabs ?? [], [section.statTabs]);
  // Stored selections; effective selections are derived at render time so they
  // survive data refreshes (e.g. bookmaker filter removed the active player).
  const [storedStatKey, setStoredStatKey] = useState<string | null>(null);
  const [storedPlayerKey, setStoredPlayerKey] = useState<string | null>(null);

  const allPlayers = useMemo(() => collectPlayersAcrossTabs(tabs), [tabs]);

  // Effective stat tab: stored value if it still exists, else first tab.
  const activeTab =
    (storedStatKey && tabs.find((t) => t.statKey === storedStatKey)) || tabs[0] || null;

  // Effective player filter: keep stored value only if the player is still present.
  const activePlayerKey =
    storedPlayerKey && allPlayers.some((p) => p.key === storedPlayerKey) ? storedPlayerKey : null;

  const visibleCards = useMemo(() => {
    if (!activeTab) return [];
    if (activePlayerKey === null) return activeTab.cards;
    return activeTab.cards.filter((c) => c.player?.key === activePlayerKey);
  }, [activeTab, activePlayerKey]);

  return (
    <section id={section.anchorId} className="border-t-2 border-text">
      <header className="flex flex-wrap items-baseline gap-3.5 px-4 pb-4 pt-4">
        <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-accent">
          {String(section.index).padStart(2, '0')}
        </span>
        <span className="text-[11px] font-medium uppercase tracking-[0.22em] text-text-secondary">
          {section.title}
        </span>
        <span aria-hidden className="h-0 flex-1 self-center border-b border-border" />
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-text-muted">
          {tabs.length} STAT{tabs.length === 1 ? '' : 'S'} · {section.cards.length} CARD
          {section.cards.length === 1 ? '' : 'S'}
        </span>
      </header>
      <StatTabBar tabs={tabs} activeStatKey={activeTab?.statKey ?? ''} onChange={setStoredStatKey} />
      <PlayerChipFilter
        players={allPlayers}
        activeKey={activePlayerKey}
        onChange={setStoredPlayerKey}
        totalCount={activeTab?.cards.length ?? 0}
      />
      <div>
        {visibleCards.length > 0 ? (
          visibleCards.map((card) => <OddsLadderCard key={card.cardKey} card={card} />)
        ) : (
          <div className="px-4 py-8 text-center text-sm text-text-muted">
            No cards for this player at the selected stat.
          </div>
        )}
      </div>
    </section>
  );
}

