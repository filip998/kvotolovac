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
      <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-white p-0.5">
        {config.logoSrc ? (
          <img
            src={config.logoSrc}
            alt={`${name} logo`}
            className="h-full w-full object-contain"
            loading="lazy"
          />
        ) : (
          <span className="text-[9px] font-bold tracking-wider text-black">
            {config.initials}
          </span>
        )}
      </span>
      {!compact && <span className="text-sm text-text-secondary">{name}</span>}
    </span>
  );
}
