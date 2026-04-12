const BOOKMAKER_CONFIG: Record<string, { initials: string; logoSrc?: string }> = {
  mozzart: { initials: 'MZ', logoSrc: '/bookmaker-logos/mozzart.png' },
  meridian: { initials: 'MR', logoSrc: '/bookmaker-logos/meridian.png' },
  maxbet: { initials: 'MB', logoSrc: '/bookmaker-logos/maxbet.png' },
  oktagonbet: { initials: 'OK', logoSrc: '/bookmaker-logos/oktagonbet.svg' },
  admiralbet: { initials: 'AD', logoSrc: '/bookmaker-logos/admiralbet.png' },
  balkanbet: { initials: 'BB', logoSrc: '/bookmaker-logos/balkanbet.png' },
  merkurxtip: { initials: 'MX', logoSrc: '/bookmaker-logos/merkurxtip.png' },
  pinnbet: { initials: 'PN', logoSrc: '/bookmaker-logos/pinnbet.png' },
};

function normalizeBookmakerKey(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

export default function BookmakerBadge({
  name,
  compact = false,
}: {
  name: string;
  compact?: boolean;
}) {
  const config =
    BOOKMAKER_CONFIG[normalizeBookmakerKey(name)] ?? {
      initials: name.slice(0, 2).toUpperCase(),
    };

  return (
    <span className="inline-flex items-center gap-2">
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line-700/80 bg-white p-1">
        {config.logoSrc ? (
          <img
            src={config.logoSrc}
            alt={`${name} logo`}
            className="h-full w-full object-contain"
            loading="lazy"
          />
        ) : (
          <span className="text-[10px] font-semibold tracking-[0.18em] text-black">
            {config.initials}
          </span>
        )}
      </span>
      {!compact && <span className="text-sm text-slate-200">{name}</span>}
    </span>
  );
}
