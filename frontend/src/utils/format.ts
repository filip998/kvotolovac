const MATCH_TIMEZONE = 'Europe/Belgrade';
const MATCH_DATE_TIME_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: MATCH_TIMEZONE,
});
const UNITS_FORMATTER = new Intl.NumberFormat('en-GB', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

function parseAppDate(isoString: string | null | undefined): Date | null {
  if (!isoString) return null;
  const trimmed = isoString.trim();
  if (!trimmed) return null;

  const hasTimezone = /(?:[zZ]|[+-]\d{2}:\d{2})$/.test(trimmed);
  const normalized = hasTimezone ? trimmed : `${trimmed}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatOdds(value: number | null): string {
  if (value === null || value === undefined) return '—';
  return value.toFixed(2);
}

export function formatPercentage(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(1)}%`;
}

export function formatGap(value: number): string {
  return value.toFixed(1);
}

export function formatThreshold(value: number): string {
  return value.toFixed(1);
}

/**
 * Render an Asian-handicap line from the project's storage convention.
 *
 * Storage convention: ``threshold`` is the home team's expected margin
 * (positive = home favoured). The user-facing Asian-handicap value is the
 * spread that the team would have to cover, which is the negation of this:
 *
 *   threshold = +4.5 (home favoured by 4.5)  → home spread = "-4.5"
 *   threshold = -3.5 (home is the underdog) → home spread = "+3.5"
 *
 * Pass ``side='away'`` to render the same line from the away team's
 * perspective (which is the opposite sign of the home spread).
 */
export function formatHandicapLine(
  threshold: number,
  side: 'home' | 'away' = 'home',
): string {
  const value = side === 'home' ? -threshold : threshold;
  if (Math.abs(value) < 1e-9) return '0';
  const sign = value > 0 ? '+' : '−';
  return `${sign}${Math.abs(value).toFixed(1)}`;
}

export function isHandicapMarket(marketType: string | null | undefined): boolean {
  return marketType === 'home_handicap_ot';
}

export function formatUnits(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return UNITS_FORMATTER.format(value);
}

export function roundUnitsDisplayValue(value: number): number {
  if (!Number.isFinite(value)) return value;
  const normalized = Math.abs(value) < 1e-9 ? 0 : value;
  const rounded = Number(normalized.toFixed(2));
  return Math.abs(rounded) < 1e-9 ? 0 : rounded;
}

export function formatSignedUnits(value: number): string {
  if (!Number.isFinite(value)) return '—';
  const roundedValue = roundUnitsDisplayValue(value);
  const roundedAbs = Math.abs(roundedValue);
  if (roundedAbs === 0) return '0';
  const sign = roundedValue > 0 ? '+' : '-';
  return `${sign}${UNITS_FORMATTER.format(roundedAbs)}`;
}

export function formatDateTime(isoString: string | null | undefined): string {
  const date = parseAppDate(isoString);
  if (!date) return '—';
  return MATCH_DATE_TIME_FORMATTER.format(date);
}

export function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return 'Never';
  const date = parseAppDate(isoString);
  if (!date) return 'Never';
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMinutes = Math.floor(diffMs / 60000);

  if (diffMinutes < 1) return 'Just now';
  if (diffMinutes < 60) return `${diffMinutes} min ago`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export function profitColor(margin: number): string {
  if (margin >= 0.03) return 'text-accent';
  if (margin >= 0.015) return 'text-warning';
  return 'text-text-secondary';
}

export function profitBgColor(margin: number): string {
  if (margin >= 0.03) return 'bg-accent/[0.06] border-accent/20';
  if (margin >= 0.015) return 'bg-warning/[0.06] border-warning/20';
  return 'bg-surface border-border';
}

/**
 * ROI for a balanced two-leg arb at a given implied %.
 *
 * Implied % = (1/odds_back + 1/odds_lay) × 100.
 *   < 100 → arbitrage opportunity (positive ROI)
 *   = 100 → break-even
 *   > 100 → bookmaker margin (negative ROI on a balanced split)
 *
 * For a perfectly balanced split, ROI = (100 / impliedPct − 1).
 * Examples:
 *   impliedPct = 96.8 → ROI ≈ +3.31%
 *   impliedPct = 105  → ROI ≈ −4.76%
 *   impliedPct = 108.8 → ROI ≈ −8.09%
 */
export function roiFromImpliedPct(impliedPct: number | null | undefined): number | null {
  if (impliedPct === null || impliedPct === undefined || !Number.isFinite(impliedPct) || impliedPct <= 0) return null;
  return (100 / impliedPct - 1) * 100;
}

/** Format a balanced ROI as "+3.3%" / "−4.8%" / "—". */
export function formatRoi(roi: number | null | undefined): string {
  if (roi === null || roi === undefined || !Number.isFinite(roi)) return '—';
  const abs = Math.abs(roi);
  // Use a real minus sign so signs render with consistent width in mono fonts.
  const sign = roi > 1e-9 ? '+' : roi < -1e-9 ? '−' : '';
  return `${sign}${abs.toFixed(2)}%`;
}

/** Tailwind text-color class for an ROI value. */
export function roiColor(roi: number | null | undefined): string {
  if (roi === null || roi === undefined || !Number.isFinite(roi)) return 'text-text-muted';
  if (roi > 0) return 'text-accent';
  if (roi >= -1) return 'text-warning';
  return 'text-danger';
}

/**
 * Format a back↔lay implied percentage for display.
 *
 * impliedPct is `1/odds_back + 1/odds_lay` expressed as a percentage of 100.
 *   < 100 → arbitrage (sure profit at the right stake split)
 *   = 100 → break-even
 *   > 100 → bookmaker margin
 */
export function formatImpliedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${value.toFixed(1)}%`;
}

/** Tailwind text-color class for an implied % value. */
export function impliedPctColor(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'text-text-muted';
  if (value < 100) return 'text-accent';
  if (value < 101) return 'text-accent';
  if (value < 105) return 'text-warning';
  return 'text-danger';
}

/**
 * Classify an implied % into a category for non-color UX (icon, label, etc.).
 */
export type ImpliedPctTier = 'arb' | 'knife-edge' | 'margin' | 'high-margin' | 'unknown';

export function impliedPctTier(value: number | null | undefined): ImpliedPctTier {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'unknown';
  if (value < 100) return 'arb';
  if (value < 101) return 'knife-edge';
  if (value < 105) return 'margin';
  return 'high-margin';
}
