interface PlayerChipFilterProps {
  players: { key: string; displayName: string }[];
  /** `null` represents "All". */
  activeKey: string | null;
  onChange: (key: string | null) => void;
  totalCount: number;
}

/**
 * Player chip filter for the basketball Player Markets section.
 * Scopes which player cards are visible inside the active stat tab.
 */
export default function PlayerChipFilter({ players, activeKey, onChange, totalCount }: PlayerChipFilterProps) {
  if (players.length === 0) return null;

  return (
    <div className="flex flex-wrap items-baseline gap-4.5 border-b border-border px-4 py-3.5 font-mono text-[10.5px] uppercase tracking-[0.14em]">
      <span className="text-text-muted">Filter</span>
      <button
        type="button"
        onClick={() => onChange(null)}
        aria-pressed={activeKey === null}
        className={`bg-transparent p-0 ${
          activeKey === null ? 'font-bold text-accent' : 'text-text-secondary hover:text-text'
        }`}
        style={{ fontFamily: 'inherit', letterSpacing: 'inherit', textTransform: 'inherit' }}
      >
        All · {totalCount}
      </button>
      {players.map((player) => {
        const isActive = activeKey === player.key;
        return (
          <button
            key={player.key}
            type="button"
            onClick={() => onChange(isActive ? null : player.key)}
            aria-pressed={isActive}
            className={`bg-transparent p-0 ${
              isActive ? 'font-bold text-accent' : 'text-text-secondary hover:text-text'
            }`}
            style={{ fontFamily: 'inherit', letterSpacing: 'inherit', textTransform: 'inherit' }}
          >
            {player.displayName}
          </button>
        );
      })}
    </div>
  );
}
