type SearchableValue = string | null | undefined;

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

export function filterItemsBySearch<T>(
  items: T[],
  query: string,
  getSearchFields: (item: T) => SearchableValue[]
): T[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) {
    return items;
  }

  return items.filter((item) =>
    getSearchFields(item).some((field) => normalizeSearchText(field).includes(normalizedQuery))
  );
}
