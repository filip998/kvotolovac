import EmptyState from './EmptyState';
import LoadingSpinner from './LoadingSpinner';
import OfferSearchStrip from './OfferSearchStrip';
import type { CanonicalTeam } from '../api/types';
import { normalizeSearchText } from '../utils/search';

function aliasPreview(aliases: string[]) {
  const preview = aliases.slice(0, 6);
  const remainder = aliases.length - preview.length;
  return {
    preview,
    remainder,
  };
}

export default function CanonicalTeamsPanel({
  teams,
  isLoading,
  errorMessage,
  searchQuery,
  onSearchChange,
  selectedSourceTeam,
  onSelectSource,
  onMerge,
  onUnmerge,
  mergingSourceTeamId,
  mergingTargetTeamId,
  unmergingTeamId,
  actionMessage,
  totalTeams,
  pageOffset,
  pageSize,
  pageSizeOptions,
  onPageSizeChange,
  onPreviousPage,
  onNextPage,
}: {
  teams: CanonicalTeam[];
  isLoading: boolean;
  errorMessage: string | null;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  selectedSourceTeam: CanonicalTeam | null;
  onSelectSource: (team: CanonicalTeam | null) => void;
  onMerge: (sourceTeamId: number, targetTeamId: number) => void;
  onUnmerge: (sourceTeamId: number) => void;
  mergingSourceTeamId: number | null;
  mergingTargetTeamId: number | null;
  unmergingTeamId: number | null;
  actionMessage: string | null;
  totalTeams: number;
  pageOffset: number;
  pageSize: number;
  pageSizeOptions: number[];
  onPageSizeChange: (pageSize: number) => void;
  onPreviousPage: () => void;
  onNextPage: () => void;
}) {
  const hasSearchQuery = normalizeSearchText(searchQuery).length > 0;
  const activeSearchLabel = searchQuery.trim();
  const selectedSourceTeamId = selectedSourceTeam?.id ?? null;
  const pageStart = totalTeams === 0 ? 0 : pageOffset + 1;
  const pageEnd = totalTeams === 0 ? 0 : pageOffset + teams.length;
  const totalPages = Math.max(1, Math.ceil(totalTeams / pageSize));
  const currentPage = totalTeams === 0 ? 1 : Math.floor(pageOffset / pageSize) + 1;
  const canGoPrevious = pageOffset > 0;
  const canGoNext = pageOffset + teams.length < totalTeams;
  const mergedTeamsOnPage = teams.filter((team) => team.merged_into_team_id != null).length;

  const searchStrip = (
    <OfferSearchStrip
      value={searchQuery}
      onChange={onSearchChange}
      scopeLabel="Canonical teams"
      placeholder="Search canonical names or aliases, e.g. Baskonia or Buducnost"
      resultCount={totalTeams}
      totalCount={totalTeams}
      tone="accent"
    />
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <LoadingSpinner />
      </div>
    );
  }

  if (errorMessage) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <div className="rounded-lg border border-danger/30 bg-danger/10 p-6 text-center">
          <p className="text-sm text-danger">Failed to load canonical teams: {errorMessage}</p>
        </div>
      </div>
    );
  }

  if (hasSearchQuery && totalTeams === 0) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <EmptyState
          title={`No canonical teams match "${activeSearchLabel}"`}
          message="Search checks both canonical names and every saved alias."
        />
      </div>
    );
  }

  if (teams.length === 0 && totalTeams === 0) {
    return (
      <div className="space-y-4">
        {searchStrip}
        <EmptyState
          title="No canonical teams yet"
          message="Run a scrape or approve team review rows to start building the canonical club list."
        />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {searchStrip}

      <section className="rounded-xl border border-border bg-surface p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h3 className="text-base font-semibold text-text">Canonical team admin</h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-text-secondary">
              Pick a duplicate source team, then merge it into the correct target. The source name
              becomes an alias of the target team.
            </p>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-3 py-2 text-xs text-text-muted">
            Merge duplicates, unmerge mistakes
          </div>
        </div>

        {actionMessage && (
          <div className="mt-4 rounded-lg border border-accent/30 bg-accent/[0.08] px-4 py-3 text-sm text-text-secondary">
            {actionMessage}
          </div>
        )}

        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Total</div>
            <div className="mt-2 font-mono text-2xl font-semibold text-text">{totalTeams}</div>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">Range</div>
            <div className="mt-2 font-mono text-2xl font-semibold text-accent">
              {pageStart}-{pageEnd}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
              Page
            </div>
            <div className="mt-2 font-mono text-2xl font-semibold text-text-secondary">
              {currentPage}/{totalPages}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-bg/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-[0.14em] text-text-muted">
              Merged here
            </div>
            <div className="mt-2 font-mono text-2xl font-semibold text-warning">
              {mergedTeamsOnPage}
            </div>
          </div>
        </div>
      </section>

      {selectedSourceTeam && (
        <section className="rounded-xl border border-warning/30 bg-warning/10 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-[11px] uppercase tracking-[0.18em] text-text-muted">
                Selected source team
              </div>
              <div className="mt-2 text-lg font-semibold text-text">
                {selectedSourceTeam.display_name}
              </div>
              <p className="mt-1 text-sm text-text-secondary">
                Choose a different team below to merge this source into it.
              </p>
            </div>
            <button
              type="button"
              onClick={() => onSelectSource(null)}
              disabled={mergingSourceTeamId != null || unmergingTeamId != null}
              className="rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
            >
              Clear selection
            </button>
          </div>
        </section>
      )}

      <section className="space-y-3">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h3 className="text-sm font-semibold text-text">Canonical teams</h3>
            <p className="mt-1 text-sm text-text-secondary">
              Search all active and merged canonical rows, inspect aliases, merge duplicates, or
              roll back a merge.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            <span>
              Showing {pageStart}-{pageEnd} of {totalTeams}
            </span>
            <label className="flex items-center gap-2">
              <span>Rows</span>
              <select
                value={pageSize}
                onChange={(event) => onPageSizeChange(Number(event.target.value))}
                className="rounded-md border border-border bg-bg px-2 py-1 text-xs text-text outline-none transition focus:border-accent/50"
              >
                {pageSizeOptions.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <div className="space-y-3">
          {teams.length === 0 ? (
            <EmptyState
              title="No teams on this page"
              message="Use the previous page control to return to available canonical rows."
            />
          ) : (
            teams.map((team) => {
              const isMerged = team.merged_into_team_id != null;
              const isSelectedSource = team.id === selectedSourceTeamId;
              const isMergingIntoTeam =
                mergingSourceTeamId === selectedSourceTeamId && mergingTargetTeamId === team.id;
              const isUnmergingTeam = unmergingTeamId === team.id;
              const { preview, remainder } = aliasPreview(team.aliases);

              return (
                <article
                  key={team.id}
                  className={`rounded-xl border p-4 ${
                    isSelectedSource
                      ? 'border-warning/40 bg-warning/10'
                      : isMerged
                        ? 'border-border bg-bg/50'
                        : 'border-border bg-surface'
                  }`}
                >
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h4 className="text-lg font-semibold tracking-tight text-text">
                          {team.display_name}
                        </h4>
                        <span className="rounded-full border border-border bg-bg px-2 py-1 text-[11px] font-medium text-text-secondary">
                          {team.alias_count} alias{team.alias_count === 1 ? '' : 'es'}
                        </span>
                        <span className="rounded-full border border-border bg-bg px-2 py-1 text-[11px] font-medium text-text-muted">
                          {team.sport}
                        </span>
                        {isMerged && (
                          <span className="rounded-full border border-warning/30 bg-warning/10 px-2 py-1 text-[11px] font-medium text-warning">
                            merged into #{team.merged_into_team_id}
                          </span>
                        )}
                      </div>

                      <div className="mt-3 flex flex-wrap gap-2">
                        {preview.map((alias) => (
                          <span
                            key={`${team.id}-${alias}`}
                            className="rounded-full border border-border bg-bg/60 px-2.5 py-1 text-xs text-text-secondary"
                          >
                            {alias}
                          </span>
                        ))}
                        {remainder > 0 && (
                          <span className="rounded-full border border-border bg-bg/60 px-2.5 py-1 text-xs text-text-muted">
                            +{remainder} more
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex shrink-0 flex-col gap-2 xl:w-44">
                      {isMerged ? (
                        <button
                          type="button"
                          onClick={() => onUnmerge(team.id)}
                          disabled={mergingSourceTeamId != null || unmergingTeamId != null}
                          className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs font-medium text-warning transition hover:border-warning hover:bg-warning/15 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {isUnmergingTeam ? 'Unmerging...' : 'Unmerge'}
                        </button>
                      ) : isSelectedSource ? (
                        <button
                          type="button"
                          onClick={() => onSelectSource(null)}
                          disabled={mergingSourceTeamId != null || unmergingTeamId != null}
                          className="rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          Selected source
                        </button>
                      ) : selectedSourceTeamId == null ? (
                        <button
                          type="button"
                          onClick={() => onSelectSource(team)}
                          disabled={mergingSourceTeamId != null || unmergingTeamId != null}
                          className="rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          Select source
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onMerge(selectedSourceTeamId, team.id)}
                          disabled={mergingSourceTeamId != null || unmergingTeamId != null}
                          className="rounded-md border border-border bg-surface px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {isMergingIntoTeam ? 'Merging...' : 'Merge source into this'}
                        </button>
                      )}
                      <p className="text-[11px] leading-5 text-text-muted">
                        {isMerged
                          ? 'Restores this source team and moves its saved aliases back when rollback metadata exists.'
                          : isSelectedSource
                            ? 'This team will disappear as a standalone canonical row after merge.'
                            : selectedSourceTeamId == null
                              ? 'Start by selecting the duplicate team you want to merge away.'
                              : 'The selected source team name will be preserved as an alias here.'}
                      </p>
                    </div>
                  </div>
                </article>
              );
            })
          )}
        </div>
        <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-text-secondary">
            Page {currentPage} of {totalPages} · showing {teams.length} row
            {teams.length === 1 ? '' : 's'}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onPreviousPage}
              disabled={!canGoPrevious || isLoading}
              className="rounded-md border border-border bg-bg px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
            >
              Previous
            </button>
            <button
              type="button"
              onClick={onNextPage}
              disabled={!canGoNext || isLoading}
              className="rounded-md border border-border bg-bg px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-border-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-60"
            >
              Next
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
