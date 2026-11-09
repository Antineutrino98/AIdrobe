/** 加载中骨架屏（M6 交互要求）。 */

export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-lg bg-slate-200 ${className}`}
      aria-hidden="true"
    />
  )
}

export function CardSkeleton() {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <Skeleton className="h-5 w-1/3" />
      <div className="mt-3 flex gap-2">
        <Skeleton className="h-20 w-20" />
        <Skeleton className="h-20 w-20" />
        <Skeleton className="h-20 w-20" />
      </div>
      <Skeleton className="mt-3 h-4 w-full" />
      <Skeleton className="mt-2 h-4 w-4/5" />
    </div>
  )
}
