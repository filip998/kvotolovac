export default function LoadingSpinner({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center justify-center py-12 ${className}`}>
      <div className="relative">
        <div className="h-10 w-10 rounded-full border-2 border-gray-700" />
        <div className="absolute top-0 h-10 w-10 animate-spin rounded-full border-2 border-transparent border-t-brand-400" />
      </div>
    </div>
  );
}
