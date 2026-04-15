import { useCallback, useSyncExternalStore } from 'react';

const STORAGE_KEY = 'kvotolovac-dashboard-stake-units';
const DEFAULT_DASHBOARD_STAKE_UNITS = 20;
const MIN_DASHBOARD_STAKE_UNITS = 0.1;

const listeners = new Set<() => void>();

function subscribe(callback: () => void) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export function normalizeDashboardStakeUnits(value: number) {
  if (!Number.isFinite(value)) {
    return DEFAULT_DASHBOARD_STAKE_UNITS;
  }

  return Math.max(
    MIN_DASHBOARD_STAKE_UNITS,
    Math.round((value + Number.EPSILON) * 10) / 10,
  );
}

export function formatDashboardStakeUnitsInput(value: number) {
  const normalized = normalizeDashboardStakeUnits(value);
  return Number.isInteger(normalized) ? normalized.toFixed(0) : normalized.toFixed(1);
}

function getSnapshot(): number {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) {
      const parsed = Number(stored);
      if (Number.isFinite(parsed) && parsed > 0) {
        return normalizeDashboardStakeUnits(parsed);
      }
    }
  } catch {
    // SSR or storage unavailable
  }

  return DEFAULT_DASHBOARD_STAKE_UNITS;
}

function getServerSnapshot(): number {
  return DEFAULT_DASHBOARD_STAKE_UNITS;
}

function setDashboardStakeUnits(value: number) {
  const normalized = normalizeDashboardStakeUnits(value);

  try {
    localStorage.setItem(STORAGE_KEY, String(normalized));
  } catch {
    // quota or private browsing
  }

  listeners.forEach((callback) => callback());
  return normalized;
}

export function useDashboardStakeUnits() {
  const units = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const updateUnits = useCallback((value: number) => setDashboardStakeUnits(value), []);

  return {
    units,
    updateUnits,
    minUnits: MIN_DASHBOARD_STAKE_UNITS,
  } as const;
}
