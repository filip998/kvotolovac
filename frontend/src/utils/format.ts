export function formatOdds(value: number | null): string {
  if (value === null || value === undefined) return '—';
  return value.toFixed(2);
}

export function formatPercentage(value: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(1)}%`;
}

export function formatGap(value: number): string {
  return value.toFixed(1);
}

export function formatThreshold(value: number): string {
  return value.toFixed(1);
}

export function formatDateTime(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleDateString('en-GB', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return 'Never';
  const now = new Date();
  const date = new Date(isoString);
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
  if (margin >= 3.0) return 'text-green-400';
  if (margin >= 1.5) return 'text-amber-400';
  return 'text-gray-400';
}

export function profitBgColor(margin: number): string {
  if (margin >= 3.0) return 'bg-green-500/10 border-green-500/20';
  if (margin >= 1.5) return 'bg-amber-500/10 border-amber-500/20';
  return 'bg-gray-500/10 border-gray-500/20';
}
