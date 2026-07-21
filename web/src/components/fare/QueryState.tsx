import type { ReactNode } from 'react'

import { ErrorState } from '@/components/ui/error-state'
import { Skeleton } from '@/components/ui/skeleton'

export function PageSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-4" aria-label="加载中" role="status">
      <Skeleton className="h-24 w-full" />
      {Array.from({ length: rows }, (_, index) => <Skeleton key={index} className="h-16 w-full" />)}
    </div>
  )
}

export function QueryState({
  loading,
  error,
  children,
  onRetry,
}: {
  loading: boolean
  error: unknown
  children: ReactNode
  onRetry?: () => void
}) {
  if (loading) return <PageSkeleton />
  if (error) return <ErrorState message={error instanceof Error ? error.message : '数据加载失败'} onRetry={onRetry} />
  return children
}
