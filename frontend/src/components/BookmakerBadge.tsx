const BOOKMAKER_CONFIG: Record<string, { initials: string; logoSrc?: string }> = {
  mozzart: { initials: 'MZ', logoSrc: '/bookmaker-logos/mozzart.png' },
  meridian: { initials: 'MR', logoSrc: '/bookmaker-logos/meridian.png' },
  maxbet: { initials: 'MB', logoSrc: '/bookmaker-logos/maxbet.png' },
  oktagonbet: { initials: 'OK', logoSrc: '/bookmaker-logos/oktagonbet.png' },
  admiralbet: { initials: 'AD', logoSrc: '/bookmaker-logos/admiralbet.png' },
  balkanbet: { initials: 'BB', logoSrc: '/bookmaker-logos/balkanbet.png' },
  merkurxtip: { initials: 'MX', logoSrc: '/bookmaker-logos/merkurxtip.png' },
  pinnbet: { initials: 'PN', logoSrc: '/bookmaker-logos/pinnbet.png' },
  soccerbet: { initials: 'SB', logoSrc: '/bookmaker-logos/soccerbet.png' },
  superbet: { initials: 'SU', logoSrc: '/bookmaker-logos/superbet.png' },
  betole: { initials: 'BO' },
};

function normalizeBookmakerKey(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

export default function BookmakerBadge({
  name,
  compact = false,
  href,
  ariaLabel,
}: {
  name: string;
  compact?: boolean;
  href?: string | null;
  ariaLabel?: string;
}) {
  const config =
    BOOKMAKER_CONFIG[normalizeBookmakerKey(name)] ?? {
      initials: name.slice(0, 2).toUpperCase(),
    };

  const content = (
    <>
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
    </>
  );

  if (!href) {
    return <span className="inline-flex items-center gap-2">{content}</span>;
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      aria-label={ariaLabel ?? `Open ${name} match page`}
      className="group inline-flex items-center gap-1.5 rounded-md outline-none transition hover:text-accent focus-visible:ring-2 focus-visible:ring-accent/35 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
    >
      <span className="inline-flex items-center gap-2">{content}</span>
      <span
        className="inline-flex h-4 w-4 items-center justify-center text-text-muted transition group-hover:text-accent"
        aria-hidden="true"
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <path
            d="M4 2H10V8"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M10 2L2 10"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    </a>
  );
}
