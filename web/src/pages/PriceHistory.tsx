import { useMemo, useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { CalendarDays, LineChart as LineChartIcon, LoaderCircle } from 'lucide-react'
import { Line, LineChart, CartesianGrid, Tooltip, XAxis, YAxis } from 'recharts'
import { useSearchParams } from 'react-router-dom'

import { fareQueryKeys, getCalendarPrices, getDashboardOverview, getPriceHistory, type HistoryResolution } from '@/api/fares'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { LowFareCalendar } from '@/components/fare/LowFareCalendar'
import { PriceFreshnessBadge } from '@/components/fare/PriceFreshnessBadge'
import { QueryState } from '@/components/fare/QueryState'
import { RoundTripMatrix } from '@/components/fare/RoundTripMatrix'
import { PageShell, PageStat, PageStatStrip, PageSurface } from '@/components/layout/PageScaffold'
import { ChartContainer, type ChartConfig } from '@/components/ui/chart'
import { EmptyState } from '@/components/ui/empty-state'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatCurrency, formatDateTime } from '@/lib/formatters'

const chartConfig: ChartConfig = {
  price: { label: '含税价格', color: 'hsl(var(--chart-1))' },
}

function formatFare(value: number | null): string {
  return value === null ? '暂无' : formatCurrency(value / 100)
}

function isRoundTrip(route: { tripType: 'oneway' | 'roundtrip' } | null | undefined): boolean {
  return route?.tripType === 'roundtrip'
}

