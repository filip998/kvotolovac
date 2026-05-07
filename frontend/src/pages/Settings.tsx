import { useState, type FormEvent, type ReactNode } from 'react';
import {
  useCreateTelegramProfile,
  useDeleteTelegramProfile,
  useScrapeSettings,
  useSystemStatus,
  useTelegramSettings,
  useTestTelegramProfile,
  useTriggerScrape,
  useUpdateTelegramProfile,
  useUpdateScrapeSettings,
} from '../api/hooks';
import type {
  ScrapeRuntimeSettings,
  ScrapeSettingsMarketOption,
  ScraperDetailMode,
  ScrapeSettingsOptions,
  TelegramNotificationProfile,
  TelegramNotificationProfileInput,
} from '../api/types';
import PageShell from '../components/PageShell';

type NumericSetting =
  | 'scrape_interval_minutes'
  | 'scrape_lookahead_hours'
  | 'max_middle_opportunities_per_market'
  | 'rate_limit_per_second'
  | 'meridian_rate_limit_per_second'
  | 'notification_gap_threshold';

const detailModeLabels: Record<ScraperDetailMode, string> = {
  partial: 'Partial',
  full: 'Full',
};

const ALL_MARKETS_TOKEN = 'all';

const EMPTY_TELEGRAM_PROFILE: TelegramNotificationProfileInput = {
  label: '',
  chat_id: '',
  enabled: true,
  min_gap: 2,
  min_roi_percent: 0,
  min_middle_ev_percent: 0,
  bookmaker_ids: [],
};

function toggleValue(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function settingsKey(values: ScrapeRuntimeSettings): string {
  return JSON.stringify(values);
}

function telegramProfileKey(values: TelegramNotificationProfileInput): string {
  return JSON.stringify({
    ...values,
    bookmaker_ids: [...values.bookmaker_ids].sort(),
  });
}

function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase());
}

function summarizeBookmakers(
  enabledIds: string[],
  options: ScrapeSettingsOptions
): string {
  const total = options.bookmakers.length;
  if (enabledIds.length === 0) {
    return 'None selected';
  }
  if (enabledIds.length === total) {
    return `All ${total}`;
  }

  const disabled = options.bookmakers.filter((bookmaker) => !enabledIds.includes(bookmaker.id));
  if (disabled.length > 0 && disabled.length <= 2) {
    return `${enabledIds.length} of ${total} · excluding ${disabled
      .map((bookmaker) => bookmaker.name)
      .join(', ')}`;
  }

  return `${enabledIds.length} of ${total} enabled`;
}

function summarizeList(values: string[], emptyLabel: string): string {
  if (values.length === 0) {
    return emptyLabel;
  }
  return values.map(titleCase).join(', ');
}

function normalizeAnalysisMarkets(values: string[]): string[] {
  const cleaned = values.map((value) => value.trim()).filter(Boolean);
  if (cleaned.length === 0 || cleaned.includes(ALL_MARKETS_TOKEN)) {
    return [ALL_MARKETS_TOKEN];
  }
  return Array.from(new Set(cleaned));
}

function toggleAnalysisMarket(values: string[], token: string): string[] {
  if (token === ALL_MARKETS_TOKEN) {
    return [ALL_MARKETS_TOKEN];
  }

  const selected = normalizeAnalysisMarkets(values).filter(
    (value) => value !== ALL_MARKETS_TOKEN
  );
  const next = selected.includes(token)
    ? selected.filter((value) => value !== token)
    : [...selected, token];
  return next.length > 0 ? next : [ALL_MARKETS_TOKEN];
}

function marketLabel(
  token: string,
  options: ScrapeSettingsMarketOption[]
): string {
  const option = options.find((item) => item.token === token);
  if (option) {
    return option.label;
  }
  if (token === ALL_MARKETS_TOKEN) {
    return 'All supported markets';
  }
  return titleCase(token.replace(':', ' · '));
}

function summarizeAnalysisMarkets(
  values: string[],
  options: ScrapeSettingsMarketOption[]
): string {
  const selected = normalizeAnalysisMarkets(values);
  if (selected.includes(ALL_MARKETS_TOKEN)) {
    return 'All supported markets';
  }
  if (selected.length <= 2) {
    return selected.map((token) => marketLabel(token, options)).join(', ');
  }
  return `${selected.length} market filters selected`;
}

