interface SkeletonProps {
  className?: string;
  rows?: number;
}

export function Skeleton({ className = "h-4 w-full", rows = 1 }: SkeletonProps) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={`animate-pulse rounded-md bg-surface/60 ${className}`} />
      ))}
    </div>
  );
}
