interface EmptyStateProps {
  title?: string;
  message?: string;
}

export default function EmptyState({
  title = 'No results found',
  message = 'Try adjusting your filters to see more discrepancies.',
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line-700/70 bg-ink-900 px-6 py-16 text-center">
      <div className="mb-4 text-4xl opacity-50">🎯</div>
      <h3 className="text-xl font-semibold text-white">{title}</h3>
      <p className="mt-3 max-w-md text-sm leading-6 text-slate-400">{message}</p>
    </div>
  );
}
