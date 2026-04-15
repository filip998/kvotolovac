import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

const BOOKMAKER_FILTER_PARAM = 'books';

function normalizeBookmakerIds(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function parseBookmakerIds(rawValue: string | null) {
  if (!rawValue) {
    return [];
  }

  return normalizeBookmakerIds(rawValue.split(','));
}

export function useBookmakerFilter() {
  const [searchParams, setSearchParams] = useSearchParams();

  const selectedBookmakerIds = useMemo(
    () => parseBookmakerIds(searchParams.get(BOOKMAKER_FILTER_PARAM)),
    [searchParams]
  );

  const updateSelectedBookmakerIds = (nextBookmakerIds: string[]) => {
    const nextSearchParams = new URLSearchParams(searchParams);
    const normalizedIds = normalizeBookmakerIds(nextBookmakerIds);

    if (normalizedIds.length === 0) {
      nextSearchParams.delete(BOOKMAKER_FILTER_PARAM);
    } else {
      nextSearchParams.set(BOOKMAKER_FILTER_PARAM, normalizedIds.join(','));
    }

    setSearchParams(nextSearchParams, { replace: true });
  };

  return {
    selectedBookmakerIds,
    hasActiveBookmakerFilter: selectedBookmakerIds.length > 0,
    updateSelectedBookmakerIds,
    clearSelectedBookmakerIds: () => updateSelectedBookmakerIds([]),
    toggleBookmakerId: (bookmakerId: string) => {
      if (selectedBookmakerIds.includes(bookmakerId)) {
        updateSelectedBookmakerIds(
          selectedBookmakerIds.filter((selectedId) => selectedId !== bookmakerId)
        );
        return;
      }

      updateSelectedBookmakerIds([...selectedBookmakerIds, bookmakerId]);
    },
    search: searchParams.toString() ? `?${searchParams.toString()}` : '',
  };
}
