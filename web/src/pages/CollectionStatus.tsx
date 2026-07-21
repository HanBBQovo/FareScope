import { useInfiniteQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, RefreshCw, ShieldAlert } from 'lucide-react'

import { fareQueryKeys, getCollectionRuns, type CollectionRunStatus } from '@/api/fares'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageStat, PageStatStrip, PageSurface } from '@/components/layout/PageScaffold'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDateTime, formatPercent } from '@/lib/formatters'

const statusMeta: Record<CollectionRunStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; icon: typeof CheckCircle2 }> = {
  success: { label: '成功', variant: 'secondary', icon: CheckCircle2 },
  running: { label: '执行中', variant: 'default', icon: Clock3 },
  failed: { label: '失败', variant: 'destructive', icon: AlertTriangle },
  blocked: { label: '被拦截', variant: 'outline', icon: ShieldAlert },
}

export default function CollectionStatus() {
  const runs = useInfiniteQuery({
    queryKey: fareQueryKeys.collectionRuns(),
    queryFn: ({ pageParam }) => getCollectionRuns(typeof pageParam === 'string' ? pageParam : undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.nextCursor || undefined : undefined,
    refetchInterval: 30_000,
  })
  const runPages = useMemo(() => runs.data?.pages || [], [runs.data?.pages])
  const latestPage = runPages[0]
  const runItems = useMemo(() => runPages.flatMap((page) => page.items), [runPages])

  return (
    <PageShell
      title="采集状态"
      description="任务健康度、反爬拦截和解析失败都在这里保留证据，而不是静默丢弃。"
      width="7xl"
      actions={<Button type="button" variant="outline" onClick={() => runs.refetch()} disabled={runs.isFetching}><RefreshCw data-icon="inline-start" className={runs.isFetching ? 'animate-spin' : undefined} />立即刷新</Button>}
    >
      <QueryState loading={runs.isPending} error={runs.error} onRetry={() => runs.refetch()}>
        {latestPage ? (
          <div className="flex flex-col gap-6">
            <DataModeNotice meta={latestPage.meta} />
            <PageStatStrip>
              <PageStat label="最近成功" value={latestPage.health.lastSuccessAt ? formatDateTime(latestPage.health.lastSuccessAt) : '暂无'} note="任意查询的最近成功时间" />
              <PageStat label="24 小时成功率" value={latestPage.health.successRate24h === null ? '暂无' : formatPercent(latestPage.health.successRate24h)} note="成功任务 / 已完成任务" />
              <PageStat label="下次调度" value={latestPage.health.nextScheduledAt ? formatDateTime(latestPage.health.nextScheduledAt) : '未安排'} note="调度器计划时间" />
              <PageStat label="已加载任务" value={runItems.length} note="当前列表中的运行记录" />
            </PageStatStrip>

            <PageSurface title="采集可用性" description="成功率仅描述任务执行，不等同于所有航线都有可售报价。">
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-4 text-sm"><span>最近 24 小时</span><span className="font-mono">{latestPage.health.successRate24h === null ? '-' : formatPercent(latestPage.health.successRate24h)}</span></div>
                {latestPage.health.successRate24h === null ? (
                  <p className="text-sm text-muted-foreground">暂无足够的已完成任务可计算成功率。</p>
                ) : <Progress value={latestPage.health.successRate24h} />}
              </div>
            </PageSurface>

            <PageSurface title="最近运行" description="错误码用于区分网络拦截、结构漂移和业务空结果。" bodyClassName="p-0">
              {runItems.length ? (
                <Table>
                  <TableHeader>
                    <TableRow><TableHead>状态</TableHead><TableHead>查询</TableHead><TableHead>来源</TableHead><TableHead>日历 / 行程 / 报价</TableHead><TableHead>尝试</TableHead><TableHead>耗时</TableHead><TableHead>开始时间</TableHead><TableHead>数据状态</TableHead></TableRow>
                  </TableHeader>
                  <TableBody>
                    {runItems.map((run) => {
                      const meta = statusMeta[run.status]
                      const Icon = meta.icon
                      return (
                        <TableRow key={run.id}>
                          <TableCell>
                            <div className="flex flex-col items-start gap-1">
                              <Badge variant={meta.variant} className="gap-1"><Icon className="size-3" aria-hidden="true" />{meta.label}</Badge>
                              {run.warningCode === 'partial_fare_data' ? <Badge variant="outline">仅日历数据</Badge> : null}
                            </div>
                          </TableCell>
                          <TableCell className="font-medium">{run.queryLabel}</TableCell>
                          <TableCell>{run.provider}</TableCell>
                          <TableCell className="font-mono">{run.calendarObservations} / {run.itineraries} / {run.offers}</TableCell>
                          <TableCell className="font-mono">{run.attempt} / {run.maxAttempts}</TableCell>
                          <TableCell>{run.durationMs === null ? '-' : `${(run.durationMs / 1000).toFixed(1)}s`}</TableCell>
                          <TableCell>{formatDateTime(run.startedAt)}</TableCell>
                          <TableCell className="font-mono text-xs text-muted-foreground">{run.errorCode || run.upstreamStatus || '-'}</TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              ) : <EmptyState title="暂无采集运行记录" description="调度器完成第一次任务后，运行状态和错误码会显示在这里。" />}
            </PageSurface>
            {runs.hasNextPage ? (
              <div className="flex justify-center">
                <Button type="button" variant="outline" onClick={() => runs.fetchNextPage()} disabled={runs.isFetchingNextPage}>
                  {runs.isFetchingNextPage ? '加载中…' : '加载更早运行记录'}
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}
      </QueryState>
    </PageShell>
  )
}
