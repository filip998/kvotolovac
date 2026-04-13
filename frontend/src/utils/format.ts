function parseAppDate(isoString: string): Date | null {
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

export function formatDateTime(isoString: string): string {
  const date = parseAppDate(isoString);
  if (!date) return '—';
  return date.toLocaleDateString('en-GB', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
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
