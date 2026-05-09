export function eventOrMatchPath(
  matchId: string,
  resolvedEventId?: string | null,
  search = ''
): string {
  const path = resolvedEventId ? `/events/${resolvedEventId}` : `/matches/${matchId}`;
  return `${path}${search}`;
}
