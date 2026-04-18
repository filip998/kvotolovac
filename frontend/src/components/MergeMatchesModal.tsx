import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Match, MatchMergeTeamPairing } from '../api/types';
import { useMergeMatches } from '../api/hooks';
import { formatDateTime } from '../utils/format';

interface MergeMatchesModalProps {
  matches: Match[];
  onClose: () => void;
  onMerged?: (targetId: string) => void;
}

interface SourcePairingState {
  swap: boolean;
}

export default function MergeMatchesModal({ matches, onClose, onMerged }: MergeMatchesModalProps) {
  const queryClient = useQueryClient();
  const mergeMutation = useMergeMatches();

  const [targetIdRaw, setTargetId] = useState<string>(matches[0]?.id ?? '');
  const [pairingState, setPairingState] = useState<Record<string, SourcePairingState>>({});

  const targetId = matches.find((m) => m.id === targetIdRaw)?.id ?? matches[0]?.id ?? '';
  const target = matches.find((m) => m.id === targetId);
  const sources = matches.filter((m) => m.id !== targetId);

  const startTimeMismatch = useMemo(() => {
    if (!target) return false;
    return sources.some((s) => (s.start_time ?? '') !== (target.start_time ?? ''));
  }, [target, sources]);

  const missingTeamIds = useMemo(() => {
    if (!target) return true;
    if (target.home_team_id == null || target.away_team_id == null) return true;
    return sources.some(
      (s) => s.home_team_id == null || s.away_team_id == null
    );
  }, [target, sources]);

  const computedPairings: MatchMergeTeamPairing[] = useMemo(() => {
    if (!target || missingTeamIds) return [];
    const out: MatchMergeTeamPairing[] = [];
    for (const src of sources) {
      const swapped = pairingState[src.id]?.swap === true;
      const srcHome = swapped ? src.away_team_id : src.home_team_id;
      const srcAway = swapped ? src.home_team_id : src.away_team_id;
      if (srcHome != null && srcHome !== target.home_team_id) {
        out.push({ source_team_id: srcHome, target_team_id: target.home_team_id! });
      }
      if (srcAway != null && srcAway !== target.away_team_id) {
        out.push({ source_team_id: srcAway, target_team_id: target.away_team_id! });
      }
    }
    return out;
  }, [target, sources, pairingState, missingTeamIds]);

  const canSubmit =
    target != null &&
    sources.length > 0 &&
    !startTimeMismatch &&
    !missingTeamIds &&
    !mergeMutation.isPending;

  async function handleConfirm() {
    if (!target) return;
    try {
      await mergeMutation.mutateAsync({
        target_match_id: target.id,
        source_match_ids: sources.map((s) => s.id),
        team_pairings: computedPairings,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['matches'] }),
        queryClient.invalidateQueries({ queryKey: ['discrepancies'] }),
        queryClient.invalidateQueries({ queryKey: ['canonicalTeams'] }),
        queryClient.invalidateQueries({ queryKey: ['teamReviewCases'] }),
      ]);
      onMerged?.(target.id);
      onClose();
    } catch {
      // Error surfaces via mergeMutation.error below
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-8"
      role="dialog"
      aria-modal="true"
      aria-label="Merge matches"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-3xl overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-text">Merge matches</h2>
            <p className="mt-0.5 text-xs text-text-muted">
              Pick the keeper, confirm team pairings, and the underlying canonical teams will be merged so future scrapes auto-consolidate.
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
              Selected matches have different start times. Match merging requires identical start times — fix the underlying scraper data first or unselect the mismatched match.
            </div>
          )}
          {!startTimeMismatch && missingTeamIds && (
            <div className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">
              One or more selected matches have no canonical team IDs yet. Resolve their team aliases via Team Review first.
            </div>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">Keeper match</h3>
            <p className="mt-1 text-xs text-text-muted">All odds, history, and discrepancies from the other matches will be reassigned here.</p>
            <div className="mt-2 grid gap-2">
              {matches.map((m) => (
                <label
                  key={m.id}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 transition ${
                    m.id === targetId ? 'border-accent bg-accent/10' : 'border-border hover:border-border-hover'
                  }`}
                >
                  <input
                    type="radio"
                    name="merge-target"
                    className="mt-0.5"
                    checked={m.id === targetId}
                    onChange={() => setTargetId(m.id)}
                  />
                  <div className="flex-1">
                    <div className="text-sm font-medium text-text">{m.home_team} vs {m.away_team}</div>
                    <div className="text-xs text-text-muted">{formatDateTime(m.start_time)} · {m.league_name}</div>
                  </div>
                  <div className="text-[11px] text-text-muted">{m.available_bookmakers.length} bms</div>
                </label>
              ))}
            </div>
          </section>

          {target && sources.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">Team pairings</h3>
              <p className="mt-1 text-xs text-text-muted">
                Confirm which side of each source matches the keeper. Use “Swap sides” when home/away are flipped on a bookmaker.
              </p>
              <div className="mt-2 grid gap-2">
                {sources.map((src) => {
                  const swapped = pairingState[src.id]?.swap === true;
                  const srcHome = swapped ? src.away_team : src.home_team;
                  const srcAway = swapped ? src.home_team : src.away_team;
                  return (
                    <div key={src.id} className="rounded-lg border border-border px-3 py-2">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-xs text-text-muted">{formatDateTime(src.start_time)} · {src.league_name}</div>
                        <button
                          type="button"
                          className="rounded-md border border-border px-2 py-0.5 text-[11px] text-text-muted hover:text-text"
                          onClick={() =>
                            setPairingState((prev) => ({
                              ...prev,
                              [src.id]: { swap: !swapped },
                            }))
                          }
                        >
                          ⇄ Swap sides
                        </button>
                      </div>
                      <div className="mt-1.5 grid grid-cols-2 gap-2 text-sm">
                        <div>
                          <div className="text-[11px] uppercase tracking-wider text-text-muted">Source → Keeper (home)</div>
                          <div className="text-text">{srcHome}{' '}<span className="text-text-muted">→</span>{' '}<span className="font-medium">{target.home_team}</span></div>
                        </div>
                        <div>
                          <div className="text-[11px] uppercase tracking-wider text-text-muted">Source → Keeper (away)</div>
                          <div className="text-text">{srcAway}{' '}<span className="text-text-muted">→</span>{' '}<span className="font-medium">{target.away_team}</span></div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {mergeMutation.error && (
            <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
              {(mergeMutation.error as Error).message || 'Merge failed'}
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
            {mergeMutation.isPending ? 'Merging…' : `Merge ${sources.length} match${sources.length === 1 ? '' : 'es'} into keeper`}
          </button>
        </footer>
      </div>
    </div>
  );
}