export default function PriceHistory() {
  const [searchParams, setSearchParams] = useSearchParams()
  const overview = useQuery({ queryKey: fareQueryKeys.overview(), queryFn: getDashboardOverview })
  const routeId = searchParams.get('route') || overview.data?.routes[0]?.id || ''
  const requestedView = searchParams.get('view')
  const selectedRoute = overview.data?.routes.find((route) => route.id === routeId)
  const view: 'history' | 'calendar' | 'matrix' = requestedView === 'calendar'
    || (requestedView === 'matrix' && isRoundTrip(selectedRoute))
    ? requestedView
    : 'history'
  const [days, setDays] = useState(Number(searchParams.get('days') || 90))
  const [resolution, setResolution] = useState<HistoryResolution>((searchParams.get('resolution') as HistoryResolution) || 'auto')

  const history = useInfiniteQuery({
    queryKey: fareQueryKeys.history(routeId, { days, resolution }),
    queryFn: ({ pageParam }) => getPriceHistory(routeId, {
      days,
      resolution,
      limit: resolution === 'raw' ? 200 : 500,
      cursor: typeof pageParam === 'string' ? pageParam : undefined,
    }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.nextCursor || undefined : undefined,
    enabled: Boolean(routeId) && view === 'history',
  })
  const calendar = useInfiniteQuery({
    queryKey: fareQueryKeys.calendar(routeId),
    queryFn: ({ pageParam }) => getCalendarPrices(routeId, {
      limit: 200,
      cursor: typeof pageParam === 'string' ? pageParam : undefined,
    }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.hasMore ? lastPage.nextCursor || undefined : undefined,
    enabled: Boolean(routeId) && (view === 'calendar' || view === 'matrix'),
  })

  const historyPages = useMemo(() => history.data?.pages || [], [history.data?.pages])
  const historySummary = historyPages[0]
  const historyPoints = useMemo(
    () => historyPages.flatMap((page) => page.points).sort((left, right) => left.observedAt.localeCompare(right.observedAt)),
    [historyPages],
  )
  const calendarPages = useMemo(() => calendar.data?.pages || [], [calendar.data?.pages])
  const calendarSummary = calendarPages[0]
  const calendarPoints = useMemo(() => calendarPages.flatMap((page) => page.points), [calendarPages])
  const chartData = useMemo(() => historyPoints.map((point) => ({
    observedAt: point.observedAt,
    date: new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(new Date(point.observedAt)),
    price: (point.averagePriceMinor ?? point.priceMinor) / 100,
  })), [historyPoints])

  const updateRoute = (value: string) => setSearchParams({ route: value, view })
  const updateView = (value: string) => setSearchParams({ route: routeId, view: value })
  const updateDays = (value: string) => {
    const nextDays = Number(value)
    setDays(nextDays)
    setSearchParams({ route: routeId, view, days: String(nextDays), resolution })
  }
  const updateResolution = (value: string) => {
    const nextResolution = value as HistoryResolution
    setResolution(nextResolution)
    setSearchParams({ route: routeId, view, days: String(days), resolution: nextResolution })
  }

  return (
    <PageShell
      title="价格历史"
      description="查看持久化观测、按日期聚合的低价，以及往返日期组合的真实总价。"
      width="7xl"
      actions={overview.data?.routes.length ? (
        <Select value={routeId} onValueChange={updateRoute}>
          <SelectTrigger className="w-full sm:w-[240px]" aria-label="选择航线"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {overview.data.routes.map((route) => <SelectItem key={route.id} value={route.id}>{route.origin} → {route.destination} · {route.tripType === 'roundtrip' ? '往返' : '单程'}</SelectItem>)}
            </SelectGroup>
          </SelectContent>
        </Select>
      ) : null}
    >
      <QueryState loading={overview.isPending} error={overview.error} onRetry={() => overview.refetch()}>
        {routeId && overview.data ? (
          <Tabs value={view} onValueChange={updateView} className="flex flex-col gap-5">
            <TabsList className="w-fit max-w-full overflow-x-auto" aria-label="路线数据视图">
              <TabsTrigger value="history"><LineChartIcon data-icon="inline-start" />历史趋势</TabsTrigger>
              <TabsTrigger value="calendar"><CalendarDays data-icon="inline-start" />低价日历</TabsTrigger>
              {isRoundTrip(selectedRoute) ? <TabsTrigger value="matrix">往返矩阵</TabsTrigger> : null}
            </TabsList>

            <TabsContent value="history">
              <QueryState loading={history.isPending} error={history.error} onRetry={() => history.refetch()}>
                {historySummary ? (
                  <div className="flex flex-col gap-6">
                    <DataModeNotice meta={historySummary.meta} />
                    {historySummary.route ? (
                      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                        <PriceFreshnessBadge status={historySummary.route.priceStatus} />
                        <span>状态根据最近观测时间和订阅采集间隔计算。</span>
                      </div>
                    ) : null}
                    <PageStatStrip>
                      <PageStat label="历史最低" value={formatFare(historySummary.minPriceMinor)} note={`${days} 天范围`} />
                      <PageStat label="历史最高" value={formatFare(historySummary.maxPriceMinor)} note={`${historySummary.resolution} 聚合`} />
                      <PageStat label="平均价格" value={formatFare(historySummary.averagePriceMinor)} note="已观测样本平均" />
                      <PageStat label="观测样本" value={historySummary.sampleCount} note="持久化价格点" />
                    </PageStatStrip>

                    <PageSurface
                      title={historySummary.route ? `${historySummary.route.originName} → ${historySummary.route.destinationName}` : '价格走势'}
                      description="可切换时间范围与聚合粒度；raw 模式支持游标继续加载。"
                      actions={(
                        <div className="flex flex-wrap gap-2">
                          <Select value={String(days)} onValueChange={updateDays}>
                            <SelectTrigger className="w-28" aria-label="历史范围"><SelectValue /></SelectTrigger>
                            <SelectContent><SelectGroup><SelectItem value="7">近 7 天</SelectItem><SelectItem value="30">近 30 天</SelectItem><SelectItem value="90">近 90 天</SelectItem><SelectItem value="180">近 180 天</SelectItem><SelectItem value="365">近 1 年</SelectItem></SelectGroup></SelectContent>
                          </Select>
                          <Select value={resolution} onValueChange={updateResolution}>
                            <SelectTrigger className="w-28" aria-label="聚合粒度"><SelectValue /></SelectTrigger>
                            <SelectContent><SelectGroup><SelectItem value="auto">自动聚合</SelectItem><SelectItem value="raw">原始点</SelectItem><SelectItem value="hour">按小时</SelectItem><SelectItem value="day">按天</SelectItem></SelectGroup></SelectContent>
                          </Select>
                        </div>
                      )}
                    >
                      {chartData.length > 1 ? (
                        <ChartContainer config={chartConfig} className="h-[360px] w-full">
                          <LineChart data={chartData} margin={{ top: 16, right: 12, left: -8, bottom: 0 }}>
                            <CartesianGrid vertical={false} />
                            <XAxis dataKey="date" axisLine={false} tickLine={false} tickMargin={8} />
                            <YAxis axisLine={false} tickLine={false} tickFormatter={(value) => `¥${value}`} width={58} domain={['dataMin - 100', 'dataMax + 100']} />
                            <Tooltip formatter={(value) => [formatCurrency(Number(value)), '含税价格']} labelFormatter={(_, payload) => payload[0]?.payload?.observedAt ? formatDateTime(payload[0].payload.observedAt) : ''} />
                            <Line type="monotone" dataKey="price" stroke="var(--color-price)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
                          </LineChart>
                        </ChartContainer>
                      ) : <EmptyState title="历史数据不足" description="至少需要两个不同时间的价格观测点才能绘制趋势。" />}
                    </PageSurface>

                    <PageSurface title="最近观测" description="每行对应一个持久化观测点，便于核对图表和数据新鲜度。" bodyClassName="p-0">
                      <Table containerClassName="max-h-none">
                        <TableHeader><TableRow><TableHead>采集时间</TableHead><TableHead>价格</TableHead><TableHead>样本</TableHead></TableRow></TableHeader>
                        <TableBody>
                          {[...historyPoints].reverse().slice(0, 20).map((point) => <TableRow key={`${point.observedAt}-${point.priceMinor}`}><TableCell>{formatDateTime(point.observedAt)}</TableCell><TableCell className="font-mono">{formatFare(point.priceMinor)}</TableCell><TableCell>{point.sampleCount ?? 1}</TableCell></TableRow>)}
                        </TableBody>
                      </Table>
                      {history.hasNextPage ? <div className="flex justify-center border-t p-4"><button type="button" className="text-sm font-medium text-primary" onClick={() => history.fetchNextPage()} disabled={history.isFetchingNextPage}>{history.isFetchingNextPage ? <LoaderCircle className="mr-2 inline animate-spin" aria-hidden="true" /> : null}加载更早观测</button></div> : null}
                    </PageSurface>
                  </div>
                ) : <EmptyState title="暂无历史观测" description="完成一次采集后，价格历史会出现在这里。" />}
              </QueryState>
            </TabsContent>

            <TabsContent value="calendar">
              <QueryState loading={calendar.isPending} error={calendar.error} onRetry={() => calendar.refetch()}>
                {calendarSummary ? (
                  <PageSurface title="低价日历" description="按出发日期展示服务端已保存的最低报价。">
                    <DataModeNotice meta={calendarSummary.meta} />
                    <div className="mt-5"><LowFareCalendar points={calendarPoints} /></div>
                    {calendar.hasNextPage ? <div className="mt-5 flex justify-center"><button type="button" className="text-sm font-medium text-primary" onClick={() => calendar.fetchNextPage()} disabled={calendar.isFetchingNextPage}>{calendar.isFetchingNextPage ? '加载中…' : '加载更多日期'}</button></div> : null}
                  </PageSurface>
                ) : <EmptyState title="暂无日历观测" description="当前路线还没有按日期保存的低价数据。" />}
              </QueryState>
            </TabsContent>

            <TabsContent value="matrix">
              <QueryState loading={calendar.isPending} error={calendar.error} onRetry={() => calendar.refetch()}>
                {calendarSummary ? (
                  <PageSurface title="往返日期矩阵" description="每个格子对应一次已观测的出发日与返程日组合。">
                    <DataModeNotice meta={calendarSummary.meta} />
                    <div className="mt-5"><RoundTripMatrix points={calendarPoints} /></div>
                  </PageSurface>
                ) : <EmptyState title="暂无往返矩阵" description="当前路线还没有保存往返日期组合的价格数据。" />}
              </QueryState>
            </TabsContent>
          </Tabs>
        ) : <EmptyState title="暂无可用航线" description="创建个人订阅并完成第一次采集后，这里会显示价格历史。" />}
      </QueryState>
    </PageShell>
  )
}
