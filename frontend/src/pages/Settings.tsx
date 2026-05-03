import { useState, type FormEvent } from 'react';
import {
  useScrapeSettings,
  useSystemStatus,
  useTriggerScrape,
  useUpdateScrapeSettings,
} from '../api/hooks';
import type {
  ScrapeMarketScope,
  ScrapeRuntimeSettings,
  ScraperDetailMode,
  ScrapeSettingsOptions,
} from '../api/types';
import PageShell from '../components/PageShell';

function toggleValue(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function settingsKey(values: ScrapeRuntimeSettings): string {
  return JSON.stringify(values);
}

function FieldLabel({ children }: { children: string }) {
  return (
    <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-muted">
      {children}
    </span>
  );
}

function SettingsForm({
  initialValues,
  options,
  hasPendingChanges,
  scanInProgress,
}: {
  initialValues: ScrapeRuntimeSettings;
  options: ScrapeSettingsOptions;
  hasPendingChanges: boolean;
  scanInProgress: boolean;
}) {
  const [draft, setDraft] = useState<ScrapeRuntimeSettings>(() => initialValues);
  const [message, setMessage] = useState<string | null>(null);
  const updateSettings = useUpdateScrapeSettings();
  const triggerScrape = useTriggerScrape();

  const setNumber = (field: keyof ScrapeRuntimeSettings, value: string) => {
    setDraft((current) => ({
      ...current,
      [field]: Number(value),
    }));
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setMessage(null);
    updateSettings.mutate(draft, {
      onSuccess: (result) => {
        setMessage(
          result.applied_immediately
            ? 'Settings saved and applied immediately.'
            : 'Settings saved as pending. They will apply before the next scrape cycle.'
        );
      },
      onError: (error) => setMessage(`Failed to save settings: ${error.message}`),
    });
  };

  return (
    <form onSubmit={submit} className="space-y-6">
      <section className="rounded-lg border border-border bg-surface p-5">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-text">Scrape controls</h3>
            <p className="mt-1 text-sm text-text-secondary">
              Choose which sources and markets participate in future scrape cycles.
            </p>
          </div>
          {hasPendingChanges && (
            <span className="rounded-full border border-warning px-3 py-1 text-xs font-medium text-warning">
              Pending next cycle
            </span>
          )}
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <div className="space-y-3">
            <FieldLabel>Bookmakers</FieldLabel>
            <div className="grid gap-2 sm:grid-cols-2">
              {options.bookmakers.map((bookmaker) => (
                <label
                  key={bookmaker.id}
                  className="flex items-center gap-2 rounded-md bg-surface-raised px-3 py-2 text-sm text-text"
                >
                  <input
                    type="checkbox"
                    checked={draft.enabled_bookmakers.includes(bookmaker.id)}
                    onChange={() =>
                      setDraft((current) => ({
                        ...current,
                        enabled_bookmakers: toggleValue(current.enabled_bookmakers, bookmaker.id),
                      }))
                    }
                    className="accent-accent"
                  />
                  <span>{bookmaker.name}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-4">
            <div className="space-y-3">
              <FieldLabel>Sports</FieldLabel>
              <div className="flex flex-wrap gap-2">
                {options.sports.map((sport) => (
                  <button
                    key={sport}
                    type="button"
                    onClick={() =>
                      setDraft((current) => ({
                        ...current,
                        enabled_sports: toggleValue(current.enabled_sports, sport),
                      }))
                    }
                    className={`rounded-md px-3 py-2 text-sm font-medium transition ${
                      draft.enabled_sports.includes(sport)
                        ? 'bg-accent text-bg'
                        : 'bg-surface-raised text-text-secondary hover:text-text'
                    }`}
                  >
                    {sport}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="space-y-2">
                <FieldLabel>Market scope</FieldLabel>
                <select
                  value={draft.scrape_market_scope}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      scrape_market_scope: event.target.value as ScrapeMarketScope,
                    }))
                  }
                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
                >
                  {options.market_scopes.map((scope) => (
                    <option key={scope} value={scope}>
                      {scope === 'all' ? 'All supported markets' : 'Player props only'}
                    </option>
                  ))}
                </select>
              </label>

              <label className="space-y-2">
                <FieldLabel>Refresh interval</FieldLabel>
                <input
                  type="number"
                  min={options.scrape_interval_minutes_min}
                  max={options.scrape_interval_minutes_max}
                  value={draft.scrape_interval_minutes}
                  onChange={(event) => setNumber('scrape_interval_minutes', event.target.value)}
                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
                />
              </label>

              <label className="space-y-2">
                <FieldLabel>Lookahead hours</FieldLabel>
                <input
                  type="number"
                  min={options.scrape_lookahead_hours_min}
                  max={options.scrape_lookahead_hours_max}
                  value={draft.scrape_lookahead_hours}
                  onChange={(event) => setNumber('scrape_lookahead_hours', event.target.value)}
                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
                />
              </label>

              <label className="space-y-2">
                <FieldLabel>Max middle rows</FieldLabel>
                <input
                  type="number"
                  min={options.max_middle_opportunities_per_market_min}
                  max={options.max_middle_opportunities_per_market_max}
                  value={draft.max_middle_opportunities_per_market}
                  onChange={(event) =>
                    setNumber('max_middle_opportunities_per_market', event.target.value)
                  }
                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
                />
              </label>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-surface p-5">
        <h3 className="text-sm font-semibold text-text">Advanced</h3>
        <p className="mt-1 text-sm text-text-secondary">
          These apply at scrape-cycle boundaries. Secret-bearing settings stay out of the UI.
        </p>

        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <label className="space-y-2">
            <FieldLabel>General rate limit / sec</FieldLabel>
            <input
              type="number"
              min={options.rate_limit_per_second_min}
              max={options.rate_limit_per_second_max}
              step="0.1"
              value={draft.rate_limit_per_second}
              onChange={(event) => setNumber('rate_limit_per_second', event.target.value)}
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
            />
          </label>

          <label className="space-y-2">
            <FieldLabel>Meridian rate limit / sec</FieldLabel>
            <input
              type="number"
              min={options.rate_limit_per_second_min}
              max={options.rate_limit_per_second_max}
              step="0.1"
              value={draft.meridian_rate_limit_per_second}
              onChange={(event) => setNumber('meridian_rate_limit_per_second', event.target.value)}
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
            />
          </label>

          <label className="space-y-2">
            <FieldLabel>Notification gap</FieldLabel>
            <input
              type="number"
              min="0"
              step="0.1"
              value={draft.notification_gap_threshold}
              onChange={(event) => setNumber('notification_gap_threshold', event.target.value)}
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
            />
          </label>

          <label className="space-y-2">
            <FieldLabel>SoccerBet detail mode</FieldLabel>
            <select
              value={draft.soccerbet_detail_mode}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  soccerbet_detail_mode: event.target.value as ScraperDetailMode,
                }))
              }
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
            >
              {options.detail_modes.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2">
            <FieldLabel>MerkurXTip detail mode</FieldLabel>
            <select
              value={draft.merkurxtip_detail_mode}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  merkurxtip_detail_mode: event.target.value as ScraperDetailMode,
                }))
              }
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text"
            >
              {options.detail_modes.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-3 rounded-md bg-surface-raised px-3 py-2 text-sm text-text">
            <input
              type="checkbox"
              checked={draft.persist_inapp_notifications}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  persist_inapp_notifications: event.target.checked,
                }))
              }
              className="accent-accent"
            />
            Persist in-app notifications
          </label>
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={updateSettings.isPending}
          className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg transition hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-50"
        >
          {updateSettings.isPending ? 'Saving…' : scanInProgress ? 'Save for next cycle' : 'Save settings'}
        </button>
        <button
          type="button"
          onClick={() => triggerScrape.mutate()}
          disabled={triggerScrape.isPending || scanInProgress}
          className="rounded-md border border-border px-4 py-2 text-sm font-semibold text-text transition hover:border-border-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {triggerScrape.isPending ? 'Starting…' : 'Run scrape now'}
        </button>
        {message && <span className="text-sm text-text-secondary">{message}</span>}
      </div>
    </form>
  );
}

export default function Settings() {
  const { data, isLoading, isError, error } = useScrapeSettings();
  const { data: status } = useSystemStatus();
  const scanInProgress = status?.scan?.in_progress ?? false;
  const activeValues = data?.pending ?? data?.applied;

  return (
    <PageShell
      eyebrow="Settings"
      title="Control what the next scrape cycle watches."
      description="Tune bookmaker coverage, sports, market scope, refresh cadence, and safe advanced scrape knobs without editing backend environment files."
    >
      {isLoading && (
        <div className="rounded-lg border border-border bg-surface p-5 text-sm text-text-secondary">
          Loading settings…
        </div>
      )}
      {isError && (
        <div className="rounded-lg border border-danger bg-surface p-5 text-sm text-danger">
          Failed to load settings: {(error as Error)?.message ?? 'Unknown error'}
        </div>
      )}
      {data && activeValues && (
        <SettingsForm
          key={settingsKey(activeValues)}
          initialValues={activeValues}
          options={data.options}
          hasPendingChanges={data.has_pending_changes}
          scanInProgress={scanInProgress}
        />
      )}
    </PageShell>
  );
}
