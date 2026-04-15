export type SearchableValue = string | null | undefined;

export interface SearchIndexEntry<T> {
  item: T;
  normalizedSearchText: string;
}

export function normalizeSearchText(value: SearchableValue): string {
  if (!value) {
    return '';
  }

  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function joinSearchFields(fields: SearchableValue[]): string {
  return fields.filter((field): field is string => Boolean(field)).join(' ');
}

export function buildSearchIndex<T>(
  items: readonly T[],
  getSearchFields: (item: T) => SearchableValue[]
): SearchIndexEntry<T>[] {
  return items.map((item) => ({
    item,
    normalizedSearchText: normalizeSearchText(joinSearchFields(getSearchFields(item))),
  }));
}

export function filterSearchIndex<T>(
  searchIndex: readonly SearchIndexEntry<T>[],
  query: string
): T[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) {
    return searchIndex.map(({ item }) => item);
  }

  const matches: T[] = [];
  for (const entry of searchIndex) {
    if (entry.normalizedSearchText.includes(normalizedQuery)) {
      matches.push(entry.item);
    }
  }

  return matches;
}
