interface EmptyStateProps {
  title?: string;
  message?: string;
}

export default function EmptyState({
  title = 'No results found',
  message = 'Try adjusting your filters to see more discrepancies.',
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border px-6 py-16 text-center">
      <h3 className="text-base font-semibold text-text">{title}</h3>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-text-secondary">{message}</p>
    </div>
  );
}