function customAnalysisMarkets(
  values: string[],
  options: ScrapeSettingsMarketOption[]
): string[] {
  const known = new Set([
    ALL_MARKETS_TOKEN,
    ...options.map((option) => option.token),
  ]);
  return normalizeAnalysisMarkets(values).filter((token) => !known.has(token));
}

function SectionTitle({
  children,
  description,
}: {
  children: string;
  description?: string;
}) {
  return (
    <div className="space-y-1">
      <h3 className="font-display text-lg font-semibold text-text">{children}</h3>
      {description && <p className="text-sm text-text-secondary">{description}</p>}
    </div>
  );
}

function DisclosureRow({
  title,
  summary,
  children,
  defaultOpen = false,
}: {
  title: string;
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details
      open={defaultOpen}
      className="group rounded-2xl border border-border bg-surface/80 transition hover:border-border-hover"
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 [&::-webkit-details-marker]:hidden">
        <div>
          <div className="text-sm font-semibold text-text">{title}</div>
          <div className="mt-0.5 text-sm text-text-secondary">{summary}</div>
        </div>
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-raised text-text-muted transition group-open:rotate-90 group-hover:text-text">
          ›
        </span>
      </summary>
      <div className="border-t border-border px-4 pb-4 pt-3">{children}</div>
    </details>
  );
}

function ChoiceChip({
  selected,
  children,
  onClick,
}: {
  selected: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`rounded-full border px-3 py-1.5 text-sm font-medium transition ${
        selected
          ? 'border-accent bg-accent text-bg'
          : 'border-border bg-bg text-text-secondary hover:border-border-hover hover:text-text'
      }`}
    >
      {children}
    </button>
  );
}

function SettingRow({
  label,
  description,
  children,
  as = 'label',
  onClick,
}: {
  label: string;
  description?: string;
  children: ReactNode;
  as?: 'label' | 'div';
  onClick?: () => void;
}) {
  const Component = as;

  return (
    <Component
      onClick={onClick}
      className={`flex flex-col gap-3 rounded-2xl border border-border bg-surface/80 px-4 py-3 sm:flex-row sm:items-center sm:justify-between ${
        onClick ? 'cursor-pointer transition hover:border-border-hover' : ''
      }`}
    >
      <span>
        <span className="block text-sm font-semibold text-text">{label}</span>
        {description && (
          <span className="mt-0.5 block text-sm text-text-secondary">{description}</span>
        )}
      </span>
      {children}
    </Component>
  );
}

function NumberControl({
  value,
  min,
  max,
  step = 1,
  unit,
  onChange,
}: {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  unit: string;
  onChange: (value: string) => void;
}) {
  return (
    <span className="flex w-full items-center rounded-xl border border-border bg-bg px-3 py-2 text-sm text-text transition focus-within:border-accent sm:w-40">
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-w-0 flex-1 bg-transparent text-right font-semibold outline-none"
      />
      <span className="ml-2 text-xs text-text-muted">{unit}</span>
    </span>
  );
}

function TextControl({
  value,
  placeholder,
  onChange,
}: {
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className="w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm font-semibold text-text outline-none transition placeholder:text-text-muted focus:border-accent sm:w-64"
    />
  );
}

function SelectControl<TValue extends string>({
  value,
  options,
  getLabel,
  onChange,
}: {
  value: TValue;
  options: TValue[];
  getLabel: (value: TValue) => string;
  onChange: (value: TValue) => void;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value as TValue)}
      className="w-full rounded-xl border border-border bg-bg px-3 py-2 text-sm font-semibold text-text outline-none transition focus:border-accent sm:w-48"
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {getLabel(option)}
        </option>
      ))}
    </select>
  );
}

