import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, ListTree, RefreshCw, ShieldAlert } from 'lucide-react'

import {
  fareQueryKeys,
  getCollectionOperations,
  getCollectionRuns,
  type CollectionRun,
  type CollectionRunStatus,
} from '@/api/fares'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageStat, PageStatStrip, PageSurface } from '@/components/layout/PageScaffold'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Progress } from '@/components/ui/progress'
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatDateTime, formatPercent } from '@/lib/formatters'
import { useCollectionRealtime } from '@/lib/use-collection-realtime'

const statusMeta: Record<CollectionRunStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; icon: typeof CheckCircle2 }> = {
  success: { label: '成功', variant: 'secondary', icon: CheckCircle2 },
  running: { label: '执行中', variant: 'default', icon: Clock3 },
  failed: { label: '失败', variant: 'destructive', icon: AlertTriangle },
  blocked: { label: '被拦截', variant: 'outline', icon: ShieldAlert },
}

export default function CollectionStatus() {
  const [selectedRun, setSelectedRun] = useState<CollectionRun | null>(null)
  const queryClient = useQueryClient()
  const realtime = useCollectionRealtime(() => {
    queryClient.invalidateQueries({ queryKey: fareQueryKeys.collectionRuns() })
    queryClient.invalidateQueries({ queryKey: fareQueryKeys.collectionOperations() })
  })
  const runs = useInfiniteQuery({
    queryKey: fareQueryKeys.collectionRuns(),
    queryFn: ({ pageParam }) => getCollectionRuns(typeof pageParam === 'string' ? pageParam : undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.nextCursor || undefined : undefined,
    refetchInterval: realtime.status === 'connected' ? 60_000 : 20_000,
  })
  const operations = useQuery({
    queryKey: fareQueryKeys.collectionOperations(),
    queryFn: getCollectionOperations,
    refetchInterval: realtime.status === 'connected' ? 60_000 : 20_000,
  })
  const runPages = useMemo(() => runs.data?.pages || [], [runs.data?.pages])
  const latestPage = runPages[0]
  const runItems = useMemo(() => runPages.flatMap((page) => page.items), [runPages])
  const readyQueueDepth = operations.data?.queues.available
    ? (operations.data.queues.collector || 0)
      + (operations.data.queues.default || 0)
      + (operations.data.queues.analysis || 0)
      + (operations.data.queues.notifications || 0)
    : null

  return (
    <PageShell
      title="采集状态"
      description="任务健康度、反爬拦截和解析失败都在这里保留证据，而不是静默丢弃。"
      width="7xl"
      actions={<Button type="button" variant="outline" onClick={() => { runs.refetch(); operations.refetch() }} disabled={runs.isFetching || operations.isFetching}><RefreshCw data-icon="inline-start" className={runs.isFetching || operations.isFetching ? 'animate-spin' : undefined} />立即刷新</Button>}
    >
      <QueryState loading={runs.isPending} error={runs.error} onRetry={() => runs.refetch()}>
        {latestPage ? (
          <div className="flex flex-col gap-6">
            <DataModeNotice meta={latestPage.meta} />
            <PageStatStrip>
              <PageStat label="最近成功" value={latestPage.health.lastSuccessAt ? formatDateTime(latestPage.health.lastSuccessAt) : '暂无'} note="任意查询的最近成功时间" />
              <PageStat label="24 小时成功率" value={latestPage.health.successRate24h === null ? '暂无' : formatPercent(latestPage.health.successRate24h)} note="成功任务 / 已完成任务" />
              <PageStat label="下次调度" value={latestPage.health.nextScheduledAt ? formatDateTime(latestPage.health.nextScheduledAt) : '未安排'} note="调度器计划时间" />
              <PageStat label="队列待消费" value={readyQueueDepth === null ? '不可用' : readyQueueDepth} note="采集、调度、分析与通知队列" />
            </PageStatStrip>

            <PageSurface title="采集可用性" description="成功率仅描述任务执行，不等同于所有航线都有可售报价。">
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-4 text-sm"><span>最近 24 小时</span><span className="font-mono">{latestPage.health.successRate24h === null ? '-' : formatPercent(latestPage.health.successRate24h)}</span></div>
                {latestPage.health.successRate24h === null ? (
                  <p className="text-sm text-muted-foreground">暂无足够的已完成任务可计算成功率。</p>
                ) : <Progress value={latestPage.health.successRate24h} />}
              </div>
            </PageSurface>

            <PageSurface title="任务与队列" description="用户任务来自数据库；队列数字是 Redis 中全局待消费任务。">
              {operations.data ? (
                <div className="grid gap-6 lg:grid-cols-2">
                  <div>
                    <h3 className="mb-3 text-sm font-medium">你的采集任务</h3>
                    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
                      <div><dt className="text-muted-foreground">待执行</dt><dd className="mt-1 font-mono text-base">{operations.data.runs.ready}</dd></div>
                      <div><dt className="text-muted-foreground">等待重试</dt><dd className="mt-1 font-mono text-base">{operations.data.runs.retrying}</dd></div>
                      <div><dt className="text-muted-foreground">已租约</dt><dd className="mt-1 font-mono text-base">{operations.data.runs.leased}</dd></div>
                      <div><dt className="text-muted-foreground">执行中</dt><dd className="mt-1 font-mono text-base">{operations.data.runs.running}</dd></div>
                      <div><dt className="text-muted-foreground">24 小时失败</dt><dd className="mt-1 font-mono text-base">{operations.data.runs.failed24h}</dd></div>
                    </dl>
                  </div>
                  <div>
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <h3 className="text-sm font-medium">全局待消费队列</h3>
                      <Badge variant={operations.data.queues.available ? 'secondary' : 'destructive'}>{operations.data.queues.available ? 'Redis 正常' : 'Redis 不可用'}</Badge>
                    </div>
                    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4 lg:grid-cols-2 xl:grid-cols-4">
                      <div><dt className="text-muted-foreground">采集</dt><dd className="mt-1 font-mono text-base">{operations.data.queues.collector ?? '-'}</dd></div>
                      <div><dt className="text-muted-foreground">调度</dt><dd className="mt-1 font-mono text-base">{operations.data.queues.default ?? '-'}</dd></div>
                      <div><dt className="text-muted-foreground">分析</dt><dd className="mt-1 font-mono text-base">{operations.data.queues.analysis ?? '-'}</dd></div>
                      <div><dt className="text-muted-foreground">通知</dt><dd className="mt-1 font-mono text-base">{operations.data.queues.notifications ?? '-'}</dd></div>
                    </dl>
                  </div>
                </div>
              ) : <p className="text-sm text-muted-foreground">{operations.isError ? '运维指标暂时不可用。' : '正在读取运维指标…'}</p>}
            </PageSurface>

            <PageSurface title="最近运行" description="错误码用于区分网络拦截、结构漂移和业务空结果。" bodyClassName="p-0">
              {runItems.length ? (
                <Table>
                  <TableHeader>
                    <TableRow><TableHead>状态</TableHead><TableHead>查询</TableHead><TableHead>来源</TableHead><TableHead>日历 / 行程 / 报价</TableHead><TableHead>尝试</TableHead><TableHead>耗时</TableHead><TableHead>开始时间</TableHead><TableHead>数据状态</TableHead><TableHead className="w-12"><span className="sr-only">详情</span></TableHead></TableRow>
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
                          <TableCell>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button type="button" size="icon" variant="ghost" onClick={() => setSelectedRun(run)} aria-label="查看采集诊断"><ListTree className="size-4" /></Button>
                              </TooltipTrigger>
                              <TooltipContent>查看采集诊断</TooltipContent>
                            </Tooltip>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              ) : <EmptyState title="暂无采集运行记录" description="调度器完成第一次任务后，运行状态和错误码会显示在这里。" />}
            </PageSurface>

            <PageSurface title="上游结构观察" description="仅显示结构指纹与顶层字段名，不保存或展示原始响应。" bodyClassName="p-0">
              {operations.data?.schemas.length ? (
                <Table>
                  <TableHeader>
                    <TableRow><TableHead>状态</TableHead><TableHead>来源</TableHead><TableHead>接口</TableHead><TableHead>结构指纹</TableHead><TableHead>顶层字段</TableHead><TableHead>最近出现</TableHead><TableHead>次数</TableHead></TableRow>
                  </TableHeader>
                  <TableBody>
                    {operations.data.schemas.map((signal) => (
                      <TableRow key={`${signal.provider}:${signal.endpoint}:${signal.schemaFingerprint}`}>
                        <TableCell><Badge variant={signal.state === 'new' ? 'destructive' : signal.state === 'current' ? 'secondary' : 'outline'}>{signal.state === 'new' ? '新结构' : signal.state === 'current' ? '当前' : '历史'}</Badge></TableCell>
                        <TableCell>{signal.provider}</TableCell>
                        <TableCell className="max-w-56 truncate font-mono text-xs" title={signal.endpoint}>{signal.endpoint}</TableCell>
                        <TableCell className="font-mono text-xs">{signal.schemaFingerprint.slice(0, 12)}</TableCell>
                        <TableCell className="max-w-80 truncate text-xs text-muted-foreground" title={signal.topLevelFields.join(', ')}>{signal.topLevelFields.join(', ') || '-'}</TableCell>
                        <TableCell>{formatDateTime(signal.lastSeenAt)}</TableCell>
                        <TableCell className="font-mono">{signal.occurrenceCount}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : <EmptyState title="暂无结构观察" description="采集到受支持的页面响应后，这里会显示脱敏后的结构指纹。" />}
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

      <Sheet open={selectedRun !== null} onOpenChange={(open) => { if (!open) setSelectedRun(null) }}>
        <SheetContent className="overflow-y-auto sm:w-[36rem] sm:max-w-[90vw]">
          <SheetHeader>
            <SheetTitle>采集诊断</SheetTitle>
            <SheetDescription>{selectedRun?.queryLabel || '采集任务'} · {selectedRun ? formatDateTime(selectedRun.startedAt) : ''}</SheetDescription>
          </SheetHeader>
          <SheetBody className="border-t">
            {selectedRun ? (
              <div className="flex flex-col gap-6">
                <dl className="grid grid-cols-2 gap-4 text-sm">
                  <div><dt className="text-muted-foreground">上游状态</dt><dd className="mt-1 font-mono text-xs">{selectedRun.upstreamStatus || '-'}</dd></div>
                  <div><dt className="text-muted-foreground">错误码</dt><dd className="mt-1 font-mono text-xs">{selectedRun.errorCode || '-'}</dd></div>
                  <div className="col-span-2"><dt className="text-muted-foreground">结构指纹</dt><dd className="mt-1 break-all font-mono text-xs">{selectedRun.schemaFingerprint || '-'}</dd></div>
                </dl>
                <div>
                  <h3 className="mb-3 text-sm font-medium">诊断记录</h3>
                  {selectedRun.diagnostics.length ? (
                    <div className="divide-y rounded-md border">
                      {selectedRun.diagnostics.map((diagnostic, index) => (
                        <div key={`${diagnostic.code}:${diagnostic.path || ''}:${index}`} className="space-y-2 p-4">
                          <div className="flex items-start justify-between gap-3"><span className="font-mono text-xs">{diagnostic.code}</span><Badge variant={diagnostic.severity === 'error' ? 'destructive' : 'outline'}>{diagnostic.severity === 'error' ? '错误' : '警告'}</Badge></div>
                          <p className="text-sm leading-6">{diagnostic.message}</p>
                          {diagnostic.path ? <p className="break-all font-mono text-xs text-muted-foreground">{diagnostic.path}</p> : null}
                          {diagnostic.retryable !== null ? <p className="text-xs text-muted-foreground">{diagnostic.retryable ? '可自动重试' : '不会自动重试'}</p> : null}
                        </div>
                      ))}
                    </div>
                  ) : <p className="text-sm text-muted-foreground">该任务没有结构或捕获诊断记录。</p>}
                </div>
              </div>
            ) : null}
          </SheetBody>
        </SheetContent>
      </Sheet>
    </PageShell>
  )
}
