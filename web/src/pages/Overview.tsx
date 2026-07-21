import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDownRight, ArrowUpRight, ExternalLink, RefreshCw } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, Tooltip, XAxis, YAxis } from 'recharts'
import { useNavigate } from 'react-router-dom'

import { fareQueryKeys, getDashboardOverview } from '@/api/fares'
import { DataModeNotice } from '@/components/fare/DataModeNotice'
import { PriceFreshnessBadge } from '@/components/fare/PriceFreshnessBadge'
import { QueryState } from '@/components/fare/QueryState'
import { PageShell, PageStat, PageStatStrip, PageSurface } from '@/components/layout/PageScaffold'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ChartContainer, type ChartConfig } from '@/components/ui/chart'
import { EmptyState } from '@/components/ui/empty-state'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatCurrency, formatDateTime, formatPercent } from '@/lib/formatters'

const chartConfig: ChartConfig = {
  price: { label: '最低价', color: 'hsl(var(--chart-1))' },
}

function formatFare(value: number | null): string {
  return value === null ? '暂无' : formatCurrency(value / 100)
}

export default function Overview() {
  const navigate = useNavigate()
  const overview = useQuery({ queryKey: fareQueryKeys.overview(), queryFn: getDashboardOverview })
  const chartData = useMemo(
    () => (overview.data?.trend || []).map((point) => ({
      date: new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(new Date(point.observedAt)),
      price: point.priceMinor / 100,
    })),
    [overview.data?.trend],
  )
  const delta = overview.data?.stats.priceChangePercent ?? null
  const deltaUp = delta !== null && delta >= 0

  return (
    <PageShell
      title="总览"
      description="把已订阅航线、最新报价和采集质量放在一个可核验的工作台里。"
      width="7xl"
      actions={
        <Button type="button" variant="outline" onClick={() => overview.refetch()} disabled={overview.isFetching}>
          <RefreshCw data-icon="inline-start" className={overview.isFetching ? 'animate-spin' : undefined} />
          刷新数据
        </Button>
      }
    >
      <QueryState loading={overview.isPending} error={overview.error} onRetry={() => overview.refetch()}>
        {overview.data ? (
          <div className="flex flex-col gap-6">
            <DataModeNotice meta={overview.data.meta} />

            <PageStatStrip>
              <PageStat label="最近最低价" value={formatFare(overview.data.stats.lowestPriceMinor)} note="已观测航线中的最低报价；请查看数据新鲜度" />
              <PageStat
                label="近 24 小时变化"
                value={
                  delta === null ? '暂无' : (
                    <span className="inline-flex items-center gap-1">
                      {deltaUp ? <ArrowUpRight className="size-5 text-destructive" /> : <ArrowDownRight className="size-5 text-primary" />}
                      {formatPercent(Math.abs(delta))}
                    </span>
                  )
                }
                note={delta === null ? '暂无可比较的历史数据' : deltaUp ? '价格上涨' : '价格下降'}
              />
              <PageStat label="活跃订阅" value={overview.data.stats.activeSubscriptions} note="当前用户的有效规则" />
              <PageStat label="采集成功率" value={overview.data.stats.collectionSuccessRate === null ? '暂无' : formatPercent(overview.data.stats.collectionSuccessRate)} note="最近 24 小时" />
            </PageStatStrip>

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <PageSurface title="价格趋势" description="以人民币展示已保存的最低报价观测点。" bodyClassName="pt-2">
                {chartData.length ? (
                  <ChartContainer config={chartConfig} className="h-[300px] w-full">
                    <AreaChart data={chartData} margin={{ top: 12, right: 12, left: -12, bottom: 0 }}>
                      <defs>
                        <linearGradient id="farePriceFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--color-price)" stopOpacity={0.3} />
                          <stop offset="100%" stopColor="var(--color-price)" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="date" axisLine={false} tickLine={false} tickMargin={8} />
                      <YAxis axisLine={false} tickLine={false} tickFormatter={(value) => `¥${value}`} width={54} />
                      <Tooltip formatter={(value) => [formatCurrency(Number(value)), '最低价']} />
                      <Area type="monotone" dataKey="price" stroke="var(--color-price)" fill="url(#farePriceFill)" strokeWidth={2} dot={false} isAnimationActive={false} />
                    </AreaChart>
                  </ChartContainer>
                ) : <p className="py-20 text-center text-sm text-muted-foreground">暂无足够的历史观测点。</p>}
              </PageSurface>

              <PageSurface title="数据边界" description="实时性和可解释性比猜测更重要。">
                <div className="flex flex-col gap-4">
                  <div className="rounded-md border bg-muted/20 p-4">
                    <p className="text-sm font-medium">报价时间</p>
                    <p className="mt-1 text-sm text-muted-foreground">页面显示采集时间，不代表当前仍可售。</p>
                  </div>
                  <div className="rounded-md border bg-muted/20 p-4">
                    <p className="text-sm font-medium">数据源</p>
                    <p className="mt-1 text-sm text-muted-foreground">当前接入适配器为 Ctrip；失败和反爬状态会在采集页单独记录。</p>
                  </div>
                  <div className="rounded-md border bg-muted/20 p-4">
                    <p className="text-sm font-medium">路线数量</p>
                    <p className="mt-1 text-sm text-muted-foreground">已保存 {overview.data.stats.routesTracked} 条路线，跨用户可复用同一采集结果。</p>
                  </div>
                </div>
              </PageSurface>
            </div>

            <PageSurface
              title="关注中的航线"
              description="点击路线进入价格历史；暂无报价的路线不会被伪造为零价。"
              actions={<Button type="button" variant="outline" size="sm" onClick={() => navigate('/subscriptions')}>管理订阅 <ExternalLink data-icon="inline-end" /></Button>}
              bodyClassName="p-0"
            >
              {overview.data.routes.length ? (
                <Table containerClassName="max-h-none">
                  <TableHeader>
                    <TableRow>
                      <TableHead>路线</TableHead>
                      <TableHead>类型</TableHead>
                      <TableHead>筛选</TableHead>
                      <TableHead>最新报价</TableHead>
                      <TableHead>观测时间</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {overview.data.routes.map((route) => (
                      <TableRow key={route.id} className="cursor-pointer" onClick={() => navigate(`/history?route=${route.id}`)}>
                        <TableCell className="font-medium">{route.originName} ({route.origin}) → {route.destinationName} ({route.destination})</TableCell>
                        <TableCell>{route.tripType === 'roundtrip' ? '往返' : '单程'}</TableCell>
                        <TableCell>{route.directOnly ? <Badge variant="secondary">直飞</Badge> : <span className="text-muted-foreground">不限</span>}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono">{formatFare(route.latestPriceMinor)}</span>
                            <PriceFreshnessBadge status={route.priceStatus} />
                          </div>
                        </TableCell>
                        <TableCell className="text-muted-foreground">{route.observedAt ? formatDateTime(route.observedAt) : '尚未采集'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : <EmptyState title="还没有关注航线" description="创建第一条订阅并完成采集后，路线和报价会显示在这里。" actions={<Button type="button" onClick={() => navigate('/subscriptions')}>创建订阅</Button>} />}
            </PageSurface>
          </div>
        ) : null}
      </QueryState>
    </PageShell>
  )
}