function SwitchControl({
  checked,
  ariaLabel,
  onChange,
}: {
  checked: boolean;
  ariaLabel: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-label={ariaLabel}
      aria-checked={checked}
      onClick={(event) => {
        event.stopPropagation();
        onChange(!checked);
      }}
      className={`flex h-7 w-12 items-center rounded-full p-1 transition ${
        checked ? 'bg-accent' : 'bg-surface-raised'
      }`}
    >
      <span
        className={`h-5 w-5 rounded-full bg-bg shadow-sm transition ${
          checked ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  );
}

function profileToInput(profile: TelegramNotificationProfile): TelegramNotificationProfileInput {
  return {
    label: profile.label,
    chat_id: profile.chat_id,
    enabled: profile.enabled,
    min_gap: profile.min_gap,
    min_roi_percent: profile.min_roi_percent,
    min_middle_ev_percent: profile.min_middle_ev_percent,
    bookmaker_ids: profile.bookmaker_ids,
  };
}

function summarizeTelegramBookmakers(
  bookmakerIds: string[],
  options: ScrapeSettingsOptions
): string {
  return bookmakerIds.length === 0 ? 'All bookmakers' : summarizeBookmakers(bookmakerIds, options);
}

function telegramProfileStatus(profile: TelegramNotificationProfile): string | null {
  if (profile.rate_limited_until) {
    const until = new Date(profile.rate_limited_until);
    if (!Number.isNaN(until.getTime()) && until.getTime() > Date.now()) {
      return `Rate-limited until ${until.toLocaleString()}`;
    }
  }
  if (profile.last_delivery_error) {
    return `Last delivery error: ${profile.last_delivery_error}`;
  }
  return null;
}

function TelegramBookmakerSelector({
  selectedIds,
  options,
  onChange,
}: {
  selectedIds: string[];
  options: ScrapeSettingsOptions;
  onChange: (ids: string[]) => void;
}) {
  return (
    <div className="space-y-3 rounded-2xl border border-border bg-surface/80 px-4 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-semibold text-text">Bookmakers</div>
          <div className="mt-0.5 text-sm text-text-secondary">
            {summarizeTelegramBookmakers(selectedIds, options)}
          </div>
        </div>
        <div className="flex flex-wrap gap-3 text-sm">
          <button
            type="button"
            onClick={() => onChange([])}
            className="font-medium text-accent hover:text-accent-dim"
          >
            All
          </button>
          <button
            type="button"
            onClick={() => onChange(options.bookmakers.map((bookmaker) => bookmaker.id))}
            className="font-medium text-text-secondary hover:text-text"
          >
            Mirror scrape
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {options.bookmakers.map((bookmaker) => (
          <ChoiceChip
            key={bookmaker.id}
            selected={selectedIds.includes(bookmaker.id)}
            onClick={() => onChange(toggleValue(selectedIds, bookmaker.id))}
          >
            {bookmaker.name}
          </ChoiceChip>
        ))}
      </div>
    </div>
  );
}

function TelegramProfileEditor({
  profile,
  options,
}: {
  profile: TelegramNotificationProfile;
  options: ScrapeSettingsOptions;
}) {
  const [draft, setDraft] = useState<TelegramNotificationProfileInput>(() => profileToInput(profile));
  const [message, setMessage] = useState<string | null>(null);
  const updateProfile = useUpdateTelegramProfile();
  const deleteProfile = useDeleteTelegramProfile();
  const testProfile = useTestTelegramProfile();
  const hasChanges = telegramProfileKey(draft) !== telegramProfileKey(profileToInput(profile));
  const status = telegramProfileStatus(profile);

  const save = () => {
    setMessage(null);
    updateProfile.mutate(
      { profileId: profile.id, payload: draft },
      {
        onSuccess: () => setMessage('Saved.'),
        onError: (error) => setMessage(`Failed to save: ${error.message}`),
      }
    );
  };

  const sendTest = () => {
    setMessage(null);
    testProfile.mutate(
      { profileId: profile.id },
      {
        onSuccess: () => setMessage('Test sent.'),
        onError: (error) => setMessage(`Test failed: ${error.message}`),
      }
    );
  };

  const remove = () => {
    if (!window.confirm(`Delete Telegram profile "${profile.label}"?`)) {
      return;
    }
    deleteProfile.mutate(
      { profileId: profile.id },
      {
        onError: (error) => setMessage(`Delete failed: ${error.message}`),
      }
    );
  };

  return (
    <DisclosureRow
      title={profile.label}
      summary={`${draft.enabled ? 'Enabled' : 'Paused'} · ${summarizeTelegramBookmakers(
        draft.bookmaker_ids,
        options
      )}`}
    >
      <div className="space-y-3">
        {status && (
          <div className="rounded-lg border border-warning/60 bg-warning/10 px-4 py-3 text-sm text-warning">
            {status}
          </div>
        )}

        <SettingRow label="Enabled" description="Pause this chat without deleting its thresholds." as="div">
          <SwitchControl
            checked={draft.enabled}
            ariaLabel={`Enable ${profile.label}`}
            onChange={(checked) =>
              setDraft((current) => ({
                ...current,
                enabled: checked,
              }))
            }
          />
        </SettingRow>

        <div className="grid gap-2 lg:grid-cols-2">
          <SettingRow label="Label">
            <TextControl
              value={draft.label}
              onChange={(value) =>
                setDraft((current) => ({
                  ...current,
                  label: value,
                }))
              }
            />
          </SettingRow>

          <SettingRow label="Chat ID">
            <TextControl
              value={draft.chat_id}
              onChange={(value) =>
                setDraft((current) => ({
                  ...current,
                  chat_id: value,
                }))
              }
            />
          </SettingRow>

          <SettingRow
            label="Fallback min gap"
            description="Used only when fitted middle EV is unavailable."
          >
            <NumberControl
              value={draft.min_gap}
              min={0}
              step={0.1}
              unit="pts"
              onChange={(value) =>
                setDraft((current) => ({
                  ...current,
                  min_gap: Number(value),
                }))
              }
            />
          </SettingRow>

          <SettingRow
            label="Min fitted middle EV"
            description="Expected ROI threshold for model-fitted middles."
          >
            <NumberControl
              value={draft.min_middle_ev_percent}
              min={0}
              step={0.1}
              unit="%"
              onChange={(value) =>
                setDraft((current) => ({
                  ...current,
                  min_middle_ev_percent: Number(value),
                }))
              }
            />
          </SettingRow>

          <SettingRow
            label="Min fallback payout"
            description="Used for fallback middles and non-middle ROI filters."
          >
            <NumberControl
              value={draft.min_roi_percent}
              min={0}
              step={0.1}
              unit="%"
              onChange={(value) =>
                setDraft((current) => ({
                  ...current,
                  min_roi_percent: Number(value),
                }))
              }
            />
          </SettingRow>
        </div>

        <TelegramBookmakerSelector
          selectedIds={draft.bookmaker_ids}
          options={options}
          onChange={(bookmakerIds) =>
            setDraft((current) => ({
              ...current,
              bookmaker_ids: bookmakerIds,
            }))
          }
        />

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-text-secondary" aria-live="polite">
            {message ?? (hasChanges ? 'Unsaved Telegram profile changes.' : 'Profile is current.')}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={sendTest}
              disabled={testProfile.isPending}
              className="rounded-full border border-border px-4 py-2 text-sm font-semibold text-text transition hover:border-border-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {testProfile.isPending ? 'Sending...' : 'Send test'}
            </button>
            <button
              type="button"
              onClick={remove}
              disabled={deleteProfile.isPending}
              className="rounded-full border border-danger/40 px-4 py-2 text-sm font-semibold text-danger transition hover:border-danger disabled:cursor-not-allowed disabled:opacity-50"
            >
              Delete
            </button>
            <button
              type="button"
              onClick={save}
              disabled={!hasChanges || updateProfile.isPending}
              className="rounded-full bg-accent px-5 py-2 text-sm font-semibold text-bg transition hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-50"
            >
              {updateProfile.isPending ? 'Saving...' : 'Save profile'}
            </button>
          </div>
        </div>
      </div>
    </DisclosureRow>
  );
}

function TelegramSection({ options }: { options: ScrapeSettingsOptions }) {
  const { data, isLoading, isError, error } = useTelegramSettings();
  const createProfile = useCreateTelegramProfile();
  const [draft, setDraft] = useState<TelegramNotificationProfileInput>(
    () => EMPTY_TELEGRAM_PROFILE
  );
  const [message, setMessage] = useState<string | null>(null);
  const canCreate = draft.label.trim().length > 0 && draft.chat_id.trim().length > 0;

  const create = () => {
    setMessage(null);
    createProfile.mutate(draft, {
      onSuccess: () => {
        setDraft(EMPTY_TELEGRAM_PROFILE);
        setMessage('Profile created.');
      },
      onError: (error) => setMessage(`Create failed: ${error.message}`),
    });
  };

  return (
    <section className="space-y-3">
      <SectionTitle description="Each Telegram chat can keep its own bookmaker and threshold filters.">
        Telegram
      </SectionTitle>

      <div className="rounded-3xl border border-border bg-surface/70 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span
              className={`inline-flex items-center rounded-full border px-3 py-1 font-medium ${
                data?.token_configured
                  ? 'border-accent text-accent'
                  : 'border-warning text-warning'
              }`}
            >
              {data?.token_configured ? 'Bot token configured' : 'Bot token missing'}
            </span>
            {data?.api_base_url && (
              <span className="rounded-full border border-border px-3 py-1 text-text-secondary">
                {data.api_base_url}
              </span>
            )}
          </div>
          <span className="text-sm text-text-secondary">
            {data?.profiles.length ?? 0} profiles
          </span>
        </div>
      </div>

      {isLoading && <div className="h-20 animate-pulse rounded-2xl border border-border bg-surface" />}
      {isError && (
        <div className="rounded-lg border border-danger bg-surface p-4 text-sm text-danger">
          Failed to load Telegram settings: {(error as Error)?.message ?? 'Unknown error'}
        </div>
      )}

      {data && (
        <div className="space-y-3">
          {data.profiles.map((profile) => (
            <TelegramProfileEditor key={profile.id} profile={profile} options={options} />
          ))}

          <div className="space-y-3 rounded-3xl border border-border bg-surface/70 p-4">
            <div>
              <h3 className="font-display text-lg font-semibold text-text">New Profile</h3>
              <p className="mt-1 text-sm text-text-secondary">
                Empty bookmaker filters allow every bookmaker in a qualifying opportunity.
              </p>
            </div>

            <div className="grid gap-2 lg:grid-cols-2">
              <SettingRow label="Label">
                <TextControl
                  value={draft.label}
                  placeholder="VIP group"
                  onChange={(value) =>
                    setDraft((current) => ({
                      ...current,
                      label: value,
                    }))
                  }
                />
              </SettingRow>

              <SettingRow label="Chat ID">
                <TextControl
                  value={draft.chat_id}
                  placeholder="123456789"
                  onChange={(value) =>
                    setDraft((current) => ({
                      ...current,
                      chat_id: value,
                    }))
                  }
                />
              </SettingRow>

              <SettingRow
                label="Fallback min gap"
                description="Used only when fitted middle EV is unavailable."
              >
                <NumberControl
                  value={draft.min_gap}
                  min={0}
                  step={0.1}
                  unit="pts"
                  onChange={(value) =>
                    setDraft((current) => ({
                      ...current,
                      min_gap: Number(value),
                    }))
                  }
                />
              </SettingRow>

              <SettingRow
                label="Min fitted middle EV"
                description="Expected ROI threshold for model-fitted middles."
              >
                <NumberControl
                  value={draft.min_middle_ev_percent}
                  min={0}
                  step={0.1}
                  unit="%"
                  onChange={(value) =>
                    setDraft((current) => ({
                      ...current,
                      min_middle_ev_percent: Number(value),
                    }))
                  }
                />
              </SettingRow>

              <SettingRow
                label="Min fallback payout"
                description="Used for fallback middles and non-middle ROI filters."
              >
                <NumberControl
                  value={draft.min_roi_percent}
                  min={0}
                  step={0.1}
                  unit="%"
                  onChange={(value) =>
                    setDraft((current) => ({
                      ...current,
                      min_roi_percent: Number(value),
                    }))
                  }
                />
              </SettingRow>
            </div>

            <TelegramBookmakerSelector
              selectedIds={draft.bookmaker_ids}
              options={options}
              onChange={(bookmakerIds) =>
                setDraft((current) => ({
                  ...current,
                  bookmaker_ids: bookmakerIds,
                }))
              }
            />

            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-text-secondary" aria-live="polite">
                {message ?? 'Create a profile to start sending matching opportunities.'}
              </div>
              <button
                type="button"
                onClick={create}
                disabled={!canCreate || createProfile.isPending}
                className="rounded-full bg-accent px-5 py-2 text-sm font-semibold text-bg transition hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-50"
              >
                {createProfile.isPending ? 'Creating...' : 'Create profile'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function SettingsForm({
  initialValues,
  defaultValues,
  options,
  hasPendingChanges,
  scanInProgress,
}: {
  initialValues: ScrapeRuntimeSettings;
  defaultValues: ScrapeRuntimeSettings;
  options: ScrapeSettingsOptions;
  hasPendingChanges: boolean;
  scanInProgress: boolean;
}) {
  const [draft, setDraft] = useState<ScrapeRuntimeSettings>(() => initialValues);
  const [message, setMessage] = useState<string | null>(null);
  const updateSettings = useUpdateScrapeSettings();
  const triggerScrape = useTriggerScrape();
  const hasLocalChanges = settingsKey(draft) !== settingsKey(initialValues);
  const draftMatchesDefaults = settingsKey(draft) === settingsKey(defaultValues);
  const selectedAnalysisMarkets = normalizeAnalysisMarkets(draft.analysis_markets);
  const customMarketTokens = customAnalysisMarkets(
    draft.analysis_markets,
    options.analysis_market_options
  );

  const setNumber = (field: NumericSetting, value: string) => {
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
        setMessage(result.applied_immediately ? 'Saved.' : 'Saved · applies on next cycle.');
      },
      onError: (error) => setMessage(`Failed to save: ${error.message}`),
    });
  };

  const resetToDefaults = () => {
    if (
      !window.confirm(
        'Reset the draft to backend defaults? This will not save until you click Save changes.'
      )
    ) {
      return;
    }
    setDraft(defaultValues);
    setMessage('Defaults loaded as a draft. Review and save when ready.');
  };

  return (
    <form onSubmit={submit} className="mx-auto max-w-4xl space-y-6">
      <div className="rounded-3xl border border-border bg-surface/70 p-4 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span
              className={`inline-flex items-center gap-2 rounded-full px-3 py-1 font-medium ${
                scanInProgress
                  ? 'border border-warning text-warning'
                  : 'border border-border text-text-secondary'
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  scanInProgress ? 'bg-warning' : 'bg-accent'
                }`}
              />
              {scanInProgress ? 'Cycle running' : 'Idle'}
            </span>
            <span className="rounded-full border border-border px-3 py-1 text-text-secondary">
              {hasPendingChanges ? 'Saved · applies next cycle' : 'Live settings'}
            </span>
          </div>

          <button
            type="button"
            onClick={() => triggerScrape.mutate()}
            disabled={triggerScrape.isPending || scanInProgress}
            className="rounded-full border border-border px-4 py-2 text-sm font-semibold text-text transition hover:border-border-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggerScrape.isPending ? 'Starting…' : 'Run scrape now'}
          </button>
        </div>
      </div>

      <section className="space-y-3">
        <SectionTitle description="Keep the common choices visible, tuck the long lists away.">
          Coverage
        </SectionTitle>

        <DisclosureRow
          title="Bookmakers"
          summary={summarizeBookmakers(draft.enabled_bookmakers, options)}
        >
          <div className="mb-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() =>
                setDraft((current) => ({
                  ...current,
                  enabled_bookmakers: options.bookmakers.map((bookmaker) => bookmaker.id),
                }))
              }
              className="text-sm font-medium text-accent hover:text-accent-dim"
            >
              Select all
            </button>
            <span className="text-text-muted">·</span>
            <button
              type="button"
              onClick={() =>
                setDraft((current) => ({
                  ...current,
                  enabled_bookmakers: [],
                }))
              }
              className="text-sm font-medium text-text-secondary hover:text-text"
            >
              Clear
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {options.bookmakers.map((bookmaker) => (
              <ChoiceChip
                key={bookmaker.id}
                selected={draft.enabled_bookmakers.includes(bookmaker.id)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    enabled_bookmakers: toggleValue(current.enabled_bookmakers, bookmaker.id),
                  }))
                }
              >
                {bookmaker.name}
              </ChoiceChip>
            ))}
          </div>
        </DisclosureRow>

        <DisclosureRow
          title="Sports"
          summary={summarizeList(draft.enabled_sports, 'No sports selected')}
        >
          <div className="flex flex-wrap gap-2">
            {options.sports.map((sport) => (
              <ChoiceChip
                key={sport}
                selected={draft.enabled_sports.includes(sport)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    enabled_sports: toggleValue(current.enabled_sports, sport),
                  }))
                }
              >
                {titleCase(sport)}
              </ChoiceChip>
            ))}
          </div>
        </DisclosureRow>

        <DisclosureRow
          title="Markets"
          summary={summarizeAnalysisMarkets(
            draft.analysis_markets,
            options.analysis_market_options
          )}
        >
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <ChoiceChip
                selected={selectedAnalysisMarkets.includes(ALL_MARKETS_TOKEN)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    analysis_markets: [ALL_MARKETS_TOKEN],
                    scrape_market_scope: 'all',
                  }))
                }
              >
                All supported markets
              </ChoiceChip>
            </div>

            {options.sports.map((sport) => {
              const sportOptions = options.analysis_market_options.filter(
                (option) => option.sport === sport
              );
              if (sportOptions.length === 0) {
                return null;
              }
              return (
                <div key={sport} className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                    {titleCase(sport)}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {sportOptions.map((option) => (
                      <ChoiceChip
                        key={option.token}
                        selected={selectedAnalysisMarkets.includes(option.token)}
                        onClick={() =>
                          setDraft((current) => ({
                            ...current,
                            analysis_markets: toggleAnalysisMarket(
                              current.analysis_markets,
                              option.token
                            ),
                            scrape_market_scope: 'all',
                          }))
                        }
                      >
                        {option.label}
                      </ChoiceChip>
                    ))}
                  </div>
                </div>
              );
            })}

            {customMarketTokens.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Custom
                </div>
                <div className="flex flex-wrap gap-2">
                  {customMarketTokens.map((token) => (
                    <ChoiceChip
                      key={token}
                      selected
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          analysis_markets: toggleAnalysisMarket(
                            current.analysis_markets,
                            token
                          ),
                          scrape_market_scope: 'all',
                        }))
                      }
                    >
                      {marketLabel(token, options.analysis_market_options)}
                    </ChoiceChip>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DisclosureRow>
      </section>

      <section className="space-y-3">
        <SectionTitle description="The three numbers that shape every cycle.">
          Cadence
        </SectionTitle>

        <div className="space-y-2">
          <SettingRow label="Refresh every" description="How often automatic cycles should run.">
            <NumberControl
              value={draft.scrape_interval_minutes}
              min={options.scrape_interval_minutes_min}
              max={options.scrape_interval_minutes_max}
              unit="min"
              onChange={(value) => setNumber('scrape_interval_minutes', value)}
            />
          </SettingRow>

          <SettingRow label="Lookahead" description="Ignore events that start later than this.">
            <NumberControl
              value={draft.scrape_lookahead_hours}
              min={options.scrape_lookahead_hours_min}
              max={options.scrape_lookahead_hours_max}
              unit="hours"
              onChange={(value) => setNumber('scrape_lookahead_hours', value)}
            />
          </SettingRow>

          <SettingRow
            label="Middles shown per market"
            description="Caps noisy middle opportunities without hiding arbitrage."
          >
            <NumberControl
              value={draft.max_middle_opportunities_per_market}
              min={options.max_middle_opportunities_per_market_min}
              max={options.max_middle_opportunities_per_market_max}
              unit="rows"
              onChange={(value) => setNumber('max_middle_opportunities_per_market', value)}
            />
          </SettingRow>
        </div>
      </section>

      <section className="space-y-3">
        <details className="group rounded-3xl border border-border bg-surface/70">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 [&::-webkit-details-marker]:hidden">
            <div>
              <h3 className="font-display text-lg font-semibold text-text">Advanced</h3>
              <p className="mt-1 text-sm text-text-secondary">
                Rate limits, detail modes, and notification behavior.
              </p>
            </div>
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface-raised text-text-muted transition group-open:rotate-90 group-hover:text-text">
              ›
            </span>
          </summary>

          <div className="space-y-4 border-t border-border px-5 pb-5 pt-4">
            <div className="grid gap-2 lg:grid-cols-2">
              <SettingRow label="General rate limit" description="Default request pace.">
                <NumberControl
                  value={draft.rate_limit_per_second}
                  min={options.rate_limit_per_second_min}
                  max={options.rate_limit_per_second_max}
                  step={0.1}
                  unit="/ sec"
                  onChange={(value) => setNumber('rate_limit_per_second', value)}
                />
              </SettingRow>

              <SettingRow label="Meridian rate limit" description="Separate pacing for Meridian.">
                <NumberControl
                  value={draft.meridian_rate_limit_per_second}
                  min={options.rate_limit_per_second_min}
                  max={options.rate_limit_per_second_max}
                  step={0.1}
                  unit="/ sec"
                  onChange={(value) => setNumber('meridian_rate_limit_per_second', value)}
                />
              </SettingRow>

              <SettingRow label="Detail mode · SoccerBet" description="Full mode fetches more detail.">
                <SelectControl
                  value={draft.soccerbet_detail_mode}
                  options={options.detail_modes}
                  getLabel={(mode) => detailModeLabels[mode]}
                  onChange={(mode) =>
                    setDraft((current) => ({
                      ...current,
                      soccerbet_detail_mode: mode,
                    }))
                  }
                />
              </SettingRow>

              <SettingRow label="Detail mode · MerkurXTip" description="Full mode fetches more detail.">
                <SelectControl
                  value={draft.merkurxtip_detail_mode}
                  options={options.detail_modes}
                  getLabel={(mode) => detailModeLabels[mode]}
                  onChange={(mode) =>
                    setDraft((current) => ({
                      ...current,
                      merkurxtip_detail_mode: mode,
                    }))
                  }
                />
              </SettingRow>

              <SettingRow
                label="Detail mode · PinnBet"
                description="Full mode also fetches per-event football detail to add double chance."
              >
                <SelectControl
                  value={draft.pinnbet_detail_mode}
                  options={options.detail_modes}
                  getLabel={(mode) => detailModeLabels[mode]}
                  onChange={(mode) =>
                    setDraft((current) => ({
                      ...current,
                      pinnbet_detail_mode: mode,
                    }))
                  }
                />
              </SettingRow>

              <SettingRow
                label="Detail mode · BetOle"
                description="Full mode also fetches per-event football detail to add double chance."
              >
                <SelectControl
                  value={draft.betole_detail_mode}
                  options={options.detail_modes}
                  getLabel={(mode) => detailModeLabels[mode]}
                  onChange={(mode) =>
                    setDraft((current) => ({
                      ...current,
                      betole_detail_mode: mode,
                    }))
                  }
                />
              </SettingRow>

              <SettingRow label="Min gap to notify" description="Smaller values can create more pings.">
                <NumberControl
                  value={draft.notification_gap_threshold}
                  min={0}
                  step={0.1}
                  unit="pts"
                  onChange={(value) => setNumber('notification_gap_threshold', value)}
                />
              </SettingRow>

              <SettingRow
                label="Keep notifications across reloads"
                description="Persist in-app notification history."
                as="div"
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    persist_inapp_notifications: !current.persist_inapp_notifications,
                  }))
                }
              >
                <SwitchControl
                  checked={draft.persist_inapp_notifications}
                  ariaLabel="Keep notifications across reloads"
                  onChange={(checked) =>
                    setDraft((current) => ({
                      ...current,
                      persist_inapp_notifications: checked,
                    }))
                  }
                />
              </SettingRow>
            </div>
          </div>
        </details>
      </section>

      <TelegramSection options={options} />

      {(hasLocalChanges || message || !draftMatchesDefaults) && (
        <div className="sticky bottom-4 z-20 rounded-3xl border border-border bg-bg/90 p-3 shadow-xl shadow-black/10 backdrop-blur">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-text-secondary" aria-live="polite">
              {message ??
                (hasLocalChanges
                  ? 'You have unsaved settings changes.'
                  : 'Current settings differ from backend defaults.')}
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={resetToDefaults}
                disabled={draftMatchesDefaults || updateSettings.isPending}
                className="rounded-full border border-border px-4 py-2 text-sm font-semibold text-text transition hover:border-border-hover disabled:cursor-not-allowed disabled:opacity-50"
              >
                Reset to defaults
              </button>
              <button
                type="submit"
                disabled={!hasLocalChanges || updateSettings.isPending}
                className="rounded-full bg-accent px-5 py-2 text-sm font-semibold text-bg transition hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-50"
              >
                {updateSettings.isPending
                  ? 'Saving…'
                  : scanInProgress
                    ? 'Queue changes'
                    : 'Save changes'}
              </button>
            </div>
          </div>
        </div>
      )}
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
      title="Settings"
      description="Tune coverage, cadence, and advanced scrape knobs without editing backend environment files."
    >
      {isLoading && (
        <div className="mx-auto max-w-4xl space-y-3">
          {[0, 1, 2].map((item) => (
            <div
              key={item}
              className="h-16 animate-pulse rounded-2xl border border-border bg-surface"
            />
          ))}
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
          defaultValues={data.defaults}
          options={data.options}
          hasPendingChanges={data.has_pending_changes}
          scanInProgress={scanInProgress}
        />
      )}
    </PageShell>
  );
}
