import type { PlayerStatTab } from '../utils/sportSections';

interface StatTabBarProps {
  tabs: PlayerStatTab[];
  activeStatKey: string;
  onChange: (statKey: string) => void;
}

/**
 * Page-level stat tabs for the basketball Player Markets section.
 * Typographic, no pill chrome — accent underline marks the active tab.
 */
export default function StatTabBar({ tabs, activeStatKey, onChange }: StatTabBarProps) {
  if (tabs.length === 0) return null;

  return (
    <div
      role="tablist"
      aria-label="Player stat"
      className="flex flex-wrap items-baseline gap-7 border-b border-border px-4 pb-3.5 pt-1.5"
    >
      {tabs.map((tab) => {
        const isActive = tab.statKey === activeStatKey;
        const isEmpty = tab.cards.length === 0;
        return (
          <button
            key={tab.statKey}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={isEmpty}
            onClick={() => onChange(tab.statKey)}
            className={`-mb-[16px] inline-flex items-baseline gap-2 border-b-2 bg-transparent py-1.5 px-0 text-[12px] font-medium uppercase tracking-[0.16em] transition-colors ${
              isActive
                ? 'border-accent text-text'
                : 'border-transparent text-text-muted hover:text-text'
            } ${isEmpty ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
            style={{ fontFamily: 'inherit' }}
          >
            {tab.statLabel}
            <span
              className={`font-mono text-[10px] tracking-[0.06em] ${
                isActive ? 'text-accent' : 'text-text-muted'
              }`}
            >
              {tab.cards.length}
            </span>
          </button>
        );
      })}
    </div>
  );
}
