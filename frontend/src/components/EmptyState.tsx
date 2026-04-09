interface EmptyStateProps {
  title?: string;
  message?: string;
}

export default function EmptyState({
  title = 'No results found',
  message = 'Try adjusting your filters to see more discrepancies.',
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 text-5xl opacity-40">🎯</div>
      <h3 className="mb-2 text-lg font-semibold text-gray-300">{title}</h3>
      <p className="max-w-sm text-sm text-gray-500">{message}</p>
    </div>
  );
}
