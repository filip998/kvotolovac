export default function LoadingSpinner({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center justify-center py-12 ${className}`}>
      <div className="relative">
        <div className="h-10 w-10 rounded-full border-2 border-line-700/80" />
        <div className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-white" />
      </div>
    </div>
  );
}
