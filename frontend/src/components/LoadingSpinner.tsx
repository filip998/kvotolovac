export default function LoadingSpinner({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center justify-center py-12 ${className}`}>
      <div className="relative">
        <div className="h-8 w-8 rounded-full border-2 border-border" />
        <div className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-accent" />
      </div>
    </div>
  );
}
