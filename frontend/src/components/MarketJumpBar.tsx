import type { PageSection } from '../utils/sportSections';

interface MarketJumpBarProps {
  sections: PageSection[];
}

const CATEGORY_LABEL: Record<PageSection['category'], string> = {
  match: 'Match',
  totals: 'Totals',
  handicap: 'Handicap',
  player: 'Players',
};

/**
 * Sticky jump-bar at the top of the markets area. Typographic chips grouped
 * by category with row-count badges. Click to anchor-scroll.
 */
export default function MarketJumpBar({ sections }: MarketJumpBarProps) {
  if (sections.length === 0) return null;

  return (
    <nav
      aria-label="Jump to market"
      className="sticky top-2 z-30 flex flex-wrap items-baseline gap-x-6 gap-y-1.5 border-b border-t border-border bg-bg/90 px-4 py-3 font-mono text-[10.5px] uppercase tracking-[0.14em] backdrop-blur"
    >
      {sections.map((section) => {
        if (section.category === 'player' && section.statTabs && section.statTabs.length > 0) {
          // For player section, link per stat tab so the user can jump to a specific stat.
          return (
            <span key={section.anchorId} className="inline-flex items-baseline gap-3">
              <span className="text-text-muted">{CATEGORY_LABEL[section.category]}</span>
              <a
                href={`#${section.anchorId}`}
                className="text-text-secondary hover:text-text"
              >
                {section.statTabs.map((tab) => tab.statLabel).join(' · ')}
                <span className="ml-1.5 text-accent">{section.cards.length}</span>
              </a>
            </span>
          );
        }
        return (
          <span key={section.anchorId} className="inline-flex items-baseline gap-3">
            <span className="text-text-muted">{CATEGORY_LABEL[section.category]}</span>
            <a href={`#${section.anchorId}`} className="text-text-secondary hover:text-text">
              {section.title}
              <span className="ml-1.5 text-accent">{section.cards.length}</span>
            </a>
          </span>
        );
      })}
    </nav>
  );
}
