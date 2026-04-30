import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Match } from '../api/types';
import { useMergeEvents } from '../api/hooks';
import { formatDateTime } from '../utils/format';

interface MergeMatchesModalProps {
  matches: Match[];
  onClose: () => void;
  onMerged?: (primaryId: string) => void;
}

export default function MergeMatchesModal({ matches, onClose, onMerged }: MergeMatchesModalProps) {
  const queryClient = useQueryClient();
  const mergeMutation = useMergeEvents();
  const [primaryIdRaw, setPrimaryId] = useState<string>(matches[0]?.id ?? '');

  const primaryId = matches.find((m) => m.id === primaryIdRaw)?.id ?? matches[0]?.id ?? '';
  const primary = matches.find((m) => m.id === primaryId);
  const sources = matches.filter((m) => m.id !== primaryId);

  const startTimeMismatch = useMemo(() => {
    if (!primary) return false;
    return sources.some((source) => (source.start_time ?? '') !== (primary.start_time ?? ''));
  }, [primary, sources]);

  const sportMismatch = useMemo(() => {
    if (!primary) return false;
    return sources.some((source) => source.sport !== primary.sport);
  }, [primary, sources]);

  const canSubmit =
    primary != null &&
    sources.length > 0 &&
    !startTimeMismatch &&
    !sportMismatch &&
    !mergeMutation.isPending;

  async function handleConfirm() {
    if (!primary) return;
    try {
      await mergeMutation.mutateAsync({
        primary_match_id: primary.id,
        source_match_ids: sources.map((source) => source.id),
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['matches'] }),
        queryClient.invalidateQueries({ queryKey: ['discrepancies'] }),
        queryClient.invalidateQueries({ queryKey: ['opportunities'] }),
        queryClient.invalidateQueries({ queryKey: ['eventReviewCases'] }),
        queryClient.invalidateQueries({ queryKey: ['matchOdds'] }),
        queryClient.invalidateQueries({ queryKey: ['matchHistory'] }),
      ]);
      onMerged?.(primary.id);
      onClose();
    } catch {
      // Error surfaces via mergeMutation.error below.
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-8"
      role="dialog"
      aria-modal="true"
      aria-label="Merge events"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-3xl overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-text">Merge events</h2>
            <p className="mt-0.5 text-xs text-text-muted">
              Group these bookmaker rows as one real event. Source match rows and canonical teams stay unchanged.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-2 py-1 text-xs text-text-muted hover:text-text"
          >
            Close
          </button>
        </header>

        <div className="space-y-4 px-5 py-4">
          {startTimeMismatch && (
            <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
              Selected events have different start times. Event merging requires identical start times; leave the mismatched row for Event Review.
            </div>
          )}
          {sportMismatch && (
            <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
              Selected events span different sports. Pick rows from the same sport before merging.
            </div>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">Primary event label</h3>
            <p className="mt-1 text-xs text-text-muted">
              This label is used as the representative matchup while all selected source variants remain visible as evidence.
            </p>
            <div className="mt-2 grid gap-2">
              {matches.map((match) => (
                <label
                  key={match.id}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 transition ${
                    match.id === primaryId ? 'border-accent bg-accent/10' : 'border-border hover:border-border-hover'
                  }`}
                >
                  <input
                    type="radio"
                    name="event-primary"
                    className="mt-0.5"
                    checked={match.id === primaryId}
                    onChange={() => setPrimaryId(match.id)}
                  />
                  <div className="flex-1">
                    <div className="text-sm font-medium text-text">
                      {match.home_team} vs {match.away_team}
                    </div>
                    <div className="text-xs text-text-muted">
                      {formatDateTime(match.start_time)} · {match.league_name} · {match.sport}
                    </div>
                  </div>
                  <div className="text-[11px] text-text-muted">
                    {match.available_bookmakers.length} source{match.available_bookmakers.length === 1 ? '' : 's'}
                  </div>
                </label>
              ))}
            </div>
          </section>

          {primary && sources.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">Source variants kept</h3>
              <div className="mt-2 grid gap-2">
                {sources.map((source) => (
                  <div key={source.id} className="rounded-lg border border-border px-3 py-2">
                    <div className="text-sm font-medium text-text">
                      {source.home_team} vs {source.away_team}
                    </div>
                    <div className="mt-0.5 text-xs text-text-muted">
                      {formatDateTime(source.start_time)} · {source.league_name} · {source.available_bookmakers.length} source
                      {source.available_bookmakers.length === 1 ? '' : 's'}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <div className="rounded-lg border border-border bg-bg/40 px-3 py-2 text-xs text-text-muted">
            Team aliases are not changed here. Use Team Review if two labels should become the same canonical team.
          </div>

          {mergeMutation.error && (
            <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
              {(mergeMutation.error as Error).message || 'Event merge failed'}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border bg-bg/40 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!canSubmit}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-semibold text-bg disabled:cursor-not-allowed disabled:border-border disabled:bg-border disabled:text-text-muted"
          >
            {mergeMutation.isPending
              ? 'Merging…'
              : `Merge ${sources.length + 1} event${sources.length === 0 ? '' : 's'}`}
          </button>
        </footer>
      </div>
    </div>
  );
}
